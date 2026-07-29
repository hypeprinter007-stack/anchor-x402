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

Targets A2A 0.3.0, which is what implementations in the wild read today. The
v1.0 deltas are listed in V1_MIGRATION below so the move is mechanical: emit
supportedInterfaces[] instead of url + preferredTransport + additionalInterfaces,
and bump PROTOCOL_VERSION. Nothing else in the shape changes.

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
        skills.append({
            "id": skill_id,
            "name": m.get("name", skill_id),
            "description": description,
            "url": f"{BASE}{path}",
            "method": method,
            # Indicative only — the 402 challenge is authoritative. Stated in the
            # pricing block below so a client with a cached card knows not to
            # trust this number at payment time.
            "price_usd": route["price_usd"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
            "tags": m.get("tags", []),
            **({"examples": m["examples"]} if m.get("examples") else {}),
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
                "mcp_server": "https://www.npmjs.com/package/anchor-x402-mcp",
                "trust_portal": f"{SITE}/trust/",
                "status_page": "https://anchor-x402.betteruptime.com",
                "llms_txt": f"{SITE}/llms.txt",
            },
            "agoragentic:federation": {
                "key_id": "anchor-pilot-2026-01",
                "public_key_der_base64": "MCowBQYDK2VwAyEAc3PaOglz6Z19niAHIMg9YopEy8f1hINJq0r0kkAJbgQ=",
                "status": "active",
                "custody": "aws-secrets-manager",
                "capability_exchange": True,
                "federation_consent": True,
                "scope": "Agoragentic federation pilot only; not used for general A2A traffic.",
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
        "methods": ["peer/hello", "capabilities/list", "peer/quote", "peer/receipt"],
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
        "revocation": (
            "Keys are revoked by setting status to 'retired' here, or removing the entry. "
            "Peers cache this card for at most 3600s, which bounds propagation."
        ),
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
