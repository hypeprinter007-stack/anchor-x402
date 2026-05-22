"""Divigent yield router integration — serverless Python pattern.

Two-layer architecture for the v1.0.3+ liquidity intelligence flow:

  Intelligence layer (Node Lambda, services/divigent-intelligence/):
    Wraps @divigent/sdk's `assessLiquidity()`. Returns JSON decisions —
    `recommendedAction` (none/deploy/recall), amounts, reserve targets,
    venue health. Reads only; no signing. Keeps Divigent's intelligence
    math compiled inside the SDK rather than ported into this codebase.

  Execution layer (this module + services/divigent_cron.py):
    Holds the operator key (Secrets Manager) and signs the deposit /
    withdraw transactions Divigent recommended, on behalf of the cold
    treasury wallet via operator delegation (`router.setOperator()`).
    The treasury private key never enters Lambda.

Companion to https://github.com/hypeprinter007-stack/signalfuse-divigent-router
(same protocol from a long-running Node sidecar).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import boto3

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

# Intelligence-layer policy. Defaults are anchor-treasury-shaped: $5 floor
# (existing reserve floor), no scheduled outflows, deploy aggressively,
# conservative posture for the first production runs.
POLICY_MIN_OPERATING_BALANCE = int(os.getenv("DIVIGENT_MIN_OPERATING_BALANCE", str(MIN_HOT_USDC_ATOMIC)))
POLICY_UPCOMING_PAYOUTS      = int(os.getenv("DIVIGENT_UPCOMING_PAYOUTS", "0"))
POLICY_MAX_DEPLOYABLE_PCT    = int(os.getenv("DIVIGENT_MAX_DEPLOYABLE_PERCENT", "95"))
POLICY_RISK_PREFERENCE       = os.getenv("DIVIGENT_RISK_PREFERENCE", "conservative")

INTELLIGENCE_FUNCTION_NAME = os.getenv("DIVIGENT_INTELLIGENCE_FUNCTION", "")

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


_lambda_client = None


def _lambda():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda", region_name=os.getenv("AWS_REGION", "us-east-1"))
    return _lambda_client


def assess_liquidity(seller: str, pending_payment_atomic: int = 0) -> dict:
    """Synchronously invoke the Divigent intelligence Lambda.

    Returns the parsed `LiquidityAssessment` dict (bigint fields as
    decimal strings — caller converts to int as needed). Raises
    RuntimeError on any non-ok response.
    """
    if not INTELLIGENCE_FUNCTION_NAME:
        raise RuntimeError("DIVIGENT_INTELLIGENCE_FUNCTION not set")

    payload = {
        "action": "assessLiquidity",
        "wallet": seller,
        "pendingPaymentAmount": str(pending_payment_atomic) if pending_payment_atomic else None,
        "policyContext": {
            "minOperatingBalance": str(POLICY_MIN_OPERATING_BALANCE),
            "upcomingKnownPayouts": str(POLICY_UPCOMING_PAYOUTS),
            "maxDeployablePercent": POLICY_MAX_DEPLOYABLE_PCT,
            "riskPreference": POLICY_RISK_PREFERENCE,
        },
        "includeVenueHealth": True,
    }
    resp = _lambda().invoke(
        FunctionName=INTELLIGENCE_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    body = json.loads(resp["Payload"].read())
    if not body.get("ok"):
        raise RuntimeError(f"intelligence_lambda_failed: {body}")
    return body["assessment"]


def assess_and_act(seller: str | None = None) -> dict:
    """v2 sweep — ask Divigent's intelligence layer what to do, then execute.

    Replaces the static `idle - 5 USDC` formula. `recommendedAction` from
    `assessLiquidity()` drives one of {none, deploy, recall}; the
    execution layer (this function) signs + broadcasts via the operator
    wallet.
    """
    from web3 import Web3
    if not DIVIGENT_ENABLED:
        return {"acted": False, "reason": "divigent_disabled"}

    seller = seller or _treasury_address()
    if not seller:
        return {"acted": False, "reason": "treasury_address_unset"}

    op_key = _operator_key()
    if not op_key:
        return {"acted": False, "reason": "operator_key_unset"}

    w3 = _w3()
    seller_cs = Web3.to_checksum_address(seller)
    operator_cs = w3.eth.account.from_key(op_key).address

    r = _router(w3)
    if r.functions.depositsPaused().call():
        return {"acted": False, "reason": "deposits_paused"}
    if not r.functions.authorizedWallets(seller_cs).call():
        return {"acted": False, "reason": "seller_not_initialized"}
    if not r.functions.isOperator(seller_cs, operator_cs).call():
        return {"acted": False, "reason": "operator_not_authorized"}

    assessment = assess_liquidity(seller_cs)
    action = assessment.get("recommendedAction")
    log.info("divigent assessment: action=%s status=%s wallet_balance=%s required_reserve=%s",
             action, assessment.get("liquidityStatus"),
             assessment.get("walletBalance"), assessment.get("requiredReserve"))

    base_result = {"action": action, "assessment_summary": {
        "liquidityStatus": assessment.get("liquidityStatus"),
        "walletBalance": assessment.get("walletBalance"),
        "requiredReserve": assessment.get("requiredReserve"),
        "positionCurrentValue": assessment.get("positionCurrentValue"),
    }}

    if action == "none":
        return {**base_result, "acted": False, "reason": "no_action_recommended"}

    if action == "insufficient_liquidity":
        return {
            **base_result, "acted": False,
            "reason": "insufficient_liquidity",
            "recall_unavailable_code": assessment.get("recallUnavailableCode"),
            "recall_unavailable_reason": assessment.get("recallUnavailableReason"),
        }

    if action == "deploy":
        amount = int(assessment.get("recommendedDeploymentAmount", "0"))
        if amount <= 0:
            return {**base_result, "acted": False, "reason": "deploy_amount_zero"}
        expected_shares = r.functions.previewDeposit(amount).call()
        min_shares_out = expected_shares * (10_000 - SLIPPAGE_BPS) // 10_000
        tx_hex = _build_and_send(
            w3, op_key,
            r.functions.deposit(amount, seller_cs, min_shares_out),
        )
        log.info("divigent deploy: %d atomic USDC, tx=%s", amount, tx_hex)
        return {
            **base_result, "acted": True, "tx_hash": tx_hex,
            "amount": amount, "expected_shares": expected_shares,
            "min_shares_out": min_shares_out,
        }

    if action == "recall":
        shares = int(assessment.get("recommendedRecallShares", "0") or "0")
        amount = int(assessment.get("recommendedRecallAmount", "0"))
        if shares <= 0 or amount <= 0:
            return {**base_result, "acted": False, "reason": "recall_amount_zero"}
        min_usdc_out = amount * (10_000 - SLIPPAGE_BPS) // 10_000
        tx_hex = _build_and_send(
            w3, op_key,
            r.functions.withdraw(shares, seller_cs, min_usdc_out),
        )
        log.info("divigent recall: %d shares for %d atomic USDC, tx=%s", shares, amount, tx_hex)
        return {
            **base_result, "acted": True, "tx_hash": tx_hex,
            "shares_burned": shares, "expected_amount": amount,
            "min_usdc_out": min_usdc_out,
        }

    return {**base_result, "acted": False, "reason": "unknown_action"}


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
