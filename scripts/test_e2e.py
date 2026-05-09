"""End-to-end paid tests for all 9 anchor-x402 services.

Pays real USDC for each service from the gavel CLIENT_PRIVATE_KEY wallet,
which already has Base USDC. Reports pass/fail per service.

Usage:
    .venv/bin/python scripts/test_e2e.py
    .venv/bin/python scripts/test_e2e.py --only anchor,screen
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import traceback

from dotenv import load_dotenv

# Pull CLIENT_PRIVATE_KEY from gavel/.env — it already has Base USDC funded.
load_dotenv("/Users/cferjoair/gavel/.env")
load_dotenv()  # local .env takes precedence if defined

from eth_account import Account
from eth_account.messages import encode_defunct
from x402 import x402ClientSync
from x402.http.clients.requests import x402_requests
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner

API = os.getenv("ANCHOR_API_URL", "https://api.anchor-x402.com")
CLIENT_KEY = os.environ["CLIENT_PRIVATE_KEY"]


def _client():
    payer = EthAccountSigner(Account.from_key(CLIENT_KEY))
    cli = x402ClientSync()
    cli.register("eip155:8453", ExactEvmClientScheme(signer=payer))
    return x402_requests(cli)


def _ok(label: str, status: int, body: str | dict, expected: int = 200) -> bool:
    if status == expected:
        print(f"  ✓ {label}: {status}")
        if isinstance(body, dict):
            preview = {k: v for i, (k, v) in enumerate(body.items()) if i < 4}
            print(f"    {preview}")
        return True
    print(f"  ✗ {label}: got {status}, expected {expected}")
    print(f"    body: {str(body)[:300]}")
    return False


def test_anchor(s) -> bool:
    h = hashlib.sha256(b"anchor-x402 test " + os.urandom(8)).hexdigest()
    r = s.post(f"{API}/v1/anchor", json={"hash": h, "note": "e2e test"})
    return _ok("anchor", r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def test_screen(s) -> bool:
    # Tornado Cash address — should match
    r = s.get(f"{API}/v1/screen?wallet=0x8589427373d6d84e98730d7795d8f6f8731fda16")
    j = r.json()
    if r.status_code != 200:
        return _ok("screen", r.status_code, j)
    matched = j.get("sanctions_match")
    print(f"  ✓ screen: 200 — sanctions_match={matched} (expected True for Tornado Cash)")
    return matched is True


def test_attest(s) -> bool:
    input_hash = hashlib.sha256(b"agent input").hexdigest()
    output_hash = hashlib.sha256(b"agent output").hexdigest()
    decision = "APPROVED"
    msg_text = (
        "anchor-x402/attest/v1\n"
        f"input={input_hash}\n"
        f"output={output_hash}\n"
        f"decision={decision}"
    )
    msg = encode_defunct(msg_text.encode("utf-8"))
    sig = Account.from_key(CLIENT_KEY).sign_message(msg)
    r = s.post(f"{API}/v1/attest", json={
        "input_hash": input_hash,
        "output_hash": output_hash,
        "decision": decision,
        "scheme": "eip191",
        "signature": "0x" + sig.signature.hex(),
    })
    return _ok("attest", r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def test_decode_tx(s) -> bool:
    # Use a real Base mainnet tx — Counsel anchor from earlier
    r = s.post(f"{API}/v1/decode/tx", json={
        "chain": "base",
        "tx_hash": "0xf2908400d45af03d8c1b65b33851434c6fd178b682a143904a2bfa89ff2c1fa7",
    })
    return _ok("decode/tx", r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def test_resolve_name(s) -> bool:
    r = s.get(f"{API}/v1/resolve/name?name=vitalik.eth")
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code == 200 and isinstance(j, dict):
        addrs = j.get("addresses", [])
        if addrs and any(a.get("address", "").lower() == "0xd8da6bf26964af9d7eed9e03e53415d37aa96045" for a in addrs):
            print(f"  ✓ resolve/name: 200 — vitalik.eth → 0xd8dA…6045 ✓")
            return True
        print(f"  ⚠ resolve/name: 200 but vitalik.eth not resolved — addresses={addrs}")
        return False
    return _ok("resolve/name", r.status_code, j)


def test_token_price(s) -> bool:
    r = s.get(f"{API}/v1/price/token?symbol=ETH")
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code == 200 and isinstance(j, dict) and j.get("usd", 0) > 100:
        print(f"  ✓ price/token: 200 — ETH=${j['usd']}")
        return True
    return _ok("price/token", r.status_code, j)


def test_decode_calldata(s) -> bool:
    # USDC transfer(0xab5801…, 1e18)
    r = s.post(f"{API}/v1/decode/calldata", json={
        "chain": "ethereum",
        "calldata_hex": "0xa9059cbb000000000000000000000000ab5801a7d398351b8be11c439e05c5b3259aec9b0000000000000000000000000000000000000000000000000de0b6b3a7640000",
    })
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code == 200 and isinstance(j, dict) and j.get("function_name") == "transfer":
        print(f"  ✓ decode/calldata: 200 — function={j['function_name']}, params={len(j.get('params', []))}")
        return True
    return _ok("decode/calldata", r.status_code, j)


def test_parse_datetime(s) -> bool:
    r = s.post(f"{API}/v1/parse/datetime", json={
        "input": "tomorrow at noon",
        "timezone": "America/New_York",
    })
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code == 200 and isinstance(j, dict) and j.get("iso"):
        print(f"  ✓ parse/datetime: 200 — '{j.get('parsed_input')}' → {j.get('iso')} (confidence={j.get('confidence')})")
        return True
    return _ok("parse/datetime", r.status_code, j)


def test_intel_wallet(s) -> bool:
    # vitalik.eth address
    r = s.get(f"{API}/v1/intel/wallet?wallet=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    if r.status_code == 200 and isinstance(j, dict):
        bal = j.get("balances", {})
        print(f"  ✓ intel/wallet: 200 — chain={j.get('chain_inferred')}, base_eth={bal.get('base_eth')}, errors={len(j.get('errors', []))}")
        return True
    return _ok("intel/wallet", r.status_code, j)


TESTS = {
    "anchor": (test_anchor, "$0.005"),
    "screen": (test_screen, "$0.001"),
    "attest": (test_attest, "$0.010"),
    "decode-tx": (test_decode_tx, "$0.001"),
    "resolve-name": (test_resolve_name, "$0.001"),
    "price-token": (test_token_price, "$0.001"),
    "decode-calldata": (test_decode_calldata, "$0.001"),
    "parse-datetime": (test_parse_datetime, "$0.001"),
    "intel-wallet": (test_intel_wallet, "$0.005"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated subset (e.g. anchor,screen)")
    args = ap.parse_args()

    selected = TESTS
    if args.only:
        keys = [k.strip() for k in args.only.split(",")]
        selected = {k: TESTS[k] for k in keys if k in TESTS}

    s = _client()
    total_cost_cents = 0
    passes = 0
    fails = []
    for name, (fn, price) in selected.items():
        print(f"\n=== {name} ({price}) ===")
        try:
            ok = fn(s)
            if ok:
                passes += 1
                # accumulate cost (rough)
                cents = float(price.lstrip("$")) * 100
                total_cost_cents += cents
            else:
                fails.append(name)
        except Exception as e:
            traceback.print_exc()
            print(f"  ✗ {name}: EXCEPTION {type(e).__name__}: {e}")
            fails.append(name)

    print(f"\n{'='*40}\n{passes}/{len(selected)} passed | spent ~${total_cost_cents/100:.4f}\n")
    if fails:
        print("FAILED:", ", ".join(fails))
        sys.exit(1)


if __name__ == "__main__":
    main()
