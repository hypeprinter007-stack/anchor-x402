#!/usr/bin/env python3
"""Answer an Agoragentic Tier-3 federation challenge end to end.

Usage:
  .venv/bin/python scripts/federation-respond.py <challenge-packet.json>          # print envelope (dry run)
  .venv/bin/python scripts/federation-respond.py <challenge-packet.json> --send   # sign + POST to their A2A endpoint

The packet is the challenge JSON issued by Agoragentic. Expected fields
(tolerant of naming): identity_challenge_id, relationship_id, challenge,
challenge_body (or body), binding (echoed verbatim — the canonical binding
template supplied with the challenge).

Per their spec (2026-07-18): sign the ASCII "sha256:<hex>" of canonical
JSON (sorted keys, no whitespace) of {"body": <challenge_body>,
"challenge": "<challenge>"} with the dedicated pilot key (Secrets Manager
anchor-x402/federation-pilot-ed25519, key_id anchor-pilot-2026-01), then
POST JSON-RPC method federation/challenge-response to
https://agoragentic.com/api/a2a with remote_origin https://anchor-x402.com
(the Agent Card URL origin, which is what their relationship binding uses).
"""

import base64
import hashlib
import json
import sys

import boto3
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key

A2A_URL = "https://agoragentic.com/api/a2a"
REMOTE_ORIGIN = "https://anchor-x402.com"
SECRET_ID = "anchor-x402/federation-pilot-ed25519"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    packet = json.loads(open(sys.argv[1]).read())
    send = "--send" in sys.argv[2:]

    body = packet.get("challenge_body", packet.get("body"))
    challenge = packet["challenge"]
    if body is None:
        sys.exit("packet has neither challenge_body nor body")

    digest = "sha256:" + hashlib.sha256(
        canonical({"body": body, "challenge": challenge}).encode()
    ).hexdigest()
    pem = boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId=SECRET_ID
    )["SecretString"]
    signature = load_pem_private_key(pem.encode(), password=None).sign(digest.encode("ascii"))

    envelope = {
        "jsonrpc": "2.0",
        "id": "anchor-pilot-challenge-1",
        "method": "federation/challenge-response",
        "params": {
            "identity_challenge_id": packet["identity_challenge_id"],
            "relationship_id": packet["relationship_id"],
            "remote_origin": REMOTE_ORIGIN,
            "challenge": challenge,
            "signature_algorithm": "ed25519",
            "signature": base64.b64encode(signature).decode(),
            "binding": packet["binding"],
        },
    }
    print(json.dumps(envelope, indent=2))
    print(f"\ndigest signed: {digest}", file=sys.stderr)

    if send:
        r = requests.post(A2A_URL, json=envelope, timeout=30)
        print(f"\nPOST {A2A_URL} -> {r.status_code}", file=sys.stderr)
        print(r.text)
    else:
        print("\n(dry run — re-run with --send to submit)", file=sys.stderr)


if __name__ == "__main__":
    main()
