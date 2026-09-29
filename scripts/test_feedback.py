#!/usr/bin/env python3
"""Offline tests for POST /v1/feedback.

    .venv/bin/python scripts/test_feedback.py
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("A2A_ALLOW_MEMORY_STORE", "1")

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
from services import a2a, feedback  # noqa: E402

_fails = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        _fails += 1


c = TestClient(app.app)
REPORT = {"endpoint": "POST /v1/screen", "what_happened": "400 on a Solana address", "expected": "a verdict"}


def post(body, ip="203.0.113.7"):
    out = io.StringIO()
    feedback.source_ip = lambda scope: ip
    with contextlib.redirect_stdout(out):
        r = c.post("/v1/feedback", json=body)
    lines = [ln for ln in out.getvalue().splitlines() if ln.startswith("FEEDBACK ")]
    return r, lines


a2a._local_store.clear()
feedback._tripped.clear()

r, lines = post(REPORT)
ok("valid report → 200 with an id", r.status_code == 200 and r.json()["id"].startswith("fb_"), r.text)
ok("free: no payment challenge", "payment-required" not in r.headers)
ok("exactly one FEEDBACK log line", len(lines) == 1)
logged = json.loads(lines[0].removeprefix("FEEDBACK "))
ok("log line carries the report and the id", logged["endpoint"] == REPORT["endpoint"] and logged["id"] == r.json()["id"])

r, lines = post({**REPORT, "what_happened": 'x"}\nFEEDBACK {"forged":true}'})
ok("newline injection stays on one log line", len(lines) == 1 and "\n" not in lines[0])
ok("injected text is data, not a second record", json.loads(lines[0].removeprefix("FEEDBACK "))["what_happened"].startswith('x"}'))

ok("missing required field → 422", post({"endpoint": "x"})[0].status_code == 422)
ok("unknown field → 422", post({**REPORT, "run": "rm -rf /"})[0].status_code == 422)
ok("oversize what_happened → 422", post({**REPORT, "what_happened": "a" * 2001})[0].status_code == 422)

a2a._local_store.clear()
feedback._tripped.clear()
codes = [post(REPORT, ip="198.51.100.1")[0].status_code for _ in range(feedback.LIMIT_PER_IP + 1)]
ok("per-IP limit: 10 accepted, the 11th → 429", codes[:10] == [200] * 10 and codes[10] == 429, str(codes))
ok("another IP is unaffected", post(REPORT, ip="198.51.100.2")[0].status_code == 200)

g = c.get("/v1/feedback").json()
ok("GET describes the route and its schema", g["method"] == "POST" and "what_happened" in g["body_schema"]["properties"])
ok("listed in OpenAPI", "/v1/feedback" in c.get("/openapi.json").json()["paths"])
ok("not a paid route", not any("feedback" in k for k in app.x402_routes))

importlib.reload(feedback)  # undo the source_ip stub used above
scope = {"aws.event": {"requestContext": {"http": {"sourceIp": "192.0.2.9"}}}, "client": ("10.0.0.1", 1)}
ok("source_ip reads API Gateway's sourceIp, not x-forwarded-for", feedback.source_ip(scope) == "192.0.2.9")

print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all feedback checks OK")
