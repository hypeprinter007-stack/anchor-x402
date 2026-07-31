"""MCP (Model Context Protocol) over Streamable HTTP — protocol layer.

Pure protocol: version negotiation, header validation, JSON-RPC envelopes. The
tool table and the paid dispatch live in app.py, so this module imports nothing
from the app and is unit-testable on its own.

Two protocol eras share the single /mcp endpoint:

  2026-07-28   Stateless. No `initialize` handshake, no `Mcp-Session-Id`. Every
               request carries its own protocolVersion + clientCapabilities in
               `params._meta`; servers MUST NOT infer capabilities from prior
               requests. Results carry `resultType`; list results also carry
               `ttlMs` + `cacheScope`. `server/discover` is mandatory. `ping`,
               `logging/setLevel` and the GET stream endpoint are gone.

  2025-03-26   The `initialize` handshake era. Sessions are optional *for the
  …2025-11-25  server* — we mint none, which is what keeps this endpoint honest
               on Lambda, where there is no cross-invocation memory to hold a
               session in anyway.

Shapes verified against the official schemas at schema/<version>/schema.json in
modelcontextprotocol/modelcontextprotocol, not from recall — the 2026-07-28
revision moved enough (initialize deleted, resultType/ttlMs/cacheScope added as
*required*, error codes renumbered into -32020…-32099) that a from-memory
implementation would have claimed a version it does not speak.

Deliberately not implemented, and why:
  * subscriptions/listen — we advertise no listChanged and no resource
    subscriptions, so there is nothing to stream. Answered -32601.
  * resources/*, prompts/*, sampling, roots, logging — not offered; the last
    three are deprecated as of 2026-07-28 anyway.
  * MRTR (InputRequiredResult) for payment — `InputRequest` is a closed anyOf of
    sampling/roots/elicitation, so payment does not fit it. x402 payment rides
    the `X-PAYMENT` HTTP header instead, which is its native carrier and works
    identically in both eras.
"""

from __future__ import annotations

import base64
import json

# --- Versions ---------------------------------------------------------------
# Revisions are ISO dates, so lexical comparison is chronological comparison.
LATEST = "2026-07-28"
MODERN = "2026-07-28"          # first stateless revision
SUPPORTED = ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26")
# Highest handshake-era revision. What we answer an `initialize` with when the
# client asks for something we don't have: returning a *modern* version to an
# initialize would be incoherent, since modern has no initialize.
LATEST_LEGACY = "2025-11-25"
# 2025-03-26 predates the MCP-Protocol-Version header, so a request without one
# is that revision by definition. The spec makes honouring this a MAY; we take
# it because plenty of deployed clients still omit the header.
DEFAULT_LEGACY = "2025-03-26"

SERVER_NAME = "anchor-x402"
# Implementation.version is the *software* version, not the protocol version —
# easy to conflate, and wrong in a way nothing would have complained about.
# One MCP product across two transports: anchor-x402-mcp 0.2.x is stdio-only, and
# adding Streamable HTTP is what makes this 0.3.0.
SERVER_VERSION = "0.3.0"
# Reverse-DNS id used by the official registry's server.json (one slash, per its
# name pattern). Distinct from SERVER_NAME, which is the display identity.
REGISTRY_NAME = "io.github.hypeprinter007-stack/anchor-x402"

# --- `_meta` keys (2026-07-28) ---------------------------------------------
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# --- Error codes ------------------------------------------------------------
# JSON-RPC standard range.
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
# MCP-reserved range (-32020…-32099), renumbered in 2026-07-28. The older
# -32001/-32003/-32004 spellings from the draft are NOT used.
ERR_HEADER_MISMATCH = -32020
ERR_MISSING_CAPABILITY = -32021
ERR_UNSUPPORTED_VERSION = -32022

# Cache hints. Our tool table is baked at import time and only changes on
# deploy, so an hour of client-side caching is safe and cuts pointless polling.
TOOLS_TTL_MS = 3_600_000
DISCOVER_TTL_MS = 3_600_000
CACHE_PUBLIC = "public"

# Methods that carry a name/uri mirrored into the `Mcp-Name` header.
_NAME_SOURCE = {
    "tools/call": "name",
    "prompts/get": "name",
    "resources/read": "uri",
}

_SENTINEL_PRE = "=?base64?"
_SENTINEL_SUF = "?="


def is_modern(version: str) -> bool:
    """True for the stateless era (>= 2026-07-28)."""
    return version >= MODERN


def server_info(version: str | None = None) -> dict:
    """Implementation — our software identity. `version` is accepted and ignored
    so callers can pass the negotiated protocol revision without it silently
    ending up here, which is the bug this signature shape prevents."""
    return {"name": SERVER_NAME, "version": SERVER_VERSION}


def capabilities() -> dict:
    """Tools only. `listChanged` is deliberately absent: advertising it would
    promise a notification we have no stream to deliver it on."""
    return {"tools": {}}


def decode_header_value(raw: str) -> str | None:
    """Undo the `=?base64?…?=` sentinel encoding. Returns None if the value
    claims to be encoded but isn't decodable — the caller turns that into a
    HeaderMismatch rather than silently comparing garbage."""
    if raw.startswith(_SENTINEL_PRE) and raw.endswith(_SENTINEL_SUF):
        inner = raw[len(_SENTINEL_PRE):-len(_SENTINEL_SUF)]
        try:
            return base64.b64decode(inner, validate=True).decode("utf-8")
        except Exception:
            return None
    return raw


# --- Envelopes --------------------------------------------------------------

def ok(request_id, result: dict, version: str, cache: tuple[int, str] | None = None) -> dict:
    """A JSON-RPC success. In the modern era every result MUST carry
    `resultType`, list-ish results MUST carry `ttlMs` + `cacheScope`, and the
    server SHOULD identify itself in `_meta`. Legacy results get none of that —
    emitting it there would be noise the older schemas never asked for."""
    body = dict(result)
    if is_modern(version):
        body.setdefault("resultType", "complete")
        if cache:
            body.setdefault("ttlMs", cache[0])
            body.setdefault("cacheScope", cache[1])
        meta = dict(body.get("_meta") or {})
        meta.setdefault(META_SERVER_INFO, server_info(version))
        body["_meta"] = meta
    return {"jsonrpc": "2.0", "id": request_id, "result": body}


def err(request_id, code: int, message: str, data=None) -> dict:
    e: dict = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": e}


def http_status(code: int) -> int:
    """The spec pins two of these explicitly: 400 for header/version failures,
    and 404 — not 200 — for an unimplemented method, so a client can tell a
    modern server's `-32601` from a legacy 404 that has no MCP endpoint at all.
    Application-level errors stay at 200 with the error in the body, which is
    the usual JSON-RPC-over-HTTP convention."""
    if code == ERR_METHOD_NOT_FOUND:
        return 404
    if code in (ERR_HEADER_MISMATCH, ERR_UNSUPPORTED_VERSION, ERR_MISSING_CAPABILITY,
                ERR_PARSE, ERR_INVALID_REQUEST):
        return 400
    return 200


# --- Negotiation + header validation ---------------------------------------

def negotiate(headers, body: dict) -> tuple[str, dict | None]:
    """Resolve the protocol version for this request.

    Returns (version, error_or_None). `headers` is any case-insensitive mapping.

    `initialize` is special-cased: it is the request that *establishes* the
    version, so the header is legitimately absent on it and the version comes
    from `params.protocolVersion`. Rejecting it for a missing header would break
    every handshake-era client on its very first message.
    """
    method = body.get("method")
    params = body.get("params")
    params = params if isinstance(params, dict) else {}
    header = (headers.get("mcp-protocol-version") or "").strip()

    if method == "initialize":
        asked = params.get("protocolVersion")
        if isinstance(asked, str) and asked in SUPPORTED:
            # Never answer a handshake with a version that has no handshake.
            return (asked if not is_modern(asked) else LATEST_LEGACY), None
        return LATEST_LEGACY, None

    meta = params.get("_meta")
    meta = meta if isinstance(meta, dict) else {}
    in_body = meta.get(META_VERSION)
    in_body = in_body.strip() if isinstance(in_body, str) else ""

    if header and in_body and header != in_body:
        return header, {
            "code": ERR_HEADER_MISMATCH,
            "message": (f"Header mismatch: MCP-Protocol-Version header {header!r} does not "
                        f"match the request body's {META_VERSION} {in_body!r}."),
        }

    version = header or in_body or DEFAULT_LEGACY

    if version not in SUPPORTED:
        return version, {
            "code": ERR_UNSUPPORTED_VERSION,
            "message": f"Unsupported MCP protocol version {version!r}.",
            "data": {"requested": version, "supported": list(SUPPORTED)},
        }

    # A client that declares the modern era must send the header; the body
    # `_meta` alone is not enough, because intermediaries route on the header.
    if is_modern(version) and not header:
        return version, {
            "code": ERR_HEADER_MISMATCH,
            "message": (f"The MCP-Protocol-Version header is required for {version}. "
                        f"Send 'MCP-Protocol-Version: {version}' matching params._meta."
                        f"['{META_VERSION}']."),
        }

    return version, None


def validate_request(headers, body: dict, version: str) -> dict | None:
    """Modern-era request metadata checks. Returns an error dict or None.

    The header mirroring exists so gateways can route and rate-limit without
    parsing the body; validating that headers agree with the body is what stops
    a gateway and this server from acting on two different truths.
    """
    if not is_modern(version):
        return None

    method = body.get("method", "")
    params = body.get("params")
    params = params if isinstance(params, dict) else {}

    h_method = headers.get("mcp-method")
    if not h_method:
        return {"code": ERR_HEADER_MISMATCH,
                "message": f"The Mcp-Method header is required. Send 'Mcp-Method: {method}'."}
    if h_method != method:
        return {"code": ERR_HEADER_MISMATCH,
                "message": (f"Header mismatch: Mcp-Method header {h_method!r} does not match "
                            f"the request body method {method!r}.")}

    field = _NAME_SOURCE.get(method)
    if field:
        want = params.get(field)
        raw = headers.get("mcp-name")
        if raw is None:
            return {"code": ERR_HEADER_MISMATCH,
                    "message": (f"The Mcp-Name header is required for {method}. "
                                f"Send 'Mcp-Name: <params.{field}>'.")}
        got = decode_header_value(raw)
        if got is None:
            return {"code": ERR_HEADER_MISMATCH,
                    "message": "The Mcp-Name header claims =?base64?…?= encoding but is not "
                               "valid base64 UTF-8."}
        if got != want:
            return {"code": ERR_HEADER_MISMATCH,
                    "message": (f"Header mismatch: Mcp-Name header {got!r} does not match "
                                f"params.{field} {want!r}.")}

    meta = params.get("_meta")
    meta = meta if isinstance(meta, dict) else {}
    if not isinstance(meta.get(META_CLIENT_CAPS), dict):
        return {"code": ERR_INVALID_PARAMS,
                "message": (f"params._meta['{META_CLIENT_CAPS}'] is required for {version} and "
                            f"must be an object (send {{}} if you support no optional "
                            f"capabilities). Capabilities are per-request in this revision; "
                            f"the server does not remember them between calls.")}
    return None


def client_label(body: dict) -> str:
    """Best-effort 'name/version' of the caller, for the access log. Modern
    clients self-report per request; handshake-era clients only do it once, in
    `initialize`, which is why /mcp logs the UA too."""
    params = body.get("params")
    params = params if isinstance(params, dict) else {}
    info = params.get("clientInfo")            # initialize
    if not isinstance(info, dict):
        meta = params.get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        info = meta.get(META_CLIENT_INFO)      # 2026-07-28, every request
    if not isinstance(info, dict):
        return ""
    name = str(info.get("name") or "")[:64]
    ver = str(info.get("version") or "")[:32]
    return f"{name}/{ver}" if name else ""


def is_notification(body: dict) -> bool:
    """JSON-RPC notifications have no `id`. Over Streamable HTTP they are
    answered with 202 Accepted and an empty body."""
    return isinstance(body, dict) and "id" not in body and isinstance(body.get("method"), str)


def text_content(payload) -> list[dict]:
    """CallToolResult.content — the human/LLM-readable rendering. JSON is dumped
    with stable key order so identical results are byte-identical, which lets
    clients cache and dedupe them."""
    if isinstance(payload, str):
        return [{"type": "text", "text": payload}]
    return [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}]
