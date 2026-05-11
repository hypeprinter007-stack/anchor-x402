"""Universal roaster: Claude-powered roast of any target (wallet, tweet, idea, code).

LLM call only — no on-chain side-effects, no external HTTP beyond Anthropic.
"""
from __future__ import annotations

from services.llm import MODEL_FAST, get_client

_MAX_TARGET_CHARS = 4000

_SYSTEM = (
    "You are a witty roastmaster. Given a target (anything: a person, project, "
    "wallet, tweet, idea, code snippet), roast it in 3-5 short paragraphs. Be "
    "clever and observational, not lazy or mean-spirited. End with one zinger. "
    "First, write a 1-sentence neutral summary of what the target is (label it "
    "'TARGET_SUMMARY:') on its own line, then a blank line, then the roast."
)


def roast(target: str) -> dict:
    """Roast anything. Returns {'roast': str, 'target_summary': str}."""
    clipped = (target or "")[:_MAX_TARGET_CHARS]
    resp = get_client().messages.create(
        model=MODEL_FAST,
        max_tokens=500,
        system=_SYSTEM,
        messages=[{"role": "user", "content": f"Roast this: {clipped}"}],
    )
    text = resp.content[0].text.strip()

    summary = ""
    body = text
    if text.upper().startswith("TARGET_SUMMARY:"):
        head, _, rest = text.partition("\n\n")
        summary = head.split(":", 1)[1].strip()
        body = rest.strip()

    return {"roast": body, "target_summary": summary}
