#!/usr/bin/env python3
"""Generate the A2A agent card from the routes that actually charge.

The card used to be hand-maintained and drifted: it advertised eighteen
services while listing nine, so an agent parsing it programmatically — the
entire point of a card — saw half the catalogue. Routes and prices now come
from app.x402_routes, which is the table the payment middleware enforces, so
that class of drift cannot recur. Human-authored parts (friendly name, tags,
examples) live in data/agent-card-skills.json.

  .venv/bin/python scripts/gen_agent_card.py            # write the card
  .venv/bin/python scripts/gen_agent_card.py --check     # CI: fail if stale

Targets A2A 0.3.0 — but NOT because it is what clients read. That was an earlier
assumption and it was wrong: the SDKs (Python, JS, Java, Go, .NET) are v1.0-native
with a 0.3 compatibility mode, and v1.0.1 is current stable. 0.3.0 is the target
because v0.3 is not deprecated, because v1.0 REQUIRES supported_interfaces which
this card does not yet emit (so claiming 1.0 would be a false claim of exactly the
kind this file exists to prevent), and because 0.3.0 ships a JSON Schema we
validate against on every test run — v1.0 replaced it with specification/a2a.proto.

The v1.0 deltas are in V1_MIGRATION below. Adding 1.0 is additive rather than a
cutover: AgentInterface carries its own required protocol_version, so one card
advertises an interface per version. The state vocabulary is the only behavioural
difference and it lives in services/a2a_tasks.STATES.

Run scripts/sign_agent_card.py afterwards — regenerating invalidates the JWS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import mcp as _mcp  # noqa: E402 — needs ROOT on sys.path first

CARD_PATH = os.path.join(ROOT, "docs", ".well-known", "agent-card.json")
SKILL_META_PATH = os.path.join(ROOT, "data", "agent-card-skills.json")

PROTOCOL_VERSION = "0.3.0"
BASE = "https://api.anchor-x402.com"
SITE = "https://anchor-x402.com"

V1_MIGRATION = """When moving to A2A 1.0:
  - replace `url` + `preferredTransport` + `additionalInterfaces` with
    `supportedInterfaces[]`, each entry carrying its own protocolBinding, in
    preference order (first = preferred)
  - bump PROTOCOL_VERSION to "1.0"
Everything else here — provider.organization, securitySchemes/security,
signatures, skills — is unchanged between 0.3.0 and 1.0."""

# Path -> (skill_id, canonical HTTP method). The method matters: several routes
# accept both GET and POST, and the card must name one so a client does not have
# to guess which the 402 challenge covers.
SKILL_IDS = {
    "/v1/anchor": ("anchor_hash", "POST"),
    "/v1/screen": ("screen_wallet", "GET"),
    "/v1/attest": ("attest_decision", "POST"),
    "/v1/decode/tx": ("decode_tx", "POST"),
    "/v1/resolve/name": ("resolve_name", "GET"),
    "/v1/price/token": ("token_price", "GET"),
    "/v1/decode/calldata": ("decode_calldata", "POST"),
    "/v1/parse/datetime": ("parse_datetime", "POST"),
    "/v1/intel/wallet": ("intel_wallet", "GET"),
    "/v1/investigate": ("investigate_wallet", "POST"),
    "/v1/oracle": ("oracle_verdict", "POST"),
    "/v1/roast": ("roast_target", "POST"),
    "/v1/tldr": ("tldr_text", "POST"),
    "/v1/aura": ("aura_read", "POST"),
    "/v1/grade": ("grade_target", "POST"),
    "/v1/roll": ("roll_random", "POST"),
    "/v1/ledger/summary": ("ledger_summary", "POST"),
    "/v1/ledger/report": ("ledger_report", "POST"),
}


def _operation_ids() -> dict[tuple[str, str], str]:
    """(path, METHOD) -> operationId, from the OpenAPI document we publish.

    A skill names a price and a URL, which leaves a peer to join card and spec
    on (url, method) and guess. An external reconciler flagged that as an
    unverifiable binding, correctly. The operationId is the spec's own stable
    identifier, so publishing it turns the join into a lookup.

    Keyed by method as well as path, because four capabilities advertise GET
    while the spec documents only their POST form (app.py strips the wrapper
    twin). Emitting the POST id under a skill that says GET would put a
    contradiction inside one object — a worse defect than the missing binding.
    Those four get no operationId until card, catalog, and spec agree on one
    canonical method for them.

    Read from the served schema rather than FastAPI's raw one, so every id
    emitted is guaranteed to resolve in the published document.
    """
    import app

    schema = app.app.openapi()
    ids: dict[tuple[str, str], str] = {}
    for path, operations in schema.get("paths", {}).items():
        for method, operation in operations.items():
            if isinstance(operation, dict) and operation.get("operationId"):
                ids[(path, method.upper())] = operation["operationId"]
    return ids


def _routes() -> dict[str, dict]:
    """Price + description per paid path, straight from the charging table."""
    import app

    out: dict[str, dict] = {}
    for key, cfg in app.x402_routes.items():
        method, path = key.split(" ", 1)
        prices = {
            float(getattr(o, "price")[1:])
            for o in (cfg.accepts or [])
            if isinstance(getattr(o, "price", None), str) and getattr(o, "price").startswith("$")
        }
        if not prices:
            continue
        entry = out.setdefault(path, {"price_usd": max(prices), "methods": set(), "desc": {}})
        entry["methods"].add(method)
        # Descriptions are kept per method. Most paths have both a canonical
        # route and a GET/POST wrapper whose description says so ("GET wrapper,
        # query: target"); collapsing them meant the card declared POST while
        # describing the wrapper.
        if cfg.description:
            entry["desc"][method] = cfg.description
    return out


def build() -> dict:
    routes = _routes()
    operation_ids = _operation_ids()
    with open(SKILL_META_PATH) as f:
        meta = json.load(f)

    missing = set(SKILL_IDS) - set(routes)
    if missing:
        raise SystemExit(f"SKILL_IDS names paths that no longer charge: {sorted(missing)}")
    unmapped = set(routes) - set(SKILL_IDS)
    if unmapped:
        raise SystemExit(f"paid routes with no skill id — add them to SKILL_IDS: {sorted(unmapped)}")

    skills = []
    for path, (skill_id, method) in sorted(SKILL_IDS.items(), key=lambda kv: kv[1][0]):
        route, m = routes[path], meta.get(skill_id, {})
        if method not in route["methods"]:
            raise SystemExit(f"{skill_id}: card claims {method} {path} but that is not a paid route")
        # Hand-authored description wins; otherwise the declared method's own.
        description = m.get("description") or route["desc"].get(method) or next(
            iter(route["desc"].values()), "")
        if "wrapper" in description.lower():
            raise SystemExit(
                f"{skill_id}: description describes a wrapper route, not {method} {path} — "
                "author one in data/agent-card-skills.json"
            )
        operation_id = operation_ids.get((path, method))
        skills.append({
            "id": skill_id,
            "name": m.get("name", skill_id),
            "description": description,
            "url": f"{BASE}{path}",
            "method": method,
            # Binds this capability to its operation in the canonical spec at
            # extensions["anchor-x402:discovery"].openapi. Present only when the
            # spec documents this path at this method; verified to resolve at
            # generation time. See _operation_ids for the four that abstain.
            **({"operationId": operation_id} if operation_id else {}),
            # Indicative only — the 402 challenge is authoritative. Stated in the
            # pricing block below so a client with a cached card knows not to
            # trust this number at payment time.
            "price_usd": route["price_usd"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
            "tags": m.get("tags", []),
            # AgentSkill.examples is string[] in the schema, not object[]. The
            # sidecar authors them as objects because that is readable and
            # matches the real request body; they are JSON-encoded here so the
            # card validates against the official v0.3.0 AgentCard schema while
            # staying machine-parseable by a client that wants the body.
            **({"examples": [json.dumps(e, ensure_ascii=False, sort_keys=True)
                             if not isinstance(e, str) else e
                             for e in m["examples"]]} if m.get("examples") else {}),
        })

    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "anchor-x402",
        "description": (
            f"{len(skills)} x402-paid services for AI agents — pay-per-call USDC on Base or "
            "Solana mainnet. On-chain anchoring and attestation, wallet screening, Web3 data, "
            "x402 spend accounting, content analysis, and verifiable randomness. No API keys, "
            "no accounts; settle per request."
        ),
        "version": "0.4.0",
        "url": f"{BASE}/v1/a2a",
        "preferredTransport": "JSONRPC",
        "documentationUrl": SITE,
        "provider": {
            "organization": "anchor-x402",
            "url": SITE,
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        # OpenAPI 3.0 Security Scheme objects, per spec. x402 is a payment, not a
        # credential, so it is modelled as the header the payment rides in — the
        # closest honest fit. Rail and asset detail is in the extension block.
        "securitySchemes": {
            "x402": {
                "type": "apiKey",
                "in": "header",
                "name": "PAYMENT-SIGNATURE",
                "description": (
                    "x402 v2 pay-per-call. Request without payment to receive a 402 challenge "
                    "carrying the accepted rails and exact amounts, then retry with the signed "
                    "payment payload. No account or API key exists."
                ),
            }
        },
        "security": [{"x402": []}],
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
        "additionalInterfaces": [
            {"url": f"{BASE}/v1/a2a", "transport": "JSONRPC"},
        ],
        "extensions": {
            "anchor-x402:a2a": _a2a_extension(),
            "anchor-x402:x402": {
                "version": 2,
                "facilitator": "https://api.cdp.coinbase.com/platform/v2/x402",
                "networks": ["eip155:8453", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"],
                "asset": "USDC",
                "discovery_extension": "bazaar",
                "price_authority": (
                    "The 402 challenge response is authoritative for price. skills[].price_usd is "
                    "indicative and a cached card may be stale — never pay from it."
                ),
            },
            "anchor-x402:discovery": {
                "openapi": f"{BASE}/openapi.json",
                "x402": f"{SITE}/.well-known/x402.json",
                # Two MCP transports, and the difference matters to a caller:
                # the npm package needs a funded private key inside the local
                # process, the HTTP endpoint does not — the client keeps its key
                # and pays per call with an X-PAYMENT header. `mcp_server` is
                # kept as-is so anything already reading it does not break.
                "mcp_server": "https://www.npmjs.com/package/anchor-x402-mcp",
                "mcp_endpoint": f"{BASE}/mcp",
                "mcp_transport": "streamable-http",
                "mcp_protocol_versions": list(_mcp.SUPPORTED),
                "trust_portal": f"{SITE}/trust/",
                "status_page": "https://anchor-x402.betteruptime.com",
                "llms_txt": f"{SITE}/llms.txt",
            },
            # Top level describes the ONE key currently accepted. Superseded keys
            # are listed below it rather than deleted, so a peer holding an old
            # signature can still see what the key was and that it no longer
            # counts. A verifier should read status here and nowhere else.
            "agoragentic:federation": {
                "key_id": "anchor-fed-2026-08",
                "algorithm": "ed25519",
                "public_key_der_base64": "MCowBQYDK2VwAyEA0PatLTb58f+WIf5g74MQkA3MnZq4xmUb6T0Xa0hyoTQ=",
                "sha256_fingerprint_of_der":
                    "c84051f3b34ee6317e7b044411e0c32964b27d28bac16b13ef63de0a7705d107",
                "status": "active",
                # Deliberately short-lived. A relationship-scoped key that outlives
                # the reason it existed is a claim nobody is checking; renewing is
                # cheaper than explaining.
                "not_before": "2026-08-21T14:13:06Z",
                "not_after": "2026-11-19T00:00:00Z",
                "custody": "aws-secrets-manager",
                "capability_exchange": False,
                "federation_consent": True,
                "scope": ("Identity only: key-control proof for the Agoragentic "
                          "relationship. Dedicated key, never the treasury EOA."),
                # Published rather than merely asserted in correspondence, so the
                # boundary travels with the key.
                "grants": {
                    "payment": False,
                    "x402_settlement": False,
                    "spend": False,
                    "treasury_access": False,
                    "capability_invocation": False,
                    "provider_execution": False,
                    "routing": False,
                    "referrals": False,
                    "ranking_or_trust_mutation": False,
                    "credentials_or_private_data": False,
                    "partnership_claim": False,
                },
                "revocation": (
                    "Revoked by setting status to 'retired' here, or removing the entry. "
                    "Hard revocation is deleting the Secrets Manager secret, which "
                    "destroys the ability to sign at all. There is no CRL endpoint."
                ),
                "revocation_max_propagation_seconds": 3600,
                "superseded": [
                    {
                        "key_id": "anchor-pilot-2026-01",
                        "public_key_der_base64":
                            "MCowBQYDK2VwAyEAc3PaOglz6Z19niAHIMg9YopEy8f1hINJq0r0kkAJbgQ=",
                        "status": "retired",
                        "retired_at": "2026-07-29",
                        "custody": "aws-secrets-manager",
                        "scope": "Agoragentic federation pilot (Tier 3, July 2026). "
                                 "Pilot closed; key retired and not accepted.",
                    }
                ],
            },
        },
        "license": "MIT",
        "source": "https://github.com/hypeprinter007-stack/anchor-x402",
    }


def _a2a_extension() -> dict:
    """Request-signing keys for POST /v1/a2a.

    Distinct from the card's own `signatures` field, which proves this document
    is authentic. The spec has no notion of a key peers use to sign *requests*
    to us, so that stays an extension.
    """
    return {
        "endpoint": f"{BASE}/v1/a2a",
        "transport": "json-rpc-2.0",
        "signature_algorithm": "ed25519",
        "digest": "sha256-canonical-json",
        # aud binds an envelope to its recipient: without it, an envelope signed
        # for us verifies at any other server running this scheme, and their
        # replay store is not ours. key_id binds the signer's key choice.
        "signed_fields": ["aud", "body", "exp", "key_id", "method", "nonce", "origin"],
        "audience": BASE,
        "a2a_methods": ["message/send", "tasks/get", "tasks/cancel"],
        "a2a_protocol_versions_accepted": ["0.3.0", "1.0"],
        "a2a_version_negotiation": (
            "The endpoint accepts either dialect and replies in the one you sent. v0.3.0 is detected by the required `kind` discriminators; v1.0 by their absence (it is protobuf-derived and has none). A v1.0 caller gets proto enum spellings and the Task wrapped in SendMessageResponse.task; a v0.3.0 caller gets the bare Task with lowercase states. protocolVersion above is 0.3.0 because that is the shape this card itself is validated against — v1.0 requires supported_interfaces, which this card does not yet emit."
        ),
        "extension_methods": ["peer/hello", "capabilities/list", "peer/quote", "peer/receipt"],
        "extension_methods_note": (
            "The a2a_methods above are the spec methods at the card's top-level `url` and need no signature — authorization is the x402 payment named in securitySchemes. The extension_methods are ours, require the signed envelope described by signed_fields, and are not part of A2A."
        ),
        "artifact_types": ["a2a.quote.v1", "a2a.receipt.v1", "a2a.receipt-root.v1"],
        "identity_only": True,
        "scope_excludes": ["payments", "treasury", "admin", "credentials", "user_data",
                           "partnership_claims"],
        "open_to": "any agent publishing an Ed25519 key in its own /.well-known/agent-card.json",
        "keys": [
            {
                "key_id": "anchor-a2a-2026-01",
                "public_key_der_base64":
                    "MCowBQYDK2VwAyEAmkSQNeLBAltBCfr39EG25OAVXyOH0zdzFQO+4/+dSig=",
                "status": "active",
                "custody": "aws-kms",
                "not_after": "2027-07-28T00:00:00Z",
            }
        ],
        # Verifies this card's own `signatures`. Separate key, separate algorithm,
        # separate job: ES256 because KMS cannot produce a spec-valid Ed25519 JWS
        # over a payload this size (4096-byte raw Sign limit; DIGEST mode forces
        # Ed25519ph, for which JOSE registers no alg). Declared statically here so
        # generation needs no AWS call and sign_agent_card.py only ever adds
        # `signatures` — which keeps `--check` able to detect hand edits.
        "card_signing_keys": [
            {
                "key_id": "anchor-card-2026-01",
                "public_key_der_base64":
                    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEmm/YYcGVzj3vKlvNlnmtK17hkq5v"
                    "ftvEo4VahGJlE/FjU+4lFovDmWnOR7KXfZ16mtd+02A+PDp12aeh5+TI7A==",
                "alg": "ES256",
                "status": "active",
                "custody": "aws-kms",
                "not_after": "2027-07-28T00:00:00Z",
                "purpose": "verifies this card's `signatures`; never used for request signing",
            }
        ],
        "revocation": (
            "Keys are revoked by setting status to 'retired' here, or removing the entry. "
            "Peers cache this card for at most 3600s, which bounds propagation."
        ),
        # The sentence above is the human version; a verifier should not have to
        # parse prose to learn how long a revocation takes to propagate. 3600 is
        # the ceiling we commit to, deliberately looser than the 600s the apex
        # actually serves, so tightening the CDN never invalidates the claim.
        "revocation_max_propagation_seconds": 3600,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the card on disk differs from generated")
    args = ap.parse_args()

    card = build()
    rendered = json.dumps(card, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        current = open(CARD_PATH).read()
        # Signatures are added after generation, so compare with them stripped.
        try:
            cur = json.loads(current)
            cur.pop("signatures", None)
            stale = json.dumps(cur, indent=2, ensure_ascii=False) + "\n" != rendered
        except ValueError:
            stale = True
        if stale:
            sys.exit("agent card is stale — run scripts/gen_agent_card.py, then sign_agent_card.py")
        print(f"agent card up to date ({len(card['skills'])} skills)")
        return

    existing_sig = None
    if os.path.exists(CARD_PATH):
        try:
            existing_sig = json.load(open(CARD_PATH)).get("signatures")
        except ValueError:
            pass
    with open(CARD_PATH, "w") as f:
        f.write(rendered)
    print(f"wrote {CARD_PATH} — protocolVersion {PROTOCOL_VERSION}, {len(card['skills'])} skills")
    if existing_sig:
        print("NOTE: previous signatures dropped (content changed) — run scripts/sign_agent_card.py")


if __name__ == "__main__":
    main()
