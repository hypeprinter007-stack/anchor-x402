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

Both versions are accepted and answered in kind, because live traffic forced it:
after the methods shipped, the same client's calls started failing on
`message.kind is required`. It was speaking v1.0, which has no `kind` field
anywhere. The versions are distinguishable by construction rather than by guess —
0.3.0 makes `kind` a REQUIRED discriminator, v1.0 is protobuf-derived and has none
— so detect_version reads the request shape and render() emits the matching one.

Divergences handled, and they are the complete set: `kind` discriminators present
vs absent; Role as "user" vs ROLE_USER; TaskState as "auth-required" vs
TASK_STATE_AUTH_REQUIRED (1.0 also drops `unknown` and adds UNSPECIFIED); Part as
a tagged object vs a protobuf oneof; and SendMessageResponse returning the Task
bare in 0.3.0 but wrapped in a `task` field in 1.0. That last one is the easiest
to miss and the most total in effect — every field can be correct and the client
still cannot parse the result.

Tasks record the version that created them, since a bare tasks/get carries no
shape to infer from.
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


def detect_version(raw: Any) -> str:
    """Infer the caller's protocol version from the shape it sent.

    Not guesswork — the versions are distinguishable by construction. 0.3.0 makes
    `kind` a REQUIRED discriminator on Message and Part; v1.0 is protobuf-derived
    and has no `kind` anywhere at all (the Part is a oneof), so its absence is
    positive evidence rather than an omission.

    This exists because of live traffic. A real client sent Messages with no
    `kind` and was rejected by strict 0.3.0 validation — it was speaking v1.0 at
    an endpoint that only accepted 0.3.0.
    """
    if not isinstance(raw, dict):
        return PROTOCOL_VERSION
    if raw.get("kind") == "message":
        return "0.3.0"
    parts = raw.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and "kind" in part:
                return "0.3.0"
    # Proto enum form is also unambiguous.
    if str(raw.get("role", "")).startswith("ROLE_"):
        return "1.0"
    return "1.0" if "kind" not in raw else "0.3.0"


def _normalize_part(part: Any, index: int) -> dict[str, Any]:
    """Reduce either version's Part to the internal 0.3.0-ish form."""
    if not isinstance(part, dict):
        raise a2a.A2AError(a2a.ERR_PARAMS, f"message.parts[{index}] must be an object")
    kind = part.get("kind")
    if kind is None:
        # v1.0 oneof: exactly one of text / data / raw / url is set.
        if "text" in part:
            kind = "text"
        elif "data" in part:
            kind = "data"
        elif "raw" in part or "url" in part:
            kind = "file"
        else:
            raise a2a.A2AError(
                a2a.ERR_PARAMS,
                f"message.parts[{index}] must set one of text, data, raw or url (v1.0) "
                "or carry a `kind` discriminator (v0.3.0)",
            )
    if kind == "file":
        # No skill takes file input, so use the code the spec reserves for it.
        raise a2a.A2AError(
            ERR_CONTENT_TYPE_NOT_SUPPORTED, "file parts are not supported by any skill")
    if kind not in ("text", "data"):
        raise a2a.A2AError(
            a2a.ERR_PARAMS, f"message.parts[{index}].kind must be 'text', 'data' or 'file'")
    out: dict[str, Any] = {"kind": kind}
    if kind == "text":
        if not isinstance(part.get("text"), str):
            raise a2a.A2AError(a2a.ERR_PARAMS, f"message.parts[{index}].text must be a string")
        out["text"] = part["text"]
    else:
        if not isinstance(part.get("data"), dict):
            raise a2a.A2AError(a2a.ERR_PARAMS, f"message.parts[{index}].data must be an object")
        out["data"] = part["data"]
    return out


def validate_message(raw: Any, version: str) -> dict[str, Any]:
    """Validate against the caller's own version and normalize to internal form.

    Field names are the same in both (`messageId`, `parts`, `role`); what differs
    is the `kind` discriminators, the Role spelling, and the Part encoding.
    """
    if not isinstance(raw, dict):
        raise a2a.A2AError(a2a.ERR_PARAMS, "params.message must be an object")
    for field in ("messageId", "parts", "role"):
        if field not in raw:
            raise a2a.A2AError(a2a.ERR_PARAMS, f"message.{field} is required")
    if version == "0.3.0":
        if raw.get("kind") != "message":
            raise a2a.A2AError(a2a.ERR_PARAMS, "message.kind must be 'message'")
        if raw["role"] not in ("user", "agent"):
            raise a2a.A2AError(a2a.ERR_PARAMS, "message.role must be 'user' or 'agent'")
    else:
        if raw["role"] not in ("ROLE_USER", "ROLE_AGENT", "user", "agent"):
            raise a2a.A2AError(
                a2a.ERR_PARAMS, "message.role must be ROLE_USER or ROLE_AGENT")
    parts = raw["parts"]
    if not isinstance(parts, list) or not parts:
        raise a2a.A2AError(a2a.ERR_PARAMS, "message.parts must be a non-empty array")

    return {
        "kind": "message",
        "messageId": str(raw["messageId"]),
        "role": "agent" if str(raw["role"]).endswith(("AGENT", "agent")) else "user",
        "parts": [_normalize_part(p, i) for i, p in enumerate(parts)],
        **({"contextId": raw["contextId"]} if raw.get("contextId") else {}),
        **({"taskId": raw["taskId"]} if raw.get("taskId") else {}),
    }


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
                 x402: dict[str, Any], version: str = PROTOCOL_VERSION) -> dict[str, Any]:
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
                 metadata={"skillId": skill["id"], "priceUsd": price_usd,
                           "protocolVersion": version})
    a2a.put_task(task_id, task, TASK_TTL_S)
    return task


def clarify_task(message: dict[str, Any], skills: list[dict[str, Any]],
                 version: str = PROTOCOL_VERSION) -> dict[str, Any]:
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
                 status_parts=parts, history=[message],
                 metadata={"protocolVersion": version})
    a2a.put_task(task_id, task, TASK_TTL_S)
    return task


def render(task: dict[str, Any], version: str) -> dict[str, Any]:
    """Render an internally-stored (0.3.0-shaped) Task in the caller's version.

    v1.0 differences, all of them: no `kind` discriminators anywhere, proto enum
    spellings for Role and TaskState, and no `unknown` state.
    """
    if version == "0.3.0":
        return task

    def msg(m: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in m.items() if k != "kind"}
        out["role"] = "ROLE_AGENT" if m.get("role") == "agent" else "ROLE_USER"
        out["parts"] = [{k: v for k, v in p.items() if k != "kind"} for p in m.get("parts", [])]
        return out

    reverse = {v: k for k, v in STATES["0.3.0"].items()}
    out = {k: v for k, v in task.items() if k != "kind"}
    status = dict(task["status"])
    status["state"] = STATES["1.0"][reverse[status["state"]]]
    if "message" in status:
        status["message"] = msg(status["message"])
    out["status"] = status
    if "history" in out:
        out["history"] = [msg(m) for m in out["history"]]
    return out


def send_result(task: dict[str, Any], version: str) -> dict[str, Any]:
    """v0.3.0 returns the Task as the JSON-RPC result; v1.0's SendMessageResponse
    is a oneof, so the Task is wrapped in a `task` field. Getting this wrong means
    a client sees a result it cannot parse even though every field is correct."""
    rendered = render(task, version)
    return rendered if version == "0.3.0" else {"task": rendered}


def get_task(params: Any) -> dict[str, Any]:
    """tasks/get — TaskQueryParams: id required, historyLength optional."""
    if not isinstance(params, dict) or not params.get("id"):
        raise a2a.A2AError(a2a.ERR_PARAMS, "params.id is required")
    task = a2a.get_task(str(params["id"])[:128])
    if task is None:
        raise a2a.A2AError(ERR_TASK_NOT_FOUND, "Task not found")
    version = (task.get("metadata") or {}).get("protocolVersion", PROTOCOL_VERSION)
    length = params.get("historyLength")
    if isinstance(length, int) and "history" in task:
        # Spec: historyLength caps the returned history. 0 means omit it.
        task = {**task, "history": task["history"][-length:]} if length > 0 else \
               {k: v for k, v in task.items() if k != "history"}
    return render(task, version)


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
    return render(task, (task.get("metadata") or {}).get("protocolVersion", PROTOCOL_VERSION))
