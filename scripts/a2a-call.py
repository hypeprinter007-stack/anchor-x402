#!/usr/bin/env python3
"""Call any agent's A2A door as anchor-x402 — the client side of POST /v1/a2a.

Reference implementation and live smoke test. The whole client is the four
lines that build `digest` and sign it; everything else is argument handling.
A peer wanting to talk to us copies those four lines in any language.

  # against ourselves (proves the deployed door end to end)
  .venv/bin/python scripts/a2a-call.py peer/hello
  .venv/bin/python scripts/a2a-call.py capabilities/list
  .venv/bin/python scripts/a2a-call.py peer/quote --body '{"skill":"screen_wallet"}'
  .venv/bin/python scripts/a2a-call.py peer/receipt --body '{"exchange_id":"ax-..."}'

  # against someone else
  .venv/bin/python scripts/a2a-call.py peer/hello --to https://peer.example/v1/a2a

Signs with our A2A identity key (composite runtime secret, key
`a2a_signing_key_pem`; A2A_SIGNING_KEY_PEM locally) — run
scripts/a2a-keygen.py first. Identity only: this key cannot move money.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets as pysecrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from services import a2a as a2a_svc

DEFAULT_TO = "https://api.anchor-x402.com/v1/a2a"
OUR_ORIGIN = "https://anchor-x402.com"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("method", choices=list(a2a_svc.METHODS))
    ap.add_argument("--to", default=DEFAULT_TO, help="peer A2A endpoint URL")
    ap.add_argument("--body", default="null", help="JSON method arguments")
    ap.add_argument("--origin", default=OUR_ORIGIN, help="our card origin (what the peer verifies)")
    ap.add_argument("--aud", default=DEFAULT_TO.rsplit("/v1/a2a", 1)[0],
                    help="recipient origin; must equal the peer's declared audience")
    ap.add_argument("--ttl", type=int, default=120, help="seconds until the envelope expires")
    args = ap.parse_args()

    body = json.loads(args.body)
    nonce = pysecrets.token_hex(16)
    exp = int(time.time()) + args.ttl

    # --- the entire client-side protocol -----------------------------------
    key_id = a2a_svc.active_key_id()
    signed_fields = {"aud": args.aud, "body": body, "exp": exp, "key_id": key_id,
                     "method": args.method, "nonce": nonce, "origin": args.origin}
    canonical = json.dumps(signed_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    raw = a2a_svc.sign_digest(digest)          # KMS, or local PEM in dev
    if raw is None:
        sys.exit("no A2A signing key reachable — run scripts/a2a-keygen.py --provision, "
                 "or set A2A_KMS_KEY_ID / A2A_SIGNING_KEY_PEM")
    signature = base64.b64encode(raw).decode()
    # -----------------------------------------------------------------------

    envelope = {
        "jsonrpc": "2.0",
        "id": nonce[:8],
        "method": args.method,
        "params": {
            "aud": args.aud,
            "origin": args.origin,
            "key_id": key_id,
            "nonce": nonce,
            "exp": exp,
            "signature_algorithm": "ed25519",
            "signature": signature,
            "body": body,
        },
    }

    r = requests.post(args.to, json=envelope, timeout=30)
    print(json.dumps(r.json() if r.headers.get("content-type", "").startswith("application/json")
                     else {"status": r.status_code, "text": r.text[:500]}, indent=2))
    print(f"\nPOST {args.to} -> {r.status_code}  digest {digest}", file=sys.stderr)
    sys.exit(0 if r.ok and "result" in r.json() else 1)


if __name__ == "__main__":
    main()
