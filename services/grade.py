"""Grader: academic letter-grade + red-pen marginalia for any target.

LLM-only. Returns a structured grade for max screenshot value.
"""
from __future__ import annotations

from services._json_extract import extract_json
from services.llm import MODEL_FAST, get_client

_MAX_TARGET_CHARS = 6000

_VALID_GRADES = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "F"}

_SYSTEM = (
    "You are a sharp, observational teacher grading whatever the user submits "
    "(code, a pitch, a tweet, a wallet, an idea, a meme). Be honest, witty, and "
    "specific. Marginalia are short red-pen one-liners (under 18 words each), "
    "3-7 of them. Summary is one paragraph of teacher commentary. Letter grade "
    "must be one of A+, A, A-, B+, B, B-, C+, C, C-, D+, D, F. Reply as JSON only: "
    '{"letter_grade": "...", "marginalia": ["...", "..."], "summary": "..."}'
)


def grade(target: str) -> dict:
    """Grade anything. Returns {letter_grade, marginalia, summary}."""
    clipped = (target or "")[:_MAX_TARGET_CHARS]
    resp = get_client().messages.create(
        model=MODEL_FAST,
        max_tokens=600,
        system=_SYSTEM,
        messages=[{"role": "user", "content": f"Grade this:\n\n{clipped}"}],
    )
    parsed = extract_json(resp.content[0].text)

    letter = str(parsed.get("letter_grade", "")).strip()
    if letter not in _VALID_GRADES:
        raise ValueError(f"grade returned invalid letter: {letter!r}")

    marginalia = parsed.get("marginalia") or []
    if not isinstance(marginalia, list) or not marginalia:
        raise ValueError("grade returned no marginalia")

    return {
        "letter_grade": letter,
        "marginalia": [str(m).strip() for m in marginalia],
        "summary": str(parsed["summary"]).strip(),
    }
