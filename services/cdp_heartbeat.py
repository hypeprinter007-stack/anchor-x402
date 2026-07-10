"""CDP discovery heartbeat — every-14-days paid probes against endpoints with no
organic outside-buyer traffic, keeping them inside CDP's 30-day active window
(2x margin). Two product surfaces:

  anchor-x402: 9 routes not covered by InvestigatorPulse or the worker
    (attest, decode/tx, parse/datetime, roll, roast, oracle, tldr, aura, grade).
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
# bake them into the path. CRITICAL: the 402-settle only happens on a 2xx
# response — a handler that 4xx/5xx's BEFORE returning does NOT settle, so the
# probe doesn't count toward the Bazaar 30-day active window. Every probe body
# below must drive the handler to a 200. (attest needs a real signature, built
# dynamically in `handler`; decode/tx needs a real on-chain tx.)
_ZERO_HASH = "00" * 32
_HB = "discovery-heartbeat"
# Permanent, immutable Base mainnet tx — decode/tx must reach a 200 to settle.
_REAL_BASE_TX = "0x9e1fd68b563cd36fbb42aa993f31762aaa7cfb876c579ccb40d36d16e178902b"

# NOTE: /v1/attest is NOT listed here — it needs a valid eip191 signature over
# the live wallet, so its probe is built in `handler` via `_attest_probe`.
ANCHOR_PROBES: list[tuple[str, str, dict[str, Any] | None]] = [
    # POST (not GET) to match decode/tx's declared discovery shape — CDP catalogs
    # and keeps the 30-day active window only on settlements matching the declared
    # method/body; the old GET probe settled daily but never counted for discovery.
    ("POST", "/v1/decode/tx", {"chain": "base", "tx_hash": _REAL_BASE_TX}),
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


def _attest_probe() -> tuple[str, str, dict[str, Any]]:
    """Build a /v1/attest probe with a REAL eip191 signature over the heartbeat
    wallet, signed the same way services/attest.py reconstructs and recovers it.
    A valid signature is required — attest 400s (and so never settles) otherwise.
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct

    pk = os.environ["HEARTBEAT_WALLET_PRIVATE_KEY"].strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    account = Account.from_key(pk)

    ih = oh = _ZERO_HASH
    decision = "HEARTBEAT"
    msg = f"anchor-x402/attest/v1\ninput={ih}\noutput={oh}\ndecision={decision}".encode("utf-8")
    sig = account.sign_message(encode_defunct(msg)).signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return ("POST", "/v1/attest", {
        "input_hash": ih, "output_hash": oh, "decision": decision,
        "scheme": "eip191", "signature": sig,
    })


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
    """EventBridge target — every-14-days heartbeat across both product surfaces."""
    results = []
    for target_name, base_url, probes in TARGETS:
        probe_list = list(probes)
        if target_name == "anchor":
            # attest needs a live signature — build it per run, not at import.
            probe_list = [_attest_probe()] + probe_list
        for method, path, body in probe_list:
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
