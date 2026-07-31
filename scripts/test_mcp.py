#!/usr/bin/env python3
"""Local test for the MCP endpoint (POST /mcp).

Runs entirely offline. The one thing it cannot exercise is a *settled* tool call
— that needs a funded wallet — so the paid path is asserted up to the 402
challenge, which is the part an agent has to be able to act on anyway.

Covers both protocol eras against the shapes in the official schemas:
  * 2026-07-28 — stateless, per-request `_meta`, mandatory `server/discover`,
    required `resultType` / `ttlMs` / `cacheScope`, header-body mirroring.
  * 2025-03-26 … 2025-11-25 — the `initialize` handshake era, which must NOT
    receive any of the modern-only fields.

  .venv/bin/python scripts/test_mcp.py
"""

import base64
import json
import os
import sys

import jsonschema

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import app, _MCP_TOOLS, _MCP_ALIASES, _MCP_TOOL_LIST
from services import mcp as mcp_svc

MODERN = mcp_svc.LATEST
LEGACY = "2025-06-18"

failures: list[str] = []
_n = 0


def ok(label: str, cond: bool, detail: str = "") -> None:
    global _n
    _n += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
        failures.append(label)


def RES(r) -> dict:
    """Result accessor that degrades to {} instead of raising. A KeyError here
    would abort the run, which reads as 'no failures' — that is how a broken
    assertion hides. Mutation testing caught exactly this."""
    try:
        v = r.json().get("result")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def ERR(r) -> dict:
    try:
        v = r.json().get("error")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def load_named(stem: str):
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", f"{stem}.json")) as f:
        doc = json.load(f)
    return doc, ("$defs" if "$defs" in doc else "definitions")


def modern_body(method: str, params: dict | None = None, ident=1, version=MODERN) -> dict:
    p = dict(params or {})
    p["_meta"] = {
        mcp_svc.META_VERSION: version,
        mcp_svc.META_CLIENT_CAPS: {},
        mcp_svc.META_CLIENT_INFO: {"name": "test-client", "version": "0.0.1"},
    }
    return {"jsonrpc": "2.0", "id": ident, "method": method, "params": p}


def modern_headers(method: str, name: str | None = None, version=MODERN) -> dict:
    h = {"MCP-Protocol-Version": version, "Mcp-Method": method,
         "Accept": "application/json, text/event-stream"}
    if name is not None:
        h["Mcp-Name"] = name
    return h


def main() -> None:
    client = TestClient(app)

    print("tool table")
    ok(f"all {len(_MCP_TOOLS)} services exposed as tools",
       len(_MCP_TOOL_LIST) == len(_MCP_TOOLS) == 18, str(len(_MCP_TOOL_LIST)))
    names = [t["name"] for t in _MCP_TOOL_LIST]
    ok("tool names match the route map", set(names) == set(_MCP_TOOLS))
    ok("order is deterministic (2026-07-28 SHOULD)", names == sorted(names))
    ok("every tool has a non-empty description", all(t.get("description") for t in _MCP_TOOL_LIST))
    # The four GET-canonical services have a POST entry whose description reads
    # "POST wrapper, body: {...}" — plumbing, not the service. Same guard the
    # agent-card generator applies, because the same trap exists here.
    leaked = [t["name"] for t in _MCP_TOOL_LIST if "wrapper" in t["description"].lower()]
    ok("no wrapper prose leaked into a description", not leaked, str(leaked))
    ok("every inputSchema is an object schema",
       all(t["inputSchema"].get("type") == "object" for t in _MCP_TOOL_LIST))
    ok("no $ref in any inputSchema (older clients choke)",
       "$ref" not in json.dumps(_MCP_TOOL_LIST))
    ok("every tool priced in its description",
       all("$" in t["description"] for t in _MCP_TOOL_LIST))
    # Proves the schema really is derived from the Pydantic model rather than
    # hand-written: `wallet` is required on the model, and the field description
    # comes from it too.
    screen = next(t for t in _MCP_TOOL_LIST if t["name"] == "screen_wallet")
    ok("schema carries model constraints (screen_wallet requires wallet)",
       screen["inputSchema"].get("required") == ["wallet"], str(screen["inputSchema"]))
    ok("schema carries per-field descriptions from the model",
       "description" in screen["inputSchema"]["properties"]["wallet"])
    ok("read-only tools annotated", screen["annotations"]["readOnlyHint"] is True)
    anchor = next(t for t in _MCP_TOOL_LIST if t["name"] == "anchor_hash")
    ok("chain-writing tools are not marked read-only",
       anchor["annotations"]["readOnlyHint"] is False)
    ok("aliases all resolve to real tools",
       all(v in _MCP_TOOLS for v in _MCP_ALIASES.values()))
    ok("aliases are not advertised", not set(_MCP_ALIASES) & set(names))

    print("\nhandshake era (initialize)")
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": LEGACY,
                                             "capabilities": {},
                                             "clientInfo": {"name": "c", "version": "1"}}})
    ok("initialize succeeds with no MCP-Protocol-Version header", r.status_code == 200,
       str(r.status_code))
    res = RES(r)
    ok("initialize echoes the requested version", res.get("protocolVersion") == LEGACY,
       str(res.get("protocolVersion")))
    ok("initialize advertises tools", "tools" in (res.get("capabilities") or {}))
    ok("initialize returns serverInfo", (res.get("serverInfo") or {}).get("name") == "anchor-x402")
    # Implementation.version is the software version. Reporting the negotiated
    # protocol revision here instead is an easy conflation that nothing else
    # would flag, so it is asserted explicitly in both eras.
    ok("serverInfo.version is the software version, not the protocol version",
       (res.get("serverInfo") or {}).get("version") == mcp_svc.SERVER_VERSION
       and (res.get("serverInfo") or {}).get("version") != LEGACY,
       str((res.get("serverInfo") or {}).get("version")))
    ok("initialize carries instructions for the model", bool(res.get("instructions")))
    ok("no resultType in a legacy result", "resultType" not in res)
    ok("no ttlMs/cacheScope in a legacy result",
       "ttlMs" not in res and "cacheScope" not in res)
    ok("no session is minted", "mcp-session-id" not in {k.lower() for k in r.headers})

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "1999-01-01"}})
    ok("initialize with an unknown version falls back to a supported one",
       RES(r).get("protocolVersion") == mcp_svc.LATEST_LEGACY,
       str(RES(r).get("protocolVersion")))

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                  "params": {}},
                    headers={"MCP-Protocol-Version": LEGACY})
    ok("legacy tools/list works", r.status_code == 200 and
       len(RES(r).get("tools", [])) == 18, str(r.status_code))
    ok("legacy tools/list omits modern cache fields",
       "ttlMs" not in RES(r) and "resultType" not in RES(r))
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
                    headers={"MCP-Protocol-Version": LEGACY})
    ok("legacy ping answers", r.status_code == 200 and RES(r) == {})
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/list",
                                  "params": {}})
    ok("missing header is treated as the pre-header revision", r.status_code == 200,
       str(r.status_code))

    print("\nstateless era (2026-07-28)")
    r = client.post("/mcp", json=modern_body("tools/list"),
                    headers=modern_headers("tools/list"))
    ok("modern tools/list works", r.status_code == 200, str(r.status_code))
    res = RES(r)
    ok("resultType is present and complete", res.get("resultType") == "complete",
       str(res.get("resultType")))
    ok("ttlMs present (required by CacheableResult)", isinstance(res.get("ttlMs"), int))
    ok("cacheScope present and valid", res.get("cacheScope") in ("public", "private"))
    si = (res.get("_meta") or {}).get(mcp_svc.META_SERVER_INFO, {})
    ok("serverInfo returned in _meta", si.get("name") == "anchor-x402")
    ok("_meta serverInfo.version is the software version, not the protocol version",
       si.get("version") == mcp_svc.SERVER_VERSION and si.get("version") != MODERN,
       str(si.get("version")))
    ok("modern tools/list still returns all 18", len(res.get("tools", [])) == 18)

    r = client.post("/mcp", json=modern_body("server/discover"),
                    headers=modern_headers("server/discover"))
    ok("server/discover is implemented (MUST)", r.status_code == 200, str(r.status_code))
    d = RES(r)
    ok("discover lists supportedVersions", MODERN in d.get("supportedVersions", []))
    ok("discover advertises tools capability", "tools" in (d.get("capabilities") or {}))
    ok("discover carries every required field",
       all(k in d for k in ("resultType", "ttlMs", "cacheScope", "capabilities",
                            "supportedVersions")), str(sorted(d)))
    ok("discover reports the same version list as the module",
       d["supportedVersions"] == list(mcp_svc.SUPPORTED))

    print("\nrequest-metadata validation")
    r = client.post("/mcp", json=modern_body("tools/list"),
                    headers={"Mcp-Method": "tools/list"})
    ok("modern request without the version header is 400/-32020",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_HEADER_MISMATCH,
       f"{r.status_code} {ERR(r)}")
    r = client.post("/mcp", json=modern_body("tools/list", version="2025-06-18"),
                    headers=modern_headers("tools/list"))
    ok("header/body version mismatch is 400/-32020",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_HEADER_MISMATCH,
       str(r.status_code))
    r = client.post("/mcp", json=modern_body("tools/list", version="2030-01-01"),
                    headers=modern_headers("tools/list", version="2030-01-01"))
    ok("unsupported version is 400/-32022",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_UNSUPPORTED_VERSION,
       str(r.status_code))
    ok("unsupported-version error lists what we do support",
       ERR(r).get("data", {}).get("supported") == list(mcp_svc.SUPPORTED)
       and ERR(r).get("data", {}).get("requested") == "2030-01-01")
    r = client.post("/mcp", json=modern_body("tools/list"),
                    headers={"MCP-Protocol-Version": MODERN})
    ok("missing Mcp-Method is 400/-32020",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_HEADER_MISMATCH,
       str(r.status_code))
    r = client.post("/mcp", json=modern_body("tools/list"),
                    headers=modern_headers("tools/call"))
    ok("Mcp-Method that disagrees with the body is 400/-32020",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_HEADER_MISMATCH)
    body = modern_body("tools/call", {"name": "token_price", "arguments": {"symbol": "ETH"}})
    r = client.post("/mcp", json=body, headers=modern_headers("tools/call", "screen_wallet"))
    ok("Mcp-Name that disagrees with params.name is 400/-32020",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_HEADER_MISMATCH)
    r = client.post("/mcp", json=body, headers=modern_headers("tools/call"))
    ok("tools/call with no Mcp-Name at all is 400/-32020",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_HEADER_MISMATCH)
    sentinel = "=?base64?" + base64.b64encode(b"token_price").decode() + "?="
    r = client.post("/mcp", json=body, headers=modern_headers("tools/call", sentinel))
    ok("base64-sentinel Mcp-Name is decoded and accepted",
       r.status_code == 200 and RES(r).get("isError") is True,
       f"{r.status_code} {str(r.json())[:200]}")
    r = client.post("/mcp", json=body,
                    headers=modern_headers("tools/call", "=?base64?not!valid!?="))
    ok("undecodable sentinel is 400/-32020",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_HEADER_MISMATCH)
    stripped = modern_body("tools/list")
    del stripped["params"]["_meta"][mcp_svc.META_CLIENT_CAPS]
    r = client.post("/mcp", json=stripped, headers=modern_headers("tools/list"))
    ok("modern request without clientCapabilities is rejected",
       ERR(r).get("code") == mcp_svc.ERR_INVALID_PARAMS, str(ERR(r)))
    ok("that rejection explains capabilities are per-request",
       "per-request" in ERR(r).get("message", ""))

    print("\nremoved-in-modern methods")
    r = client.post("/mcp", json=modern_body("ping"), headers=modern_headers("ping"))
    ok("ping is gone in 2026-07-28 → 404/-32601",
       r.status_code == 404 and ERR(r).get("code") == mcp_svc.ERR_METHOD_NOT_FOUND,
       str(r.status_code))
    r = client.post("/mcp", json=modern_body("subscriptions/listen"),
                    headers=modern_headers("subscriptions/listen"))
    ok("unimplemented subscriptions/listen → 404/-32601",
       r.status_code == 404 and ERR(r).get("code") == mcp_svc.ERR_METHOD_NOT_FOUND)
    r = client.post("/mcp", json=modern_body("resources/list"),
                    headers=modern_headers("resources/list"))
    ok("unoffered resources/list → 404/-32601 (not 200)", r.status_code == 404)

    print("\ntransport mechanics")
    ok("GET /mcp is 405", client.get("/mcp").status_code == 405)
    ok("DELETE /mcp is 405", client.delete("/mcp").status_code == 405)
    ok("405 advertises Allow: POST", client.get("/mcp").headers.get("allow") == "POST")
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    ok("notification gets 202 with no body", r.status_code == 202 and not r.content,
       str(r.status_code))
    r = client.post("/mcp", content=b"not json")
    ok("unparseable body is 400/-32700",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_PARSE)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 9})
    ok("missing method is 400/-32600",
       r.status_code == 400 and ERR(r).get("code") == mcp_svc.ERR_INVALID_REQUEST)
    r = client.post("/mcp", json=modern_body("tools/list"),
                    headers={**modern_headers("tools/list"),
                             "Mcp-Session-Id": "abc", "Last-Event-ID": "5"})
    ok("stale session/resumability headers are ignored, not echoed",
       r.status_code == 200 and "mcp-session-id" not in {k.lower() for k in r.headers})

    print("\ntool invocation (unpaid — the 402 an agent must act on)")
    r = client.post("/mcp", json=modern_body(
        "tools/call", {"name": "token_price", "arguments": {"symbol": "ETH"}}),
        headers=modern_headers("tools/call", "token_price"))
    ok("unpaid tools/call is a result, not a JSON-RPC error", r.status_code == 200,
       str(r.status_code))
    res = RES(r)
    ok("unpaid call is flagged isError so the model sees it", res.get("isError") is True)
    ok("resultType still present on an errored result", res.get("resultType") == "complete")
    sc = res.get("structuredContent") or {}
    ok("the x402 challenge is in structuredContent", "accepts" in sc, str(sorted(sc))[:200])
    ok("challenge lists at least one payment option", len(sc.get("accepts") or []) >= 1)
    ok("content text tells the agent to retry with X-PAYMENT",
       "X-PAYMENT" in (res.get("content") or [{}])[0].get("text", ""), (res.get("content") or [{}])[0].get("text", "")[:160])
    r = client.post("/mcp", json=modern_body(
        "tools/call", {"name": "roast", "arguments": {"target": "x"}}),
        headers=modern_headers("tools/call", "roast"))
    ok("npm short-name alias resolves", r.status_code == 200
       and (RES(r).get("structuredContent") or {}).get("accepts") is not None,
       str(r.json())[:200])
    r = client.post("/mcp", json=modern_body("tools/call", {"name": "nope"}),
                    headers=modern_headers("tools/call", "nope"))
    ok("unknown tool is -32602", ERR(r).get("code") == mcp_svc.ERR_INVALID_PARAMS)
    ok("unknown-tool message points at tools/list",
       "tools/list" in ERR(r).get("message", ""))
    # Checked in the legacy era on purpose: in the modern era a tools/call with
    # no params.name is caught earlier, by the missing-Mcp-Name header rule.
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                  "params": {"arguments": {}}},
                    headers={"MCP-Protocol-Version": LEGACY})
    ok("tools/call with no name is -32602",
       ERR(r).get("code") == mcp_svc.ERR_INVALID_PARAMS, str(ERR(r)))

    print("\ntool invocation (settled path, without spending money)")
    # Every real tool is paid, so the isError=False branch would otherwise go
    # untested offline. Point the dispatcher at a free route instead: this runs
    # the actual _mcp_invoke ASGI sub-request and the actual result rendering,
    # and needs no wallet and no payment bypass. Deliberately not done by
    # forwarding x-internal-auth — widening a bypass to make a test convenient
    # is the wrong trade.
    # /v1/a2a is the only free POST route; _mcp_invoke always POSTs, so a
    # GET-only route like /health would just come back 405.
    import app as _app
    _app._MCP_TOOLS["__probe"] = "/v1/a2a"
    try:
        r = client.post("/mcp", json=modern_body(
            "tools/call", {"name": "__probe", "arguments": {"jsonrpc": "2.0", "id": 1}}),
            headers=modern_headers("tools/call", "__probe"))
        res = RES(r)
        ok("a 200 from the forwarded route renders as a success",
           res.get("isError") is False, str(r.json())[:220])
        ok("the route's JSON body is returned as structuredContent",
           (res.get("structuredContent") or {}).get("jsonrpc") == "2.0",
           str(res.get("structuredContent"))[:160])
        ok("content carries the payload as text",
           "jsonrpc" in (res.get("content") or [{}])[0].get("text", ""))
        ok("resultType is complete on a settled call", res.get("resultType") == "complete")
        ok("__probe is not advertised in tools/list",
           "__probe" not in [t["name"] for t in _MCP_TOOL_LIST])
    finally:
        _app._MCP_TOOLS.pop("__probe", None)

    print("\ndiscoverable surface")
    for path in ("/v1/mcp", "/api/mcp"):
        r = client.post(path, json=modern_body("tools/list"),
                        headers=modern_headers("tools/list"))
        ok(f"{path} alias reaches the same endpoint",
           r.status_code == 200 and len(RES(r).get("tools", [])) == 18, str(r.status_code))
    r = client.get("/sse")
    ok("/sse explains the dead transport instead of 404-ing blankly",
       r.status_code == 404 and r.json().get("mcp_endpoint", "").endswith("/mcp"),
       str(r.status_code))
    ok("/sse HEAD works too", client.head("/sse").status_code == 404)
    r = client.get("/.well-known/mcp/server-card.json")
    card = r.json()
    pub = (card.get("_meta") or {}).get(
        "io.modelcontextprotocol.registry/publisher-provided") or {}
    remotes = card.get("remotes") or [{}]
    ok("server card served", r.status_code == 200)
    ok("card uses the official registry schema",
       card.get("$schema", "").endswith("server.schema.json"))
    ok("card name matches the registry reverse-DNS pattern",
       card.get("name") == mcp_svc.REGISTRY_NAME)
    ok("card advertises the HTTP endpoint under remotes[]",
       remotes[0].get("type") == "streamable-http"
       and remotes[0].get("url", "").endswith("/mcp"), str(remotes[0]))
    ok("card description is within the schema's 100-char cap",
       len(card.get("description", "")) <= 100, str(len(card.get("description", ""))))
    ok("card still lists the stdio npm package",
       (card.get("packages") or [{}])[0].get("identifier") == "anchor-x402-mcp")
    ok("card lists all 18 tools", len(pub.get("tools", [])) == 18)
    ok("card declares x402 auth", pub.get("authentication", {}).get("type") == "x402")
    ok("card version list matches the server", pub.get("protocolVersions") == list(mcp_svc.SUPPORTED))
    doc, cont = load_named("mcp-server-json-schema")
    try:
        jsonschema.validate(card, doc)
        ok("server card validates against the official server.json schema", True)
    except jsonschema.ValidationError as e:
        ok("server card validates against the official server.json schema", False,
           f"{'.'.join(str(x) for x in e.absolute_path)}: {e.message[:200]}")
    ok("card HEAD works for link previews",
       client.head("/.well-known/mcp/server-card.json").status_code == 200)

    print("\nofficial schema validation (vendored from modelcontextprotocol)")
    # The claim "this server speaks 2026-07-28 and 2025-06-18" is only worth
    # anything if the bytes we emit validate against those revisions' own
    # schemas. Everything above tests our reading of the spec; this tests the
    # spec itself.
    def load(rev: str):
        return load_named(f"mcp-{rev}-schema")

    def check(rev: str, defname: str, instance, label: str) -> None:
        doc, container = load(rev)
        if defname not in doc[container]:
            ok(f"{rev} defines {defname}", False, "definition missing from vendored schema")
            return
        try:
            jsonschema.validate(instance, {"$ref": f"#/{container}/{defname}", **doc})
            ok(f"{rev} {defname}: {label}", True)
        except jsonschema.ValidationError as e:
            ok(f"{rev} {defname}: {label}", False,
               f"{'.'.join(str(p) for p in e.absolute_path)}: {e.message[:180]}")

    r = client.post("/mcp", json=modern_body("tools/list"),
                    headers=modern_headers("tools/list"))
    check(MODERN, "ListToolsResult", RES(r), "modern tools/list")
    bad = []
    doc, cont = load(MODERN)
    for t in _MCP_TOOL_LIST:
        try:
            jsonschema.validate(t, {"$ref": f"#/{cont}/Tool", **doc})
        except jsonschema.ValidationError as e:
            bad.append(f"{t['name']}: {e.message[:80]}")
    ok(f"{MODERN} Tool: all 18 tool definitions valid", not bad, str(bad[:2]))

    r = client.post("/mcp", json=modern_body("server/discover"),
                    headers=modern_headers("server/discover"))
    check(MODERN, "DiscoverResult", RES(r), "server/discover")

    r = client.post("/mcp", json=modern_body(
        "tools/call", {"name": "token_price", "arguments": {"symbol": "ETH"}}),
        headers=modern_headers("tools/call", "token_price"))
    check(MODERN, "CallToolResult", RES(r), "unpaid tools/call (isError)")

    r = client.post("/mcp", json=modern_body("tools/list", version="2030-01-01"),
                    headers=modern_headers("tools/list", version="2030-01-01"))
    check(MODERN, "UnsupportedProtocolVersionError", r.json(), "rejected version")

    r = client.post("/mcp", json=modern_body("tools/list", version=LEGACY),
                    headers=modern_headers("tools/list"))
    check(MODERN, "HeaderMismatchError", r.json(), "header/body disagreement")

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": LEGACY, "capabilities": {},
                                             "clientInfo": {"name": "c", "version": "1"}}})
    check(LEGACY, "InitializeResult", RES(r), "handshake")

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                  "params": {}},
                    headers={"MCP-Protocol-Version": LEGACY})
    check(LEGACY, "ListToolsResult", RES(r), "legacy tools/list")
    doc, cont = load(LEGACY)
    bad = []
    for t in _MCP_TOOL_LIST:
        try:
            jsonschema.validate(t, {"$ref": f"#/{cont}/Tool", **doc})
        except jsonschema.ValidationError as e:
            bad.append(f"{t['name']}: {e.message[:80]}")
    ok(f"{LEGACY} Tool: all 18 tool definitions valid", not bad, str(bad[:2]))

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "token_price",
                                             "arguments": {"symbol": "ETH"}}},
                    headers={"MCP-Protocol-Version": LEGACY})
    check(LEGACY, "CallToolResult", RES(r), "legacy unpaid tools/call")

    print("\nno money path on the protocol surface")
    for p in ("POST /mcp", "POST /v1/mcp", "POST /api/mcp"):
        ok(f"{p} is not itself a metered route", p not in __import__("app").x402_routes)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print(f"all {_n} assertions passed — MCP over HTTP OK "
          f"({len(mcp_svc.SUPPORTED)} protocol revisions, {len(_MCP_TOOL_LIST)} tools)")


if __name__ == "__main__":
    main()
