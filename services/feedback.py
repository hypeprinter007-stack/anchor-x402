"""Free structured feedback from agents: POST /v1/feedback.

An agent that hits a gap (a 4xx it can't explain, a missing field, a wrong
example) files a report here instead of silently giving up. Reports are logged
as one FEEDBACK line each, which a metric filter turns into an email alarm.

The text is untrusted input written by whoever calls the route. It is stored and
read by a person, never executed, rendered or acted on automatically.

Rate-limited in the shared A2A state table, globally and per source IP. The IP
comes from API Gateway's requestContext, not x-forwarded-for, whose first entry
the caller controls.
"""
from __future__ import annotations

import json
import secrets
import time

from services import a2a

LIMIT_PER_IP = 10
LIMIT_GLOBAL = 200
WINDOW_S = 3600

_tripped: dict[str, int] = {}


class RateLimited(Exception):
    pass


def source_ip(scope: dict) -> str:
    event = scope.get("aws.event") or {}
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp")
    return (ip or (scope.get("client") or ("unknown",))[0] or "unknown")[:45]


def rate_check(ip: str) -> None:
    now = int(time.time())
    bucket = now // WINDOW_S
    window_end, expiry = (bucket + 1) * WINDOW_S, (bucket + 2) * WINDOW_S
    for key in [k for k, until in _tripped.items() if until <= now]:
        _tripped.pop(key, None)
    for key, limit in ((f"fb#all#{bucket}", LIMIT_GLOBAL), (f"fb#{ip}#{bucket}", LIMIT_PER_IP)):
        # Once a counter trips, refuse without another write for the rest of the
        # window, so a flood costs one write per container, not one per request.
        if _tripped.get(key, 0) > now:
            raise RateLimited()
        if a2a._bump(key, expiry) > limit:
            _tripped[key] = window_end
            raise RateLimited()


def record(report: dict, ip: str) -> str:
    feedback_id = f"fb_{secrets.token_hex(8)}"
    # json.dumps escapes quotes and control characters, so a report cannot
    # forge a second log line or break the metric filter's parsing.
    print("FEEDBACK " + json.dumps(
        {"id": feedback_id, "ts": int(time.time()), "ip": ip, **report},
        ensure_ascii=True, separators=(",", ":"),
    ))
    return feedback_id
