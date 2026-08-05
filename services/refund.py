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

from services import screen as screen_svc
from services import secrets

log = logging.getLogger("anchor.refund")

BASE_RPC_URL = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6
# $1.77 USDC in 6-decimal atomic units. Mirrors the /v1/investigate price.
# Fallback only: jobs that carry price_atomic (e.g. $0.35 ledger reports)
# refund their own amount instead.
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


def _svm_payer_from_tx(tx_b64: str) -> str | None:
    """Buyer pubkey out of an x402 `exact` SVM payment.

    The SVM payload carries a serialized, partially-signed VersionedTransaction
    instead of the EVM shape's flat authorization object, so there is no `from`
    field to read — the buyer exists only inside the transaction, and reading it
    is the difference between knowing who to refund and not.

    The x402 SVM client compiles the message with exactly two signers and pins
    their order: index 0 is the facilitator's fee payer, index 1 is the buyer
    (see x402/mechanisms/svm/exact/client.py, which builds
    `signatures = [Signature.default(), client_signature]`). We require that
    two-signer shape and return None otherwise, so an unfamiliar layout yields
    "unknown" rather than a confidently wrong address that a refund is sent to.
    """
    from solders.transaction import VersionedTransaction

    tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
    message = tx.message
    if message.header.num_required_signatures < 2:
        return None
    keys = list(message.account_keys)
    if len(keys) < 2:
        return None
    return str(keys[1])


def parse_buyer_from_x_payment(x_payment_header: str | None) -> tuple[str | None, str | None]:
    """Decode a base64 x402 payment header to extract (buyer_wallet, network).

    Accepts both the V2 `PAYMENT-SIGNATURE` and the legacy V1 `X-PAYMENT` payload
    shapes — V2 carries the network under `accepted.network`, V1 at the top level.
    On EVM the payer `from` sits under `payload.authorization`; on Solana there is
    no such field and the payer is recovered from the serialized transaction.

    Returns (None, None) on an absent or unparseable header — the caller should
    treat that as "buyer wallet unknown, no auto-refund possible." Internal-auth
    bypass calls and missing headers fall through this path silently. The network
    is returned even when the payer cannot be determined, because the refund path
    keys its manual-followup decision on it.
    """
    if not x_payment_header:
        return None, None
    try:
        payload = json.loads(base64.b64decode(x_payment_header))
    except Exception:
        return None, None

    network = payload.get("network") or (payload.get("accepted") or {}).get("network")
    inner = payload.get("payload") or {}
    buyer = (inner.get("authorization") or {}).get("from")

    if not buyer and isinstance(inner.get("transaction"), str):
        # Isolated: a decode failure must not cost us the network too, since that
        # alone is enough to queue a manual refund.
        try:
            buyer = _svm_payer_from_tx(inner["transaction"])
        except Exception:
            buyer = None

    return buyer, network


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
    # Network is checked before the wallet, deliberately. A non-Base payment must
    # reach the manual-followup flag even when the payer could not be parsed —
    # checking the wallet first meant every Solana-paid job fell out silently at
    # the guard below, because the payer is not in the EVM-shaped field and we
    # never consulted the network we had already recorded. The refund was then
    # neither sent nor queued for a human. Base behaviour is unchanged.
    if buyer_network and buyer_network != "eip155:8453":
        # Flag for manual followup; v1 only auto-refunds Base USDC (_send_usdc is
        # web3-only, so there is no Solana send path to fall back on).
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET refund_pending = :p",
            ExpressionAttributeValues={":p": "manual"},
        )
        return {"skipped": f"non-Base network {buyer_network}", "refund_pending": "manual",
                "buyer_wallet": buyer_wallet, "job_id": job_id}
    if not buyer_wallet:
        return {"skipped": "no buyer_wallet captured", "job_id": job_id}

    # Screen the payout recipient before sending — we dogfood our own /v1/screen
    # at the one place anchor sends USDC to an outside wallet. A refund does not
    # exempt us from OFAC: paying a wallet that's been sanctioned since it bought
    # is still a violation. Hard-block ONLY (sanctions match / `block` verdict) —
    # a mere `review` must not strand a legitimate refund — and fail OPEN on a
    # screen error, because the recipient is a proven prior payer and a screen
    # hiccup shouldn't hold their money.
    try:
        verdict = screen_svc.screen(buyer_wallet)
        if verdict.get("sanctions_match") or verdict.get("recommendation") == "block":
            log.warning("refund HELD by sanctions screen job=%s to=%s lists=%s",
                        job_id, buyer_wallet, verdict.get("sanctioned_lists"))
            table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET refund_pending = :p",
                ExpressionAttributeValues={":p": "sanctions_hold"},
            )
            return {"skipped": "recipient failed sanctions screen",
                    "refund_pending": "sanctions_hold", "buyer_wallet": buyer_wallet,
                    "job_id": job_id, "sanctioned_lists": verdict.get("sanctioned_lists")}
    except Exception:
        log.exception("refund screen failed (fail-open, proceeding) job=%s", job_id)

    amount_atomic = int(item.get("price_atomic") or REFUND_AMOUNT_ATOMIC)
    tx_hash = _send_usdc(buyer_wallet, amount_atomic)
    log.info("refunded job=%s amount=%d to=%s tx=%s", job_id, amount_atomic, buyer_wallet, tx_hash)

    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET refund_tx = :t, refund_amount_atomic = :a, refunded_at = :ts",
        ExpressionAttributeValues={
            ":t": tx_hash,
            ":a": amount_atomic,
            ":ts": int(time.time()),
        },
    )
    return {"refund_tx": tx_hash, "refund_amount_atomic": amount_atomic}
