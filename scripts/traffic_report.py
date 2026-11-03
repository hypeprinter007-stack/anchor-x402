#!/usr/bin/env python3
"""Paid-traffic report: last 7 days vs the 7 before, from PAID_CALL log lines.

    .venv/bin/python scripts/traffic_report.py

Excludes our own wallets (treasury, pulse/heartbeat). Separates the Arc batch
pattern (fresh wallets making one or two calls to /v1/intel/wallet or
/v1/price/token, seen since October) from the rest, which is reported as "real".
Prices come from the live OpenAPI spec.
"""
from __future__ import annotations

import collections
import json
import time
import urllib.request

import boto3

LOG_GROUP = "/aws/lambda/anchor-x402-AnchorFunction-fsYxBKhvWHK4"
OWN = {
    "0x127462e296fac1a7f5cf33ba57bb2f0fff5cd0b6",  # treasury
    "0x7818cb9cead1e13e64a259f0867089db75e374c5",  # pulse + CDP heartbeat
}
BATCH_ROUTES = ("/v1/intel/wallet", "/v1/price/token")


def prices() -> dict[str, float]:
    spec = json.load(urllib.request.urlopen("https://api.anchor-x402.com/openapi.json", timeout=30))
    out = {}
    for path, ops in spec["paths"].items():
        for op in ops.values():
            amount = ((op.get("x-payment-info") or {}).get("price") or {}).get("amount")
            if amount:
                out[path] = float(amount)
    return out


def query(logs, q: str, days: int, now: int) -> list[dict]:
    qid = logs.start_query(logGroupName=LOG_GROUP, startTime=now - days * 86400, endTime=now, queryString=q)["queryId"]
    while (r := logs.get_query_results(queryId=qid))["status"] != "Complete":
        time.sleep(2)
    return [{f["field"]: f["value"] for f in row} for row in r["results"]]


def main() -> None:
    logs, now, price = boto3.client("logs"), int(time.time()), prices()
    calls = []
    for row in query(logs, "filter @message like /^PAID_CALL/ | fields @timestamp, @message | limit 10000", 14, now):
        try:
            call = json.loads(row["@message"].split(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        call["t"] = row["@timestamp"]
        calls.append(call)
    cut = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 7 * 86400))
    revenue = lambda xs: sum(price.get(c["path"], 0) for c in xs)  # noqa: E731

    for label, window in (("last 7d", [c for c in calls if c["t"] >= cut]), ("prior 7d", [c for c in calls if c["t"] < cut])):
        outside = [c for c in window if (c.get("payer") or "").lower() not in OWN]
        per_payer = collections.Counter(c.get("payer") for c in outside)
        batch = [c for c in outside if c.get("network") == "eip155:5042"
                 and per_payer[c.get("payer")] <= 2 and c["path"] in BATCH_ROUTES]
        real = [c for c in outside if c not in batch]
        print(f"\n== {label}: {len(outside)} outside paid calls ~${revenue(outside):.3f} | "
              f"Arc batch {len(batch)} ~${revenue(batch):.3f} | real {len(real)} ~${revenue(real):.3f}, "
              f"{len({c.get('payer') for c in real})} payers")
        print("  real by rail:", dict(collections.Counter(c.get("network") for c in real)))
        print("  real by route:", collections.Counter(c["path"] for c in real).most_common(8))
        print("  repeat payers (>=3):", [(p[:10], n) for p, n in per_payer.most_common() if n >= 3][:6])
        screen = [c for c in real if c["path"] == "/v1/screen"]
        print(f"  /v1/screen: {len(screen)} calls, {len({c.get('payer') for c in screen})} payers")

    week = [c for c in calls if c["t"] >= cut and (c.get("payer") or "").lower() not in OWN]
    print("\nby day (7d, outside):", sorted(collections.Counter(c["t"][:10] for c in week).items()))
    for label, pattern in (("payment_replayed", "payment_replayed"), ("402 challenges", "^CHALLENGE "), ("feedback", "^FEEDBACK")):
        rows = query(logs, f"filter @message like /{pattern}/ | stats count() as n", 7, now)
        print(f"{label} (7d):", rows[0]["n"] if rows else 0)


if __name__ == "__main__":
    main()
