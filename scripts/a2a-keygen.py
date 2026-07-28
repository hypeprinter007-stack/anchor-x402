#!/usr/bin/env python3
"""Provision and publish the anchor-x402 A2A identity key.

Own namespace, own key — deliberately NOT the Agoragentic pilot key, which
stays scoped to their federation pilot. The key is identity-only: it signs
quotes and receipts and cannot move funds or reach the treasury.

Production custody is AWS KMS (keyspec ECC_NIST_EDWARDS25519, signing algorithm
ED25519_SHA_512). The private key is never extractable, every signature is
CloudTrail-logged, and revocation is removing the IAM grant rather than
noticing an exfiltration. Verified interoperable: KMS returns a raw 64-byte
RFC 8032 signature and GetPublicKey returns a standard Ed25519 SPKI, so peers
verify with any stock library — custody never reaches the wire.

  .venv/bin/python scripts/a2a-keygen.py                        # show the published key + KMS state
  .venv/bin/python scripts/a2a-keygen.py --provision            # create key + alias if absent (idempotent)
  .venv/bin/python scripts/a2a-keygen.py --publish              # write the KMS pubkey into the agent card
  .venv/bin/python scripts/a2a-keygen.py --rotate anchor-a2a-2026-02
  .venv/bin/python scripts/a2a-keygen.py --local-pem            # dev-only key for offline signing

Rotation: create a second KMS key, repoint nothing yet, then --rotate <new-id>
to publish the new key active while marking the old one retired. Peers keep
verifying old signatures because both stay in the card's `keys` array. Repoint
the alias when you are ready for new signatures to use the new key.

Deletion is the one irreversible step: KMS enforces a 7-30 day pending window
and nothing here schedules it.
"""

import argparse
import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CARD_PATH = os.path.join(ROOT, "docs", ".well-known", "agent-card.json")
NAMESPACE = "anchor-x402:a2a"
ALIAS = os.getenv("A2A_KMS_KEY_ID", "alias/anchor-x402-a2a")
KEY_SPEC = "ECC_NIST_EDWARDS25519"
DEFAULT_KEY_ID = "anchor-a2a-2026-01"


def kms():
    import boto3

    return boto3.client("kms", region_name=os.getenv("AWS_REGION", "us-east-1"))


def read_card() -> dict:
    with open(CARD_PATH) as f:
        return json.load(f)


def write_card(card: dict) -> None:
    with open(CARD_PATH, "w") as f:
        json.dump(card, f, indent=2)
        f.write("\n")


def public_key_b64() -> str:
    """DER SubjectPublicKeyInfo, base64 — exactly the agent-card field."""
    return base64.b64encode(kms().get_public_key(KeyId=ALIAS)["PublicKey"]).decode()


def provision() -> None:
    c = kms()
    try:
        meta = c.describe_key(KeyId=ALIAS)["KeyMetadata"]
        print(f"alias {ALIAS} already resolves to {meta['KeyId']} ({meta['KeySpec']})")
        if meta["KeySpec"] != KEY_SPEC:
            sys.exit(f"WRONG KEYSPEC: {meta['KeySpec']} — peers expect Ed25519")
        return
    except c.exceptions.NotFoundException:
        pass

    key = c.create_key(
        KeySpec=KEY_SPEC,
        KeyUsage="SIGN_VERIFY",
        Description=(
            "anchor-x402 A2A identity signing key — signs quotes/receipts on "
            "POST /v1/a2a. Identity only: cannot move funds or touch treasury."
        ),
        Tags=[
            {"TagKey": "project", "TagValue": "anchor-x402"},
            {"TagKey": "purpose", "TagValue": "a2a-identity"},
        ],
    )["KeyMetadata"]
    c.create_alias(AliasName=ALIAS, TargetKeyId=key["KeyId"])
    print(f"created {key['Arn']}\naliased {ALIAS}")
    print("this key cannot be deleted immediately — KMS enforces a 7-30 day window")


def publish(key_id: str, retire_others: bool) -> None:
    pub = public_key_b64()
    card = read_card()
    block = card.setdefault("extensions", {}).setdefault(NAMESPACE, {})
    keys = [k for k in block.get("keys", []) if isinstance(k, dict)]

    if retire_others:
        for k in keys:
            if k.get("key_id") != key_id and k.get("status") == "active":
                k["status"] = "retired"
                print(f"retired {k['key_id']} (peers can still verify its old signatures)")

    entry = next((k for k in keys if k.get("key_id") == key_id), None)
    if entry is None:
        entry = {"key_id": key_id}
        keys.append(entry)
    entry.update(
        public_key_der_base64=pub, status="active", custody="aws-kms",
    )
    block["keys"] = keys
    write_card(card)

    print(f"published {key_id} → {pub}")
    print(f"updated {CARD_PATH}")
    print("commit + redeploy so the apex site and the API host serve the same card")


def local_pem() -> None:
    """Dev-only: a local key so signing works offline. Never for production —
    a PEM is exfiltratable, which is the whole reason production uses KMS."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )

    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    pub = base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode()
    path = os.path.join(ROOT, ".a2a-dev-key.pem")
    with open(path, "w") as f:
        f.write(pem)
    os.chmod(path, 0o600)
    print(f"dev key written to {path} (gitignored — do not publish)")
    print(f"dev public key: {pub}")
    print(f'\nexport A2A_SIGNING_KEY_PEM="$(cat {path})"')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provision", action="store_true", help="create the KMS key + alias if absent")
    ap.add_argument("--publish", action="store_true", help="write the KMS pubkey into the agent card")
    ap.add_argument("--rotate", metavar="KEY_ID", help="publish KEY_ID active and retire the rest")
    ap.add_argument("--local-pem", action="store_true", help="dev-only local signing key")
    args = ap.parse_args()

    if args.local_pem:
        local_pem()
        return
    if args.provision:
        provision()
    if args.rotate:
        publish(args.rotate, retire_others=True)
        return
    if args.publish:
        publish(DEFAULT_KEY_ID, retire_others=False)
        return
    if not args.provision:
        card_keys = (read_card().get("extensions", {}).get(NAMESPACE, {})).get("keys", [])
        print("card keys:", json.dumps(card_keys, indent=2))
        try:
            print(f"\nKMS {ALIAS} public key: {public_key_b64()}")
        except Exception as e:
            print(f"\nKMS {ALIAS} unreachable: {e}")


if __name__ == "__main__":
    main()
