"""TL;DR: summarize a URL or pasted text into 3-5 bullets via Claude.

Fetches at most 500KB on the URL path. Strips HTML with BeautifulSoup.
"""
from __future__ import annotations

from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from services._json_extract import extract_json
from services.llm import MODEL_FAST, get_client

_MAX_FETCH_BYTES = 500_000
_MAX_LLM_CHARS = 15_000
_MIN_TEXT_CHARS = 200
_FETCH_TIMEOUT = 10

_SYSTEM = (
    "Summarize the user's text into 3 to 5 concise bullets. Each bullet a "
    "single sentence, at most 25 words. No preamble. Respond as JSON: "
    '{"bullets": ["...", "..."]}.'
)


def _fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must be http or https")

    r = requests.get(
        url,
        timeout=_FETCH_TIMEOUT,
        allow_redirects=True,
        stream=True,
        headers={"User-Agent": "anchor-x402-tldr/1.0"},
    )
    r.raise_for_status()

    chunks: list[bytes] = []
    total = 0
    for chunk in r.iter_content(chunk_size=16_384):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= _MAX_FETCH_BYTES:
            break
    body = b"".join(chunks)[:_MAX_FETCH_BYTES]

    text = BeautifulSoup(body, "html.parser").get_text(separator=" ", strip=True)
    if len(text) < _MIN_TEXT_CHARS:
        raise ValueError("fetched content has too little text to summarize")
    return text


def tldr(text: str | None = None, url: str | None = None) -> dict:
    """Summarize either pasted text OR a fetched URL. Exactly one required."""
    if (text is None) == (url is None):
        raise ValueError("provide exactly one of text or url")

    raw = _fetch_url(url) if url else (text or "")
    source_chars = len(raw)
    body = raw[:_MAX_LLM_CHARS]

    resp = get_client().messages.create(
        model=MODEL_FAST,
        max_tokens=400,
        system=_SYSTEM,
        messages=[{"role": "user", "content": body}],
    )
    parsed = extract_json(resp.content[0].text)
    bullets = parsed.get("bullets") or []
    if not bullets or not isinstance(bullets, list):
        raise ValueError("tldr returned no bullets")

    return {"summary_bullets": [str(b) for b in bullets], "source_chars": source_chars}
