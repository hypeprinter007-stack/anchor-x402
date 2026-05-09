"""Transaction decoder: fetch + structure an on-chain tx by hash.

Stateless except for an in-process cache. Once a tx is mined and
decoded successfully, we cache it forever (mined data is immutable),
keyed by `(chain, tx_hash)`. The Lambda warm container re-uses the
cache; a cold start re-fetches from RPC.

EVM (base, ethereum):
    web3.py — eth_getTransactionByHash + eth_getTransactionReceipt.

Solana:
    raw JSON-RPC — getTransaction with maxSupportedTransactionVersion=0
    and jsonParsed encoding (solders is used in sibling services for
    signing; for read-only decode we just call the RPC directly).
"""
from __future__ import annotations

import logging
import os
import re
from decimal import Decimal
from typing import Any, Literal

log = logging.getLogger("tx_decode")

EVM_CHAIN = Literal["base", "ethereum"]
Chain = Literal["base", "ethereum", "solana"]

_BASE_RPC = os.getenv("TX_DECODE_BASE_RPC", "https://mainnet.base.org")
_ETH_RPC = os.getenv("TX_DECODE_ETH_RPC", "https://ethereum.publicnode.com")
_SOL_RPC = os.getenv("TX_DECODE_SOL_RPC", "https://api.mainnet-beta.solana.com")

_EVM_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SOL_SIG_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,90}$")

# Module-level cache. Keyed by (chain, tx_hash_normalized) -> decoded dict.
# Only populated on a *successful, mined* decode. Pending / not-found
# results are NOT cached so the caller can retry.
_CACHE: dict[tuple[str, str], dict] = {}


def _normalize_evm_hash(h: str) -> str:
    if not h.startswith("0x") and not h.startswith("0X"):
        h = "0x" + h
    if not _EVM_HASH_RE.match(h):
        raise ValueError("invalid EVM tx hash; expected 0x + 64 hex chars")
    return h.lower()


def _normalize_sol_sig(s: str) -> str:
    if not _SOL_SIG_RE.match(s):
        raise ValueError("invalid Solana signature; expected base58 (64-90 chars)")
    return s


def _evm_rpc_for(chain: str) -> tuple[str, str]:
    """Return (rpc_url, native_currency) for an EVM chain."""
    if chain == "base":
        return _BASE_RPC, "ETH"
    if chain == "ethereum":
        return _ETH_RPC, "ETH"
    raise ValueError(f"unsupported EVM chain: {chain}")


def _decode_evm(chain: EVM_CHAIN, tx_hash: str) -> dict:
    from web3 import Web3

    rpc_url, native_currency = _evm_rpc_for(chain)
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    tx = w3.eth.get_transaction(tx_hash)
    if tx is None:
        raise RuntimeError("tx not found")
    if tx.get("blockNumber") is None:
        raise RuntimeError("tx pending — not yet mined")

    receipt = w3.eth.get_transaction_receipt(tx_hash)
    block = w3.eth.get_block(tx["blockNumber"])
    block_ts = int(block["timestamp"])

    raw_input = tx.get("input", "0x")
    if isinstance(raw_input, (bytes, bytearray)):
        input_hex = "0x" + bytes(raw_input).hex()
    else:
        input_hex = str(raw_input)
        if not input_hex.startswith("0x"):
            input_hex = "0x" + input_hex

    value_wei = int(tx["value"])
    value_eth = (Decimal(value_wei) / Decimal(10**18)).normalize()
    # Avoid scientific-notation surprises on very small values.
    value_eth_str = format(value_eth, "f") if value_eth != 0 else "0"

    to_addr = tx.get("to")
    return {
        "chain": chain,
        "tx_hash": tx_hash,
        "block_number": int(tx["blockNumber"]),
        "timestamp": block_ts,
        "from_address": str(tx["from"]),
        "to_address": str(to_addr) if to_addr else None,
        "value_wei": str(value_wei),
        "value_eth": value_eth_str,
        "gas_used": int(receipt["gasUsed"]),
        "status": int(receipt.get("status", 0)),
        "input_calldata_hex": input_hex,
        "native_currency": native_currency,
    }


def _decode_solana(tx_hash: str) -> dict:
    import requests as rq

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            tx_hash,
            {
                "encoding": "jsonParsed",
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }
    r = rq.post(_SOL_RPC, json=body, timeout=15)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"Solana RPC error: {j['error']}")
    result = j.get("result")
    if not result:
        raise RuntimeError("tx not found or not yet finalized")

    meta = result.get("meta") or {}
    transaction = result.get("transaction") or {}
    message = transaction.get("message") or {}

    # Status: Solana returns {"Ok": null} on success, {"Err": ...} on failure.
    raw_status = meta.get("status") or {}
    status = "success" if "Ok" in raw_status else "failed"

    # Signers: account keys with signer=true (jsonParsed includes the flag).
    signers: list[str] = []
    for ak in message.get("accountKeys") or []:
        if isinstance(ak, dict):
            if ak.get("signer"):
                pk = ak.get("pubkey")
                if pk:
                    signers.append(pk)
        elif isinstance(ak, str):
            # legacy/unparsed: first N keys are signers per header
            pass
    if not signers:
        # Fallback for non-jsonParsed shapes: use the first numRequiredSignatures keys.
        header = message.get("header") or {}
        n_sig = int(header.get("numRequiredSignatures", 0))
        keys = message.get("accountKeys") or []
        for k in keys[:n_sig]:
            if isinstance(k, str):
                signers.append(k)
            elif isinstance(k, dict) and k.get("pubkey"):
                signers.append(k["pubkey"])

    program_calls: list[dict[str, Any]] = []
    for ix in message.get("instructions") or []:
        if not isinstance(ix, dict):
            continue
        entry: dict[str, Any] = {
            "program": ix.get("program"),
            "program_id": ix.get("programId"),
        }
        parsed = ix.get("parsed")
        if parsed is not None:
            if isinstance(parsed, dict):
                entry["type"] = parsed.get("type")
                entry["info"] = parsed.get("info")
            else:
                entry["data"] = parsed
        else:
            # Unparsed instruction — keep raw fields.
            entry["accounts"] = ix.get("accounts")
            entry["data"] = ix.get("data")
        program_calls.append(entry)

    return {
        "chain": "solana",
        "tx_hash": tx_hash,
        "slot": int(result.get("slot", 0)),
        "block_time": result.get("blockTime"),
        "fee_lamports": int(meta.get("fee", 0)),
        "status": status,
        "signers": signers,
        "program_calls": program_calls,
    }


def decode(chain: Chain, tx_hash: str) -> dict:
    """Decode a transaction by chain + hash. Caches successful decodes forever."""
    if chain == "base" or chain == "ethereum":
        normalized = _normalize_evm_hash(tx_hash)
    elif chain == "solana":
        normalized = _normalize_sol_sig(tx_hash)
    else:
        raise ValueError(f"unsupported chain: {chain!r} (must be base|ethereum|solana)")

    cache_key = (chain, normalized)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    if chain == "solana":
        decoded = _decode_solana(normalized)
    else:
        decoded = _decode_evm(chain, normalized)

    _CACHE[cache_key] = decoded
    return decoded


def cache_size() -> int:
    """For diagnostics / tests."""
    return len(_CACHE)
