"""Base Sepolia trial of /v1/screen with a counterparty-held settlement gate.

One bounded testnet call for Agoragentic's release gate. The flow, inside the
x402 handler (after the buyer's authorization is verified, before settlement):

  1. claim the single attempt for this window (DynamoDB, write-once)
  2. compute the screen verdict
  3. sign a result proof binding the quote, the request body, the buyer's
     payment authorization (hash + nonce) and the result
  4. POST it to the counterparty's verifier and wait for its signed decision
  5. return 2xx, and so settle, only on a valid "allow_settlement"

A deny, a bad or missing signature, a mismatched binding, a timeout or any
error returns non-2xx, and the x402 middleware settles only on 2xx. Our proof
is signed with the A2A identity key (anchor-a2a-2026-01, published in our agent
card and did.json); their decision is verified against the key they publish in
their own agent card, read with the same namespace-agnostic reader as /v1/a2a.

Everything is off until WINDOW and VERIFIER are set, and the window is checked
on every request, so the route closes itself when it ends.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Any

import requests

from services import a2a
from services import screen as screen_svc

log = logging.getLogger("sepolia_trial")

PATH = "/v1/trial/screen"
CAPABILITY_ID = "anchor-x402/trial-screen"
CAPABILITY_VERSION = "1"
NETWORK = "eip155:84532"
ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia USDC
AMOUNT = "20000"  # 0.02 USDC, 6 decimals
PRICE = "$0.02"
FACILITATOR_URL = "https://api.cdp.coinbase.com/platform/v2/x402"
RESULT_RULE = (
    "An address in the static OFAC corpus returns recommendation=block, "
    "risk_score=100, risk_level=critical, sanctions_match=true, regardless of "
    "the GoPlus reputation layer. deterministic_result carries only fields "
    "that follow from the corpus; the full result may add reputation signals."
)
DECISION_TYPE = "agoragentic.trial_decision.v1"
DECISION_TIMEOUT_S = 15  # API Gateway cuts the whole request at 29s

# Set both to open the trial. WINDOW is (start, end) in UTC ISO-8601.
WINDOW: tuple[str, str] | None = None
VERIFIER: dict[str, str] | None = None  # {"url", "origin", "key_id"}

_UNSIGNED = ("digest", "signature", "signature_algorithm")


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def configured() -> bool:
    return bool(WINDOW and VERIFIER)


def is_open(now: float | None = None) -> bool:
    if not configured():
        return False
    t = time.time() if now is None else now
    return _ts(WINDOW[0]) <= t < _ts(WINDOW[1])


def corpus_sha256() -> str:
    return a2a.digest_of({"evm": screen_svc._EVM_SANCTIONED, "solana": screen_svc._SOLANA_SANCTIONED})


def quote(resource_url: str, pay_to: str) -> dict[str, Any]:
    """Route-specific quote, signed. No timestamp inside, so the digest is stable
    for the life of the window and both sides can pin it in advance."""
    return a2a.sign({
        "type": "anchor.trial_quote.v1",
        "capability_id": CAPABILITY_ID,
        "capability_version": CAPABILITY_VERSION,
        "method": "POST",
        "resource": resource_url,
        "scheme": "exact",
        "network": NETWORK,
        "asset": ASSET,
        "amount": AMOUNT,
        "pay_to": pay_to,
        "facilitator_url": FACILITATOR_URL,
        "provider_charge": "none",
        "valid_from": WINDOW[0],
        "valid_until": WINDOW[1],
        "max_attempts": 1,
        "retry": "none; the attempt is consumed when the paid request reaches the handler, whatever the outcome",
        "corpus_version": screen_svc._OFAC_CORPUS_VERSION,
        "corpus_sha256": corpus_sha256(),
        "result_rule": RESULT_RULE,
        "verifier": {"url": VERIFIER["url"], "origin": VERIFIER["origin"], "key_id": VERIFIER["key_id"]},
        "decision_type": DECISION_TYPE,
        "decision_timeout_s": DECISION_TIMEOUT_S,
    })


def _payment(headers) -> dict[str, Any]:
    """Bind the exact buyer authorization: hash of the header as sent, plus the
    fields a verifier needs to recognise it."""
    raw = headers.get("payment-signature") or headers.get("x-payment") or ""
    out: dict[str, Any] = {"authorization_sha256": "sha256:" + hashlib.sha256(raw.encode()).hexdigest()}
    try:
        decoded = json.loads(base64.b64decode(raw))
        auth = (decoded.get("payload") or {}).get("authorization") or {}
        accepted = decoded.get("accepted") or {}
        out.update({
            "payer": auth.get("from"),
            "nonce": auth.get("nonce"),
            "value": auth.get("value"),
            "valid_before": auth.get("validBefore"),
            "network": accepted.get("network"),
            "pay_to": accepted.get("payTo") or auth.get("to"),
        })
    except (ValueError, binascii.Error, AttributeError, TypeError):
        pass
    return out


def _deterministic(result: dict[str, Any]) -> dict[str, Any]:
    keys = ("wallet", "chain_inferred", "sanctions_match", "sanctioned_lists",
            "recommendation", "risk_score", "risk_level", "corpus_version")
    return {k: result.get(k) for k in keys}


def _verify_decision(decision: Any, proof: dict[str, Any], payment: dict[str, Any]) -> str | None:
    """None if the decision is a valid allow; otherwise the reason it is not."""
    if not isinstance(decision, dict):
        return "decision is not a JSON object"
    if decision.get("type") != DECISION_TYPE:
        return "wrong decision type"
    if decision.get("key_id") != VERIFIER["key_id"]:
        return "decision signed with an unexpected key_id"
    for field, want in (
        ("result_proof_digest", proof["digest"]),
        ("quote_digest", proof["quote_digest"]),
        ("buyer_nonce", payment.get("nonce")),
        ("payment_authorization_sha256", payment["authorization_sha256"]),
    ):
        if not want or decision.get(field) != want:
            return f"decision does not bind {field}"
    payload = {k: v for k, v in decision.items() if k not in _UNSIGNED}
    if decision.get("digest") != a2a.digest_of(payload):
        return "decision digest does not match its payload"
    try:
        key = a2a.peer_key(VERIFIER["origin"], VERIFIER["key_id"])
        key.verify(base64.b64decode(decision.get("signature") or ""), decision["digest"].encode("ascii"))
    except Exception as e:
        return f"decision signature does not verify: {a2a.log_safe(e, 120)}"
    if decision.get("decision") != "allow_settlement":
        return "verifier denied settlement"
    return None


def run(wallet: str, body: dict[str, Any], headers, resource_url: str, pay_to: str) -> tuple[int, dict[str, Any]]:
    """Returns (status, body). Only 200 leads to settlement."""
    if not a2a._put_once(f"trial:sepolia:{WINDOW[0]}", int(_ts(WINDOW[1])) + 7 * 86400):
        return 409, {"error": "trial_already_attempted", "settled": False}

    payment = _payment(headers)
    q = quote(resource_url, pay_to)
    result = screen_svc.screen(wallet)
    deterministic = _deterministic(result)
    proof = a2a.sign({
        "type": "anchor.trial_result.v1",
        "capability_id": CAPABILITY_ID,
        "capability_version": CAPABILITY_VERSION,
        "quote_digest": q["digest"],
        "request": {"method": "POST", "resource": resource_url, "body_digest": a2a.digest_of(body)},
        "payment": payment,
        "result": result,
        "deterministic_result": deterministic,
        "deterministic_result_digest": a2a.digest_of(deterministic),
        "issued_at": int(time.time()),
        "decision_deadline": int(time.time()) + DECISION_TIMEOUT_S,
        "settlement": "pending the verifier's decision; nothing has settled",
    })
    if not proof.get("signed"):
        return 503, {"error": "result_proof_unsigned", "settled": False}

    try:
        r = requests.post(
            VERIFIER["url"],
            json={"result_proof": proof},
            timeout=DECISION_TIMEOUT_S,
            allow_redirects=False,
            headers={"user-agent": "anchor-x402-trial/1"},
        )
        decision = r.json() if r.status_code == 200 else None
        reason = _verify_decision(decision, proof, payment) if decision is not None else f"verifier returned HTTP {r.status_code}"
    except requests.Timeout:
        decision, reason = None, "verifier timed out"
    except Exception as e:
        decision, reason = None, f"verifier unreachable: {a2a.log_safe(e, 120)}"

    print("TRIAL " + json.dumps({
        "ts": int(time.time()), "proof": proof["digest"], "nonce": payment.get("nonce"),
        "recommendation": result.get("recommendation"), "allowed": reason is None, "reason": reason,
    }, separators=(",", ":")))

    trial = {"result_proof": proof, "decision": decision}
    if reason is not None:
        return 403, {"error": "settlement_not_allowed", "reason": reason, "settled": False, "trial": trial}
    return 200, {**result, "trial": trial}
