#!/usr/bin/env python3
"""Offline tests for the one-run-per-authorization guard and PAID_CALL timing.

    .venv/bin/python scripts/test_payment_claim.py
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("A2A_ALLOW_MEMORY_STORE", "1")

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
from services import a2a, payment_claim  # noqa: E402

_fails = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        _fails += 1


def header(nonce: str, network: str = "eip155:8453", frm: str = "0xAbC") -> str:
    return base64.b64encode(json.dumps({
        "x402Version": 2, "accepted": {"network": network},
        "payload": {"authorization": {"from": frm, "nonce": nonce}, "signature": "0x00"},
    }).encode()).decode()


# claim_key
k1 = payment_claim.claim_key(header("0xAA"))
ok("EVM key is network + payer + nonce", k1 == "pay:eip155:8453:0xabc:0xaa", k1)
ok("same authorization re-encoded → same key",
   payment_claim.claim_key(base64.b64encode(json.dumps(json.loads(base64.b64decode(header("0xAA"))), indent=2).encode()).decode()) == k1)
sol = base64.b64encode(json.dumps({"accepted": {"network": "solana:x"}, "payload": {"transaction": "AQID"}}).encode()).decode()
ok("unparseable Solana tx falls back to its bytes", payment_claim.claim_key(sol).startswith("pay:solana:x:"))
# Solana: a copy that differs only in the fee payer's signature slot is the same payment
from solders.hash import Hash  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.message import MessageV0, to_bytes_versioned  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.signature import Signature  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402

_fee, _payer = Keypair(), Keypair()
_ix = Instruction(Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"), b"x",
                  [AccountMeta(_payer.pubkey(), True, False)])
_msg = MessageV0.try_compile(_fee.pubkey(), [_ix], [], Hash.default())
_sig = _payer.sign_message(to_bytes_versioned(_msg))


def _svm_header(fee_slot: Signature) -> str:
    tx = base64.b64encode(bytes(VersionedTransaction.populate(_msg, [fee_slot, _sig]))).decode()
    return base64.b64encode(json.dumps({"accepted": {"network": "solana:x"},
                                        "payload": {"transaction": tx}}).encode()).decode()


ok("Solana copy with a different fee-payer slot → same key",
   payment_claim.claim_key(_svm_header(Signature.default())) == payment_claim.claim_key(_svm_header(Signature.new_unique())))
ok("garbage header still gets a key", payment_claim.claim_key("not base64!").startswith("pay:raw:"))

# claim
a2a._local_store.clear()
ok("first presentation is claimed", payment_claim.claim(header("0x01")) is True)
ok("second presentation is refused", payment_claim.claim(header("0x01")) is False)
ok("a different nonce is independent", payment_claim.claim(header("0x02")) is True)


# claim lifetime follows the signed authorization, not a fixed TTL
def header_vb(nonce: str, valid_before) -> str:
    return base64.b64encode(json.dumps({
        "x402Version": 2, "accepted": {"network": "eip155:8453"},
        "payload": {"authorization": {"from": "0xAbC", "nonce": nonce, "validBefore": str(valid_before)},
                    "signature": "0x00"},
    }).encode()).decode()


T = 1_800_000_000
F = T + payment_claim.CLAIM_TTL_S
ok("short authorization keeps the 900 s floor", payment_claim.claim_expiry(header_vb("0x10", T + 300), T) == F)
ok("1 h authorization is kept past its validBefore",
   payment_claim.claim_expiry(header_vb("0x11", T + 3600), T) == T + 3600 + payment_claim.CLOCK_SKEW_S)
ok("authorization valid > 1 year is beyond retention (refused, not given a shorter claim)",
   payment_claim.beyond_retention(header_vb("0x12", 2**256), T)
   and payment_claim.beyond_retention(header_vb("0x15", T + 400 * 86400), T))
ok("normal and 1 h authorizations are within retention",
   not payment_claim.beyond_retention(header_vb("0x16", T + 300), T)
   and not payment_claim.beyond_retention(header_vb("0x17", T + 3600), T)
   and not payment_claim.beyond_retention(header("0x18"), T) and not payment_claim.beyond_retention(sol, T))
ok("missing or junk validBefore → floor",
   payment_claim.claim_expiry(header("0x13"), T) == F and payment_claim.claim_expiry(header_vb("0x14", "soon"), T) == F)
ok("Solana / raw headers → floor", payment_claim.claim_expiry(sol, T) == F and payment_claim.claim_expiry("junk", T) == F)

a2a._local_store.clear()
_real_time = time.time  # a2a and payment_claim both read the time module
h1 = header_vb("0x20", int(_real_time()) + 3600)
ok("1 h authorization claimed", payment_claim.claim(h1) is True)
time.time = lambda: _real_time() + 16 * 60   # past the old fixed TTL
ok("copy at 16 min is still refused", payment_claim.claim(h1) is False)
time.time = lambda: _real_time() + 3600 + payment_claim.CLOCK_SKEW_S + 1
ok("after validBefore + skew the claim lapses", payment_claim.claim(h1) is True)
time.time = _real_time

# middleware: a replayed authorization never reaches the route
a2a._local_store.clear()
c = TestClient(app.app)
ran = []
real = app.payment_middleware


def fake_payment_middleware(routes, server):
    async def mw(request, call_next):
        ran.append(request.url.path)
        return await call_next(request)
    return mw


app.payment_middleware = fake_payment_middleware
h = header("0x99")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    r1 = c.post("/v1/roll", json={"low": 1, "high": 6}, headers={"PAYMENT-SIGNATURE": h})
    r2 = c.post("/v1/roll", json={"low": 1, "high": 6}, headers={"PAYMENT-SIGNATURE": h})
logs = out.getvalue().splitlines()
ok("first copy runs the route", r1.status_code == 200 and ran == ["/v1/roll"], f"{r1.status_code} {ran}")
ok("second copy → 402 payment_replayed, route not run",
   r2.status_code == 402 and r2.json()["error"] == "payment_replayed" and ran == ["/v1/roll"], f"{r2.status_code} {ran}")
ok("exactly one PAID_CALL, for the delivered call", sum(ln.startswith("PAID_CALL ") for ln in logs) == 1, str(logs))
ok("replay is logged as payment_replayed", any('"kind":"payment_replayed"' in ln for ln in logs))

# an authorization valid longer than a claim is kept never reaches the route
a2a._local_store.clear()
ran.clear()
app.payment_middleware = fake_payment_middleware
far = header_vb("0x88", int(time.time()) + 2 * 366 * 86400)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    r = c.post("/v1/roll", json={"low": 1, "high": 6}, headers={"PAYMENT-SIGNATURE": far})
ok("over-long authorization → 402 authorization_validity_too_long, route not run, nothing claimed",
   r.status_code == 402 and r.json()["error"] == "authorization_validity_too_long" and ran == [] and not a2a._local_store,
   f"{r.status_code} {ran}")

# PAID_CALL follows the final status: a failed settlement is not a sale
a2a._local_store.clear()


def settle_fails(routes, server):
    async def mw(request, call_next):
        await call_next(request)  # the route runs, then settlement fails
        return app.JSONResponse(status_code=402, content={})
    return mw


app.payment_middleware = settle_fails
out = io.StringIO()
with contextlib.redirect_stdout(out):
    r = c.post("/v1/roll", json={"low": 1, "high": 6}, headers={"PAYMENT-SIGNATURE": header("0x77")})
ok("failed settlement → no PAID_CALL", r.status_code == 402 and "PAID_CALL " not in out.getvalue())

# free routes are untouched by a stray payment header
app.payment_middleware = real
a2a._local_store.clear()
ok("free route with a payment header is not claimed",
   c.get("/health", headers={"PAYMENT-SIGNATURE": header("0x55")}).status_code == 200 and not a2a._local_store)

print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all payment-claim checks OK")
