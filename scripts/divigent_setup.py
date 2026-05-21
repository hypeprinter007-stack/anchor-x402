"""Divigent integration setup helper.

One-time operational tasks: generate the operator wallet, surface the
calldata for the three treasury bootstrap transactions, inspect live
state, and (optionally) ping the oracle keeper by hand.

Run from the repo root with the project venv active:

    python scripts/divigent_setup.py status
    python scripts/divigent_setup.py generate-operator
    python scripts/divigent_setup.py treasury-ops <operator-address>
    python scripts/divigent_setup.py record-oracle

The treasury wallet should sign the three `treasury-ops` transactions
externally (Rabby / Frame / Safe / hardware wallet). This script never
asks for the treasury private key.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running as `python scripts/divigent_setup.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from services import divigent  # noqa: E402

MAX_UINT256 = (1 << 256) - 1


def _usdc(amount_atomic: int) -> str:
    return f"{amount_atomic / 1_000_000:.6f}"


def cmd_status(_args: argparse.Namespace) -> int:
    treasury = os.getenv("TREASURY_ADDRESS", "")
    if not treasury:
        print("TREASURY_ADDRESS not set — load .env first.", file=sys.stderr)
        return 1

    from web3 import Web3
    treasury_cs = Web3.to_checksum_address(treasury)
    w3 = divigent._w3()
    r = divigent._router(w3)
    u = divigent._usdc(w3)

    op_key = divigent._operator_key()
    operator_cs = w3.eth.account.from_key(op_key).address if op_key else None

    deposits_paused = r.functions.depositsPaused().call()
    authorized     = r.functions.authorizedWallets(treasury_cs).call()
    operator_ok    = r.functions.isOperator(treasury_cs, operator_cs).call() if operator_cs else False
    min_deposit    = r.functions.MIN_DEPOSIT().call()
    tvl_cap        = r.functions.currentTVLCap().call()
    oracle_status  = divigent.get_oracle_status()
    position       = divigent.get_position(treasury_cs)
    idle           = divigent.get_idle_usdc(treasury_cs)
    op_eth_wei     = w3.eth.get_balance(operator_cs) if operator_cs else 0

    print("─── Divigent integration status ─────────────────────────────")
    print(f"Network            base mainnet (chainId 8453)")
    print(f"Router             {divigent.ROUTER_ADDRESS}")
    print(f"Oracle             {divigent.ORACLE_ADDRESS}")
    print(f"Treasury (seller)  {treasury_cs}")
    print(f"Operator (Lambda)  {operator_cs or '— not configured (set DIVIGENT_OPERATOR_PRIVATE_KEY) —'}")
    print()
    print(f"Deposits paused:   {deposits_paused}")
    print(f"Treasury authed:   {authorized}   (router.authorizedWallets)")
    print(f"Operator approved: {operator_ok}   (router.isOperator)")
    print(f"Oracle fresh:      {oracle_status['fresh']}   (last obs: {oracle_status['last_observation_at']})")
    print(f"Deposits enabled:  {divigent.DIVIGENT_ENABLED}   (DIVIGENT_ENABLED env)")
    print()
    print(f"Idle USDC          {_usdc(idle)} ({idle} atomic)")
    print(f"Reserve floor      {_usdc(divigent.MIN_HOT_USDC_ATOMIC)} ({divigent.MIN_HOT_USDC_ATOMIC} atomic)")
    print(f"MIN_DEPOSIT        {_usdc(min_deposit)} ({min_deposit} atomic)")
    print(f"Current TVL cap    {_usdc(tvl_cap)}")
    sweep_amount = max(0, idle - divigent.MIN_HOT_USDC_ATOMIC)
    print(f"Would sweep:       {_usdc(sweep_amount)}   ({'eligible' if sweep_amount >= min_deposit else 'below MIN_DEPOSIT — skip'})")
    print()
    print(f"Position deposited:  {_usdc(position['deposited_usdc'])} USDC")
    print(f"Position current:    {_usdc(position['current_value'])} USDC")
    print(f"Position accrued:    {_usdc(position['accrued_yield'])} USDC")
    print()
    if operator_cs:
        eth = op_eth_wei / 1e18
        marker = " ⚠ low" if eth < 0.0005 else ""
        print(f"Operator gas (ETH): {eth:.6f}{marker}")
    return 0


def cmd_generate_operator(_args: argparse.Namespace) -> int:
    from eth_account import Account
    acct = Account.create()
    print("─── New operator wallet ──────────────────────────────────────")
    print(f"Address:     {acct.address}")
    print(f"Private key: 0x{acct.key.hex()}")
    print()
    print("Next steps:")
    print("  1. Fund this address with ~0.001 ETH on Base for gas.")
    print("  2. Store the private key in Secrets Manager under the key")
    print("     'divigent_operator_key' inside the anchor-x402/runtime secret.")
    print("  3. From the treasury wallet, call:")
    print(f"        router.setOperator({acct.address}, true)")
    print("     See `scripts/divigent_setup.py treasury-ops <address>` for calldata.")
    print("  4. Set DIVIGENT_ENABLED=true and redeploy.")
    return 0


def cmd_treasury_ops(args: argparse.Namespace) -> int:
    from web3 import Web3
    operator = Web3.to_checksum_address(args.operator)
    treasury = Web3.to_checksum_address(os.getenv("TREASURY_ADDRESS", "") or "0x0")

    w3 = divigent._w3()
    r = divigent._router(w3)
    u = divigent._usdc(w3)

    initialize_data = r.encode_abi("initialize", args=[])
    set_operator_data = r.encode_abi("setOperator", args=[operator, True])
    approve_data = u.encode_abi("approve", args=[Web3.to_checksum_address(divigent.ROUTER_ADDRESS), MAX_UINT256])

    print("─── Treasury one-time bootstrap transactions ─────────────────")
    print(f"Sign these from the treasury wallet ({treasury}).")
    print(f"Each is to the contract at `to`, value=0, data=`data`. Chain 8453 (Base).")
    print()
    print("# 1. Register treasury wallet with Divigent")
    print(f"to:   {divigent.ROUTER_ADDRESS}")
    print(f"data: {initialize_data}")
    print()
    print("# 2. Authorize Lambda operator to deposit/withdraw on treasury's behalf")
    print(f"to:   {divigent.ROUTER_ADDRESS}")
    print(f"data: {set_operator_data}")
    print()
    print("# 3. Approve router to pull USDC from treasury (MAX_UINT256 — one-time)")
    print(f"to:   {divigent.USDC_ADDRESS}")
    print(f"data: {approve_data}")
    print()
    print("After all three confirm, run:")
    print("    python scripts/divigent_setup.py status")
    print("to verify authorized=true, operator approved=true.")
    return 0


def cmd_bootstrap_treasury(args: argparse.Namespace) -> int:
    """Sign + broadcast the 3 treasury bootstrap txs using the treasury key
    from Secrets Manager / .env. Use this when the treasury key is already
    available in this runtime; otherwise use `treasury-ops` to emit calldata
    for an external signer."""
    from services import secrets as _secrets
    from web3 import Web3

    operator = Web3.to_checksum_address(args.operator)
    key = _secrets.get("treasury_evm_key", env_fallback="TREASURY_PRIVATE_KEY")
    if not key:
        print("treasury_evm_key not available — set TREASURY_PRIVATE_KEY in .env or ANCHOR_SECRET_ARN.", file=sys.stderr)
        return 1

    w3 = divigent._w3()
    acct = w3.eth.account.from_key(key)
    r = divigent._router(w3)
    u = divigent._usdc(w3)

    print(f"Treasury wallet:  {acct.address}")
    print(f"Operator wallet:  {operator}")
    print(f"Network:          base mainnet (chainId 8453)")
    print()

    # Skip steps that are already done — initialize() reverts on re-call;
    # setOperator/approve are idempotent but cost gas needlessly.
    plan = []
    if not r.functions.authorizedWallets(acct.address).call():
        plan.append(("initialize", r.functions.initialize()))
    else:
        print("✓ treasury already authorized (skipping initialize)")
    if not r.functions.isOperator(acct.address, operator).call():
        plan.append(("setOperator", r.functions.setOperator(operator, True)))
    else:
        print("✓ operator already approved (skipping setOperator)")
    current_allowance = u.functions.allowance(
        acct.address, Web3.to_checksum_address(divigent.ROUTER_ADDRESS)
    ).call()
    if current_allowance < 10**24:  # 1M USDC headroom — re-approve if dust
        plan.append(("approve", u.functions.approve(
            Web3.to_checksum_address(divigent.ROUTER_ADDRESS), MAX_UINT256,
        )))
    else:
        print(f"✓ USDC allowance already set ({current_allowance} atomic, skipping approve)")

    if not plan:
        print("\nNothing to do. Run `status` to confirm.")
        return 0

    print(f"\nWill broadcast {len(plan)} transaction(s):")
    for name, _ in plan:
        print(f"  • {name}")
    print()

    nonce = w3.eth.get_transaction_count(acct.address)
    for name, fn in plan:
        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "chainId": 8453,
            "maxFeePerGas": w3.eth.gas_price,
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        })
        tx["gas"] = w3.eth.estimate_gas(tx)
        signed = w3.eth.account.sign_transaction(tx, key)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hex = "0x" + h.hex()
        print(f"{name}: {tx_hex} — waiting for receipt…", flush=True)
        receipt = w3.eth.wait_for_transaction_receipt(h, timeout=120)
        status = "✓ confirmed" if receipt.status == 1 else "✗ reverted"
        print(f"  {status} (block {receipt.blockNumber}, gas used {receipt.gasUsed})")
        nonce += 1

    print("\nDone. Verify with: python scripts/divigent_setup.py status")
    return 0


def cmd_record_oracle(_args: argparse.Namespace) -> int:
    result = divigent.record_oracle_observation()
    print(result)
    return 0 if result.get("recorded") else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="divigent_setup")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show live state of the integration")
    sub.add_parser("generate-operator", help="generate a fresh operator wallet")
    t = sub.add_parser("treasury-ops", help="emit calldata for the 3 treasury bootstrap txs (external signing)")
    t.add_argument("operator", help="0x-prefixed operator address (from generate-operator)")
    bt = sub.add_parser("bootstrap-treasury", help="sign + broadcast the 3 treasury bootstrap txs using the treasury key")
    bt.add_argument("operator", help="0x-prefixed operator address (from generate-operator)")
    sub.add_parser("record-oracle", help="manually trigger oracle.recordObservation()")

    args = p.parse_args()
    return {
        "status": cmd_status,
        "generate-operator": cmd_generate_operator,
        "treasury-ops": cmd_treasury_ops,
        "bootstrap-treasury": cmd_bootstrap_treasury,
        "record-oracle": cmd_record_oracle,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
