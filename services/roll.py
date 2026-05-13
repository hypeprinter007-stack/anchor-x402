"""Verifiable RNG: cryptographically-secure random integers, signed.

Drop-in replacement for VRF-style randomness in games. Cheap enough to
call on every loot drop, card flip, or matchmaking shuffle.

Trust model:
  - The result is sampled with secrets.randbelow (CSPRNG, urandom-backed).
  - The signed payload is (input_hash, result_hash) where input_hash
    canonicalises the request (range/count/commitment/label). A client
    that pre-commits a hash before requesting the roll can prove the
    server's result was not picked after seeing the client's downstream
    intent.
  - The signature alone is verifiable. For temporal proof that the
    result wasn't fabricated after-the-fact, the client can compose
    /v1/anchor with the returned result_hash for a dual-chain receipt.

Domain-separated message (signed verbatim, EIP-191 personal_sign):

    anchor-x402/roll/v1
    input=<input_hash>
    result=<result_hash>

The signer is the anchor-x402 treasury address. The public key is
stable and discoverable from /v1/config or any prior on-chain anchor.
"""
from __future__ import annotations

import hashlib
import json
import secrets as _csprng
from typing import Any

from services import secrets

MAX_COUNT = 100
MAX_LABEL_LEN = 200
MAX_SPAN = 1 << 32  # 4.29B values — fits most game RNG needs


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_commitment(commitment: str | None) -> str | None:
    if commitment is None:
        return None
    c = commitment[2:] if commitment.startswith(("0x", "0X")) else commitment
    if len(c) != 64 or not all(ch in "0123456789abcdefABCDEF" for ch in c):
        raise ValueError("commitment must be a 32-byte hex string")
    return "0x" + c.lower()


def generate(
    low: int,
    high: int,
    count: int = 1,
    commitment: str | None = None,
    label: str | None = None,
) -> dict:
    """Sample `count` integers in [low, high] inclusive and sign the result."""
    if not isinstance(low, int) or not isinstance(high, int):
        raise ValueError("low and high must be integers")
    if high < low:
        raise ValueError("high must be >= low")
    if count < 1 or count > MAX_COUNT:
        raise ValueError(f"count must be 1..{MAX_COUNT}")
    span = high - low + 1
    if span > MAX_SPAN:
        raise ValueError(f"range too wide (max span {MAX_SPAN})")
    if label is not None and len(label) > MAX_LABEL_LEN:
        raise ValueError(f"label too long (max {MAX_LABEL_LEN} chars)")
    commitment = _normalize_commitment(commitment)

    input_obj = {"range": [low, high], "count": count, "commitment": commitment, "label": label}
    input_hash = _sha256(_canonical(input_obj))

    result = [_csprng.randbelow(span) + low for _ in range(count)]

    result_obj = {"input_hash": input_hash, "result": result}
    result_hash = _sha256(_canonical(result_obj))

    message = (
        f"anchor-x402/roll/v1\ninput={input_hash}\nresult={result_hash}"
    ).encode("utf-8")

    from eth_account import Account
    from eth_account.messages import encode_defunct

    key = secrets.get("treasury_evm_key", env_fallback="TREASURY_PRIVATE_KEY")
    if not key:
        raise RuntimeError("treasury EVM key unavailable")
    acct = Account.from_key(key)
    signed = acct.sign_message(encode_defunct(message))
    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    return {
        "range": [low, high],
        "count": count,
        "commitment": commitment,
        "label": label,
        "result": result,
        "input_hash": input_hash,
        "result_hash": result_hash,
        "signature": sig_hex,
        "signer": acct.address,
        "scheme": "eip191",
        "domain": "anchor-x402/roll/v1",
    }
