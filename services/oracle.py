"""Yes/no oracle: Claude returns YES|NO|MAYBE + reason, then we dual-chain anchor
the (question, answer, timestamp) tuple as cryptographic receipt.
"""
from __future__ import annotations

import hashlib
import json
import time

from services.anchor import anchor_dual_chain
from services.llm import MODEL_FAST, get_client

_MAX_QUESTION_CHARS = 1000

_SYSTEM = (
    'You are an oracle. Given any yes/no question, you must answer with exactly '
    'one of YES, NO, or MAYBE, followed by a single-sentence explanation. Format '
    'your entire reply as JSON: {"answer": "YES|NO|MAYBE", "explanation": "..."}. '
    'Nothing else.'
)

_VALID_ANSWERS = {"YES", "NO", "MAYBE"}


def oracle(question: str) -> dict:
    """Answer + dual-chain-anchored receipt."""
    clipped = (question or "")[:_MAX_QUESTION_CHARS]
    resp = get_client().messages.create(
        model=MODEL_FAST,
        max_tokens=200,
        system=_SYSTEM,
        messages=[{"role": "user", "content": f"Question: {clipped}"}],
    )
    raw = resp.content[0].text.strip()
    parsed = json.loads(raw)
    answer = str(parsed["answer"]).upper().strip()
    if answer not in _VALID_ANSWERS:
        raise ValueError(f"oracle returned invalid answer: {answer!r}")
    explanation = str(parsed["explanation"]).strip()

    asked_at = int(time.time())
    payload = f"{clipped}|{answer}|{asked_at}"
    merkle_root = hashlib.sha256(payload.encode()).hexdigest()

    result = anchor_dual_chain(merkle_root)
    return {
        "answer": answer,
        "explanation": explanation,
        "merkle_root": merkle_root,
        "base_tx": result["base_tx"],
        "solana_tx": result["solana_tx"],
        "asked_at": asked_at,
    }
