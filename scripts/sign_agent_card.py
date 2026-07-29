#!/usr/bin/env python3
"""Sign the agent card with a detached JWS, per A2A 0.3.0 `signatures`.

A2A defines Signed Agent Cards: the card is canonicalized with JCS (RFC 8785),
signed as a JWS (RFC 7515), and the detached signature is stored in the card's
own `signatures` array. A client can then confirm the card it fetched is the one
we published — DNS and TLS say who served it, this says nobody altered it.

This is a different job from extensions["anchor-x402:a2a"].keys, which is how
peers sign *requests* to us. The spec has no field for that, so it stays an
extension. Both are needed; neither replaces the other.

  .venv/bin/python scripts/sign_agent_card.py            # sign in place
  .venv/bin/python scripts/sign_agent_card.py --verify    # check the signature

Signs with the same KMS Ed25519 key the A2A door uses (alg EdDSA), so there is
one identity to trust rather than two. Re-run after every card regeneration —
gen_agent_card.py drops stale signatures on purpose.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CARD_PATH = os.path.join(ROOT, "docs", ".well-known", "agent-card.json")
KMS_ALIAS = os.getenv("A2A_KMS_KEY_ID", "alias/anchor-x402-a2a")


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def jcs(obj) -> bytes:
    """JSON Canonicalization Scheme, RFC 8785.

    Sorted keys, no whitespace, UTF-8. Python's json emits ES6-compatible number
    forms for the value domain this card uses (small decimals like 0.005 and
    integers); it would diverge from JCS only for exponent-range numbers, which
    a card has no reason to contain. Asserted below rather than assumed.
    """
    def check(node, path="$"):
        if isinstance(node, float):
            text = repr(node)
            if "e" in text or "E" in text:
                raise SystemExit(f"{path}: {node} needs exponent notation — not JCS-safe here")
        elif isinstance(node, dict):
            for k, v in node.items():
                check(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                check(v, f"{path}[{i}]")

    check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def signing_input(card: dict, protected: dict) -> bytes:
    """Detached JWS signing input: b64u(protected) || '.' || b64u(JCS(payload)).

    The payload is the card with `signatures` removed — a signature cannot cover
    itself.
    """
    payload = {k: v for k, v in card.items() if k != "signatures"}
    return f"{b64u(jcs(protected))}.{b64u(jcs(payload))}".encode()


def load_card() -> dict:
    with open(CARD_PATH) as f:
        return json.load(f)


def active_key_id(card: dict) -> str:
    for entry in card["extensions"]["anchor-x402:a2a"]["keys"]:
        if entry.get("status", "active") == "active":
            return entry["key_id"]
    raise SystemExit("no active key in the card")


CARD_KMS_ALIAS = os.getenv("A2A_CARD_KMS_KEY_ID", "alias/anchor-x402-card")


def der_to_raw_ecdsa(der: bytes, size: int = 32) -> bytes:
    """DER SEQUENCE(r, s) -> fixed-width r||s, which is what JWS ES256 wants.

    KMS returns ECDSA signatures DER-encoded; JOSE requires the raw pair. Not
    interchangeable — a verifier fed DER where it expects raw simply fails.
    """
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    r, s = decode_dss_signature(der)
    return r.to_bytes(size, "big") + s.to_bytes(size, "big")


def sign() -> None:
    """Sign with a KMS P-256 key as JWS ES256.

    Why not the Ed25519 key the A2A door uses: KMS caps Sign at 4096 bytes of
    raw message, and a JWS signing input over this card is ~17 KB. Going over
    that requires MessageType=DIGEST, which for Ed25519 means
    ED25519_PH_SHA_512 — Ed25519ph, for which JOSE registers no `alg` and which
    stock verifiers reject. ES256 hashes to 32 bytes, is the most widely
    supported JOSE algorithm, and keeps the key non-exfiltratable in KMS.
    """
    import boto3
    from cryptography.hazmat.primitives import hashes

    card = load_card()
    kid = os.getenv("A2A_CARD_KEY_ID", "anchor-card-2026-01")
    protected = {"alg": "ES256", "kid": kid, "typ": "JOSE"}
    message = signing_input(card, protected)

    digest = hashes.Hash(hashes.SHA256())
    digest.update(message)
    kms = boto3.client("kms", region_name=os.getenv("AWS_REGION", "us-east-1"))
    try:
        der = kms.sign(
            KeyId=CARD_KMS_ALIAS,
            Message=digest.finalize(),
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )["Signature"]
    except kms.exceptions.NotFoundException:
        sys.exit(
            f"{CARD_KMS_ALIAS} does not exist. Provision it once with:\n\n"
            "  aws kms create-key --key-spec ECC_NIST_P256 --key-usage SIGN_VERIFY \\\n"
            "    --description 'anchor-x402 agent card signing (ES256)'\n"
            "  aws kms create-alias --alias-name alias/anchor-x402-card --target-key-id <id>\n\n"
            "Note: KMS keys cannot be deleted immediately (7-30 day pending window)."
        )

    # Confirm the key we just signed with is the one the card tells clients to
    # verify against. A mismatch here would publish a card nobody can verify.
    published = next((k for k in card["extensions"]["anchor-x402:a2a"]["card_signing_keys"]
                      if k["key_id"] == kid), None)
    if published is None:
        sys.exit(f"card publishes no card_signing_keys entry for {kid}")
    live = base64.b64encode(kms.get_public_key(KeyId=CARD_KMS_ALIAS)["PublicKey"]).decode()
    if live != published["public_key_der_base64"]:
        sys.exit(
            f"{CARD_KMS_ALIAS} public key does not match the one published for {kid}.\n"
            f"  published: {published['public_key_der_base64'][:44]}...\n"
            f"  live:      {live[:44]}...\n"
            "Update card_signing_keys in scripts/gen_agent_card.py and regenerate."
        )

    # Only `signatures` is added here. Everything else comes from the generator,
    # so `gen_agent_card.py --check` can still tell a hand edit from a signature.
    card["signatures"] = [{
        "protected": b64u(jcs(protected)),
        "signature": b64u(der_to_raw_ecdsa(der)),
    }]
    with open(CARD_PATH, "w") as f:
        json.dump(card, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"signed {CARD_PATH} as ES256 with {kid}")


def verify() -> None:
    """Verify exactly as an outside client would: public key from the card."""
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    card = load_card()
    sigs = card.get("signatures") or []
    if not sigs:
        sys.exit("card carries no signatures")

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    ext = card["extensions"]["anchor-x402:a2a"]
    published = (ext.get("card_signing_keys") or []) + ext["keys"]

    for i, sig in enumerate(sigs):
        protected = json.loads(b64u_decode(sig["protected"]).decode())
        kid, alg = protected.get("kid"), protected.get("alg")
        entry = next((k for k in published if k["key_id"] == kid), None)
        if entry is None:
            sys.exit(f"signature {i}: kid {kid} is not published in this card")
        pub = load_der_public_key(base64.b64decode(entry["public_key_der_base64"]))
        message = signing_input(card, protected)
        raw = b64u_decode(sig["signature"])
        if alg == "ES256":
            r = int.from_bytes(raw[:32], "big")
            s = int.from_bytes(raw[32:], "big")
            pub.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
        else:
            pub.verify(raw, message)
        print(f"signature {i}: VALID  alg={alg} kid={kid}")
    print(f"card verifies against its own published key ({len(card['skills'])} skills)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true", help="verify instead of signing")
    args = ap.parse_args()
    verify() if args.verify else sign()


if __name__ == "__main__":
    main()
