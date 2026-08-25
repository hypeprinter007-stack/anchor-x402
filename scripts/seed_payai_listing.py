#!/usr/bin/env python3
"""Seed a PayAI Bazaar listing by paying one of our own routes on Solana.

PayAI has no submission form. Its facilitator indexes the `bazaar` extension out
of payments it settles itself, so a service on another facilitator cannot appear
in the catalog at all. Solana settlement routes to PayAI (see
`_NetworkScopedFacilitator` in app.py), so one paid Solana call to a route that
carries the declaration is what creates the entry.

The payer is the Solana treasury, which is also the `payTo` for that rail — the
transfer nets to zero and PayAI sponsors the gas, so seeding costs nothing beyond
running the service once. Two consequences worth being explicit about:

  * This buys a LISTING, not demand. The resulting PAID_CALL telemetry records
    the treasury as payer; it must never be counted as a buyer. Same discipline
    as the 63 /v1/anchor calls that turned out to be an internal cron.
  * Catalog entries have no TTL and refreshes are forward-only, so whatever the
    declaration says at settle time persists until another paid call overwrites
    it. Check the 402 first — that is what --dry-run is for.

Only routes whose declaration lives on POST are useful here: the GET wrapper
twins carry no bazaar extension, so paying one would settle without indexing.

  .venv/bin/python scripts/seed_payai_listing.py --dry-run
  .venv/bin/python scripts/seed_payai_listing.py --path /v1/price/token
"""
from __future__ import annotations

import argparse
import base64
import json
import sys

import boto3
import requests

API = "https://api.anchor-x402.com"
SOLANA_MAINNET_CAIP2 = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SECRET_ID = "anchor-x402/runtime"
DISCOVERY = "https://facilitator.payai.network/discovery/resources"

# Route -> request body. Deliberately limited to cheap, side-effect-free routes:
# anchor and attest would post real transactions and burn treasury gas, and the
# LLM-backed ones cost a Bedrock call per seed.
BODIES = {
    "/v1/price/token": {"symbol": "ETH"},
    "/v1/resolve/name": {"name": "vitalik.eth"},
    "/v1/parse/datetime": {"input": "next tuesday at 3pm"},
    # A real mined tx: the handler runs before settlement, so a body the service
    # rejects would 4xx without ever creating a catalog entry.
    "/v1/decode/tx": {
        "chain": "base",
        "tx_hash": "0x54998d7e5f3614114a9b160c5ab7bbb8b367bc77fed04ce8257d832ba6d0ed90",
    },
    "/v1/roll": {"low": 1, "high": 100},
}


def request_body(path: str, challenge: dict | None = None) -> dict:
    """Prefer the example the route publishes in its own bazaar declaration.

    That example is what a buyer agent reads and sends, so paying with it tests
    the documentation at the same time as seeding the listing — /v1/parse/datetime
    was found to 400 exactly this way. BODIES is a fallback for routes whose
    declared example is not a usable body on its own.
    """
    if challenge:
        declared = (((challenge.get("extensions") or {}).get("bazaar") or {})
                    .get("info") or {}).get("input") or {}
        body = declared.get("body")
        if isinstance(body, dict) and body:
            return body
    return BODIES.get(path, {})


def inspect_challenge(path: str) -> dict:
    """Read the 402 before paying it. The declaration we see here is the one
    that gets frozen into the catalog."""
    response = requests.post(f"{API}{path}", json=BODIES.get(path, {}), timeout=30)
    if response.status_code != 402:
        raise SystemExit(f"expected 402 from {path}, got {response.status_code}")
    header = response.headers.get("payment-required")
    if not header:
        raise SystemExit("402 carried no payment-required header")
    return json.loads(base64.b64decode(header))


def solana_option(challenge: dict) -> dict:
    for option in challenge["accepts"]:
        if option["network"] == SOLANA_MAINNET_CAIP2:
            return option
    raise SystemExit("no Solana rail in the challenge")


def build_session():
    from solders.keypair import Keypair
    from x402.client import x402ClientSync
    from x402.http.clients.requests import x402_requests
    from x402.mechanisms.svm.exact import register_exact_svm_client
    from x402.mechanisms.svm.signers import KeypairSigner

    secret = json.loads(
        boto3.client("secretsmanager", region_name="us-east-1")
        .get_secret_value(SecretId=SECRET_ID)["SecretString"]
    )
    keypair = Keypair.from_base58_string(secret["treasury_solana_key"])
    client = x402ClientSync()
    register_exact_svm_client(client, KeypairSigner(keypair), networks=SOLANA_MAINNET_CAIP2)
    return x402_requests(client), str(keypair.pubkey())


def catalog_hits() -> list[dict]:
    found, offset = [], 0
    while True:
        page = requests.get(DISCOVERY, params={"limit": 1000, "offset": offset},
                            timeout=30).json()
        items = page.get("items", [])
        found += [i for i in items if "anchor-x402" in json.dumps(i).lower()]
        if len(items) < 1000:
            return found
        offset += 1000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="/v1/price/token")
    parser.add_argument("--body", help="JSON body override (default: the route's own declared example)")
    parser.add_argument("--dry-run", action="store_true",
                        help="inspect the 402 and the catalog, pay nothing")
    args = parser.parse_args()

    challenge = inspect_challenge(args.path)
    option = solana_option(challenge)
    bazaar = (challenge.get("extensions") or {}).get("bazaar") or {}
    info = bazaar.get("info") or {}

    print(f"route            {args.path}")
    print(f"amount           {option['amount']} base units ({int(option['amount'])/1e6:.6f} USDC)")
    print(f"payTo            {option['payTo']}")
    print(f"feePayer         {(option.get('extra') or {}).get('feePayer')}")
    print(f"declaration      info.input={'input' in info} info.output={'output' in info}")
    if not (info.get("input") and info.get("output")):
        raise SystemExit("this route publishes no bazaar declaration — paying it would "
                         "settle without creating a catalog entry")

    before = catalog_hits()
    print(f"catalog before   {len(before)} anchor-x402 entries")

    if args.dry_run:
        print("\n(dry run — nothing paid)")
        return

    session, payer = build_session()
    print(f"payer            {payer}")
    if payer != option["payTo"]:
        print("NOTE: payer differs from payTo, so this moves real USDC")

    body = json.loads(args.body) if args.body else request_body(args.path, challenge)
    print(f"body sent        {json.dumps(body)[:120]}")
    response = session.post(f"{API}{args.path}", json=body, timeout=120)
    print(f"\npaid call        HTTP {response.status_code}")
    if response.status_code != 200:
        print(response.text[:600])
        raise SystemExit("payment did not settle")

    receipt = response.headers.get("payment-response") or response.headers.get("x-payment-response")
    if receipt:
        try:
            print("settlement       " + json.dumps(json.loads(base64.b64decode(receipt)))[:400])
        except Exception:
            print(f"settlement       {receipt[:200]}")
    print(f"body             {response.text[:200]}")

    after = catalog_hits()
    print(f"\ncatalog after    {len(after)} anchor-x402 entries")
    for entry in after:
        print(f"  {entry.get('resource')}  {entry.get('lastUpdated')}")
    if len(after) <= len(before):
        print("\nNot indexed yet. Upsert may lag the settle — re-check with --dry-run "
              "before paying again, since a second payment would be a second settle.")


if __name__ == "__main__":
    main()
