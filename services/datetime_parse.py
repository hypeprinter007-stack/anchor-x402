"""Natural-language datetime parser.

Parses freeform datetime strings ("next Tuesday at 3pm", "yesterday noon UTC",
"2026-05-08T15:30Z", "in 2 hours", "march 15 2026") into a fully structured
normalized form so AI agents don't have to spend LLM tokens parsing it.

Stateless. Pure function of (input, base_time, timezone) -> structured dict.

Layered strategy:
  1. ISO/RFC fast path via dateutil (high confidence).
  2. Natural-language via dateparser (medium confidence).
  3. dateparser with RELATIVE_BASE + extra settings as a low-confidence fallback.
"""
from __future__ import annotations

from datetime import datetime, timezone as _tz
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


Confidence = Literal["high", "medium", "low"]


_WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _resolve_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"unknown IANA timezone: {name!r}") from e


def _resolve_base(base_time: str | None) -> datetime:
    """base_time is ISO 8601; defaults to now in UTC."""
    if base_time is None or base_time == "":
        return datetime.now(_tz.utc)
    try:
        from dateutil import parser as du_parser
        bt = du_parser.isoparse(base_time)
    except Exception as e:
        raise ValueError(f"invalid base_time (must be ISO 8601): {base_time!r}") from e
    if bt.tzinfo is None:
        bt = bt.replace(tzinfo=_tz.utc)
    return bt


def _try_iso(value: str) -> datetime | None:
    """Fast path: clean ISO/RFC formats via dateutil. Returns aware datetime
    or None if it isn't a clean machine-readable form."""
    from dateutil import parser as du_parser
    try:
        # isoparse is strict-ish ISO 8601. Many "natural" strings will fail
        # here and fall through to dateparser, which is what we want.
        dt = du_parser.isoparse(value)
        return dt
    except (ValueError, TypeError):
        return None


def _humanize(delta_seconds: int) -> str:
    """Render a signed delta-seconds as 'in 5 days' / '2 hours ago' / 'now'."""
    if delta_seconds == 0:
        return "now"
    future = delta_seconds > 0
    s = abs(delta_seconds)
    if s < 60:
        unit, n = "second", s
    elif s < 3600:
        unit, n = "minute", s // 60
    elif s < 86400:
        unit, n = "hour", s // 3600
    elif s < 86400 * 30:
        unit, n = "day", s // 86400
    elif s < 86400 * 365:
        unit, n = "month", s // (86400 * 30)
    else:
        unit, n = "year", s // (86400 * 365)
    plural = "" if n == 1 else "s"
    return f"in {n} {unit}{plural}" if future else f"{n} {unit}{plural} ago"


def parse_datetime(
    input_str: str,
    base_time: str | None = None,
    timezone_name: str = "UTC",
) -> dict:
    """Parse a freeform datetime string into a structured dict.

    Raises ValueError on parse failure or invalid base_time / timezone.
    """
    if not input_str or not input_str.strip():
        raise ValueError("`input` must be a non-empty string")

    raw = input_str.strip()
    tz = _resolve_tz(timezone_name)
    base = _resolve_base(base_time)

    parsed: datetime | None = None
    confidence: Confidence = "low"

    # 1. Fast ISO path.
    iso_attempt = _try_iso(raw)
    if iso_attempt is not None:
        parsed = iso_attempt
        confidence = "high"

    # 2. Natural-language via dateparser.
    if parsed is None:
        import dateparser
        settings = {
            "RELATIVE_BASE": base.astimezone(tz).replace(tzinfo=None),
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        }
        attempt = dateparser.parse(raw, settings=settings)
        if attempt is not None:
            parsed = attempt
            confidence = "medium"

    # 3. Last-ditch: looser dateparser settings.
    if parsed is None:
        import dateparser
        settings = {
            "RELATIVE_BASE": base.astimezone(tz).replace(tzinfo=None),
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DAY_OF_MONTH": "first",
            "PREFER_DATES_FROM": "current_period",
        }
        attempt = dateparser.parse(raw, settings=settings, languages=["en"])
        if attempt is not None:
            parsed = attempt
            confidence = "low"

    if parsed is None:
        raise ValueError(f"could not parse datetime from input: {raw!r}")

    # Normalize: ensure tz-aware, then convert to caller's requested tz for
    # the returned `components` while emitting `iso` in that tz too.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    in_tz = parsed.astimezone(tz)
    unix = int(in_tz.timestamp())

    delta = unix - int(base.timestamp())
    components = {
        "year": in_tz.year,
        "month": in_tz.month,
        "day": in_tz.day,
        "hour": in_tz.hour,
        "minute": in_tz.minute,
        "second": in_tz.second,
        "weekday": in_tz.weekday(),
        "day_name": _WEEKDAY_NAMES[in_tz.weekday()],
    }

    return {
        "iso": in_tz.isoformat(),
        "unix": unix,
        "timezone": timezone_name,
        "components": components,
        "relative_seconds": delta,
        "relative_human": _humanize(delta),
        "confidence": confidence,
        "parsed_input": input_str,
    }
