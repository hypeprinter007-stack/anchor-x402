"""Refund a paid /v1/investigate job whose status terminated FAILED.

Sends the investigation fare ($1.77 USDC) from the treasury wallet back to
the buyer's wallet on Base, idempotent against DDB's refund_tx column.
Only Base USDC (eip155:8453) is auto-refunded in v1 — Solana / Polygon
JPYC payers get a `refund_pending=manual` flag and a human follow-up.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

import boto3

from services import secrets

log = logging.getLogger("anchor.refund")

BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6
# $1.77 USDC in 6-decimal atomic units. Mirrors the /v1/investigate price.
REFUND_AMOUNT_ATOMIC = 1_770_000

_ERC20_TRANSFER_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    }
]


_DDB = None


def _ddb_table():
    global _DDB
    if _DDB is None:
        _DDB = boto3.resource("dynamodb").Table(
            os.environ.get("INVESTIGATOR_JOBS_TABLE", "risk-investigator-jobs")
        )
    return _DDB


def parse_buyer_from_x_payment(x_payment_header: str | None) -> tuple[str | None, str | None]:
    """Decode the X-PAYMENT header to extract (buyer_wallet, network). Returns
    (None, None) on absent/unparseable header — the caller should treat that
    as "buyer wallet unknown, no auto-refund possible." Internal-auth bypass
    calls and missing headers fall through this path silently."""
    if not x_payment_header:
        return None, None
    try:
        payload = json.loads(base64.b64decode(x_payment_header))
        network = payload.get("network")
        auth = payload.get("payload", {}).get("authorization", {})
        return auth.get("from"), network
    except Exception:
        return None, None


def _send_usdc(to_address: str, amount_atomic: int) -> str:
    """ERC-20 transfer from treasury → buyer on Base. Returns 0x-prefixed tx hash."""
    from web3 import Web3

    key = secrets.get("treasury_evm_key", env_fallback="TREASURY_PRIVATE_KEY")
    if not key:
        raise RuntimeError("TREASURY_PRIVATE_KEY not set")

    w3 = Web3(Web3.HTTPProvider(BASE_RPC_URL))
    acct = w3.eth.account.from_key(key)
    usdc = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_BASE),
        abi=_ERC20_TRANSFER_ABI,
    )
    to_addr = Web3.to_checksum_address(to_address)
    nonce = w3.eth.get_transaction_count(acct.address)
    gas_price = w3.eth.gas_price
    tx = usdc.functions.transfer(to_addr, amount_atomic).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "chainId": 8453,
        "maxFeePerGas": gas_price,
        "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
    })
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = w3.eth.account.sign_transaction(tx, key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return "0x" + tx_hash.hex()


def refund_failed_job(job_id: str) -> dict[str, Any]:
    """Refund a single FAILED job. Idempotent — checks DDB for an existing
    refund_tx before sending. Returns the refund result for caller logging."""
    table = _ddb_table()
    item = table.get_item(Key={"job_id": job_id}).get("Item")
    if not item:
        return {"skipped": "job not found", "job_id": job_id}
    if item.get("status") != "FAILED":
        return {"skipped": f"status={item.get('status')}", "job_id": job_id}
    if item.get("refund_tx"):
        return {"skipped": "already refunded", "refund_tx": item["refund_tx"]}

    buyer_wallet = item.get("buyer_wallet")
    buyer_network = item.get("buyer_network")
    if not buyer_wallet:
        return {"skipped": "no buyer_wallet captured", "job_id": job_id}
    if buyer_network != "eip155:8453":
        # Flag for manual followup; v1 only auto-refunds Base USDC.
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET refund_pending = :p",
            ExpressionAttributeValues={":p": "manual"},
        )
        return {"skipped": f"non-Base network {buyer_network}", "refund_pending": "manual"}

    tx_hash = _send_usdc(buyer_wallet, REFUND_AMOUNT_ATOMIC)
    log.info("refunded job=%s amount=%d to=%s tx=%s", job_id, REFUND_AMOUNT_ATOMIC, buyer_wallet, tx_hash)

    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET refund_tx = :t, refund_amount_atomic = :a, refunded_at = :ts",
        ExpressionAttributeValues={
            ":t": tx_hash,
            ":a": REFUND_AMOUNT_ATOMIC,
            ":ts": int(time.time()),
        },
    )
    return {"refund_tx": tx_hash, "refund_amount_atomic": REFUND_AMOUNT_ATOMIC}
