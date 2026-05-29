"""CDP discovery heartbeat — daily paid probes against the 8 endpoints with no
organic outside-buyer traffic (/v1/attest, /v1/parse/datetime, /v1/roll, and
the five LLM routes). The InvestigatorPulse cron covers /v1/investigate and
the investigator worker pays for 7 more during each job, leaving these 8
exposed to CDP's activity-driven 30-day TTL.

Mirrors the x402 client pattern from risk-investigator/poller/pulse.py.
Reuses the same EOA wallet (`risk-investigator/acp-pulse-client` secret) so
no new funding stream to manage.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("anchor.cdp_heartbeat")

ANCHOR_BASE_URL = os.environ.get("ANCHOR_X402_BASE_URL", "https://api.anchor-x402.com")

# Endpoint roster. Payloads are shape-valid so the request reaches the route
# handler; whether the handler returns 200 or a business-logic 4xx doesn't
# matter — payment settles in x402_mw before the route runs, which is the
# activity CDP indexes.
_ZERO_HASH = "00" * 32
_HEARTBEAT_NOTE = "discovery-heartbeat"

PROBES: list[tuple[str, str, dict[str, Any]]] = [
    ("POST", "/v1/attest", {
        "input_hash": _ZERO_HASH,
        "output_hash": _ZERO_HASH,
        "decision": "HEARTBEAT",
        "scheme": "eip191",
        "signature": "0x" + "00" * 65,
    }),
    ("POST", "/v1/parse/datetime", {"input": "tomorrow at noon"}),
    ("POST", "/v1/roll", {"low": 1, "high": 100, "count": 1}),
    ("POST", "/v1/roast", {"target": _HEARTBEAT_NOTE}),
    ("POST", "/v1/oracle", {"question": "is this a heartbeat?"}),
    ("POST", "/v1/tldr", {"text": _HEARTBEAT_NOTE}),
    ("POST", "/v1/aura", {"target": _HEARTBEAT_NOTE}),
    ("POST", "/v1/grade", {"target": _HEARTBEAT_NOTE}),
]


_X402_HTTP_CLIENT = None


def _x402_http_client():
    global _X402_HTTP_CLIENT
    if _X402_HTTP_CLIENT is not None:
        return _X402_HTTP_CLIENT

    from eth_account import Account
    from x402.client import x402ClientSync
    from x402.http.x402_http_client import x402HTTPClientSync
    from x402.mechanisms.evm.exact import register_exact_evm_client
    from x402.mechanisms.evm.signers import EthAccountSigner

    pk = os.environ["HEARTBEAT_WALLET_PRIVATE_KEY"].strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    account = Account.from_key(pk)
    signer = EthAccountSigner(account)

    base = x402ClientSync()
    register_exact_evm_client(base, signer)
    _X402_HTTP_CLIENT = x402HTTPClientSync(base)
    log.info("x402 client built for wallet %s", account.address)
    return _X402_HTTP_CLIENT


def _emit(event_type: str, **fields: Any) -> None:
    payload = {"event_type": event_type, "ts": datetime.now(timezone.utc).isoformat(), **fields}
    log.info("CDP_HEARTBEAT_EVENT %s", json.dumps(payload, default=str))


def _probe(method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    timeout = httpx.Timeout(60.0, connect=10.0)
    with httpx.Client(base_url=ANCHOR_BASE_URL, timeout=timeout, follow_redirects=False) as client:
        r = client.request(method, path, json=body)

    if r.status_code != 402:
        return {"status": r.status_code, "paid": False, "note": "no 402 challenge"}

    headers_in = {k.lower(): v for k, v in r.headers.items()}
    payment_headers, _ = _x402_http_client().handle_402_response(headers_in, r.content)

    with httpx.Client(base_url=ANCHOR_BASE_URL, timeout=timeout, follow_redirects=False) as client:
        r2 = client.request(method, path, json=body, headers=payment_headers)

    return {"status": r2.status_code, "paid": r2.status_code != 402}


def handler(event, context):
    """EventBridge target — daily heartbeat across the uncovered endpoints."""
    results = []
    for method, path, body in PROBES:
        try:
            result = _probe(method, path, body)
            _emit("heartbeat.probe", path=path, **result)
            results.append({"path": path, **result})
        except Exception as e:
            _emit("heartbeat.error", path=path, error_type=type(e).__name__, error=str(e)[:300])
            results.append({"path": path, "error": type(e).__name__})

    paid = sum(1 for r in results if r.get("paid"))
    _emit("heartbeat.summary", probed=len(results), paid=paid)
    return {"probed": len(results), "paid": paid, "results": results}
