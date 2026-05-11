"""Aura reader: vibe + color + tier + score for any target.

LLM-only, no on-chain side-effects. Output is structured JSON parsed from the
model's reply.
"""
from __future__ import annotations

import json

from services.llm import MODEL_FAST, get_client

_MAX_TARGET_CHARS = 4000

_SYSTEM = (
    "You are an aura-reader. Given any target (wallet, tweet, project, person, "
    "code, idea, meme), return a structured aura reading. Be punchy, opinionated, "
    "creative. Use evocative color names (\"molten gold\", \"vantablack with sparks\", "
    "\"millennial-pink mist\"). Score is 0-9999 — chaotic specific numbers feel right "
    "(e.g. 4271, 8845, 113). Tier is one of S, A, B, C, D, F. Description is 2-3 "
    "tight sentences with attitude. Reply as JSON only: "
    '{"color": "...", "tier": "S|A|B|C|D|F", "score": <int 0-9999>, "description": "..."}'
)

_VALID_TIERS = {"S", "A", "B", "C", "D", "F"}


def aura(target: str) -> dict:
    """Read the aura of anything. Returns {color, tier, score, description}."""
    clipped = (target or "")[:_MAX_TARGET_CHARS]
    resp = get_client().messages.create(
        model=MODEL_FAST,
        max_tokens=400,
        system=_SYSTEM,
        messages=[{"role": "user", "content": f"Read the aura of: {clipped}"}],
    )
    parsed = json.loads(resp.content[0].text.strip())

    tier = str(parsed.get("tier", "")).upper().strip()
    if tier not in _VALID_TIERS:
        raise ValueError(f"aura returned invalid tier: {tier!r}")

    score = int(parsed["score"])
    score = max(0, min(9999, score))

    return {
        "color": str(parsed["color"]).strip(),
        "tier": tier,
        "score": score,
        "description": str(parsed["description"]).strip(),
    }
