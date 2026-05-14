"""End-to-end JPYC paid tests on Polygon.

Exercises the JPYC rail by paying real JPYC from a buyer wallet to the
anchor-x402 treasury via EIP-3009 transferWithAuthorization. The buyer
never pays gas — the anchor-x402 relayer submits the transfer on its
behalf, paying POL out of the same EOA that receives the JPYC (single-
wallet mode).

Setup:
    export JPYC_TEST_PRIVATE_KEY=0x...   # buyer wallet on Polygon
    The wallet must hold ≥ ¥12 JPYC (≈ $0.08). No POL needed; the
    server-side relayer pays gas.

Usage:
    .venv/bin/python scripts/test_jpyc_e2e.py
    .venv/bin/python scripts/test_jpyc_e2e.py --only screen
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from eth_account import Account
from web3 import Web3
from x402 import x402ClientSync
from x402.http.clients.requests import x402_requests
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner

API = os.getenv("ANCHOR_API_URL", "https://api.anchor-x402.com")
RPC = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
JPYC = "0x431D5dfF03120AFA4bDf332c61A6e1766eF37BDB"
TREASURY = "0x127462e296fAc1A7F5cF33bA57bB2f0FFf5cD0B6"
BAL_OF_ABI = [{
    "name": "balanceOf",
    "type": "function",
    "stateMutability": "view",
    "inputs": [{"name": "_owner", "type": "address"}],
    "outputs": [{"name": "balance", "type": "uint256"}],
}]


def _jpyc_balance(w3: Web3, addr: str) -> int:
    c = w3.eth.contract(address=Web3.to_checksum_address(JPYC), abi=BAL_OF_ABI)
    return c.functions.balanceOf(Web3.to_checksum_address(addr)).call()


def _make_client(pk: str):
    payer = EthAccountSigner(Account.from_key(pk))
    cli = x402ClientSync()
    cli.register("eip155:137", ExactEvmClientScheme(signer=payer))
    return x402_requests(cli)


def _settlement(resp) -> dict | None:
    # v2 uses PAYMENT-RESPONSE; X-PAYMENT-RESPONSE is the v1 legacy name.
    hdr = resp.headers.get("payment-response") or resp.headers.get("x-payment-response")
    if not hdr:
        return None
    try:
        return json.loads(base64.b64decode(hdr))
    except Exception:
        return None


def _run_one(label: str, fn, expected_atomic: int) -> tuple[bool, int]:
    print(f"\n[{label}] ¥{expected_atomic / 1e18:g} expected …")
    try:
        r = fn()
    except Exception as e:
        print(f"  ✗ exception: {type(e).__name__}: {e}")
        return False, 0
    info = _settlement(r)
    if r.status_code != 200:
        print(f"  ✗ status {r.status_code}: {r.text[:200]}")
        return False, 0
    if not info:
        print("  ✗ no X-PAYMENT-RESPONSE settlement header")
        return False, 0
    tx = info.get("transaction") or info.get("tx") or "?"
    print(f"  ✓ paid; settle tx={tx}")
    print(f"    https://polygonscan.com/tx/{tx}")
    return True, expected_atomic


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated subset: screen,anchor,oracle")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    pk = os.environ.get("JPYC_TEST_PRIVATE_KEY")
    if not pk:
        print("set JPYC_TEST_PRIVATE_KEY in env (Polygon EOA holding ≥ ¥12 JPYC)")
        return 2

    buyer = Account.from_key(pk)
    w3 = Web3(Web3.HTTPProvider(RPC))
    print(f"buyer    : {buyer.address}")
    print(f"treasury : {TREASURY}")
    print(f"rpc      : {RPC}")
    print(f"api      : {API}")

    pre_buyer = _jpyc_balance(w3, buyer.address)
    pre_treas = _jpyc_balance(w3, TREASURY)
    print(f"buyer JPYC pre-test: ¥{pre_buyer / 1e18:.4f}")
    print(f"treas JPYC pre-test: ¥{pre_treas / 1e18:.4f}")
    if pre_buyer < 12 * 10**18:
        print(f"\nbuyer needs at least ¥12 JPYC; has ¥{pre_buyer / 1e18:.4f}")
        return 1

    s = _make_client(pk)
    expected = 0
    failures = 0

    tests = [
        ("screen", lambda: s.get(f"{API}/v1/screen?wallet=0x8589427373d6d84e98730d7795d8f6f8731fda16"), 10**17),  # ¥0.1
        ("anchor", lambda: s.post(f"{API}/v1/anchor", json={
            "hash": hashlib.sha256(b"jpyc e2e " + os.urandom(8)).hexdigest(),
        }), 10**18),  # ¥1
        ("oracle", lambda: s.post(f"{API}/v1/oracle", json={
            "question": "Will this JPYC settle on Polygon?",
        }), 10 * 10**18),  # ¥10
    ]
    for label, fn, atomic in tests:
        if only and label not in only:
            continue
        ok, spent = _run_one(label, fn, atomic)
        if ok:
            expected += spent
        else:
            failures += 1

    if expected == 0:
        print("\nno tests ran")
        return 1

    print(f"\nexpected on-chain ¥-spend: {expected / 1e18:g}")
    print("waiting 12s for finality …")
    time.sleep(12)

    post_buyer = _jpyc_balance(w3, buyer.address)
    post_treas = _jpyc_balance(w3, TREASURY)
    buyer_delta = pre_buyer - post_buyer
    treas_delta = post_treas - pre_treas
    print(f"buyer Δ: -¥{buyer_delta / 1e18:.4f}")
    print(f"treas Δ: +¥{treas_delta / 1e18:.4f}")

    on_chain_ok = buyer_delta == expected and treas_delta == expected
    if on_chain_ok:
        print(f"  ✓ on-chain deltas match expected spend")
    else:
        print(f"  ✗ delta mismatch — expected ¥{expected / 1e18:g}")
        failures += 1

    print(f"\n{'PASS' if failures == 0 else 'FAIL'}: {len(tests) - failures}/{len(tests)} endpoints + on-chain check")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
