"""CDP discovery heartbeat — daily paid probes against endpoints with no
organic outside-buyer traffic. Two product surfaces:

  anchor-x402: 8 routes not covered by InvestigatorPulse or the worker
    (attest, parse/datetime, roll, roast, oracle, tldr, aura, grade).
  signalfuse:  all 10 paid routes — no internal cron currently pays for any.

Mirrors the x402 client pattern from risk-investigator/poller/pulse.py.
Reuses the same EOA wallet (`risk-investigator/acp-pulse-client` secret).
SignalFuse payments recycle back to Christopher's treasury (own product);
upstream costs (Brave/Tavily/e2b) are real but ~$0.015/day, negligible.
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
SIGNALFUSE_BASE_URL = os.environ.get("SIGNALFUSE_BASE_URL", "https://api.signalfuse.co")

# Probe shape: (method, path, body-or-None). GET endpoints with query params
# bake them into the path. Payloads are shape-valid so the request reaches
# the route handler; the 402-settle happens in middleware regardless of
# whether the handler ultimately returns 200 or a business-logic 4xx.
_ZERO_HASH = "00" * 32
_HB = "discovery-heartbeat"

ANCHOR_PROBES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/v1/attest", {
        "input_hash": _ZERO_HASH,
        "output_hash": _ZERO_HASH,
        "decision": "HEARTBEAT",
        "scheme": "eip191",
        "signature": "0x" + "00" * 65,
    }),
    ("POST", "/v1/parse/datetime", {"input": "tomorrow at noon"}),
    ("POST", "/v1/roll", {"low": 1, "high": 100, "count": 1}),
    ("POST", "/v1/roast", {"target": _HB}),
    ("POST", "/v1/oracle", {"question": "is this a heartbeat?"}),
    ("POST", "/v1/tldr", {"text": _HB}),
    ("POST", "/v1/aura", {"target": _HB}),
    ("POST", "/v1/grade", {"target": _HB}),
]

SIGNALFUSE_PROBES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/v1/regime", None),
    ("GET", "/v1/sentiment/BTC", None),
    ("GET", "/v1/signal/BTC", None),
    ("GET", "/v1/signal/batch", None),
    ("GET", "/v1/gateway/search/brave?q=heartbeat", None),
    ("GET", "/v1/arena/vwap_reversion/BTC", None),
    ("GET", "/v1/arena/batch", None),
    ("POST", "/v1/gateway/search", {"query": _HB, "max_results": 1}),
    ("POST", "/v1/gateway/search/tavily", {"query": _HB, "max_results": 1}),
    ("POST", "/v1/gateway/execute/e2b", {"code": "print('hb')", "timeout": 10}),
]

TARGETS = [
    ("anchor", ANCHOR_BASE_URL, ANCHOR_PROBES),
    ("signalfuse", SIGNALFUSE_BASE_URL, SIGNALFUSE_PROBES),
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


def _probe(base_url: str, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    timeout = httpx.Timeout(60.0, connect=10.0)
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body

    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False) as client:
        r = client.request(method, path, **kwargs)

    if r.status_code != 402:
        return {"status": r.status_code, "paid": False, "note": "no 402 challenge"}

    headers_in = {k.lower(): v for k, v in r.headers.items()}
    payment_headers, _ = _x402_http_client().handle_402_response(headers_in, r.content)

    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False) as client:
        r2 = client.request(method, path, headers=payment_headers, **kwargs)

    return {"status": r2.status_code, "paid": r2.status_code != 402}


def handler(event, context):
    """EventBridge target — daily heartbeat across both product surfaces."""
    results = []
    for target_name, base_url, probes in TARGETS:
        for method, path, body in probes:
            try:
                result = _probe(base_url, method, path, body)
                _emit("heartbeat.probe", target=target_name, path=path, **result)
                results.append({"target": target_name, "path": path, **result})
            except Exception as e:
                _emit("heartbeat.error", target=target_name, path=path,
                      error_type=type(e).__name__, error=str(e)[:300])
                results.append({"target": target_name, "path": path, "error": type(e).__name__})

    paid = sum(1 for r in results if r.get("paid"))
    _emit("heartbeat.summary", probed=len(results), paid=paid)
    return {"probed": len(results), "paid": paid, "results": results}
