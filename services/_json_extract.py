"""Robust JSON extractor for LLM responses.

Bedrock Claude sometimes wraps JSON in markdown code fences or precedes
it with a short preamble, even when the system prompt says "JSON only".
This helper strips fences and finds the first balanced JSON object.
"""
from __future__ import annotations

import json
import re


_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*\n?|\n?```\s*$", re.MULTILINE)


def extract_json(text: str) -> dict:
    """Best-effort: return the first JSON object found in `text`.

    Strategies, in order:
      1. Strip markdown code fences (```json ... ``` or ``` ... ```).
      2. Try json.loads on the stripped text.
      3. If that fails, scan for the first '{' and use json.JSONDecoder.raw_decode
         so we tolerate trailing text after the object.

    Raises ValueError if no JSON object can be extracted.
    """
    if not text:
        raise ValueError("empty LLM response")

    stripped = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Find the first {, try raw_decode from there.
    start = stripped.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in LLM response: {text[:200]!r}")

    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(stripped[start:])
        if not isinstance(obj, dict):
            raise ValueError("LLM returned non-object JSON")
        return obj
    except json.JSONDecodeError as e:
        raise ValueError(f"malformed JSON in LLM response: {e}; got: {text[:200]!r}")
