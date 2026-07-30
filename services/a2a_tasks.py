"""A2A 0.3.0 core JSON-RPC methods: message/send, tasks/get, tasks/cancel.

Built because a real client was already knocking. Four `message/send` calls
arrived from one address over eighteen hours and were all refused, because the
agent card's top-level `url` points at /v1/a2a — which in A2A *is* the JSON-RPC
endpoint — while that endpoint only spoke methods we had invented. A conformant
card in front of a non-conformant endpoint is worse than neither: it earns
engagement and then rejects it.

Payment maps onto the spec rather than needing an extension. §4.5 defines
`auth-required` for "I need credentials before proceeding", with the details
carried in a DataPart. So: message/send names a skill, we return a Task in
auth-required whose status message carries the payable URL, price and rails, and
the client pays that route with x402. We deliberately do NOT then report
`completed` — the paid route returns the result in its own response body, and we
have no way to observe that settlement, so claiming completion would be a state
we cannot back.

Version boundary: 0.3.0 and 1.0.1 differ in the state vocabulary (lowercase
"completed" vs TASK_STATE_COMPLETED, plus 1.0 dropping `unknown` and adding
TASK_STATE_UNSPECIFIED). That difference lives in STATES below and nowhere else,
so advertising a 1.0 interface later is a table plus a card entry rather than a
rewrite. Method names are identical across both.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from services import a2a

# JSON-RPC methods defined by the spec, as opposed to our own peer/* extension.
# Auth differs: a conformant client will not produce our signed envelope, so
# these are unsigned and authorized by x402 payment on the metered route (which
# is what securitySchemes in the card declares).
METHODS = ("message/send", "tasks/get", "tasks/cancel", "agent/getAuthenticatedExtendedCard")

# A2A-specific error codes, verbatim from the v0.3.0 JSON Schema.
ERR_TASK_NOT_FOUND = -32001
ERR_TASK_NOT_CANCELABLE = -32002
ERR_PUSH_NOT_SUPPORTED = -32003
ERR_UNSUPPORTED_OPERATION = -32004
ERR_CONTENT_TYPE_NOT_SUPPORTED = -32005
ERR_EXTENDED_CARD_NOT_CONFIGURED = -32007

# The only place the two protocol versions diverge.
STATES = {
    "0.3.0": {
        "submitted": "submitted", "working": "working", "input_required": "input-required",
        "completed": "completed", "canceled": "canceled", "failed": "failed",
        "rejected": "rejected", "auth_required": "auth-required", "unknown": "unknown",
    },
    "1.0": {
        "submitted": "TASK_STATE_SUBMITTED", "working": "TASK_STATE_WORKING",
        "input_required": "TASK_STATE_INPUT_REQUIRED", "completed": "TASK_STATE_COMPLETED",
        "canceled": "TASK_STATE_CANCELED", "failed": "TASK_STATE_FAILED",
        "rejected": "TASK_STATE_REJECTED", "auth_required": "TASK_STATE_AUTH_REQUIRED",
        "unknown": "TASK_STATE_UNSPECIFIED",
    },
}
PROTOCOL_VERSION = "0.3.0"

TASK_TTL_S = 86400
TERMINAL = ("completed", "canceled", "failed", "rejected")


def state(name: str, version: str = PROTOCOL_VERSION) -> str:
    return STATES[version][name]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _text(parts: list[dict[str, Any]]) -> str:
    return " ".join(p.get("text", "") for p in parts if p.get("kind") == "text").strip()


def _data(parts: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for p in parts:
        if p.get("kind") == "data" and isinstance(p.get("data"), dict):
            merged.update(p["data"])
    return merged


def validate_message(raw: Any) -> dict[str, Any]:
    """Check the required Message fields. Required per schema: kind, messageId,
    parts, role."""
    if not isinstance(raw, dict):
        raise a2a.A2AError(a2a.ERR_PARAMS, "params.message must be an object")
    for field in ("kind", "messageId", "parts", "role"):
        if field not in raw:
            raise a2a.A2AError(a2a.ERR_PARAMS, f"message.{field} is required")
    if raw["kind"] != "message":
        raise a2a.A2AError(a2a.ERR_PARAMS, "message.kind must be 'message'")
    if raw["role"] not in ("user", "agent"):
        raise a2a.A2AError(a2a.ERR_PARAMS, "message.role must be 'user' or 'agent'")
    parts = raw["parts"]
    if not isinstance(parts, list) or not parts:
        raise a2a.A2AError(a2a.ERR_PARAMS, "message.parts must be a non-empty array")
    for i, part in enumerate(parts):
        if not isinstance(part, dict) or part.get("kind") not in ("text", "data", "file"):
            raise a2a.A2AError(
                a2a.ERR_PARAMS, f"message.parts[{i}].kind must be 'text', 'data' or 'file'")
        if part["kind"] == "file":
            # We accept no file input on any skill, so say so with the code the
            # spec reserves for it rather than a generic parameter error.
            raise a2a.A2AError(
                ERR_CONTENT_TYPE_NOT_SUPPORTED, "file parts are not supported by any skill")
    return raw


def resolve_skill(message: dict[str, Any], skills: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the requested skill. A DataPart naming it wins; otherwise match a
    skill id or name appearing in the text. Returns None when ambiguous, which
    the caller turns into an input-required Task listing the options."""
    parts = message["parts"]
    wanted = _data(parts).get("skill") or _data(parts).get("skillId")
    if wanted:
        for skill in skills:
            if skill["id"] == wanted or skill["name"] == wanted:
                return skill
        raise a2a.A2AError(
            a2a.ERR_PARAMS,
            f"no skill '{a2a.safe_echo(wanted)}' — see the agent card's skills array",
        )
    text = _text(parts).lower()
    if not text:
        return None
    for skill in skills:
        if skill["id"].lower() in text or f" {skill['name'].lower()} " in f" {text} ":
            return skill
    return None


def _task(task_id: str, context_id: str, state_name: str, *,
          status_parts: list[dict[str, Any]], history: list[dict[str, Any]] | None = None,
          artifacts: list[dict[str, Any]] | None = None,
          metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a schema-valid Task. Required: contextId, id, kind, status."""
    task: dict[str, Any] = {
        "id": task_id,
        "contextId": context_id,
        "kind": "task",
        "status": {
            "state": state(state_name),
            "timestamp": _now_iso(),
            "message": {
                "kind": "message",
                "messageId": "msg-" + uuid.uuid4().hex,
                "role": "agent",
                "parts": status_parts,
                "taskId": task_id,
                "contextId": context_id,
            },
        },
    }
    if history:
        task["history"] = history
    if artifacts:
        task["artifacts"] = artifacts
    if metadata:
        task["metadata"] = metadata
    return task


def payment_task(message: dict[str, Any], skill: dict[str, Any], price_usd: float,
                 x402: dict[str, Any]) -> dict[str, Any]:
    """A Task in auth-required carrying everything needed to pay.

    The DataPart is the machine-readable half; the TextPart exists because some
    clients surface only text to their operator.
    """
    task_id = "task-" + uuid.uuid4().hex
    context_id = message.get("contextId") or "ctx-" + uuid.uuid4().hex
    parts = [
        {"kind": "text", "text": (
            f"{skill['name']} costs ${price_usd} per call. Send a request to {skill['url']} "
            f"with an x402 payment; the response to that request is the result. No account "
            f"or API key exists — request it without payment first to receive a 402 challenge "
            f"carrying the exact amount and accepted rails."
        )},
        {"kind": "data", "data": {
            "authScheme": "x402",
            "skillId": skill["id"],
            "resource": skill["url"],
            "httpMethod": skill.get("method", "POST"),
            "priceUsd": price_usd,
            "priceAuthority": (
                "The 402 challenge on `resource` is authoritative. priceUsd is indicative and a "
                "cached agent card may be stale — do not pay from it."
            ),
            "networks": x402.get("networks", []),
            "asset": x402.get("asset"),
            "facilitator": x402.get("facilitator"),
            "resultDelivery": (
                "inline-in-402-retry: the paid response body carries the result. This task stays "
                "in auth-required because settlement happens off this endpoint and we do not "
                "observe it, so completion is not something we can attest to."
            ),
        }},
    ]
    task = _task(task_id, context_id, "auth_required",
                 status_parts=parts, history=[message],
                 metadata={"skillId": skill["id"], "priceUsd": price_usd})
    a2a.put_task(task_id, task, TASK_TTL_S)
    return task


def clarify_task(message: dict[str, Any], skills: list[dict[str, Any]]) -> dict[str, Any]:
    """No skill could be resolved, so ask — input-required is exactly this case."""
    task_id = "task-" + uuid.uuid4().hex
    context_id = message.get("contextId") or "ctx-" + uuid.uuid4().hex
    catalogue = [{"id": s["id"], "name": s["name"], "priceUsd": s.get("price_usd")} for s in skills]
    parts = [
        {"kind": "text", "text": (
            "Name the skill you want. Send a DataPart of {\"skill\": \"<id>\"}, or mention the "
            "skill id in text. The available ids are listed in the accompanying data part and in "
            "the agent card's skills array."
        )},
        {"kind": "data", "data": {"availableSkills": catalogue}},
    ]
    task = _task(task_id, context_id, "input_required",
                 status_parts=parts, history=[message])
    a2a.put_task(task_id, task, TASK_TTL_S)
    return task


def get_task(params: Any) -> dict[str, Any]:
    """tasks/get — TaskQueryParams: id required, historyLength optional."""
    if not isinstance(params, dict) or not params.get("id"):
        raise a2a.A2AError(a2a.ERR_PARAMS, "params.id is required")
    task = a2a.get_task(str(params["id"])[:128])
    if task is None:
        raise a2a.A2AError(ERR_TASK_NOT_FOUND, "Task not found")
    length = params.get("historyLength")
    if isinstance(length, int) and "history" in task:
        # Spec: historyLength caps the returned history. 0 means omit it.
        task = {**task, "history": task["history"][-length:]} if length > 0 else \
               {k: v for k, v in task.items() if k != "history"}
    return task


def cancel_task(params: Any) -> dict[str, Any]:
    """tasks/cancel — TaskIdParams: id required."""
    if not isinstance(params, dict) or not params.get("id"):
        raise a2a.A2AError(a2a.ERR_PARAMS, "params.id is required")
    task_id = str(params["id"])[:128]
    task = a2a.get_task(task_id)
    if task is None:
        raise a2a.A2AError(ERR_TASK_NOT_FOUND, "Task not found")
    if task["status"]["state"] in tuple(state(n) for n in TERMINAL):
        raise a2a.A2AError(ERR_TASK_NOT_CANCELABLE, "Task cannot be canceled")
    task["status"] = {
        "state": state("canceled"),
        "timestamp": _now_iso(),
        "message": {
            "kind": "message", "messageId": "msg-" + uuid.uuid4().hex, "role": "agent",
            "parts": [{"kind": "text", "text": "Canceled at the client's request."}],
            "taskId": task_id, "contextId": task["contextId"],
        },
    }
    a2a.replace_task(task_id, task, TASK_TTL_S)
    return task
