"""Divigent yield router integration — serverless Python pattern.

Wraps Divigent's vault router (Base mainnet) so anchor-x402 can route idle
treasury USDC into Divigent yield without a long-running sidecar process.

Pattern: operator delegation. The treasury wallet (cold) calls
`router.setOperator(operatorAddr, true)` once at setup. Thereafter, a
Lambda-held operator wallet signs deposit/withdraw transactions on the
treasury's behalf via `deposit(amount, treasury, minSharesOut)`. The
treasury private key never enters a Lambda execution environment.

Companion to https://github.com/hypeprinter007-stack/signalfuse-divigent-router
(which uses the same protocol from a long-running Node sidecar instead).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from services import secrets

log = logging.getLogger("divigent")

# ── Divigent Base mainnet contracts ─────────────────────────────────
# Source: https://github.com/Divigent/divigent-sdk/blob/main/src/core/chains.ts
ROUTER_ADDRESS = "0xE958A89c2CCa697d4896990685800cc1D5AF2A01"
ORACLE_ADDRESS = "0x3Ba775E8fAE60E72c99dE10C720fC44ab38BF71A"
USDC_ADDRESS   = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

BASE_RPC_URL  = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
BASE_CHAIN_ID = 8453

# Reserve floor — never sweep below this idle balance. USDC has 6 decimals.
MIN_HOT_USDC_ATOMIC = int(float(os.getenv("DIVIGENT_MIN_HOT_USDC", "5")) * 1_000_000)

# Slippage tolerance for previewDeposit / previewWithdrawNet (basis points).
SLIPPAGE_BPS = int(os.getenv("DIVIGENT_SLIPPAGE_BPS", "50"))

DIVIGENT_ENABLED = os.getenv("DIVIGENT_ENABLED", "false").lower() == "true"

_ROUTER_ABI = json.loads((Path(__file__).parent / "abis" / "divigent_router.json").read_text())

_ERC20_MIN_ABI = [
    {"type": "function", "name": "balanceOf",
     "inputs": [{"name": "owner", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
    {"type": "function", "name": "approve",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable"},
    {"type": "function", "name": "allowance",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"},
]

_ORACLE_MIN_ABI = [
    {"type": "function", "name": "recordObservation",
     "inputs": [], "outputs": [], "stateMutability": "nonpayable"},
]


def _operator_key() -> str:
    return secrets.get("divigent_operator_key", env_fallback="DIVIGENT_OPERATOR_PRIVATE_KEY")


def _treasury_address() -> str:
    return os.getenv("TREASURY_ADDRESS", "")


def _w3():
    from web3 import Web3
    return Web3(Web3.HTTPProvider(BASE_RPC_URL))


def _router(w3=None):
    from web3 import Web3
    w3 = w3 or _w3()
    return w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESS), abi=_ROUTER_ABI)


def _usdc(w3=None):
    from web3 import Web3
    w3 = w3 or _w3()
    return w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=_ERC20_MIN_ABI)


def _oracle(w3=None):
    from web3 import Web3
    w3 = w3 or _w3()
    return w3.eth.contract(address=Web3.to_checksum_address(ORACLE_ADDRESS), abi=_ORACLE_MIN_ABI)


# ── Read-only views ─────────────────────────────────────────────────

def get_position(wallet: str) -> dict:
    """Seller's Divigent position. All values in USDC atomic units (6 dec)."""
    from web3 import Web3
    deposited, current_value, accrued_yield = _router().functions.getPosition(
        Web3.to_checksum_address(wallet)
    ).call()
    return {
        "deposited_usdc": deposited,
        "current_value": current_value,
        "accrued_yield": accrued_yield,
    }


def get_idle_usdc(wallet: str) -> int:
    """Wallet's USDC balance on Base, in atomic units (6 dec)."""
    from web3 import Web3
    return _usdc().functions.balanceOf(Web3.to_checksum_address(wallet)).call()


def get_oracle_status() -> dict:
    last, fresh = _router().functions.oracleStatus().call()
    return {"last_observation_at": last, "fresh": fresh}


def get_dashboard_snapshot(wallet: str | None = None) -> dict:
    """One read-only call used by both the /divigent/dashboard route and
    the snapshot ticker event body. Returns atomic units; let the caller
    format for humans."""
    wallet = wallet or _treasury_address()
    if not wallet:
        return {"error": "treasury_address_unset"}
    return {
        "wallet": wallet,
        "chain": "base",
        "router": ROUTER_ADDRESS,
        "idle_usdc_atomic": get_idle_usdc(wallet),
        "position": get_position(wallet),
        "oracle": get_oracle_status(),
    }


# ── Mutating calls (operator-signed) ────────────────────────────────

def _build_and_send(w3, op_key: str, fn) -> str:
    op = w3.eth.account.from_key(op_key)
    tx = fn.build_transaction({
        "from": op.address,
        "nonce": w3.eth.get_transaction_count(op.address),
        "chainId": BASE_CHAIN_ID,
        "maxFeePerGas": w3.eth.gas_price,
        "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
    })
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = w3.eth.account.sign_transaction(tx, op_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return "0x" + tx_hash.hex()


def sweep_idle(seller: str | None = None) -> dict:
    """Deposit (idle - MIN_HOT_USDC) into Divigent on behalf of seller.

    Returns {swept: bool, ...}. Idempotent in the sense that a re-invocation
    with no fresh idle will return swept=false with a reason.
    """
    from web3 import Web3
    if not DIVIGENT_ENABLED:
        return {"swept": False, "reason": "divigent_disabled"}

    seller = seller or _treasury_address()
    if not seller:
        return {"swept": False, "reason": "treasury_address_unset"}

    op_key = _operator_key()
    if not op_key:
        return {"swept": False, "reason": "operator_key_unset"}

    w3 = _w3()
    seller_cs = Web3.to_checksum_address(seller)
    operator_cs = w3.eth.account.from_key(op_key).address

    r = _router(w3)
    u = _usdc(w3)

    if r.functions.depositsPaused().call():
        return {"swept": False, "reason": "deposits_paused"}
    if not r.functions.authorizedWallets(seller_cs).call():
        return {"swept": False, "reason": "seller_not_initialized"}
    if not r.functions.isOperator(seller_cs, operator_cs).call():
        return {"swept": False, "reason": "operator_not_authorized"}

    idle = u.functions.balanceOf(seller_cs).call()
    min_deposit = r.functions.MIN_DEPOSIT().call()
    sweep_amount = max(0, idle - MIN_HOT_USDC_ATOMIC)
    if sweep_amount < min_deposit:
        return {
            "swept": False, "reason": "below_min_deposit",
            "idle": idle, "min_deposit": min_deposit,
        }

    expected_shares = r.functions.previewDeposit(sweep_amount).call()
    min_shares_out = expected_shares * (10_000 - SLIPPAGE_BPS) // 10_000

    tx_hex = _build_and_send(
        w3, op_key,
        r.functions.deposit(sweep_amount, seller_cs, min_shares_out),
    )
    log.info("divigent sweep: %d atomic USDC, tx=%s", sweep_amount, tx_hex)
    return {
        "swept": True,
        "tx_hash": tx_hex,
        "amount": sweep_amount,
        "expected_shares": expected_shares,
        "min_shares_out": min_shares_out,
    }


def withdraw_net_usdc(seller: str, desired_net_usdc_atomic: int) -> dict:
    """Burn enough shares to deliver desired_net_usdc_atomic to seller."""
    from web3 import Web3
    if not DIVIGENT_ENABLED:
        return {"withdrawn": False, "reason": "divigent_disabled"}
    op_key = _operator_key()
    if not op_key:
        raise RuntimeError("operator_key_unset")
    w3 = _w3()
    seller_cs = Web3.to_checksum_address(seller)
    r = _router(w3)

    shares_needed = r.functions.previewWithdrawNet(desired_net_usdc_atomic, seller_cs).call()
    min_usdc_out = desired_net_usdc_atomic * (10_000 - SLIPPAGE_BPS) // 10_000

    tx_hex = _build_and_send(
        w3, op_key,
        r.functions.withdraw(shares_needed, seller_cs, min_usdc_out),
    )
    return {
        "withdrawn": True,
        "tx_hash": tx_hex,
        "shares_burned": shares_needed,
        "min_usdc_out": min_usdc_out,
    }


def record_oracle_observation() -> dict:
    """Keeper call to refresh the oracle. Any address can call; gas paid by
    the operator wallet. Costs roughly $0.50/month if run hourly on Base."""
    if not DIVIGENT_ENABLED:
        return {"recorded": False, "reason": "divigent_disabled"}
    op_key = _operator_key()
    if not op_key:
        return {"recorded": False, "reason": "operator_key_unset"}
    w3 = _w3()
    tx_hex = _build_and_send(w3, op_key, _oracle(w3).functions.recordObservation())
    return {"recorded": True, "tx_hash": tx_hex}
