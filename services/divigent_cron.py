"""EventBridge → Lambda entry points for the Divigent serverless integration.

Two scheduled handlers:

  sweep_handler           — every N minutes; runs services.divigent.sweep_idle().
  oracle_keeper_handler   — every hour; pings oracle.recordObservation().

Both are intentionally thin. The bulk of the logic lives in services/divigent.py
so it can also be exercised from the FastAPI dashboard route.
"""
from __future__ import annotations

import logging

from services import divigent

log = logging.getLogger("divigent.cron")


def sweep_handler(event, context):
    """EventBridge target for the sweep schedule."""
    try:
        result = divigent.sweep_idle()
    except Exception as e:
        log.exception("divigent sweep failed")
        return {"swept": False, "reason": "exception", "error": f"{type(e).__name__}: {e}"}
    log.info("divigent sweep result: %s", result)
    return result


def oracle_keeper_handler(event, context):
    """EventBridge target for the oracle freshness keeper."""
    status = divigent.get_oracle_status()
    if status.get("fresh"):
        log.info("oracle fresh; skipping recordObservation (last=%s)", status.get("last_observation_at"))
        return {"recorded": False, "reason": "oracle_already_fresh", "status": status}
    try:
        result = divigent.record_oracle_observation()
    except Exception as e:
        log.exception("divigent oracle keeper failed")
        return {"recorded": False, "reason": "exception", "error": f"{type(e).__name__}: {e}"}
    log.info("divigent oracle keeper result: %s", result)
    return result
