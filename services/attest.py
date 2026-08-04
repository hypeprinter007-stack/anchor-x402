"""Decision attestation: verify a signature over (input_hash, output_hash, decision)
and dual-chain anchor the resulting Merkle root.

Domain-separated message format (signed verbatim by the agent's signer):

    anchor-x402/attest/v1
    input=<input_hash>
    output=<output_hash>
    decision=<decision>

The signature must cover the exact UTF-8 bytes above, with `\n` line
separators and no trailing newline. Two schemes supported:

  - eip191:  EVM personal_sign (Metamask `eth_sign`-style prefix). Signer
             address is recovered from signature; no signer_pubkey required.
  - ed25519: Solana wallet (Phantom). Caller must supply signer_pubkey.

Domain separation prevents cross-app replay — a signature over
"anchor-x402/attest/v1\n…" cannot be reused as a Counsel officer
signature, an EVM transaction, or any other app's payload.
"""
from __future__ import annotations

import hashlib
from typing import Literal


def build_message(input_hash: str, output_hash: str, decision: str) -> bytes:
    """Domain-separated message bytes that the signer signs."""
    text = (
        "anchor-x402/attest/v1\n"
        f"input={input_hash}\n"
        f"output={output_hash}\n"
        f"decision={decision}"
    )
    return text.encode("utf-8")


def attest_merkle_root(input_hash: str, output_hash: str, decision: str) -> str:
    """The 32-byte digest that gets anchored on-chain.

    SHA-256 over the same domain-separated string, hex-encoded. Anyone
    holding (input_hash, output_hash, decision) can reproduce this and
    cross-check the on-chain anchor.
    """
    return hashlib.sha256(build_message(input_hash, output_hash, decision)).hexdigest()


def verify_eip191(message: bytes, signature_hex: str) -> str:
    """Recover the EVM signer address from a personal_sign signature.

    Returns 0x-prefixed checksum address. Raises if the signature is
    malformed or doesn't recover.
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct
    if signature_hex.startswith("0x") or signature_hex.startswith("0X"):
        signature_hex = signature_hex[2:]
    msg = encode_defunct(message)
    addr = Account.recover_message(msg, signature=bytes.fromhex(signature_hex))
    return addr


def verify_ed25519(message: bytes, signature_b58: str, signer_pubkey: str) -> bool:
    """Verify an Ed25519 signature against signer_pubkey (base58).

    Returns True iff the signature is mathematically valid. Caller must
    decide whether the recovered pubkey is authorized.
    """
    import base58
    from solders.pubkey import Pubkey
    from solders.signature import Signature
    sig = Signature.from_bytes(base58.b58decode(signature_b58))
    pk = Pubkey.from_string(signer_pubkey)
    return sig.verify(pk, message)


def sign_with_treasury(input_hash: str, output_hash: str, decision: str) -> tuple[str, str]:
    """Sign the attest message with the anchor-x402 treasury key (eip191).

    For agents that have no wallet of their own (e.g. calling over MCP). The
    on-chain anchor still carries trustless temporal proof regardless of whose
    key signed; the signature adds a treasury-attributable statement on top.

    Returns (signature_hex, signer_address).
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from services import secrets

    key = secrets.get("treasury_evm_key", env_fallback="TREASURY_PRIVATE_KEY")
    if not key:
        raise RuntimeError("treasury EVM key unavailable")
    acct = Account.from_key(key)
    signed = acct.sign_message(encode_defunct(build_message(input_hash, output_hash, decision)))
    sig = signed.signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return sig, acct.address


def confirm_base_anchor(base_tx: str, expected_root: str) -> dict:
    """Confirm a Base tx anchored exactly `expected_root` as its calldata.

    Anchors are self-transfer txs whose calldata is `0x<root>` (see
    services/anchor.anchor_to_base). Stateless — reads the chain, stores
    nothing. Never raises; returns a structured verdict.
    """
    import os

    from web3 import Web3

    out = {"root_matches": False, "confirmed": False, "block": None, "anchored_at": None}
    rpc = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 4}))
        tx = w3.eth.get_transaction(base_tx)
    except Exception:
        return out
    data = tx.get("input")
    data_hex = data.hex() if hasattr(data, "hex") else str(data)
    if data_hex.startswith("0x") or data_hex.startswith("0X"):
        data_hex = data_hex[2:]
    if data_hex.lower() != expected_root.lower():
        return out
    out["root_matches"] = True
    try:
        rcpt = w3.eth.get_transaction_receipt(base_tx)
        if rcpt.get("status") == 1:
            out["confirmed"] = True
            out["block"] = rcpt["blockNumber"]
            out["anchored_at"] = w3.eth.get_block(rcpt["blockNumber"])["timestamp"]
    except Exception:
        pass  # calldata matched but not yet mined → root_matches, not confirmed
    return out


def verify(
    scheme: Literal["eip191", "ed25519"],
    input_hash: str,
    output_hash: str,
    decision: str,
    signature: str,
    signer_pubkey: str | None = None,
) -> tuple[bool, str]:
    """Top-level verifier. Returns (verified, recovered_signer)."""
    msg = build_message(input_hash, output_hash, decision)
    if scheme == "eip191":
        try:
            addr = verify_eip191(msg, signature)
            return True, addr
        except Exception:
            return False, ""
    if scheme == "ed25519":
        if not signer_pubkey:
            return False, ""
        try:
            ok = verify_ed25519(msg, signature, signer_pubkey)
            return ok, signer_pubkey if ok else ""
        except Exception:
            return False, ""
    return False, ""
