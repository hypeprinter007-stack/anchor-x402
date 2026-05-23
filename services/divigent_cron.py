"""EventBridge → Lambda entry points for the Divigent serverless integration.

Two scheduled handlers:

  sweep_handler           — every N minutes; runs services.divigent.assess_and_act().
                            Asks the intelligence Lambda for a decision, executes
                            it via operator-delegated signing.
  oracle_keeper_handler   — every hour; pings oracle.recordObservation().

Both are intentionally thin. The bulk of the logic lives in services/divigent.py
so it can also be exercised from the FastAPI dashboard route.
"""
from __future__ import annotations

import logging

# Lambda's Python runtime installs a CloudWatch handler on the root logger,
# but doesn't set a level. Without this line, INFO/DEBUG messages from this
# module and services/divigent.py never reach CloudWatch.
logging.getLogger().setLevel(logging.INFO)

from services import divigent

log = logging.getLogger("divigent.cron")


def sweep_handler(event, context):
    """EventBridge target for the sweep schedule.

    Renamed semantically — this is no longer a "sweep" in the static sense.
    It's an assess-and-act cycle: ask Divigent what to do, then do it.
    """
    try:
        result = divigent.assess_and_act()
    except Exception as e:
        # log.exception() does NOT include local variable values in tracebacks
        # by default — safe. But the returned error string is scrubbed of any
        # 0x-prefixed 64-hex shapes (private-key length) via _safe_err.
        log.exception("divigent assess_and_act failed")
        return {"acted": False, "reason": "exception", "error": divigent._safe_err(e)}
    log.info("divigent assess_and_act result: %s", result)
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
        return {"recorded": False, "reason": "exception", "error": divigent._safe_err(e)}
    log.info("divigent oracle keeper result: %s", result)
    return result
