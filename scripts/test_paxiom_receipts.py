#!/usr/bin/env python3
"""Verify opt-in paxiom.receipt.v0 delivery headers (X-Paxiom-Receipt).

Checks:
  1. Default disabled mode: /health (200) and paid 402 challenges omit X-Paxiom-Receipt,
     and CORS expose-headers include x-paxiom-receipt.
  2. Opt-in enabled mode (PAXIOM_PRIVATE_KEY_PEM set):
     - 200 JSON responses carry a valid Ed25519 X-Paxiom-Receipt bound to the body.
     - Tampered output bodies fail verification with output_hash_mismatch.
     - Unpaid 402 challenges and HEAD probes pass through untouched without signing.

    .venv/bin/python scripts/test_paxiom_receipts.py
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("A2A_ALLOW_MEMORY_STORE", "1")
os.environ.pop("PAXIOM_PRIVATE_KEY_PEM", None)

from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from paxiom_receipts import generate_receipt_keypair, verify_response_receipt  # noqa: E402

import app  # noqa: E402


def _offline_payment_mw(routes, _server):
    async def _mw(request, call_next):
        if f"{request.method} {request.url.path}" not in routes:
            return await call_next(request)
        return JSONResponse(
            status_code=402,
            content={"error": "payment_required"},
            headers={"payment-required": "dGVzdC1jaGFsbGVuZ2U="},
        )

    return _mw


def main() -> int:
    fails = 0

    def check(cond: bool, label: str, detail: str = "") -> None:
        nonlocal fails
        if cond:
            print(f"  PASS  {label}")
        else:
            fails += 1
            print(f"  FAIL  {label}" + (f": {detail}" if detail else ""))

    # 1. Default disabled mode
    app.payment_middleware = _offline_payment_mw
    c_default = TestClient(app.app)
    r_health = c_default.get("/health", headers={"Origin": "https://agent.example"})
    check(
        r_health.status_code == 200 and "x-paxiom-receipt" not in r_health.headers,
        "default disabled: GET /health omits x-paxiom-receipt",
        f"status={r_health.status_code}",
    )
    exposed = r_health.headers.get("access-control-expose-headers", "").lower()
    check(
        "x-paxiom-receipt" in exposed,
        "CORS expose_headers lists x-paxiom-receipt",
        exposed,
    )

    r_402 = c_default.post("/v1/anchor", json={})
    check(
        r_402.status_code == 402
        and r_402.headers.get("payment-required") == "dGVzdC1jaGFsbGVuZ2U="
        and "x-paxiom-receipt" not in r_402.headers,
        "default disabled: POST /v1/anchor preserves 402 payment-required challenge",
        f"status={r_402.status_code}",
    )

    # 2. Opt-in enabled mode via PAXIOM_PRIVATE_KEY_PEM
    kp = generate_receipt_keypair(key_id="anchor-test-01")
    os.environ["PAXIOM_PRIVATE_KEY_PEM"] = kp["privateKeyPem"]
    os.environ["PAXIOM_KEY_ID"] = "anchor-test-01"
    try:
        importlib.reload(app)
        app.payment_middleware = _offline_payment_mw
        c_enabled = TestClient(app.app)

        r_signed = c_enabled.get("/health")
        verdict = verify_response_receipt(dict(r_signed.headers), output=r_signed.json())
        check(
            r_signed.status_code == 200
            and verdict["present"] is True
            and verdict["valid"] is True
            and verdict["receipt"]["payload"]["issuer"]["keyId"] == "anchor-test-01",
            "enabled: GET /health attaches valid Ed25519 X-Paxiom-Receipt",
            str(verdict),
        )

        tampered = verify_response_receipt(dict(r_signed.headers), output={"status": "tampered"})
        check(
            tampered["present"] is True
            and tampered["valid"] is False
            and tampered["trustState"] == "output_hash_mismatch",
            "enabled: tampered response body fails with output_hash_mismatch",
            str(tampered),
        )

        r_402_enabled = c_enabled.post("/v1/anchor", json={})
        check(
            r_402_enabled.status_code == 402
            and r_402_enabled.headers.get("payment-required") == "dGVzdC1jaGFsbGVuZ2U="
            and "x-paxiom-receipt" not in r_402_enabled.headers,
            "enabled: unpaid 402 challenge passes through unsigned",
            f"status={r_402_enabled.status_code}",
        )

        r_head = c_enabled.head("/health")
        check(
            r_head.status_code == 200 and r_head.content == b"",
            "enabled: HEAD /health passes through HeadAsGetMiddleware cleanly",
            f"status={r_head.status_code} len={len(r_head.content)}",
        )
    finally:
        os.environ.pop("PAXIOM_PRIVATE_KEY_PEM", None)
        os.environ.pop("PAXIOM_KEY_ID", None)

    if fails:
        print(f"\n{fails} FAILED")
        return 1
    print("\nall paxiom receipt checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
