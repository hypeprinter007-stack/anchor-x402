#!/usr/bin/env python3
"""Sign an Agoragentic Tier-3 federation challenge.

Usage: .venv/bin/python scripts/federation-sign.py '<challenge JSON>'
where the argument is their challenge message containing at least
{"challenge": "...", "body": ...}.

Per their spec (message 2026-07-18): canonical JSON = lexically sorted
object keys, preserved array order, no extra whitespace, over exactly
{"body": <challenge_body>, "challenge": "<challenge>"}; sign the ASCII
string "sha256:<hex>" of that; return a detached base64 Ed25519
signature. Key: Secrets Manager anchor-x402/federation-pilot-ed25519
(key_id anchor-pilot-2026-01) — dedicated pilot key, never the treasury.
"""

import base64
import hashlib
import json
import sys

import boto3
from cryptography.hazmat.primitives.serialization import load_pem_private_key


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    msg = json.loads(sys.argv[1])
    payload = {"body": msg["body"], "challenge": msg["challenge"]}
    digest = "sha256:" + hashlib.sha256(canonical(payload).encode()).hexdigest()

    pem = boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="anchor-x402/federation-pilot-ed25519"
    )["SecretString"]
    key = load_pem_private_key(pem.encode(), password=None)
    signature = key.sign(digest.encode("ascii"))

    print(json.dumps({
        "key_id": "anchor-pilot-2026-01",
        "digest": digest,
        "signature_base64": base64.b64encode(signature).decode(),
    }, indent=2))


if __name__ == "__main__":
    main()
