"""Live delivery stats for /v1/investigate, embedded into the 402 challenge
so buyers see "21/21 delivered in 30d" before committing $1.77. Scans DDB
once per hour (process-memory cache) — the table is small enough that the
scan is effectively free."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

log = logging.getLogger("anchor.delivery_stats")

_CACHE: dict[str, Any] = {"stats": None, "expires_at": 0}
_CACHE_TTL_S = 3600  # 1 hour
_WINDOW_S = 30 * 24 * 3600  # 30 days

_DDB = None


def _ddb_table():
    global _DDB
    if _DDB is None:
        _DDB = boto3.resource("dynamodb").Table(
            os.environ.get("INVESTIGATOR_JOBS_TABLE", "risk-investigator-jobs")
        )
    return _DDB


def get_30d_stats() -> dict[str, int]:
    """Returns {delivered, failed, total} over the last 30 days."""
    now = int(time.time())
    if _CACHE["stats"] and now < _CACHE["expires_at"]:
        return _CACHE["stats"]

    cutoff = now - _WINDOW_S
    try:
        resp = _ddb_table().scan(
            FilterExpression=Attr("created_at").gte(cutoff),
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        items = resp.get("Items", [])
        delivered = sum(1 for i in items if i.get("status") == "DELIVERED")
        failed = sum(1 for i in items if i.get("status") == "FAILED")
        stats = {"delivered": delivered, "failed": failed, "total": len(items)}
    except Exception as e:  # noqa: BLE001
        log.warning("delivery_stats scan failed: %s — returning empty", type(e).__name__)
        stats = {"delivered": 0, "failed": 0, "total": 0}

    _CACHE["stats"] = stats
    _CACHE["expires_at"] = now + _CACHE_TTL_S
    return stats
