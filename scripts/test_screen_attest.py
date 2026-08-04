#!/usr/bin/env python3
"""Offline unit tests for the enriched screen verdict and the attest dual-mode
mint + free verify. External calls (GoPlus, chain, treasury key) are
monkeypatched so the logic — scoring, recommendation, degrade path, the
verify AND-gate — is exercised deterministically.

    .venv/bin/python scripts/test_screen_attest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import services.screen as screen_svc  # noqa: E402
from models import AttestRequest, AttestVerifyRequest  # noqa: E402

_fails = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    status = "PASS" if cond else "FAIL"
    if not cond:
        _fails += 1
    print(f"  {status}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def _mock_goplus(flags: dict | None):
    """Return a fake _goplus_lookup that ignores the address and yields `flags`."""
    return lambda _addr: flags


TORNADO = "0x8589427373d6d84e98730d7795d8f6f8731fda16"  # OFAC in corpus
CLEAN = "0x1111111111111111111111111111111111111111"


def test_screen():
    print("screen: enriched payment-risk verdict")

    v = screen_svc.screen("not-an-address")
    ok("unknown address → review + partial", v["recommendation"] == "review" and v["partial"], str(v))

    # OFAC floor stands even when the enrichment layer is down.
    screen_svc._goplus_lookup = _mock_goplus(None)
    v = screen_svc.screen(TORNADO)
    ok("OFAC hit → block/critical/score100", v["recommendation"] == "block" and v["risk_level"] == "critical" and v["risk_score"] == 100)
    ok("OFAC hit → sanctions_match true", v["sanctions_match"] is True)
    ok("OFAC hit → exactly one ofac_sdn signal", [s["code"] for s in v["signals"]] == ["ofac_sdn"], str(v["signals"]))
    ok("OFAC hit with GoPlus down → partial", v["partial"] is True)

    all_zero = {k: "0" for k in screen_svc._GOPLUS_FLAGS}
    all_zero["contract_address"] = "0"
    screen_svc._goplus_lookup = _mock_goplus(dict(all_zero))
    v = screen_svc.screen(CLEAN)
    ok("clean EVM → allow/low/score0", v["recommendation"] == "allow" and v["risk_level"] == "low" and v["risk_score"] == 0)
    ok("clean EVM → not partial", v["partial"] is False)
    ok("clean EVM → address_type eoa", v["address_type"] == "eoa")

    crit = dict(all_zero); crit["phishing_activities"] = "1"
    screen_svc._goplus_lookup = _mock_goplus(crit)
    v = screen_svc.screen(CLEAN)
    ok("critical GoPlus flag → block", v["recommendation"] == "block" and v["risk_score"] >= 90)
    ok("critical flag surfaces as a signal", any(s["code"] == "phishing_activities" for s in v["signals"]))

    med = dict(all_zero); med["blacklist_doubt"] = "1"
    screen_svc._goplus_lookup = _mock_goplus(med)
    v = screen_svc.screen(CLEAN)
    ok("medium-only flag → review (not block)", v["recommendation"] == "review" and v["risk_level"] == "medium", str(v["risk_score"]))

    contract = dict(all_zero); contract["contract_address"] = "1"
    screen_svc._goplus_lookup = _mock_goplus(contract)
    v = screen_svc.screen(CLEAN)
    ok("contract detected via GoPlus", v["address_type"] == "contract")

    # Timeout on a clean-looking EVM address: verdict must be provisional.
    screen_svc._goplus_lookup = _mock_goplus(None)
    v = screen_svc.screen(CLEAN)
    ok("GoPlus down on EVM → allow but partial", v["recommendation"] == "allow" and v["partial"] is True)


def test_attest():
    print("\nattest: dual-mode mint + free verify")

    # Neutralize the chain + key so we test control flow, not settlement.
    app.anchor_svc.anchor_dual_chain = lambda root: {"base_tx": "0x" + "aa" * 32, "solana_tx": ""}

    # Hosted-signer mode: no signature supplied → treasury signs.
    app.attest_svc.sign_with_treasury = lambda i, o, d: ("0x" + "bb" * 65, "0xTREASURY")
    r = app.attest(AttestRequest(input_hash="a" * 64, output_hash="b" * 64, decision="APPROVED"))
    ok("hosted-sign → signed_by treasury", r.signed_by == "treasury" and r.signer == "0xTREASURY")
    ok("hosted-sign → verify_url present", bool(r.verify_url))

    # Bring-your-own valid signature.
    app.attest_svc.verify = lambda **kw: (True, "0xCALLER")
    r = app.attest(AttestRequest(input_hash="a" * 64, output_hash="b" * 64, decision="X", scheme="eip191", signature="0xdead"))
    ok("byo-sign valid → signed_by caller", r.signed_by == "caller" and r.signer == "0xCALLER")

    # Bring-your-own invalid signature → 400, never anchors.
    app.attest_svc.verify = lambda **kw: (False, "")
    try:
        app.attest(AttestRequest(input_hash="a" * 64, output_hash="b" * 64, decision="X", scheme="eip191", signature="0xbad"))
        ok("byo-sign invalid → rejected", False, "no exception raised")
    except Exception as e:
        ok("byo-sign invalid → 400", getattr(e, "status_code", None) == 400)

    # Free verify is an AND-gate: signature valid AND anchor confirmed.
    base_req = dict(input_hash="a" * 64, output_hash="b" * 64, decision="X", scheme="eip191", signature="0xsig", base_tx="0x" + "aa" * 32)

    app.attest_svc.verify = lambda **kw: (True, "0xCALLER")
    app.attest_svc.confirm_base_anchor = lambda tx, root: {"root_matches": True, "confirmed": True, "block": 100, "anchored_at": 1785000000}
    r = app.attest_verify(AttestVerifyRequest(**base_req))
    ok("verify: sig ok + anchor confirmed → valid", r.valid is True and r.anchored_at_block == 100)

    app.attest_svc.verify = lambda **kw: (False, "")
    r = app.attest_verify(AttestVerifyRequest(**base_req))
    ok("verify: bad signature → invalid", r.valid is False and r.signature_valid is False)

    app.attest_svc.verify = lambda **kw: (True, "0xCALLER")
    app.attest_svc.confirm_base_anchor = lambda tx, root: {"root_matches": True, "confirmed": False, "block": None, "anchored_at": None}
    r = app.attest_verify(AttestVerifyRequest(**base_req))
    ok("verify: sig ok but anchor unconfirmed → invalid", r.valid is False and r.signature_valid is True)


if __name__ == "__main__":
    test_screen()
    test_attest()
    print()
    if _fails:
        print(f"{_fails} FAILED")
        sys.exit(1)
    print("all screen + attest checks OK")
