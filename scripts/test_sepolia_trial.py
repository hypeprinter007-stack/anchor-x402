#!/usr/bin/env python3
"""Offline tests for the Base Sepolia trial gate (services/sepolia_trial.py).

The verifier is faked with a local Ed25519 key and our A2A signer with another,
so the full decision protocol runs without KMS, a network or a payment. The
settlement rule under test: only a validly signed, correctly bound
"allow_settlement" yields 200; everything else is non-2xx, so nothing settles.

    .venv/bin/python scripts/test_sepolia_trial.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("A2A_ALLOW_MEMORY_STORE", "1")

import requests  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
from services import a2a, sepolia_trial  # noqa: E402

_fails = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        _fails += 1


FIXTURE = "0x8589427373d6d84e98730d7795d8f6f8731fda16"
RESOURCE = "https://api.anchor-x402.com/v1/trial/screen"
PAY_TO = "0x127462e296fAc1A7F5cF33bA57bB2f0FFf5cD0B6"
NONCE = "0x" + "ab" * 32

anchor_key = Ed25519PrivateKey.generate()
verifier_key = Ed25519PrivateKey.generate()
a2a.sign_digest = lambda d: anchor_key.sign(d.encode("ascii"))
a2a.peer_key = lambda origin, key_id: verifier_key.public_key()
sepolia_trial.screen_svc._goplus_lookup = lambda addr: None  # keep the OFAC floor the only input

now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
sepolia_trial.WINDOW = (iso(now - timedelta(minutes=5)), iso(now + timedelta(minutes=25)))
sepolia_trial.VERIFIER = {"url": "https://verifier.example/decide", "origin": "https://verifier.example", "key_id": "agx-trial-1"}

payment_header = base64.b64encode(json.dumps({
    "x402Version": 2,
    "accepted": {"network": sepolia_trial.NETWORK, "payTo": PAY_TO},
    "payload": {"authorization": {"from": "0xbuyer", "to": PAY_TO, "value": "20000", "validBefore": "9999999999", "nonce": NONCE}},
}).encode()).decode()
HEADERS = {"payment-signature": payment_header}


def decision_for(proof: dict, verdict: str = "allow_settlement", **override) -> dict:
    payload = {
        "type": sepolia_trial.DECISION_TYPE,
        "decision": verdict,
        "result_proof_digest": proof["digest"],
        "quote_digest": proof["quote_digest"],
        "buyer_nonce": proof["payment"]["nonce"],
        "payment_authorization_sha256": proof["payment"]["authorization_sha256"],
        "decided_at": 1,
        "key_id": "agx-trial-1",
    }
    payload.update(override)
    out = dict(payload, digest=a2a.digest_of(payload), signature_algorithm="ed25519")
    out["signature"] = base64.b64encode(verifier_key.sign(out["digest"].encode("ascii"))).decode()
    return out


class _Resp:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def verifier(behaviour):
    def post(url, json=None, **kw):
        return behaviour(json["result_proof"])
    requests.post = post
    sepolia_trial.requests.post = post


def attempt():
    a2a._local_store.clear()
    return sepolia_trial.run(FIXTURE, {"wallet": FIXTURE}, HEADERS, RESOURCE, PAY_TO)


print("allow path")
verifier(lambda p: _Resp(200, decision_for(p)))
status, body = attempt()
ok("valid signed allow → 200 (settles)", status == 200, str(body.get("reason")))
ok("verdict is block for the OFAC fixture", body.get("recommendation") == "block" and body.get("risk_score") == 100)
proof = body["trial"]["result_proof"]
ok("result proof is signed", proof.get("signed") is True)
ok("proof digest recomputes", proof["digest"] == a2a.digest_of({k: v for k, v in proof.items() if k not in ("digest", "signed", "signature_algorithm", "key_id", "signature")}))
ok("proof signature verifies with our key", anchor_key.public_key().verify(base64.b64decode(proof["signature"]), proof["digest"].encode()) is None)
ok("proof binds the buyer nonce", proof["payment"]["nonce"] == NONCE)
ok("proof binds the exact authorization header", proof["payment"]["authorization_sha256"].startswith("sha256:"))
ok("deterministic result digest is stable", proof["deterministic_result_digest"] == a2a.digest_of(proof["deterministic_result"]))

print("one attempt")
status, body = sepolia_trial.run(FIXTURE, {"wallet": FIXTURE}, HEADERS, RESOURCE, PAY_TO)
ok("second attempt in the window → 409, no settle", status == 409 and body.get("settled") is False)

print("everything else refuses")
cases = {
    "deny": lambda p: _Resp(200, decision_for(p, "deny")),
    "signature by another key": lambda p: _Resp(200, {**decision_for(p), "signature": base64.b64encode(Ed25519PrivateKey.generate().sign(b"x")).decode()}),
    "tampered after signing": lambda p: _Resp(200, {**decision_for(p), "decision": "allow_settlement", "buyer_nonce": "0x00"}),
    "wrong nonce, validly signed": lambda p: _Resp(200, decision_for(p, buyer_nonce="0x00")),
    "wrong result digest": lambda p: _Resp(200, decision_for(p, result_proof_digest="sha256:00")),
    "wrong key_id": lambda p: _Resp(200, decision_for(p, key_id="other")),
    "verifier HTTP 500": lambda p: _Resp(500, {}),
    "verifier not JSON object": lambda p: _Resp(200, ["allow_settlement"]),
}
for name, behaviour in cases.items():
    verifier(behaviour)
    status, body = attempt()
    ok(f"{name} → non-2xx", status >= 400 and body.get("settled") is False, f"{status} {body.get('reason')}")


def _timeout(p):
    raise requests.Timeout()


verifier(_timeout)
status, body = attempt()
ok("verifier timeout → non-2xx", status >= 400 and body.get("reason") == "verifier timed out")

print("quote")
q1 = sepolia_trial.quote(RESOURCE, PAY_TO)
q2 = sepolia_trial.quote(RESOURCE, PAY_TO)
ok("quote digest is stable", q1["digest"] == q2["digest"])
ok("quote carries amount, asset, payTo, window, corpus hash", all(q1.get(k) for k in ("amount", "asset", "pay_to", "valid_until", "corpus_sha256")))

print("routes")
c = TestClient(app.app)
r = c.post("/v1/trial/screen", json={"wallet": FIXTURE})
ok("open window, unpaid → 402 on Base Sepolia", r.status_code == 402 and "84532" in base64.b64decode(r.headers["payment-required"]).decode())
ok("quote endpoint serves the signed quote", c.get("/v1/trial/screen/quote").json().get("digest") == q1["digest"])
ok("not in OpenAPI", "/v1/trial/screen" not in c.get("/openapi.json").json()["paths"])
ok("not in x402_routes", not any("trial" in k for k in app.x402_routes))
sepolia_trial.WINDOW = (iso(now - timedelta(hours=2)), iso(now - timedelta(hours=1)))
ok("after the window → 404", c.post("/v1/trial/screen", json={"wallet": FIXTURE}).status_code == 404)
sepolia_trial.WINDOW = None
ok("unconfigured → quote 404", c.get("/v1/trial/screen/quote").status_code == 404)

print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all sepolia trial checks OK")
