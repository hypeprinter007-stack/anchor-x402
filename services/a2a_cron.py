"""Daily on-chain anchoring of A2A receipt roots.

Receipts are the evidence that makes human-free agent-to-agent exchange
auditable after the fact instead of gated before it. On their own they are only
as good as our signature, so once a day we hash the live receipt set into a
single root and write it to Base and Solana mainnet through the same path
/v1/anchor uses. After that, a receipt's existence at a point in time is
provable against two L1s without trusting us at all — forging it would require
breaking SHA-256 or reorging both chains.

Invoked by an EventBridge schedule on the main function with
`{"a2a_root": true}`, dispatched in app.handler alongside the ledger_job branch.

The root is content-addressed, which is what makes the job idempotent: an
unchanged receipt set hashes to a root already on file and is skipped, so a
retry or a double-fire costs nothing on-chain. Because receipts live longer than
the interval between runs, consecutive roots overlap — a receipt can appear in
more than one root, which is harmless (each independently proves it existed) and
the reverse index cites the first.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from services import a2a
from services import anchor as anchor_svc

log = logging.getLogger("anchor.a2a.root")

# Roots and their reverse index outlive the receipts they cover — the point is to
# still be able to prove an old exchange. 400 days matches the ledger reports'
# retention; the on-chain record is permanent regardless.
ROOT_TTL_S = 400 * 24 * 3600


def anchor_receipt_root_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Hash the live receipt set, anchor it dual-chain, index each member."""
    receipts = a2a.list_receipts()
    digests = sorted({r["digest"] for r in receipts if isinstance(r, dict) and r.get("digest")})
    if not digests:
        log.info("no live a2a receipts; nothing to anchor")
        return {"anchored": 0, "reason": "no_receipts"}

    root_hex = a2a.compute_receipt_root(digests)
    if a2a.get_root(root_hex) is not None:
        log.info("root %s already anchored (%d receipts unchanged)", root_hex[:16], len(digests))
        return {"anchored": 0, "root": root_hex, "count": len(digests), "reason": "unchanged"}

    try:
        chains = anchor_svc.anchor_dual_chain(root_hex)
    except Exception:
        # Leave no root record: the next run recomputes the same root and retries.
        log.exception("dual-chain anchor failed for a2a receipt root %s", root_hex[:16])
        return {"anchored": 0, "root": root_hex, "count": len(digests), "reason": "anchor_failed"}

    record = a2a.sign({
        "type": a2a.TYPE_RECEIPT_ROOT,
        "root": root_hex,
        "count": len(digests),
        "members": digests,
        "anchored_at": int(time.time()),
        "chains": chains,
        "proves": [
            "each member digest existed at anchored_at, provable against Base and Solana mainnet",
            "the member list is complete for this root — recompute sha256 over the sorted members",
        ],
        "does_not_prove": [
            "anything about settlement — see the individual receipts, which say so themselves",
        ],
    })
    a2a.put_root(root_hex, record, ROOT_TTL_S)

    proof = {
        "root": root_hex,
        "anchored_at": record["anchored_at"],
        "chains": chains,
        "verify": "sha256 over the sorted member list of root#<root> equals <root>",
    }
    for digest in digests:
        a2a.put_anchored(digest, proof, ROOT_TTL_S)

    log.info("anchored a2a receipt root %s over %d receipts", root_hex[:16], len(digests))
    print("A2A_ROOT " + a2a.canonical(
        {"root": root_hex, "count": len(digests), "chains": chains}))
    return {"anchored": len(digests), "root": root_hex, "chains": chains}
