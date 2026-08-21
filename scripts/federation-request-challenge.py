#!/usr/bin/env python3
"""Pull a fresh Agoragentic federation challenge over the signed relay.

The pull model exists because the push model could not be trusted: a challenge
that arrives by email has no provenance, and one arrived in July whose
agent_card_hash we could not reproduce under any serialization. Here we
initiate over TLS to their published endpoint and they select the verifying key
by (relationship_id, remote_origin) from what the owner pinned — so nothing is
signed on the strength of an unauthenticated packet.

Canonical message, exactly as published at
https://agoragentic.com/api/federation/challenge-relay/status — six UTF-8 lines
joined with LF, signed DIRECTLY (this is not a digest-then-sign scheme):

    federation/request-challenge
    <relationship_id>
    <remote_origin>
    <auth.nonce>
    <String(auth.timestamp)>
    sha256:<hex of stable-sorted {relationship_id, remote_origin} JSON>

The timestamp is signed as its exact wire value, so it is built once as a
string and reused verbatim in both places.

  .venv/bin/python scripts/federation-request-challenge.py          # dry run
  .venv/bin/python scripts/federation-request-challenge.py --send   # POST it
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import time

import boto3
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key

RELAY = "https://agoragentic.com/api/federation/challenge-relay/request-challenge"
RELATIONSHIP_ID = "anchor-x402-pilot-2026-07"
# The Agent Card URL origin, not the api host — their relationship binding keys
# on the origin that serves the card.
REMOTE_ORIGIN = "https://anchor-x402.com"
KEY_ID = "anchor-fed-2026-08"
SECRET_ID = "anchor-x402/federation-2026-08-ed25519"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build() -> tuple[dict, str]:
    params_hash = "sha256:" + hashlib.sha256(
        canonical({"relationship_id": RELATIONSHIP_ID, "remote_origin": REMOTE_ORIGIN}).encode()
    ).hexdigest()

    nonce = "anchor-" + secrets.token_hex(16)
    timestamp = str(int(time.time() * 1000))

    message = "\n".join([
        "federation/request-challenge",
        RELATIONSHIP_ID,
        REMOTE_ORIGIN,
        nonce,
        timestamp,
        params_hash,
    ])

    pem = boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId=SECRET_ID
    )["SecretString"]
    signature = load_pem_private_key(pem.encode(), password=None).sign(message.encode("utf-8"))

    body = {
        "relationship_id": RELATIONSHIP_ID,
        "remote_origin": REMOTE_ORIGIN,
        "auth": {
            "nonce": nonce,
            "timestamp": timestamp,
            "signature_algorithm": "ed25519",
            "signature": base64.b64encode(signature).decode(),
        },
    }
    return body, message


def main() -> None:
    body, message = build()
    print("canonical message signed (key %s):" % KEY_ID, file=sys.stderr)
    print(message, file=sys.stderr)
    print(file=sys.stderr)

    if "--send" not in sys.argv[1:]:
        print(json.dumps(body, indent=2))
        print("\n(dry run — re-run with --send to submit)", file=sys.stderr)
        return

    response = requests.post(RELAY, json=body, timeout=30)
    print("POST %s -> %s" % (RELAY, response.status_code), file=sys.stderr)
    try:
        print(json.dumps(response.json(), indent=2))
    except ValueError:
        print(response.text)


if __name__ == "__main__":
    main()
