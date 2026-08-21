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


# key_id -> Secrets Manager id. anchor-pilot-2026-01 signed the July Tier-3
# pilot and is published `retired`; it stays here only so an old challenge can
# be reproduced, never to sign a new one. Default is the current active key.
KEYS = {
    "anchor-fed-2026-08": "anchor-x402/federation-2026-08-ed25519",
    "anchor-pilot-2026-01": "anchor-x402/federation-pilot-ed25519",
}
ACTIVE_KEY_ID = "anchor-fed-2026-08"
RETIRED = {"anchor-pilot-2026-01"}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)

    key_id = ACTIVE_KEY_ID
    for flag in flags:
        if flag.startswith("--key-id="):
            key_id = flag.split("=", 1)[1]
    if key_id not in KEYS:
        sys.exit(f"unknown key_id {key_id!r}; known: {', '.join(KEYS)}")
    if key_id in RETIRED and "--allow-retired" not in flags:
        sys.exit(f"{key_id} is published as retired — signing with it would assert "
                 "authority we have revoked. Pass --allow-retired only to reproduce "
                 "a historical signature.")

    msg = json.loads(args[0])
    payload = {"body": msg["body"], "challenge": msg["challenge"]}
    digest = "sha256:" + hashlib.sha256(canonical(payload).encode()).hexdigest()

    pem = boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId=KEYS[key_id]
    )["SecretString"]
    key = load_pem_private_key(pem.encode(), password=None)
    signature = key.sign(digest.encode("ascii"))

    print(json.dumps({
        "key_id": key_id,
        "digest": digest,
        "signature_base64": base64.b64encode(signature).decode(),
    }, indent=2))


if __name__ == "__main__":
    main()
