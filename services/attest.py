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
