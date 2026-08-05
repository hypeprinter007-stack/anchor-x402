#!/usr/bin/env python3
"""Buyer extraction + refund routing across both payment rails.

Runs offline. The Solana transaction is built the same way x402's SVM `exact`
client builds it (fee payer at signer index 0, buyer at index 1), so the
extraction is checked against a buyer whose pubkey we already know rather than
against a hand-copied fixture.

Covers the two defects found when Solana settlements showed payer=None:
  * the payer is not in the EVM-shaped `payload.authorization.from` on Solana,
    so it has to come out of the serialized transaction;
  * the refund path checked the wallet before the network, so a Solana-paid
    FAILED job was skipped silently instead of being queued for manual followup.

  .venv/bin/python scripts/test_refund_payer.py
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import refund as refund_svc

SOLANA = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
BASE = "eip155:8453"

failures: list[str] = []
_n = 0


def ok(label: str, cond: bool, detail: str = "") -> None:
    global _n
    _n += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
        failures.append(label)


def build_svm_payload(network: str = SOLANA):
    """Mirror x402/mechanisms/svm/exact/client.py's transaction construction."""
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.instruction import AccountMeta, Instruction
    from solders.message import MessageV0
    from solders.hash import Hash
    from solders.signature import Signature
    from solders.transaction import VersionedTransaction

    fee_payer = Keypair()
    buyer = Keypair()
    token_program = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    ix = Instruction(
        program_id=token_program,
        accounts=[AccountMeta(buyer.pubkey(), is_signer=True, is_writable=False)],
        data=b"\x03",
    )
    msg = MessageV0.try_compile(
        payer=fee_payer.pubkey(), instructions=[ix],
        address_lookup_table_accounts=[], recent_blockhash=Hash.default(),
    )
    sig = buyer.sign_message(bytes([0x80]) + bytes(msg))
    tx = VersionedTransaction.populate(msg, [Signature.default(), sig])
    tx_b64 = base64.b64encode(bytes(tx)).decode()
    header = base64.b64encode(json.dumps({
        "x402Version": 2,
        "accepted": {"network": network},
        "payload": {"transaction": tx_b64},
    }).encode()).decode()
    return header, str(buyer.pubkey()), str(fee_payer.pubkey())


def build_evm_payload(sender="0x000000000000000000000000000000000000bEEF"):
    return base64.b64encode(json.dumps({
        "x402Version": 2,
        "accepted": {"network": BASE},
        "payload": {"authorization": {"from": sender}},
    }).encode()).decode()


class FakeTable:
    """Minimal DDB stand-in: records the update the refund path performs."""

    def __init__(self, item):
        self.item = item
        self.updates = []

    def get_item(self, Key):
        return {"Item": self.item} if self.item else {}

    def update_item(self, **kw):
        self.updates.append(kw)
        return {}


def try_refund(job_id: str) -> dict:
    """A raising refund path is a failure, not a reason to abort the run — an
    aborted run prints no FAILs and reads like a pass. Without the network guard
    this genuinely raises: a Solana job with a known wallet falls through to the
    Base USDC send path and tries to pay a Solana address over web3.
    """
    try:
        return refund_svc.refund_failed_job(job_id)
    except Exception as e:
        return {"raised": f"{type(e).__name__}: {e}"[:120]}


def main() -> None:
    print("Solana buyer extraction")
    header, buyer, fee_payer = build_svm_payload()
    got, net = refund_svc.parse_buyer_from_x_payment(header)
    ok("the buyer pubkey is recovered from the serialized transaction",
       got == buyer, f"{got} != {buyer}")
    ok("it is NOT the facilitator's fee payer (signer index 0)", got != fee_payer)
    ok("the network is returned alongside", net == SOLANA, str(net))

    print("\nEVM still works (regression)")
    got, net = refund_svc.parse_buyer_from_x_payment(build_evm_payload())
    ok("EVM payer read from payload.authorization.from",
       got == "0x000000000000000000000000000000000000bEEF", str(got))
    ok("EVM network read from accepted.network", net == BASE, str(net))

    print("\ndegrading safely")
    ok("absent header -> (None, None)",
       refund_svc.parse_buyer_from_x_payment(None) == (None, None))
    ok("garbage header -> (None, None)",
       refund_svc.parse_buyer_from_x_payment("!!!not base64!!!") == (None, None))
    # The network alone is enough to queue a manual refund, so a transaction we
    # cannot decode must not cost us the network too.
    bad = base64.b64encode(json.dumps({
        "accepted": {"network": SOLANA}, "payload": {"transaction": "AAAA"},
    }).encode()).decode()
    got, net = refund_svc.parse_buyer_from_x_payment(bad)
    ok("undecodable transaction still yields the network", got is None and net == SOLANA,
       f"{got} {net}")
    # A single-signer transaction is not the shape the client produces; refuse to
    # guess rather than hand a refund a wrong address.
    from solders.keypair import Keypair
    from solders.message import MessageV0
    from solders.hash import Hash
    from solders.transaction import VersionedTransaction
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey
    lone, bystander = Keypair(), Keypair()
    # Two account keys but only ONE required signature, so account_keys[1] exists
    # and is a non-signer. Without the header check this returns that bystander as
    # the "buyer" — an address a refund could then be sent to.
    ix = Instruction(
        program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
        accounts=[AccountMeta(bystander.pubkey(), is_signer=False, is_writable=False)],
        data=b"\x03",
    )
    msg = MessageV0.try_compile(payer=lone.pubkey(), instructions=[ix],
                                address_lookup_table_accounts=[], recent_blockhash=Hash.default())
    tx = VersionedTransaction.populate(msg, [lone.sign_message(bytes([0x80]) + bytes(msg))])
    one_signer_b64 = base64.b64encode(bytes(tx)).decode()
    ok("a one-signer transaction returns None rather than a guess",
       refund_svc._svm_payer_from_tx(one_signer_b64) is None,
       str(refund_svc._svm_payer_from_tx(one_signer_b64)))
    ok("...and specifically does not hand back the non-signer at index 1",
       refund_svc._svm_payer_from_tx(one_signer_b64) != str(bystander.pubkey()))

    print("\nrefund routing on a FAILED job")
    # Solana, payer unknown (the pre-fix production state): must be queued for a
    # human, not dropped.
    t = FakeTable({"job_id": "j1", "status": "FAILED", "buyer_network": SOLANA})
    refund_svc._ddb_table = lambda: t
    res = try_refund("j1")
    ok("Solana job with no wallet is flagged manual, not skipped",
       res.get("refund_pending") == "manual", str(res))
    ok("and the flag is actually written to the row",
       any(u.get("ExpressionAttributeValues", {}).get(":p") == "manual" for u in t.updates),
       str(t.updates))

    # Solana with the wallet now recoverable: still manual (no Solana send path),
    # but the followup knows the destination.
    t = FakeTable({"job_id": "j2", "status": "FAILED", "buyer_network": SOLANA,
                   "buyer_wallet": buyer})
    refund_svc._ddb_table = lambda: t
    res = try_refund("j2")
    ok("Solana job with a wallet is still manual (auto-send is Base-only)",
       res.get("refund_pending") == "manual", str(res))
    ok("the manual result carries the wallet to refund",
       res.get("buyer_wallet") == buyer, str(res))

    # Genuinely unknown: no wallet, no network. Nothing to act on.
    t = FakeTable({"job_id": "j3", "status": "FAILED"})
    refund_svc._ddb_table = lambda: t
    ok("job with neither wallet nor network is still skipped",
       try_refund("j3").get("skipped") == "no buyer_wallet captured")

    # Base path untouched.
    t = FakeTable({"job_id": "j4", "status": "FAILED", "buyer_network": BASE})
    refund_svc._ddb_table = lambda: t
    ok("Base job with no wallet still skips (unchanged behaviour)",
       try_refund("j4").get("skipped") == "no buyer_wallet captured")
    ok("...and is NOT flagged manual", not t.updates, str(t.updates))

    t = FakeTable({"job_id": "j5", "status": "SUCCEEDED", "buyer_network": SOLANA})
    refund_svc._ddb_table = lambda: t
    ok("a non-FAILED job is never refunded or flagged",
       try_refund("j5").get("skipped", "").startswith("status=") and not t.updates)

    print("\nscreen-before-refund guard (dogfood /v1/screen at the one outbound send)")
    BUYER = "0x000000000000000000000000000000000000bEEF"
    sent: list = []
    refund_svc._send_usdc = lambda to, amt: (sent.append((to, amt)) or "0xrefundtx")

    def base_job(jid):
        t = FakeTable({"job_id": jid, "status": "FAILED", "buyer_network": BASE,
                       "buyer_wallet": BUYER, "price_atomic": 1_770_000})
        refund_svc._ddb_table = lambda: t
        return t

    # allow -> refund sends
    sent.clear(); t = base_job("s1")
    refund_svc.screen_svc.screen = lambda w: {"recommendation": "allow", "sanctions_match": False}
    res = try_refund("s1")
    ok("clean recipient -> refund sends", res.get("refund_tx") == "0xrefundtx" and len(sent) == 1, str(res))

    # sanctions/block -> HELD, USDC NOT sent (a refund does not exempt us from OFAC)
    sent.clear(); t = base_job("s2")
    refund_svc.screen_svc.screen = lambda w: {"recommendation": "block", "sanctions_match": True,
                                              "sanctioned_lists": ["OFAC SDN"]}
    res = try_refund("s2")
    ok("sanctioned recipient -> refund HELD, USDC NOT sent",
       res.get("refund_pending") == "sanctions_hold" and not sent, str(res))
    ok("...and the hold is written to the row",
       any(u.get("ExpressionAttributeValues", {}).get(":p") == "sanctions_hold" for u in t.updates),
       str(t.updates))

    # review -> must NOT strand a legitimate refund; only a hard block does
    sent.clear(); t = base_job("s3")
    refund_svc.screen_svc.screen = lambda w: {"recommendation": "review", "sanctions_match": False}
    res = try_refund("s3")
    ok("a 'review' verdict does NOT strand the refund", res.get("refund_tx") == "0xrefundtx" and len(sent) == 1, str(res))

    # screen error -> fail-open (recipient is a proven prior payer; a hiccup mustn't hold funds)
    sent.clear(); t = base_job("s4")
    def _boom(w):
        raise RuntimeError("screen down")
    refund_svc.screen_svc.screen = _boom
    res = try_refund("s4")
    ok("screen error -> fail-open, refund still sends", res.get("refund_tx") == "0xrefundtx" and len(sent) == 1, str(res))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print(f"all {_n} assertions passed — payer extraction + refund routing OK")


if __name__ == "__main__":
    main()
