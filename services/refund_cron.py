"""Daily backstop refund cron — scans DDB for FAILED jobs without a
refund_tx and refunds them. The fast path is investigate_status refunding
inline when the buyer polls; this catches buyers who never poll back.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("anchor.refund_cron")

_DDB = None


def _ddb_table():
    global _DDB
    if _DDB is None:
        _DDB = boto3.resource("dynamodb").Table(
            os.environ.get("INVESTIGATOR_JOBS_TABLE", "risk-investigator-jobs")
        )
    return _DDB


def handler(event: Any, context: Any) -> dict[str, Any]:
    from services import refund

    resp = _ddb_table().scan(
        FilterExpression=Attr("status").eq("FAILED") & Attr("refund_tx").not_exists(),
        ProjectionExpression="job_id",
    )
    items = resp.get("Items", [])
    log.info("refund_cron found %d FAILED jobs without refund_tx", len(items))

    results = []
    for item in items:
        job_id = item["job_id"]
        try:
            result = refund.refund_failed_job(job_id)
            log.info("refund_cron job=%s result=%s", job_id, result)
            results.append({"job_id": job_id, **result})
        except Exception as e:  # noqa: BLE001
            log.exception("refund_cron failed for job=%s", job_id)
            results.append({"job_id": job_id, "error": type(e).__name__})

    return {"scanned": len(items), "results": results}
