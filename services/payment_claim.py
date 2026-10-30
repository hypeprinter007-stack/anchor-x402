"""One route run per payment authorization.

The x402 middleware verifies a payment, runs the route, then settles. Verify
does not consume the authorization, so the same signed payment sent N times at
once passes verify N times and runs the route N times; only one settles. On
/v1/investigate that is N agent runs for one $1.77 payment (reproduced
2026-10-12 with three concurrent copies of one /v1/roll payment: three runs,
one settlement).

claim() records the authorization before the route runs, with a conditional
write in the A2A table, so a second copy is refused without running anything.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time

from services import a2a

# Minimum claim lifetime: covers the longest payment window anchor quotes
# (maxTimeoutSeconds 300) with margin. EVM claims last longer when the signed
# authorization does; see claim_expiry.
CLAIM_TTL_S = 900
CLOCK_SKEW_S = 60
# Longest authorization we will guard. The signer can pick any validBefore, up
# to 2**256; a claim that lapsed before the authorization did would reopen its
# replay window, so longer authorizations are refused (beyond_retention) rather
# than given a shorter claim. A year is far past any real payment, and keeps the
# expiry inside DynamoDB's 38-digit numbers.
MAX_CLAIM_S = 366 * 24 * 3600

log = logging.getLogger("payment_claim")


def claim_key(header: str) -> str:
    """Stable id for the authorization a payment header carries.

    EVM (Base, Arc, Polygon): the EIP-3009 nonce, which the signer chose and
    the token contract consumes exactly once. Solana: the message the payer
    signed. Not the whole transaction: the fee payer's signature slot is
    outside the payer's signature, so a copy with different bytes there is
    the same payment. Anything else: the header bytes.
    """
    try:
        payload = json.loads(base64.b64decode(header))
        network = payload.get("network") or (payload.get("accepted") or {}).get("network") or ""
        inner = payload.get("payload") or {}
        auth = inner.get("authorization") or {}
        if auth.get("nonce") and auth.get("from"):
            return f"pay:{network}:{str(auth['from']).lower()}:{str(auth['nonce']).lower()}"
        if isinstance(inner.get("transaction"), str):
            return f"pay:{network}:{_svm_message_digest(inner['transaction'])}"
    except Exception:
        pass
    return "pay:raw:" + hashlib.sha256(header.encode()).hexdigest()


def _svm_message_digest(tx_b64: str) -> str:
    try:
        from solders.message import to_bytes_versioned
        from solders.transaction import VersionedTransaction

        message = VersionedTransaction.from_bytes(base64.b64decode(tx_b64)).message
        return "msg:" + hashlib.sha256(to_bytes_versioned(message)).hexdigest()
    except Exception:
        # Unparseable: fall back to the raw bytes, which still catches exact copies.
        return hashlib.sha256(tx_b64.encode()).hexdigest()


def _valid_before(header: str) -> int | None:
    try:
        payload = json.loads(base64.b64decode(header))
        return int(((payload.get("payload") or {}).get("authorization") or {})["validBefore"])
    except Exception:
        return None


def beyond_retention(header: str, now: int) -> bool:
    """True if the authorization stays valid longer than a claim can be kept.

    Refuse these before claiming: shortening the claim would let a copy run the
    route again once it lapsed, while the authorization could still settle.
    (Raised in review of affaan-m/ECC#3658.)
    """
    valid_before = _valid_before(header)
    return valid_before is not None and valid_before + CLOCK_SKEW_S > now + MAX_CLAIM_S


def claim_expiry(header: str, now: int) -> int:
    """When a claim may be dropped: not before the authorization itself expires.

    The signer picks EIP-3009 validBefore, and verification does not cap it at
    maxTimeoutSeconds. With a fixed TTL, a copy of a still-valid authorization
    whose settlement failed without consuming it could run the route again once
    the claim lapsed. (Found in review of x402-foundation/x402#3865.)
    """
    floor = now + CLAIM_TTL_S
    valid_before = _valid_before(header)
    if valid_before is None:
        return floor
    # Callers refuse beyond_retention() first, so this is never past MAX_CLAIM_S.
    return max(floor, valid_before + CLOCK_SKEW_S)


def claim(header: str) -> bool:
    """True if this authorization has not been presented before.

    Fails open: if the store is unreachable the facilitator still prevents a
    second settlement, so a store outage must not block paying buyers.
    """
    try:
        now = int(time.time())
        return a2a._put_once(claim_key(header), claim_expiry(header, now))
    except Exception:
        log.exception("payment claim store unavailable; allowing")
        return True
