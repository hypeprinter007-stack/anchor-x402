"""Divigent telemetry export.

Pulls structured `DIVIGENT_EVENT` lines from both the seller-side
Lambdas (anchor-x402) and the buyer-side AgentCore Runtime
(risk-investigator), aggregates, and prints a summary suitable for
NDA-internal sharing with Divigent (Ed + Harsh).

No public surface — output is human-readable on stdout. Pipe to a file
to attach in a DM, never commit it to a public repo.

Usage:
    .venv/bin/python scripts/divigent_metrics.py [--hours 24] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import boto3

REGION = "us-east-1"

SELLER_LOG_GROUP_PREFIXES = [
    "/aws/lambda/anchor-x402-DivigentSweepFunction-",
    "/aws/lambda/anchor-x402-DivigentOracleKeeperFunction-",
]
BUYER_LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/runtimes/risk_investigator-"


def _discover_log_groups(client) -> list[str]:
    """Resolve log groups by prefix so we don't have to chase Lambda
    physical-resource-id suffixes after each deploy."""
    found: list[str] = []
    prefixes = SELLER_LOG_GROUP_PREFIXES + [BUYER_LOG_GROUP_PREFIX]
    for pfx in prefixes:
        paginator = client.get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=pfx):
            for lg in page.get("logGroups", []):
                found.append(lg["logGroupName"])
    return found

EVENT_LINE_RE = re.compile(r"DIVIGENT_EVENT (\{.*\})")


def _run_insights(client, log_group: str, hours: int) -> list[dict]:
    end = int(time.time())
    start = end - hours * 3600
    query = (
        "fields @timestamp, @message\n"
        "| filter @message like /DIVIGENT_EVENT/\n"
        "| sort @timestamp desc\n"
        "| limit 1000"
    )
    try:
        start_resp = client.start_query(
            logGroupName=log_group,
            startTime=start, endTime=end, queryString=query,
        )
    except client.exceptions.ResourceNotFoundException:
        return []
    query_id = start_resp["queryId"]
    # Poll
    while True:
        r = client.get_query_results(queryId=query_id)
        if r["status"] in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(0.5)
    if r["status"] != "Complete":
        return []
    events = []
    for row in r.get("results") or []:
        fields = {c["field"]: c["value"] for c in row if c["field"] != "@ptr"}
        msg = fields.get("@message", "")
        m = EVENT_LINE_RE.search(msg)
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        payload["_log_group"] = log_group
        events.append(payload)
    return events


def _fmt_usdc(atomic: str | int | None) -> str:
    if atomic in (None, "", 0, "0"):
        return "—"
    try:
        return f"{int(atomic) / 1_000_000:.6f}"
    except (ValueError, TypeError):
        return "—"


def _summarize(events: list[dict], hours: int) -> dict:
    by_event = Counter(e.get("event_type", "?") for e in events)
    by_action = Counter()
    role_action = Counter()
    deployed_total = 0
    recalled_total = 0
    latest_snapshot = {}

    # Events come back newest-first. Iterate that way and merge each
    # field into the per-wallet snapshot only if it hasn't been seen yet —
    # gives us the most recent non-null value for every field, even when
    # different event types carry different field subsets (e.g. preflight
    # has balance + status, postflight only has action + tx).
    snapshot_fields = (
        "wallet_balance_atomic", "position_current_value_atomic",
        "required_reserve_atomic", "liquidity_status", "risk_preference",
    )
    for e in events:
        role = e.get("role", "?")
        action = e.get("action") or e.get("reason") or "?"
        by_action[action] += 1
        role_action[(role, action)] += 1
        if action == "deploy" and e.get("amount_atomic"):
            deployed_total += int(e["amount_atomic"])
        if action == "recall" and e.get("amount_atomic"):
            recalled_total += int(e["amount_atomic"])
        wallet = e.get("wallet")
        if not wallet:
            continue
        snap = latest_snapshot.setdefault(wallet, {"ts": e.get("ts")})
        for f in snapshot_fields:
            v = e.get(f)
            if v not in (None, "", "None") and f not in snap:
                snap[f] = v

    return {
        "window_hours": hours,
        "total_events": len(events),
        "by_event_type": dict(by_event),
        "by_action": dict(by_action),
        "by_role_action": {f"{r}/{a}": c for (r, a), c in role_action.items()},
        "deployed_total_atomic": deployed_total,
        "recalled_total_atomic": recalled_total,
        "latest_snapshot_by_wallet": latest_snapshot,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=24, help="Lookback window (default 24h)")
    p.add_argument("--json", action="store_true", help="Emit raw JSON summary")
    args = p.parse_args()

    client = boto3.client("logs", region_name=REGION)
    log_groups = _discover_log_groups(client)
    if not log_groups:
        print("No Divigent log groups discovered.", file=sys.stderr)
        return 1
    all_events: list[dict] = []
    for lg in log_groups:
        evts = _run_insights(client, lg, args.hours)
        all_events.extend(evts)
        print(f"  {len(evts):>4} events from {lg}", file=sys.stderr)
    summary = _summarize(all_events, args.hours)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return 0

    # Human-readable table
    print(f"\n─── Divigent integration — last {args.hours}h ─────────────────────")
    print(f"Total events:    {summary['total_events']}")
    print()
    print("By event type:")
    for k, v in sorted(summary["by_event_type"].items()):
        print(f"  {k:36s} {v}")
    print()
    print("By role × action:")
    for k, v in sorted(summary["by_role_action"].items()):
        print(f"  {k:36s} {v}")
    print()
    print(f"Deployed total:  {_fmt_usdc(summary['deployed_total_atomic'])} USDC")
    print(f"Recalled total:  {_fmt_usdc(summary['recalled_total_atomic'])} USDC")
    net_deployed = summary["deployed_total_atomic"] - summary["recalled_total_atomic"]
    print(f"Net flow:        {_fmt_usdc(net_deployed)} USDC into Divigent")
    print()
    print("Latest wallet snapshots:")
    for wallet, snap in summary["latest_snapshot_by_wallet"].items():
        print(f"  {wallet}")
        print(f"    status:    {snap.get('liquidity_status')}   ({snap.get('risk_preference')})")
        print(f"    idle USDC: {_fmt_usdc(snap.get('wallet_balance_atomic'))}")
        print(f"    position:  {_fmt_usdc(snap.get('position_current_value_atomic'))}")
        print(f"    reserve:   {_fmt_usdc(snap.get('required_reserve_atomic'))}")
        print(f"    as-of:     {snap.get('ts')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
