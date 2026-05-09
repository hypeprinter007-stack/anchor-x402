"""Dual-chain anchor: post a 32-byte hash to Base mainnet (calldata)
and Solana mainnet (Memo program) in parallel.

Stateless. Pure function of (hash, treasury keys, RPC URLs) → tx hashes.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("anchor")

BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
_MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


def _treasury_evm_key() -> str:
    return os.getenv("TREASURY_PRIVATE_KEY", "")


def _treasury_solana_key() -> str:
    return os.getenv("SOLANA_TREASURY_KEY", "")


def anchor_to_base(merkle_root: str) -> str:
    """Post merkle_root as EIP-1559 calldata to Base. Returns 0x-prefixed tx hash."""
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
    key = _treasury_evm_key()
    if not key:
        raise RuntimeError("TREASURY_PRIVATE_KEY not set")
    acct = w3.eth.account.from_key(key)
    nonce = w3.eth.get_transaction_count(acct.address)
    gas_price = w3.eth.gas_price
    tx = {
        "from": acct.address,
        "to": acct.address,
        "value": 0,
        "data": "0x" + merkle_root,
        "nonce": nonce,
        "chainId": 8453,
        "maxFeePerGas": gas_price,
        "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
    }
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = w3.eth.account.sign_transaction(tx, key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return "0x" + tx_hash.hex()


def anchor_to_solana(merkle_root: str) -> str:
    """Post merkle_root via Solana Memo program. Returns base58 tx signature."""
    import base58
    import requests as rq
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.pubkey import Pubkey
    from solders.transaction import Transaction

    sol_key = _treasury_solana_key()
    if not sol_key:
        raise RuntimeError("SOLANA_TREASURY_KEY not set")
    kp = Keypair.from_bytes(base58.b58decode(sol_key))

    def _rpc(method, params, retries: int = 2):
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = rq.post(
                    SOLANA_RPC_URL,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=10,
                )
                r.raise_for_status()
                j = r.json()
                if "error" in j:
                    raise RuntimeError(f"Solana RPC: {j['error']}")
                return j["result"]
            except Exception as e:
                last_err = e
                if attempt < retries:
                    log.warning("solana RPC %s failed (attempt %d): %s; retrying", method, attempt + 1, e)
                    time.sleep(1)
                    continue
                raise
        raise last_err  # unreachable

    blockhash = _rpc("getLatestBlockhash", [{"commitment": "finalized"}])["value"]["blockhash"]
    ix = Instruction(
        program_id=Pubkey.from_string(_MEMO_PROGRAM_ID),
        accounts=[AccountMeta(pubkey=kp.pubkey(), is_signer=True, is_writable=True)],
        data=merkle_root.encode("utf-8"),
    )
    msg = Message.new_with_blockhash([ix], kp.pubkey(), Hash.from_string(blockhash))
    tx = Transaction([kp], msg, Hash.from_string(blockhash))
    raw = bytes(tx)
    return _rpc("sendTransaction", [base58.b58encode(raw).decode(), {"encoding": "base58", "preflightCommitment": "processed"}])


def anchor_dual_chain(merkle_root: str) -> dict:
    """Anchor merkle_root on Base + Solana in parallel. Best-effort on Solana
    (returns base+null if Solana fails so the caller still gets Base proof).
    """
    base_tx: str = ""
    solana_tx: str | None = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_base = pool.submit(anchor_to_base, merkle_root)
        f_sol = pool.submit(anchor_to_solana, merkle_root)
        base_tx = f_base.result(timeout=30)
        try:
            solana_tx = f_sol.result(timeout=20)
        except Exception as e:
            log.warning("solana anchor failed: %s: %s", type(e).__name__, e)
            solana_tx = None
    return {"base_tx": base_tx, "solana_tx": solana_tx, "merkle_root": merkle_root}
