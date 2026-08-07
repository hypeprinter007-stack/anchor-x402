"""anchor-x402: dual-chain mainnet anchoring as an x402-paid service.

POST /v1/anchor — accept a hash (or arbitrary JSON to be hashed),
write the resulting 32-byte digest to Base mainnet (calldata) and
Solana mainnet (Memo program) in parallel, return both tx hashes.

Pay-per-call: $0.005 USDC, settle on Base or Solana.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from mangum import Mangum
from starlette.concurrency import run_in_threadpool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

from x402 import AssetAmount
from x402.http.middleware.fastapi import payment_middleware, RouteConfig
from x402.http import HTTPFacilitatorClient, FacilitatorConfig, PaymentOption
from x402.server import x402ResourceServer
from x402.extensions.bazaar import (
    bazaar_resource_server_extension,
    declare_discovery_extension,
    OutputConfig,
)
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.mechanisms.svm.exact import register_exact_svm_server

from models import (
    AnchorRequest,
    AnchorResponse,
    AttestRequest,
    AttestResponse,
    AttestVerifyRequest,
    AttestVerifyResponse,
    AuraRequest,
    AuraResponse,
    CalldataDecodeRequest,
    CalldataDecodeResponse,
    ChainAnchor,
    ChatRequest,
    ChatResponse,
    DatetimeParseRequest,
    DatetimeParseResponse,
    GradeRequest,
    GradeResponse,
    IntelWalletResponse,
    NameResolveResponse,
    OracleRequest,
    OracleResponse,
    RoastRequest,
    RoastResponse,
    RollRequest,
    RollResponse,
    ScreenResponse,
    TldrRequest,
    TldrResponse,
    TokenPriceResponse,
    TxDecodeRequest,
    TxDecodeResponse,
)
from services import a2a as a2a_svc
from services import a2a_tasks
from services import anchor as anchor_svc
from services import attest as attest_svc
from services import aura as aura_svc
from services import calldata_decode as calldata_decode_svc
from services import chat as chat_svc
from services import datetime_parse as datetime_parse_svc
from services import divigent as divigent_svc
from services import grade as grade_svc
from services import intel_wallet as intel_wallet_svc
from services import name_resolve as name_resolve_svc
from services import oracle as oracle_svc
from services import roast as roast_svc
from services import roll as roll_svc
from services import screen as screen_svc
from services import tldr as tldr_svc
from services import token_price as token_price_svc
from services import tx_decode as tx_decode_svc
from services.cdp_auth import build_cdp_auth_provider
from services.jpyc_facilitator import (
    JPYC_EIP712_NAME,
    JPYC_EIP712_VERSION,
    JPYC_POLYGON_ADDRESS,
    build_jpyc_facilitator,
)

TREASURY = os.getenv("TREASURY_ADDRESS", "")
SOLANA_TREASURY = os.getenv("SOLANA_TREASURY_ADDRESS", "")
# Polygon treasury falls back to the Base treasury — same EVM address works
# on every EVM chain. Override only when running a dedicated Polygon EOA.
POLYGON_TREASURY = os.getenv("POLYGON_TREASURY_ADDRESS", "") or TREASURY
SOLANA_MAINNET_CAIP2 = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

app = FastAPI(
    title="anchor-x402",
    description="18 pay-per-call x402 services for AI agents — on-chain anchoring & attestation, wallet/address security screening, Web3 data (tx & calldata decode, ENS, token prices), x402 spend accounting, content analysis, and verifiable randomness. No API keys or accounts; settle per request in USDC on Base or Solana. $0.001–$1.77 per call.",
    version="0.3.0",
    docs_url=None,  # custom /docs below — directory scrapers read its <title> + favicon
)


@app.get("/docs", include_in_schema=False)
def swagger_docs():
    """Swagger UI with branded <title> and favicon. x402 directories (x402scan
    et al.) scrape this page for the service's display name and icon; the
    FastAPI default gave them 'anchor-x402 - Swagger UI' and the stock
    FastAPI favicon."""
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="anchor-x402 — 18 x402 pay-per-call services for AI agents",
        swagger_favicon_url="/icon.png",
    )


# uagents_core.logger.get_logger() defaults to creating a FileHandler at
# 'uagents_core.log' — a relative path that resolves to /var/task in Lambda,
# which is read-only. Patch the default before any submodule imports it.
try:
    import uagents_core.logger as _uacore_logger
    _orig_uacore_get_logger = _uacore_logger.get_logger

    def _uacore_no_filelog(name=None, level=logging.INFO, log_file=None):  # noqa: ARG001
        return _orig_uacore_get_logger(name, level, None)

    _uacore_logger.get_logger = _uacore_no_filelog
except ImportError:
    pass


# Free, machine-facing paths where knowing the caller is the point.
# The MCP endpoint and its aliases. Declared here because the settled-payment
# middleware below has to know which paths are transport rather than product.
_MCP_ENDPOINT_PATHS = frozenset({"/mcp", "/v1/mcp", "/api/mcp"})

_DISCOVERY_PATHS = frozenset({
    "/.well-known/agent-card.json",
    "/.well-known/agent-registration.json",
    "/.well-known/x402.json",
    "/.well-known/x402",
    "/v1/a2a",
    "/mcp",
    "/.well-known/mcp/server-card.json",
    "/llms.txt",
    "/openapi.json",
})


@app.middleware("http")
async def _access_log(request, call_next):
    """One-line access log per request → Lambda CloudWatch.
    Enables host-level traffic split (chat.* vs api.*) via Insights."""
    response = await call_next(request)
    host = request.headers.get("host", "").split(":")[0]
    print(f"access host={host} method={request.method} path={request.url.path} status={response.status_code}")
    # Discovery surfaces get caller attribution, and only they do. Whoever reads
    # the agent card is the thing we most need to identify — is it an agent, a
    # directory crawler, or us — and the plain access line above carries no
    # user-agent or client address, which made "has anyone found us?"
    # unanswerable. Restricted to a handful of free paths so this does not add a
    # field to all ~20k daily requests.
    if request.url.path in _DISCOVERY_PATHS:
        try:
            fwd = request.headers.get("x-forwarded-for", "")
            print("DISCOVERY " + json.dumps({
                "ts": int(time.time()),
                "path": request.url.path,
                "status": response.status_code,
                "host": host,
                # First hop only; the rest is proxy chain. Public client address.
                "ip": fwd.split(",")[0].strip()[:45],
                "ua": request.headers.get("user-agent", "")[:120],
            }, separators=(",", ":")))
        except Exception:
            pass
    # Settled-payment telemetry. This middleware is innermost (inside x402_mw),
    # so a request carrying X-PAYMENT that reached here has already passed
    # verification — a <400 response means a paid call was delivered. One
    # structured line per paid call gives per-rail / per-route / per-payer usage
    # via Insights (the routes are otherwise stateless). Payer is a public
    # on-chain address; best-effort, never raises into the request path.
    pay_header = request.headers.get("payment-signature") or request.headers.get("x-payment")
    # The MCP endpoint is not itself metered: it forwards the payment inward to the
    # real route, which logs its own PAID_CALL. Counting the outer hop too would
    # book one settlement twice — a 2x overstatement of paid calls and revenue for
    # every MCP-routed payment, in the same ledger /v1/ledger/* reports from.
    if request.url.path in _MCP_ENDPOINT_PATHS:
        pay_header = None
    if pay_header and response.status_code < 400:
        try:
            from services import refund as refund_svc
            payer, network = refund_svc.parse_buyer_from_x_payment(pay_header)
            if payer or network:
                # An A2A correlation id, if the caller echoed the one we handed
                # out in a Task. This is the join that makes the funnel
                # measurable: without it PAID_CALL knows the wallet but not who
                # was quoted, and the A2A log knows the caller but not the wallet.
                exchange = request.headers.get(a2a_tasks.CORRELATION_HEADER, "")[:64]
                if exchange and a2a_svc.EXCHANGE_ID_RE.fullmatch(exchange):
                    if a2a_tasks.settle(exchange, {"payer": payer, "rail": network,
                                                   "path": request.url.path}):
                        print("A2A_SETTLED " + json.dumps(
                            {"ts": int(time.time()), "exchange": exchange,
                             "path": request.url.path, "payer": payer, "rail": network},
                            separators=(",", ":")))
                print("PAID_CALL " + json.dumps(
                    {
                        "ts": int(time.time()),
                        "path": request.url.path,
                        "method": request.method,
                        "network": network,
                        "payer": payer,
                        "status": response.status_code,
                        # Set by _mcp_invoke on the forwarded sub-request, so the
                        # single surviving line still records that this arrived
                        # over MCP and which tool the caller asked for.
                        **({"via": "mcp",
                            "tool": request.headers.get("x-mcp-tool", "")[:40]}
                           if request.headers.get("x-mcp-tool") else {}),
                        **({"a2a_exchange": exchange} if exchange else {}),
                    },
                    separators=(",", ":"),
                ))
        except Exception:
            pass
    return response


# Cross-sell pointers up the value ladder, injected into paid JSON responses.
# The buyer at that moment is an agent with a funded wallet that just paid —
# the only point where we can merchandise to it. Only pairings with a real
# workflow adjacency are listed; novelty routes are deliberately absent.
_RELATED_UPSELLS = {
    "/v1/price/token": ["/v1/screen"],
    "/v1/resolve/name": ["/v1/screen"],
    "/v1/decode/tx": ["/v1/intel/wallet"],
    "/v1/screen": ["/v1/intel/wallet", "/v1/investigate"],
    "/v1/intel/wallet": ["/v1/investigate"],
    "/v1/anchor": ["/v1/attest"],
}

_UPSELL_CARDS = {
    "/v1/screen": {
        "method": "GET",
        "price": "$0.001",
        "reason": "OFAC SDN + AML screening for any wallet you transact with (EVM or Solana)",
    },
    "/v1/intel/wallet": {
        "method": "GET",
        "price": "$0.005",
        "reason": "Full wallet intel bundle — balances, activity, identity, sanctions — from 8+ sources in one call",
    },
    "/v1/investigate": {
        "method": "POST",
        "price": "$1.77",
        "reason": "Deep agent-driven wallet due diligence — signed report, dual-chain anchored, auto-refund on failure",
    },
    "/v1/attest": {
        "method": "POST",
        "price": "$0.01",
        "reason": "Upgrade a bare anchor to a verifiable attestation — signature over (input, output, decision), anchored",
    },
}


@app.middleware("http")
async def _related_upsell(request, call_next):
    """Append `x402_related` to paid JSON responses per _RELATED_UPSELLS.
    Gated on a payment header so free/internal traffic passes untouched;
    best-effort — any parse failure returns the original body unmodified."""
    response = await call_next(request)
    upsells = _RELATED_UPSELLS.get(request.url.path)
    if (
        not upsells
        or response.status_code != 200
        or "application/json" not in response.headers.get("content-type", "")
        or not (request.headers.get("payment-signature") or request.headers.get("x-payment"))
    ):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    from fastapi.responses import JSONResponse as _JSONResponse, Response as _Response
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("non-object body")
        host = request.headers.get("host", "api.anchor-x402.com").split(":")[0]
        payload["x402_related"] = [
            {"resource": f"https://{host}{p}", **_UPSELL_CARDS[p]} for p in upsells
        ]
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return _JSONResponse(content=payload, status_code=200, headers=headers)
    except Exception:
        headers = {k: v for k, v in response.headers.items()}
        return _Response(content=body, status_code=response.status_code, headers=headers)


cdp_facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(
        url="https://api.cdp.coinbase.com/platform/v2/x402",
        auth_provider=build_cdp_auth_provider(),
    )
)

# In-process JPYC facilitator on Polygon — None when relayer key/RPC unset.
jpyc_facilitator = build_jpyc_facilitator()

_facilitator_clients = [cdp_facilitator]
if jpyc_facilitator is not None:
    _facilitator_clients.append(jpyc_facilitator)

x402_server = x402ResourceServer(facilitator_clients=_facilitator_clients)
x402_server.register("eip155:8453", ExactEvmServerScheme())
if jpyc_facilitator is not None:
    x402_server.register("eip155:137", ExactEvmServerScheme())
register_exact_svm_server(x402_server, networks=SOLANA_MAINNET_CAIP2)
x402_server.register_extension(bazaar_resource_server_extension)

_anchor_bazaar_ext = declare_discovery_extension(
    input={
        "hash": "ab0898397c86fbf97c99c6f8b29e55ab697315705777ee1d106e2dcb9bd686b3",
        "note": "vendor approval batch 2026-Q2",
    },
    input_schema={
        "properties": {
            "hash": {"type": "string", "description": "32-byte hex hash (64 chars, no 0x prefix). Mutually exclusive with `data`."},
            "data": {"description": "Arbitrary JSON to be canonicalized + SHA-256'd by the server. Mutually exclusive with `hash`."},
            "note": {"type": "string", "description": "Optional 200-char note returned in the response (not on-chain).", "maxLength": 200},
        },
    },
    body_type="json",
    output=OutputConfig(example={
        "merkle_root": "ab0898397c86fbf97c99c6f8b29e55ab697315705777ee1d106e2dcb9bd686b3",
        "base": {
            "tx": "0x7fb4d107d8c1b65b33851434c6fd178b682a143904a2bfa89ff2c1fa70974e96",
            "explorer_url": "https://basescan.org/tx/0x7fb4d107d8c1b65b33851434c6fd178b682a143904a2bfa89ff2c1fa70974e96",
        },
        "solana": {
            "tx": "u8rqU4oSQkkHQw93nCCtQym5crh4UjuoNvYhspLcUTEny59asrNNffpmxpgzRuZ4MXQnN5UEoCKQuDS16ZMPKW2",
            "explorer_url": "https://solscan.io/tx/u8rqU4oSQkkHQw93nCCtQym5crh4UjuoNvYhspLcUTEny59asrNNffpmxpgzRuZ4MXQnN5UEoCKQuDS16ZMPKW2",
        },
        "anchored_at": 1746820000,
    }),
)
_anchor_bazaar_ext["bazaar"]["discoverable"] = True
_anchor_bazaar_ext["bazaar"]["category"] = "security"

# USD → JPYC (18-decimal atomic). Snapped to clean yen amounts; a small
# premium absorbs USDC↔JPYC FX volatility. ¥1 is the "pay 1 yen per call" hook.
_JPYC_TIERS_ATOMIC: dict[str, int] = {
    "$0.001":  10**17,         # ¥0.1
    "$0.005":  10**18,         # ¥1
    "$0.01":   2 * 10**18,     # ¥2
    "$0.02":   4 * 10**18,     # ¥4  (¥200/USD, matches the other tiers)
    "$0.05":   10 * 10**18,    # ¥10
    "$5.00":   1000 * 10**18,  # ¥1000
    "$1.77":   350 * 10**18,   # ¥350 (~$1.77 USD at ¥200/USD with FX buffer)
}


def _accepts_at(price: str) -> list[PaymentOption]:
    out: list[PaymentOption] = []
    if TREASURY:
        out.append(PaymentOption(scheme="exact", pay_to=TREASURY, price=price, network="eip155:8453"))
    if SOLANA_TREASURY:
        out.append(PaymentOption(scheme="exact", pay_to=SOLANA_TREASURY, price=price, network=SOLANA_MAINNET_CAIP2))
    if POLYGON_TREASURY and jpyc_facilitator is not None and price in _JPYC_TIERS_ATOMIC:
        out.append(PaymentOption(
            scheme="exact",
            pay_to=POLYGON_TREASURY,
            price=AssetAmount(
                amount=str(_JPYC_TIERS_ATOMIC[price]),
                asset=JPYC_POLYGON_ADDRESS,
                extra={"name": JPYC_EIP712_NAME, "version": JPYC_EIP712_VERSION},
            ),
            network="eip155:137",
        ))
    return out


# --- Bazaar declarations for /v1/screen and /v1/attest ---

_screen_bazaar_ext = declare_discovery_extension(
    input={"wallet": "0x8589427373d6d84e98730d7795d8f6f8731fda16"},  # Tornado Cash example
    input_schema={
        "properties": {
            "wallet": {"type": "string", "description": "Wallet address — EVM 0x… (40 hex) or Solana base58 pubkey."},
        },
        "required": ["wallet"],
    },
    body_type="json",
    output=OutputConfig(example={
        "wallet": "0x8589427373d6d84e98730d7795d8f6f8731fda16",
        "chain_inferred": "ethereum",
        "sanctions_match": True,
        "sanctioned_lists": ["OFAC SDN", "Tornado Cash"],
        "risk_level": "high",
        "notes": "Address matches 2 sanctions program(s)…",
        "checked_at": 1746820000,
    }),
)

_attest_bazaar_ext = declare_discovery_extension(
    input={
        "input_hash": "ab0898397c86fbf97c99c6f8b29e55ab697315705777ee1d106e2dcb9bd686b3",
        "output_hash": "121bff3514725274e35bf3407fec31b5bbf458ee89ae8b75f3a01492f8a9ecef",
        "decision": "APPROVED",
        "scheme": "eip191",
        "signature": "0x...",
    },
    input_schema={
        "properties": {
            "input_hash": {"type": "string", "description": "64-char hex SHA-256 of the agent's input."},
            "output_hash": {"type": "string", "description": "64-char hex SHA-256 of the agent's output / decision payload."},
            "decision": {"type": "string", "maxLength": 64, "description": "Free-form short label."},
            "scheme": {"type": "string", "enum": ["eip191", "ed25519"]},
            "signature": {"type": "string", "description": "0x-prefixed hex (eip191) or base58 (ed25519)."},
            "signer_pubkey": {"type": "string", "description": "Required for ed25519. Ignored for eip191."},
        },
        "required": ["input_hash", "output_hash", "decision", "scheme", "signature"],
    },
    body_type="json",
    output=OutputConfig(example={
        "merkle_root": "10ce4d38b59746febff0651a2d19d5da9a36f4e209e4333414d6d8e4abad898b",
        "signer_verified": True,
        "signer": "0xFE708ED41DE893390240C95A801A49ed8F974702",
        "base": {"tx": "0x...", "explorer_url": "https://basescan.org/tx/0x..."},
        "solana": {"tx": "...", "explorer_url": "https://solscan.io/tx/..."},
        "decision": "APPROVED",
        "signed_at": 1746820000,
    }),
)

_tx_decode_bazaar_ext = declare_discovery_extension(
    input={"chain": "base", "tx_hash": "0x7fb4d107d8c1b65b33851434c6fd178b682a143904a2bfa89ff2c1fa70974e96"},
    input_schema={
        "properties": {
            "chain": {"type": "string", "enum": ["base", "ethereum", "solana"]},
            "tx_hash": {"type": "string", "description": "EVM 0x+64hex or Solana base58 signature."},
        },
        "required": ["chain", "tx_hash"],
    },
    body_type="json",
    output=OutputConfig(example={
        "chain": "base", "tx_hash": "0x7fb4...", "block_number": 24000000,
        "timestamp": 1746820000, "from_address": "0xFE70...", "to_address": "0xFE70...",
        "value_wei": "0", "value_eth": "0", "gas_used": 21064, "status": 1,
        "input_calldata_hex": "0xab08...", "native_currency": "ETH",
    }),
)

_name_resolve_bazaar_ext = declare_discovery_extension(
    input={"name": "vitalik.eth"},
    input_schema={
        "properties": {
            "name": {"type": "string", "description": "Human-readable name (e.g. vitalik.eth, bonfida.sol)."},
        },
        "required": ["name"],
    },
    body_type="json",
    output=OutputConfig(example={
        "name": "vitalik.eth",
        "addresses": [{"chain": "ethereum", "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", "ttl_hint_seconds": 3600}],
        "resolved_at": 1746820000,
        "registry_used": "ENS",
        "supported_tlds": [".eth", ".sol"],
        "notes": None,
    }),
)

_token_price_bazaar_ext = declare_discovery_extension(
    input={"symbol": "ETH"},
    input_schema={
        "properties": {
            "symbol": {"type": "string", "description": "Token symbol (BTC, ETH, SOL, USDC, …). Mutually exclusive with chain+contract."},
            "chain": {"type": "string", "description": "Chain slug: base, ethereum, solana, polygon, arbitrum, optimism, bsc, avalanche."},
            "contract": {"type": "string", "description": "Token contract address. Required with chain."},
        },
    },
    body_type="json",
    output=OutputConfig(example={
        "symbol": "ETH", "name": "Ethereum", "contract": None, "chain": None,
        "usd": 3120.55, "usd_24h_change_pct": 1.23, "market_cap_usd": 375000000000.0,
        "source": "coingecko", "fetched_at": 1746820000, "age_seconds": 0,
    }),
)

_calldata_decode_bazaar_ext = declare_discovery_extension(
    input={
        "chain": "ethereum",
        "calldata_hex": "0xa9059cbb000000000000000000000000ab5801a7d398351b8be11c439e05c5b3259aec9b0000000000000000000000000000000000000000000000000de0b6b3a7640000",
    },
    input_schema={
        "properties": {
            "chain": {"type": "string", "enum": ["ethereum", "solana"], "description": 'EVM-only; "solana" returns 400.'},
            "calldata_hex": {"type": "string", "description": "Raw EVM calldata (>=4 byte selector), with or without 0x prefix."},
            "contract_address": {"type": "string", "description": "Optional. Reserved for future on-chain ABI lookups."},
        },
        "required": ["chain", "calldata_hex"],
    },
    body_type="json",
    output=OutputConfig(example={
        "function_selector": "0xa9059cbb",
        "function_name": "transfer",
        "function_signature": "transfer(address,uint256)",
        "params": [
            {"name": None, "type": "address", "value": "0xab5801a7d398351b8be11c439e05c5b3259aec9b"},
            {"name": None, "type": "uint256", "value": "1000000000000000000"},
        ],
        "decoded": True,
        "candidates": [],
        "source": "openchain.xyz",
    }),
)

_intel_wallet_bazaar_ext = declare_discovery_extension(
    input={"wallet": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"},
    input_schema={
        "properties": {"wallet": {"type": "string", "description": "EVM 0x… (40 hex) or Solana base58 pubkey."}},
        "required": ["wallet"],
    },
    body_type="json",
    output=OutputConfig(example={
        "wallet": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        "chain_inferred": "ethereum",
        "balances": {"base_eth": "0.0123", "eth_eth": "4.21", "base_usdc": "1503.27", "sol": None, "solana_usdc": None},
        "activity": {"base_tx_count": 42, "has_history": True},
        "identity": {"ens_name": "vitalik.eth", "sns_name": None},
        "sanctions": {"wallet": "0xd8da…", "chain_inferred": "ethereum", "sanctions_match": False, "sanctioned_lists": [], "risk_level": "low", "notes": "…", "checked_at": 1746820000},
        "errors": [],
        "fetched_at": 1746820000,
        "cache_age_seconds": 0,
    }),
)

_investigate_bazaar_ext = declare_discovery_extension(
    input={"address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"},
    input_schema={
        "properties": {
            "address": {"type": "string", "description": "EVM 0x… (40 hex) or Solana base58 pubkey to investigate."},
        },
        "required": ["address"],
    },
    body_type="json",
    output=OutputConfig(example={
        "job_id": "1be3bd50-51df-4e47-8624-ae5bd1df5953",
        "status": "accepted",
        "status_url": "https://api.anchor-x402.com/v1/investigate/status/1be3bd50-51df-4e47-8624-ae5bd1df5953",
        "eta_seconds": 600,
    }),
)


_datetime_parse_bazaar_ext = declare_discovery_extension(
    input={"input": "next Tuesday at 3pm", "timezone": "America/New_York"},
    input_schema={
        "properties": {
            "input": {"type": "string", "description": "Freeform datetime string."},
            "base_time": {"type": "string", "description": "Optional ISO 8601 reference; defaults to now UTC."},
            "timezone": {"type": "string", "description": "Optional IANA tz name; defaults to UTC."},
        },
        "required": ["input"],
    },
    body_type="json",
    output=OutputConfig(example={
        "iso": "2026-05-13T15:00:00-04:00",
        "unix": 1778000400,
        "timezone": "America/New_York",
        "components": {"year": 2026, "month": 5, "day": 13, "hour": 15, "minute": 0, "second": 0, "weekday": 2, "day_name": "Wednesday"},
        "relative_seconds": 432000,
        "relative_human": "in 5 days",
        "confidence": "medium",
        "parsed_input": "next Tuesday at 3pm",
    }),
)

_roast_bazaar_ext = declare_discovery_extension(
    input={"target": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"},
    input_schema={
        "properties": {
            "target": {"type": "string", "description": "Anything to roast: wallet, tweet, idea, code, etc.", "maxLength": 8000},
        },
        "required": ["target"],
    },
    body_type="json",
    output=OutputConfig(example={
        "roast": "A wallet so old it remembers when gas was a vibe, not a tax...",
        "target_summary": "Vitalik Buterin's well-known Ethereum address.",
    }),
)

_oracle_bazaar_ext = declare_discovery_extension(
    input={"question": "Will I finish my todo list this week?"},
    input_schema={
        "properties": {
            "question": {"type": "string", "description": "A yes/no question for the oracle.", "maxLength": 1000},
        },
        "required": ["question"],
    },
    body_type="json",
    output=OutputConfig(example={
        "answer": "MAYBE",
        "explanation": "Possible if you cut the bottom three items and stop opening Twitter.",
        "merkle_root": "ab0898397c86fbf97c99c6f8b29e55ab697315705777ee1d106e2dcb9bd686b3",
        "base_tx": "0x7fb4d107d8c1b65b33851434c6fd178b682a143904a2bfa89ff2c1fa70974e96",
        "solana_tx": "u8rqU4oSQkkHQw93nCCtQym5crh4UjuoNvYhspLcUTEny59asrNNffpmxpgzRuZ4MXQnN5UEoCKQuDS16ZMPKW2",
        "asked_at": 1746820000,
    }),
)

_aura_bazaar_ext = declare_discovery_extension(
    input={"target": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"},
    input_schema={
        "properties": {
            "target": {"type": "string", "description": "Anything to read the aura of.", "maxLength": 4000},
        },
        "required": ["target"],
    },
    body_type="json",
    output=OutputConfig(example={
        "color": "molten gold with copper veins",
        "tier": "S",
        "score": 8845,
        "description": "Old-money aura. Doesn't need to prove anything to anyone. Has seen things.",
    }),
)

_roll_bazaar_ext = declare_discovery_extension(
    input={"low": 1, "high": 100, "count": 1, "label": "treasure_drop_42"},
    input_schema={
        "properties": {
            "low": {"type": "integer", "description": "Inclusive low bound of the integer range."},
            "high": {"type": "integer", "description": "Inclusive high bound. Must be >= low."},
            "count": {"type": "integer", "description": "How many integers to sample. 1-100.", "minimum": 1, "maximum": 100, "default": 1},
            "commitment": {"type": "string", "description": "Optional 32-byte hex pre-commitment from the caller."},
            "label": {"type": "string", "description": "Optional caller tag, included in the signed payload.", "maxLength": 200},
        },
        "required": ["low", "high"],
    },
    body_type="json",
    output=OutputConfig(example={
        "range": [1, 100],
        "count": 1,
        "commitment": None,
        "label": "treasure_drop_42",
        "result": [47],
        "input_hash": "f4a1c5e3b9d8a2e0c7b4f6d9e1a3c8b5f7d2e4a9c1b6e8f3d5a7c2b4e9f1d6a8",
        "result_hash": "8a6d1f9e4b2c7a3e5f0c9b8a4d2f6e1c3b7a5f9d2e8c4b6a1f3e7d5c9b2a4f6e",
        "signature": "0x6b3e2a7c…",
        "signer": "0x127462e296fAc1A7F5cF33bA57bB2f0FFf5cD0B6",
        "scheme": "eip191",
        "domain": "anchor-x402/roll/v1",
    }),
)
_roll_bazaar_ext["bazaar"]["discoverable"] = True
_roll_bazaar_ext["bazaar"]["category"] = "gaming"

_grade_bazaar_ext = declare_discovery_extension(
    input={"target": "def add(a, b): return a - b  # surely this is correct"},
    input_schema={
        "properties": {
            "target": {"type": "string", "description": "Anything to grade.", "maxLength": 6000},
        },
        "required": ["target"],
    },
    body_type="json",
    output=OutputConfig(example={
        "letter_grade": "D",
        "marginalia": [
            "Function name says 'add' but operator says 'subtract' — pick a side.",
            "The comment is the funniest thing in this codebase.",
            "Add a test and your subconscious will thank you.",
        ],
        "summary": "Strong vibes, weak math. See me after class.",
    }),
)

_tldr_bazaar_ext = declare_discovery_extension(
    input={"url": "https://example.com/some-article"},
    input_schema={
        "properties": {
            "text": {"type": "string", "description": "Pasted text to summarize. Provide either `text` or `url`."},
            "url": {"type": "string", "description": "URL to fetch and summarize. Provide either `text` or `url`."},
        },
    },
    body_type="json",
    output=OutputConfig(example={
        "summary_bullets": [
            "Example domain is reserved by IANA for documentation use.",
            "It is safe to use in literature without coordination or permission.",
            "More info available at iana.org.",
        ],
        "source_chars": 1170,
    }),
)

_ledger_summary_bazaar_ext = declare_discovery_extension(
    input={"wallet": "0x7818cB9cEad1E13E64A259F0867089dB75E374c5", "from": "2026-06-01", "to": "2026-07-01"},
    input_schema={
        "properties": {
            "wallet": {"type": "string", "description": "EVM address (Base) whose x402 spend to reconstruct."},
            "from": {"type": "string", "description": "ISO date, start of range. Default: 30 days before `to`."},
            "to": {"type": "string", "description": "ISO date, end of range. Default: now."},
            "direction": {"type": "string", "enum": ["outbound", "inbound", "both"], "description": "outbound = spend (default), inbound = revenue."},
            "min_amount": {"type": "string", "description": "Minimum USDC per transfer, e.g. 0.001."},
            "group_by": {"type": "string", "enum": ["service", "recipient", "day"]},
        },
        "required": ["wallet"],
    },
    body_type="json",
    output=OutputConfig(example={
        "wallet": "0x7818cb9cead1e13e64a259f0867089db75e374c5",
        "chain": "base",
        "range": {"from": "2026-06-01T00:00:00Z", "to": "2026-07-01T23:59:59Z"},
        "direction": "outbound",
        "totals": {"usdc": "12.2910", "tx_count": 454, "unique_recipients": 4, "identified_pct": 99.9},
        "groups": [{"label": "anchor-x402", "recipient": "0x127462e296fac1a7f5cf33ba57bb2f0fff5cd0b6",
                    "service_id": "anchor-x402.com", "usdc": "8.8880", "tx_count": 231,
                    "avg_call_price": "0.0384", "first_tx": "2026-06-01T04:11:22Z", "last_tx": "2026-07-01T20:03:14Z"}],
        "daily": [{"date": "2026-06-01", "usdc": "0.4110", "tx_count": 17}],
        "granularity": "day",
        "registry_version": "2026-07-15",
        "spec_version": "1.0",
    }),
)

_ledger_report_bazaar_ext = declare_discovery_extension(
    input={"wallet": "0x7818cB9cEad1E13E64A259F0867089dB75E374c5", "from": "2026-04-01", "to": "2026-06-30",
           "title": "Q2 agent spend"},
    input_schema={
        "properties": {
            "wallet": {"type": "string", "description": "EVM address (Base) whose x402 spend to report on."},
            "from": {"type": "string", "description": "ISO date, start of range. Default: 30 days before `to`."},
            "to": {"type": "string", "description": "ISO date, end of range. Default: now."},
            "direction": {"type": "string", "enum": ["outbound", "inbound", "both"]},
            "format": {"type": "string", "enum": ["markdown", "csv", "both"], "description": "Which report files to produce. Default both."},
            "title": {"type": "string", "description": "Report title for the markdown header."},
            "prepared_for": {"type": "string", "description": "Optional client name for the report header."},
        },
        "required": ["wallet"],
    },
    body_type="json",
    output=OutputConfig(example={
        "job_id": "8f14e45f-ea9d-4a4c-b8be-1c1c672f9b2d",
        "status": "accepted",
        "status_url": "https://api.anchor-x402.com/v1/ledger/report/8f14e45f-ea9d-4a4c-b8be-1c1c672f9b2d",
        "eta_seconds": 120,
    }),
)

# --- Bazaar category backfill ---
# anchor (security) and roll (gaming) set their own category at declaration.
# Everything else defaults to discoverable with no category; tag them here so
# Bazaar category-browse surfaces them. Tags are chosen for DISCOVERY VOLUME:
# each is an exact string already populated in the live CDP Bazaar taxonomy
# (so we cluster with existing sellers rather than orphan a new label), biased
# toward the larger browse pools while staying semantically accurate. `gaming`
# (roll) and `content-extraction` (tldr) are the deliberate niche-but-precise
# picks. This is independent of docs/.well-known/x402.json, which CDP does not
# read and keeps its own taxonomy.
for _ext, _cat in (
    (_screen_bazaar_ext, "security"),
    (_attest_bazaar_ext, "security"),
    (_tx_decode_bazaar_ext, "web3"),
    (_name_resolve_bazaar_ext, "web3"),
    (_token_price_bazaar_ext, "finance"),
    (_calldata_decode_bazaar_ext, "web3"),
    (_intel_wallet_bazaar_ext, "security"),
    (_investigate_bazaar_ext, "security"),
    (_datetime_parse_bazaar_ext, "ai"),
    (_roast_bazaar_ext, "ai"),
    (_oracle_bazaar_ext, "ai"),
    (_aura_bazaar_ext, "ai"),
    (_grade_bazaar_ext, "ai"),
    (_tldr_bazaar_ext, "content-extraction"),
    (_ledger_summary_bazaar_ext, "finance"),
    (_ledger_report_bazaar_ext, "finance"),
):
    _ext["bazaar"]["discoverable"] = True
    _ext["bazaar"]["category"] = _cat

x402_routes = {
    "POST /v1/anchor": RouteConfig(
        accepts=_accepts_at("$0.005"),
        description="Anchor a 32-byte hash to Base + Solana mainnet — $0.005 USDC",
        extensions={**_anchor_bazaar_ext},
    ),
    "GET /v1/screen": RouteConfig(
        accepts=_accepts_at("$0.02"),
        description="Wallet risk pre-flight for agent payments — OFAC sanctions + address-reputation (drainer/phishing/mixer) → allow/review/block verdict. $0.02 USDC.",
    ),
    "POST /v1/attest": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Attest a decision: sign (input_hash, output_hash, decision) yourself, or omit the signature and the treasury signs — then dual-chain anchor. Free re-verify at /v1/attest/verify. $0.01 USDC.",
        extensions={**_attest_bazaar_ext},
    ),
    "POST /v1/decode/tx": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Structured decode of any mainnet tx (base | ethereum | solana) — $0.001 USDC",
        extensions={**_tx_decode_bazaar_ext},
    ),
    "GET /v1/resolve/name": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Cross-chain name resolver (ENS, Bonfida SNS) — $0.001 USDC",
    ),
    "GET /v1/price/token": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="USD price for any major token by symbol or chain+contract — $0.001 USDC",
    ),
    "POST /v1/decode/calldata": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Decode raw EVM calldata into function + typed params via openchain.xyz — $0.001 USDC",
        extensions={**_calldata_decode_bazaar_ext},
    ),
    "POST /v1/parse/datetime": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Parse any freeform datetime string into a structured normalized form — $0.001 USDC",
        extensions={**_datetime_parse_bazaar_ext},
    ),
    "GET /v1/intel/wallet": RouteConfig(
        accepts=_accepts_at("$0.005"),
        description="Unified wallet intelligence bundle (balances + activity + identity + sanctions) — $0.005 USDC",
    ),
    "POST /v1/investigate": RouteConfig(
        accepts=_accepts_at("$1.77"),
        description="Agent-driven wallet due diligence — multi-step investigation, signed markdown report + JSON sidecar, dual-chain anchored. Async — returns job_id; poll /v1/investigate/status/{job_id} for the deliverable. ETA 5-10 min. $1.77 USDC.",
        extensions={**_investigate_bazaar_ext},
    ),
    "POST /v1/roast": RouteConfig(
        accepts=_accepts_at("$0.05"),
        description="Witty roast of any target — wallet, tweet, idea, code, anything. $0.05 USDC.",
        extensions={**_roast_bazaar_ext},
    ),
    "POST /v1/oracle": RouteConfig(
        accepts=_accepts_at("$0.05"),
        description="Yes/no oracle with dual-chain anchored verdict (Base + Solana). $0.05 USDC.",
        extensions={**_oracle_bazaar_ext},
    ),
    "POST /v1/tldr": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Summarize a URL or pasted text into 3-5 concise bullets. $0.01 USDC.",
        extensions={**_tldr_bazaar_ext},
    ),
    "POST /v1/aura": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Read the aura of anything — color, tier (S/A/B/C/D/F), score 0-9999, description. $0.01 USDC.",
        extensions={**_aura_bazaar_ext},
    ),
    "POST /v1/grade": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Academic letter grade with red-pen marginalia for anything. $0.01 USDC.",
        extensions={**_grade_bazaar_ext},
    ),
    "POST /v1/roll": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Verifiable RNG — cryptographically-random integer(s) signed by the treasury key. Drop-in VRF for game studios. $0.001 USDC.",
        extensions={**_roll_bazaar_ext},
    ),
    "POST /v1/ledger/summary": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="x402 spend accounting for any Base wallet — totals + per-service breakdown reconstructed from chain data. $0.01 USDC.",
        extensions={**_ledger_summary_bazaar_ext},
    ),
    "POST /v1/ledger/report": RouteConfig(
        accepts=_accepts_at("$0.35"),
        description="Signed + dual-chain-anchored x402 expense report (markdown + CSV, async job). $0.35 USDC.",
        extensions={**_ledger_report_bazaar_ext},
    ),
    # GET wrappers for function-like callers (Virtuals ACP, etc.) — same price, no
    # bazaar extensions to avoid duplicate listings (POST is the canonical entry).
    "GET /v1/anchor": RouteConfig(
        accepts=_accepts_at("$0.005"),
        description="Anchor a 32-byte hash to Base + Solana mainnet (GET wrapper) — $0.005 USDC",
    ),
    "GET /v1/attest": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Attest a decision and dual-chain anchor it (GET wrapper) — $0.01 USDC",
    ),
    "GET /v1/decode/tx": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Structured decode of any mainnet tx (GET wrapper) — $0.001 USDC",
    ),
    "GET /v1/decode/calldata": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Decode raw EVM calldata into function + typed params (GET wrapper) — $0.001 USDC",
    ),
    "GET /v1/parse/datetime": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Parse any freeform datetime string into a structured form (GET wrapper) — $0.001 USDC",
    ),
    "GET /v1/roll": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Verifiable RNG (GET wrapper) — $0.001 USDC. Query params: low, high, count, commitment, label.",
    ),
    # POST wrappers for GET-only endpoints — every crawler probe now reaches
    # the 402 challenge instead of bouncing on 405 method-mismatch.
    "POST /v1/screen": RouteConfig(
        accepts=_accepts_at("$0.02"),
        description="Wallet risk pre-flight (POST wrapper, body: {wallet}) — OFAC + address-reputation → allow/review/block. $0.02 USDC.",
        extensions={**_screen_bazaar_ext},
    ),
    "POST /v1/resolve/name": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Cross-chain name resolver (POST wrapper, body: {name}) — $0.001 USDC",
        extensions={**_name_resolve_bazaar_ext},
    ),
    "POST /v1/price/token": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="USD token price (POST wrapper, body: {symbol} or {chain, contract}) — $0.001 USDC",
        extensions={**_token_price_bazaar_ext},
    ),
    "POST /v1/intel/wallet": RouteConfig(
        accepts=_accepts_at("$0.005"),
        description="Unified wallet intelligence (POST wrapper, body: {wallet}) — $0.005 USDC",
        extensions={**_intel_wallet_bazaar_ext},
    ),
    # GET wrappers for POST-only endpoints (LLM + investigate). Same price, no
    # bazaar extensions to avoid duplicate listings.
    "GET /v1/investigate": RouteConfig(
        accepts=_accepts_at("$1.77"),
        description="Agent-driven wallet due diligence (GET wrapper, query: address) — $1.77 USDC",
    ),
    "GET /v1/roast": RouteConfig(
        accepts=_accepts_at("$0.05"),
        description="Witty roast (GET wrapper, query: target) — $0.05 USDC",
    ),
    "GET /v1/oracle": RouteConfig(
        accepts=_accepts_at("$0.05"),
        description="Yes/no oracle with anchored verdict (GET wrapper, query: question) — $0.05 USDC",
    ),
    "GET /v1/tldr": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Summarize URL or text (GET wrapper, query: url or text) — $0.01 USDC",
    ),
    "GET /v1/aura": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Aura of anything (GET wrapper, query: target) — $0.01 USDC",
    ),
    "GET /v1/grade": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Letter grade + marginalia (GET wrapper, query: target) — $0.01 USDC",
    ),
}

# --- Builder-code attribution (ERC-8021 Schema 2 via the CDP facilitator) ---
# Declare anchor's Base Builder Code on every paid route. A supporting facilitator
# (CDP) appends the ERC-8021 suffix to each settlement-tx calldata, attributing the
# volume to this app for Base.dev analytics + future fee-share. Hand-rolled to match
# the TS/Go SDK's declareBuilderCodeExtension() output (no Python helper yet); shape
# per the x402 spec specs/extensions/builder_code.md. The facilitator fills `w`
# (wallet) and the client fills `s` (service); the resource server only sets `a`.
# Verify a settled tx at https://buildercode-checker.vercel.app/.
_BUILDER_CODE = "bc_kxz79e8i"
_builder_code_ext = {
    "builder-code": {
        "info": {"a": _BUILDER_CODE},
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "a": {"type": "string", "pattern": "^[a-z0-9_]{1,32}$", "description": "App builder code"},
                "w": {"type": "string", "pattern": "^[a-z0-9_]{1,32}$", "description": "Wallet builder code"},
                "s": {"type": "array", "items": {"type": "string", "pattern": "^[a-z0-9_]{1,32}$"}, "description": "Service builder codes"},
            },
            "additionalProperties": False,
        },
    }
}
for _rc in x402_routes.values():
    _rc.extensions = {**(_rc.extensions or {}), **_builder_code_ext}


# Per-accept resource binding — echo the resource URL into each accepts[].extra
# so agents verifying signatures see the exact URL they're authorizing for each
# (network, asset) option. The top-level resource.url is already set by the
# middleware, but redundant per-accept echo helps multi-rail offerings where a
# client may sign across several networks in one challenge.
_RESOURCE_BASE = os.environ.get("PUBLIC_BASE_URL", "https://api.anchor-x402.com")
for _route_key, _cfg in x402_routes.items():
    _method, _path = _route_key.split(" ", 1)
    _resource_url = f"{_RESOURCE_BASE}{_path}"
    _accepts = _cfg.accepts if isinstance(_cfg.accepts, list) else [_cfg.accepts]
    for _opt in _accepts:
        if _opt.extra is None:
            _opt.extra = {}
        _opt.extra["resource"] = _resource_url


from services import secrets as _secrets_mod
_INTERNAL_AUTH = _secrets_mod.get("internal_auth_secret", env_fallback="INTERNAL_AUTH_SECRET")


# Bazaar service metadata. Lives on resource.{serviceName,tags,iconUrl} per
# x402-foundation/x402#2200 (merged 2026-05-06). The Python SDK landed the
# ResourceInfo schema fields in 2.11.0 but RouteConfig still has no wiring
# for them, so we inject into the challenge JSON in _inject_402_challenge_body
# below until upstream plumbs RouteConfig → ResourceInfo.
_ICON_URL = "https://anchor-x402.com/icon.png"
_RESOURCE_METADATA: dict[str, dict[str, Any]] = {
    "/v1/anchor": {
        "serviceName": "Anchor",
        "tags": ["anchor", "attestation", "merkle", "evm", "solana"],
        "iconUrl": _ICON_URL,
    },
    "/v1/screen": {
        "serviceName": "Wallet Screen",
        "tags": ["sanctions", "aml", "compliance", "wallet"],
        "iconUrl": _ICON_URL,
    },
    "/v1/attest": {
        "serviceName": "Attest",
        "tags": ["attestation", "signature", "verify", "anchor"],
        "iconUrl": _ICON_URL,
    },
    "/v1/decode/tx": {
        "serviceName": "Tx Decode",
        "tags": ["tx", "decode", "evm", "solana", "explorer"],
        "iconUrl": _ICON_URL,
    },
    "/v1/resolve/name": {
        "serviceName": "Name Resolve",
        "tags": ["ens", "sns", "resolver", "naming"],
        "iconUrl": _ICON_URL,
    },
    "/v1/price/token": {
        "serviceName": "Token Price",
        "tags": ["price", "token", "usd", "market"],
        "iconUrl": _ICON_URL,
    },
    "/v1/decode/calldata": {
        "serviceName": "Calldata Decode",
        "tags": ["calldata", "decode", "abi", "evm"],
        "iconUrl": _ICON_URL,
    },
    "/v1/parse/datetime": {
        "serviceName": "Datetime Parse",
        "tags": ["datetime", "parse", "nlp", "iso8601"],
        "iconUrl": _ICON_URL,
    },
    "/v1/intel/wallet": {
        "serviceName": "Wallet Intel",
        "tags": ["wallet", "intel", "balance", "identity", "sanctions"],
        "iconUrl": _ICON_URL,
    },
    "/v1/investigate": {
        "serviceName": "Wallet Investigate",
        "tags": ["wallet", "investigation", "due-diligence", "agent", "report"],
        "iconUrl": _ICON_URL,
    },
    "/v1/roast": {
        "serviceName": "Roast",
        "tags": ["roast", "humor", "comedy", "social"],
        "iconUrl": _ICON_URL,
    },
    "/v1/oracle": {
        "serviceName": "Oracle",
        "tags": ["oracle", "verdict", "anchor", "yes-no"],
        "iconUrl": _ICON_URL,
    },
    "/v1/tldr": {
        "serviceName": "TLDR",
        "tags": ["summary", "tldr", "url", "text", "brief"],
        "iconUrl": _ICON_URL,
    },
    "/v1/aura": {
        "serviceName": "Aura",
        "tags": ["aura", "tier", "score", "fun"],
        "iconUrl": _ICON_URL,
    },
    "/v1/grade": {
        "serviceName": "Grade",
        "tags": ["grade", "score", "feedback", "editor"],
        "iconUrl": _ICON_URL,
    },
    "/v1/roll": {
        "serviceName": "Roll",
        "tags": ["rng", "vrf", "random", "gaming", "signed"],
        "iconUrl": _ICON_URL,
    },
}


# Per-path Bazaar listing card injected into the 402 challenge body. The x402
# SDK surfaces RouteConfig.extensions only in the payment-required header, never
# as a body-level `extensions` field that strict validators (x402trace
# bazaar-check) require. Build {name, description, category} from data already
# declared, taking only the canonical route per path (the one carrying the
# bazaar extension; method wrappers carry none and are skipped).
_BAZAAR_CARD: dict[str, dict[str, str]] = {}
for _route_key, _route_cfg in x402_routes.items():
    _bz = (_route_cfg.extensions or {}).get("bazaar")
    if not _bz:
        continue
    _card_path = _route_key.split(" ", 1)[1]
    _card: dict[str, str] = {
        "name": (_RESOURCE_METADATA.get(_card_path) or {}).get("serviceName") or "anchor-x402",
    }
    if _route_cfg.description:
        _card["description"] = _route_cfg.description
    if _bz.get("category"):
        _card["category"] = _bz["category"]
    _BAZAAR_CARD[_card_path] = _card


# --- AgentCash / Poncho discoverability ---------------------------------------
# Annotate the FastAPI-generated /openapi.json with the x402 payment metadata
# AgentCash's discovery pipeline reads: per-paid-route `x-payment-info` + a 402
# response, plus top-level `info.x-guidance` and `info.contact`. Prices come from
# x402_routes so this stays in sync with the live 402 challenge — when the
# OpenAPI and runtime 402 agree, agents succeed on the first call. Helps every
# x402 agent/aggregator, not just Poncho. x402-only; USDC is what these read.
_AGENTCASH_GUIDANCE = (
    "Pay-per-call x402 services for AI agents — no API keys or accounts. Each "
    "paid route returns HTTP 402 with x402 payment requirements; pay in USDC on "
    "Base or Solana (select routes also settle JPYC on Polygon) and retry with "
    "the payment header. Prices are $0.001–$1.77 per call. POST routes take a "
    "JSON body per each operation's requestBody schema."
)
_AGENTCASH_CONTACT_EMAIL = "hypeprinter007@gmail.com"


def _route_usd_amount(route_cfg) -> str | None:
    """USD price for a route as a 6-dp string, from its first $-denominated
    accept (the Base USDC option). None if no USD price is declared."""
    for opt in route_cfg.accepts or []:
        price = getattr(opt, "price", None)
        if isinstance(price, str) and price.startswith("$"):
            try:
                return f"{float(price[1:]):.6f}"
            except ValueError:
                return None
    return None


def _agentcash_openapi():
    """Custom OpenAPI builder: FastAPI's default schema + AgentCash payment
    annotations. Cached on app.openapi_schema after first build."""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    info = schema.setdefault("info", {})
    info["x-guidance"] = _AGENTCASH_GUIDANCE
    info.setdefault("contact", {})["email"] = _AGENTCASH_CONTACT_EMAIL
    info["x-logo"] = {"url": "https://api.anchor-x402.com/icon.png", "altText": "anchor-x402"}
    paths = schema.get("paths", {})
    for route_key, route_cfg in x402_routes.items():
        method, _, path = route_key.partition(" ")
        operation = (paths.get(path) or {}).get(method.lower())
        if not operation:
            continue
        # Entries without a bazaar extension are the GET/POST wrapper twins
        # for function-like callers (Virtuals ACP, crawler probes). Keep them
        # functional but out of the agent contract: their 402s carry no
        # extensions.bazaar input/output schema, so discovery validators flag
        # them, and they'd double every listing. The canonical method stays.
        # (Every route gets the builder-code extension injected, so test for
        # the bazaar key specifically, not extension presence.)
        if "bazaar" not in (route_cfg.extensions or {}):
            del paths[path][method.lower()]
            if not paths[path]:
                del paths[path]
            continue
        amount = _route_usd_amount(route_cfg)
        if amount is not None:
            operation["x-payment-info"] = {
                "price": {"mode": "fixed", "currency": "USD", "amount": amount},
                "protocols": [{"x402": {}}],
            }
        operation.setdefault("responses", {}).setdefault(
            "402", {"description": "Payment Required"}
        )
    # Free routes (health, job-status polls): declare "no auth" explicitly so
    # discovery validators don't flag a missing auth mode.
    for path_ops in paths.values():
        for operation in path_ops.values():
            if isinstance(operation, dict) and "x-payment-info" not in operation:
                operation.setdefault("security", [])
    app.openapi_schema = schema
    return schema


app.openapi = _agentcash_openapi


def _inject_402_challenge_body(response):
    """The x402 Python SDK defaults to header-only 402 challenges: the full
    PaymentRequired JSON lands in the `payment-required` response header
    (base64-encoded) and the body is `{}`. The canonical x402 spec — and
    strict third-party tools like x402trace — expect the challenge JSON in
    the response body too. Decode the header and inject it as the JSON body
    so anchor-x402 is compatible with both surface styles.
    """
    pr_header = response.headers.get("payment-required")
    if not pr_header:
        return response
    try:
        challenge = json.loads(base64.b64decode(pr_header))
    except Exception:
        return response
    from urllib.parse import urlparse
    path = urlparse((challenge.get("resource") or {}).get("url", "")).path
    meta = _RESOURCE_METADATA.get(path)
    if meta:
        challenge.setdefault("resource", {}).update(meta)

    # Top-level extensions.bazaar listing card (see _BAZAAR_CARD). The SDK omits
    # RouteConfig extensions from the challenge body; surface them here so strict
    # validators see a complete Bazaar manifest. /v1/investigate layers extra
    # signals on top below.
    card = _BAZAAR_CARD.get(path)
    if card:
        _bz = challenge.setdefault("extensions", {}).setdefault("bazaar", {})
        for _k, _v in card.items():
            _bz.setdefault(_k, _v)

    # Buyer-confidence signal for /v1/investigate ($1.77 is high enough that
    # delivery proof + refund-on-fail materially shifts willingness to pay).
    # `delivery_stats` is the live track record; `refund_policy` documents
    # the auto-refund promise so the buyer sees it before they commit.
    challenge_mutated = bool(meta) or bool(card)
    if path == "/v1/investigate":
        from services import delivery_stats
        bazaar_ext = challenge.setdefault("extensions", {}).setdefault("bazaar", {})
        bazaar_ext["delivery_stats"] = {
            **delivery_stats.get_30d_stats(),
            "window_days": 30,
        }
        bazaar_ext["refund_policy"] = {
            "policy": "auto_refund_on_failed",
            "networks": ["eip155:8453"],
            "amount_usdc": "1.77",
            "trigger": "status=FAILED",
            "delivery": "USDC transfer treasury → buyer wallet on Base; refund_tx exposed in /v1/investigate/status response",
        }
        challenge_mutated = True

    from fastapi.responses import JSONResponse as _JSONResponse
    new_headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    if challenge_mutated:
        new_headers["payment-required"] = base64.b64encode(json.dumps(challenge).encode()).decode()
    return _JSONResponse(content=challenge, status_code=402, headers=new_headers)


def _log_402(request, kind, err=None):
    """Funnel telemetry: 402s short-circuit inside x402_mw and never reach the
    innermost _access_log, so without this line the top of the conversion
    funnel (challenges served per path) is invisible in CloudWatch. `kind`
    separates a fresh challenge from a rejected payment retry; the truncated
    user-agent separates SDK agents from crawlers. Rejected payments are the
    revenue leak, so they additionally carry payer + network (from the payment
    header) and the facilitator error — enough to classify failures per payer
    without a debugger. Best-effort, never raises."""
    try:
        entry = {
            "ts": int(time.time()),
            "host": request.headers.get("host", "").split(":")[0],
            "path": request.url.path,
            "method": request.method,
            "kind": kind,
            "ua": request.headers.get("user-agent", "")[:80],
        }
        pay_header = request.headers.get("payment-signature") or request.headers.get("x-payment")
        if pay_header:
            try:
                from services import refund as refund_svc
                payer, network = refund_svc.parse_buyer_from_x_payment(pay_header)
                if payer:
                    entry["payer"] = payer
                if network:
                    entry["network"] = network
            except Exception:
                pass
        if err:
            # 600, not 160. The facilitator's rejection body is the only place the
            # actual cause lives (invalidReason, isValid, the reverted call), and
            # 160 chars cut it off mid-field — three funded payers failed on
            # 2026-07-31 and the diagnosis had already been truncated away.
            entry["err"] = str(err)[:600]
        print("CHALLENGE " + json.dumps(entry, separators=(",", ":")))
    except Exception:
        pass


def _internal_auth_matches(request) -> bool:
    """Constant-time compare for the internal-auth bypass header. Cheap defense
    against timing oracles even though they're not practically exploitable across
    the public internet for a single string compare."""
    provided = request.headers.get("x-internal-auth", "")
    if not _INTERNAL_AUTH or not provided:
        return False
    return hmac.compare_digest(provided.encode(), _INTERNAL_AUTH.encode())


@app.middleware("http")
async def x402_mw(request, call_next):
    if _internal_auth_matches(request):
        return await call_next(request)
    try:
        response = await payment_middleware(x402_routes, x402_server)(request, call_next)
    except ValueError as e:
        # The x402 facilitator raises a bare ValueError when it REJECTS a payment
        # — bad/expired/reused authorization, or the payer's on-chain
        # transferWithAuthorization reverts (e.g. insufficient USDC). That's a
        # client payment problem, not a server fault, so it must surface as 402,
        # not 500. (Was 500ing every rejected/underfunded payer + the keepalive
        # once its wallet drained.) Unknown ValueErrors still bubble up as 500.
        msg = str(e)
        if "verify failed" in msg.lower() or "facilitator" in msg.lower():
            logging.getLogger("x402").warning("payment rejected by facilitator: %s", msg)
            _log_402(request, "payment_rejected", err=msg)
            from fastapi.responses import JSONResponse as _JSONResponse
            return _JSONResponse(
                status_code=402,
                content={
                    "error": "payment_invalid",
                    "detail": "Payment verification failed — the authorization was rejected "
                              "(insufficient balance, expired, or already used). Submit a fresh, "
                              "funded payment authorization and retry.",
                    "retry_hint": {
                        "retryable": True,
                        "action": "sign_fresh_authorization",
                        "likely_causes": [
                            "insufficient_usdc_balance",
                            "authorization_expired",
                            "authorization_already_used",
                        ],
                    },
                },
            )
        raise
    if response.status_code == 402:
        response = _inject_402_challenge_body(response)
        has_pay = request.headers.get("payment-signature") or request.headers.get("x-payment")
        _log_402(request, "payment_rejected" if has_pay else "challenge")
    return response


# HEAD → GET rewrite. Link-preview scrapers (FB, Twitter, Slack, Discord) and
# opportunistic indexers HEAD-preflight before GET; a 405 across /v1/* was
# silently 405'ing ~140 requests/day. Convert HEAD to GET at the ASGI layer
# and strip the body on the way out. x402_mw still gets to issue 402 challenge
# headers, so indexers can read prices from the payment-required header without
# paying. Registered before CORS so CORS remains outermost (see memory:
# starlette-middleware-order-last-is-outermost).
class HeadAsGetMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") == "HEAD":
            scope = {**scope, "method": "GET"}
            body_sent = False

            async def strip_body(message):
                nonlocal body_sent
                if message["type"] == "http.response.body":
                    if body_sent:
                        return
                    body_sent = True
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    return
                await send(message)

            await self.app(scope, receive, strip_body)
            return
        await self.app(scope, receive, send)


app.add_middleware(HeadAsGetMiddleware)


# Canonical-host 308 redirect. AWS API Gateway serves the same Lambda under
# both the custom domain and the raw `*.execute-api.us-east-1.amazonaws.com`
# hostname; CDP's discovery crawler was indexing both, duplicating every paid
# endpoint per host (x402trace host_pollution facet, X402-53). 308 preserves
# method + body so POST /v1/anchor on a non-canonical host redirects cleanly
# without method downgrade. Fires before HeadAsGetMiddleware (so HEAD probes
# also redirect) and before x402_mw (so no 402 challenge is emitted for the
# crawler to index). CORS remains outermost.
from urllib.parse import urlparse as _urlparse
_CANONICAL_HOST = _urlparse(_RESOURCE_BASE).hostname or "api.anchor-x402.com"
_HOST_ALLOWLIST = {
    _CANONICAL_HOST,
    "chat.anchor-x402.com",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "testserver",
}


class CanonicalHostMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        host = ""
        for k, v in scope.get("headers", []):
            if k == b"host":
                host = v.decode("latin-1").split(":")[0].lower()
                break

        if host and host not in _HOST_ALLOWLIST:
            raw_path = scope.get("raw_path") or scope.get("path", "/").encode("latin-1")
            qs = scope.get("query_string", b"")
            target = f"https://{_CANONICAL_HOST}{raw_path.decode('latin-1')}"
            if qs:
                target += "?" + qs.decode("latin-1")
            await send({
                "type": "http.response.start",
                "status": 308,
                "headers": [
                    (b"location", target.encode("latin-1")),
                    (b"content-length", b"0"),
                ],
            })
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        await self.app(scope, receive, send)


app.add_middleware(CanonicalHostMiddleware)


# CORS registered LAST so it ends up outermost in the Starlette stack — must
# wrap x402_mw so that short-circuited 402 responses still receive ACAO +
# expose-headers. Allow * origin (payment auth via X-PAYMENT replaces origin-
# based security; no cookies involved). Expose the x402 response headers so
# browser-resident agents can read 402 challenges + settle confirmations.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
    # The MCP request-metadata headers must be listed or a browser-resident MCP
    # client fails preflight before it ever reaches /mcp.
    allow_headers=["content-type", "x-payment", "payment-signature", "authorization",
                   "mcp-protocol-version", "mcp-method", "mcp-name"],
    expose_headers=["payment-response", "x-payment-response", "payment-required",
                    "payment-error"],
    max_age=86400,
)


@app.get("/health", summary="Report service health and version status")
def health():
    return {"status": "ok", "service": "anchor-x402"}


@app.post("/v1/anchor", response_model=AnchorResponse, summary="Create a dual-chain proof anchoring a 32-byte hash")
def anchor(req: AnchorRequest) -> AnchorResponse:
    merkle_root = req.merkle_root()
    started = int(time.time())
    try:
        result = anchor_svc.anchor_dual_chain(merkle_root)
    except Exception as e:
        logging.getLogger("anchor").exception("anchor failed")
        raise HTTPException(status_code=502, detail=f"anchor failed: {type(e).__name__}: {e}")

    base = ChainAnchor(
        tx=result["base_tx"],
        explorer_url=f"https://basescan.org/tx/{result['base_tx']}",
    )
    solana = None
    if result["solana_tx"]:
        solana = ChainAnchor(
            tx=result["solana_tx"],
            explorer_url=f"https://solscan.io/tx/{result['solana_tx']}",
        )
    return AnchorResponse(
        merkle_root=merkle_root,
        base=base,
        solana=solana,
        anchored_at=started,
        note=req.note,
    )


@app.get("/v1/screen", response_model=ScreenResponse)
def screen(wallet: str) -> ScreenResponse:
    verdict = screen_svc.screen(wallet)
    verdict["checked_at"] = int(time.time())
    return ScreenResponse(**verdict)


@app.post("/v1/attest", response_model=AttestResponse, summary="Create a signed, anchored receipt for an AI decision")
def attest(req: AttestRequest) -> AttestResponse:
    # Two modes on one route: bring your own signature (we verify it), or omit
    # it and the treasury signs — for wallet-less agents (e.g. calling over MCP).
    if req.signature is None:
        try:
            _, recovered = attest_svc.sign_with_treasury(req.input_hash, req.output_hash, req.decision)
        except Exception as e:
            logging.getLogger("attest").exception("treasury sign failed")
            raise HTTPException(status_code=502, detail=f"sign failed: {type(e).__name__}: {e}")
        signed_by = "treasury"
    else:
        ok, recovered = attest_svc.verify(
            scheme=req.scheme or "eip191",
            input_hash=req.input_hash,
            output_hash=req.output_hash,
            decision=req.decision,
            signature=req.signature,
            signer_pubkey=req.signer_pubkey,
        )
        if not ok:
            raise HTTPException(status_code=400, detail="signature verification failed")
        signed_by = "caller"

    merkle_root = attest_svc.attest_merkle_root(req.input_hash, req.output_hash, req.decision)
    started = int(time.time())
    base_anchor: ChainAnchor | None = None
    solana_anchor: ChainAnchor | None = None
    try:
        result = anchor_svc.anchor_dual_chain(merkle_root)
        base_anchor = ChainAnchor(
            tx=result["base_tx"],
            explorer_url=f"https://basescan.org/tx/{result['base_tx']}",
        )
        if result["solana_tx"]:
            solana_anchor = ChainAnchor(
                tx=result["solana_tx"],
                explorer_url=f"https://solscan.io/tx/{result['solana_tx']}",
            )
    except Exception as e:
        logging.getLogger("attest").exception("anchor failed")
        raise HTTPException(status_code=502, detail=f"anchor failed: {type(e).__name__}: {e}")

    return AttestResponse(
        merkle_root=merkle_root,
        signer_verified=True,
        signer=recovered,
        signed_by=signed_by,
        base=base_anchor,
        solana=solana_anchor,
        decision=req.decision,
        signed_at=started,
        verify_url=f"{_PUBLIC_BASE}/v1/attest/verify",
    )


# FREE — no x402_routes entry. Lets a relying party independently re-verify a
# receipt (signature + on-chain anchor) at no cost. A paid mint nobody can
# check for free is worthless; this is what gives the receipt value.
@app.post("/v1/attest/verify", response_model=AttestVerifyResponse, summary="Verify a previously anchored decision attestation")
def attest_verify(req: AttestVerifyRequest) -> AttestVerifyResponse:
    merkle_root = attest_svc.attest_merkle_root(req.input_hash, req.output_hash, req.decision)
    sig_ok, signer = attest_svc.verify(
        scheme=req.scheme,
        input_hash=req.input_hash,
        output_hash=req.output_hash,
        decision=req.decision,
        signature=req.signature,
        signer_pubkey=req.signer_pubkey,
    )
    anchor = attest_svc.confirm_base_anchor(req.base_tx, merkle_root)
    valid = sig_ok and anchor["confirmed"]
    if not sig_ok:
        notes = "Signature did not verify — the receipt does not bind this signer to this decision."
    elif not anchor["root_matches"]:
        notes = "Signature valid, but the Base tx does not carry this root — anchor unproven."
    elif not anchor["confirmed"]:
        notes = "Signature valid and calldata matches, but the anchor tx is not yet mined/successful."
    else:
        notes = "Verified: signature binds the signer to the decision and the root is anchored on Base."
    return AttestVerifyResponse(
        valid=valid,
        merkle_root=merkle_root,
        signature_valid=sig_ok,
        signer=signer,
        anchor_confirmed=anchor["confirmed"],
        root_matches=anchor["root_matches"],
        anchored_at_block=anchor["block"],
        anchored_at_time=anchor["anchored_at"],
        notes=notes,
    )


@app.post("/v1/decode/tx", response_model=TxDecodeResponse, summary="Parse a Base or Solana transaction into plain English")
def decode_tx(req: TxDecodeRequest) -> TxDecodeResponse:
    try:
        decoded = tx_decode_svc.decode(req.chain, req.tx_hash)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.getLogger("tx_decode").exception("decode failed")
        raise HTTPException(status_code=502, detail=f"decode failed: {type(e).__name__}: {e}")
    return TxDecodeResponse(**decoded)


@app.get("/v1/resolve/name", response_model=NameResolveResponse)
def resolve_name(name: str) -> NameResolveResponse:
    return NameResolveResponse(**name_resolve_svc.resolve(name))


@app.get("/v1/price/token", response_model=TokenPriceResponse)
def token_price(symbol: str | None = None, chain: str | None = None, contract: str | None = None) -> TokenPriceResponse:
    if symbol and (chain or contract):
        raise HTTPException(400, "supply either `symbol` or (`chain` and `contract`), not both")
    try:
        if symbol:
            data = token_price_svc.by_symbol(symbol)
        elif chain and contract:
            data = token_price_svc.by_contract(chain, contract)
        else:
            raise HTTPException(400, "supply `symbol` or (`chain` and `contract`)")
    except token_price_svc.TokenPriceError as e:
        status = {"not_found": 404, "bad_request": 400, "upstream_error": 503}.get(e.kind, 500)
        detail: dict = {"error": e.message}
        if e.supported:
            detail["supported_symbols"] = e.supported
        raise HTTPException(status, detail)
    return TokenPriceResponse(**data)


@app.post("/v1/decode/calldata", response_model=CalldataDecodeResponse, summary="Parse raw EVM calldata into a named function call")
def decode_calldata(req: CalldataDecodeRequest) -> CalldataDecodeResponse:
    if req.chain == "solana":
        raise HTTPException(status_code=400, detail="calldata-decode is EVM-only; Solana instruction decoding is not supported")
    try:
        result = calldata_decode_svc.decode_calldata(req.calldata_hex)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.getLogger("calldata_decode").exception("decode failed")
        raise HTTPException(status_code=502, detail=f"decode failed: {type(e).__name__}: {e}")
    return CalldataDecodeResponse(**result)


@app.post("/v1/parse/datetime", response_model=DatetimeParseResponse, summary="Parse natural-language dates into a normalized datetime")
def parse_datetime(req: DatetimeParseRequest) -> DatetimeParseResponse:
    try:
        result = datetime_parse_svc.parse_datetime(req.input, base_time=req.base_time, timezone_name=req.timezone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return DatetimeParseResponse(**result)


@app.get("/v1/intel/wallet", response_model=IntelWalletResponse)
def intel_wallet(wallet: str) -> IntelWalletResponse:
    return IntelWalletResponse(**intel_wallet_svc.fetch(wallet))


# POST wrappers for GET-only endpoints — discovery-aware crawlers probe with
# POST first; without these we bounce them at 405 before x402 can introduce
# itself. Now every probe reaches the 402 challenge.
from pydantic import BaseModel as _BM, Field as _F


# Field descriptions are load-bearing, not decoration: these models are the
# source for both the OpenAPI schema and the MCP tool inputSchema, so a bare
# `wallet: str` tells a calling agent nothing about which address formats work.
class _WalletBody(_BM):
    wallet: str = _F(..., description="EVM 0x… address (40 hex) or Solana base58 pubkey.")


class _NameBody(_BM):
    name: str = _F(..., description="Human-readable name, e.g. 'vitalik.eth' or 'bonfida.sol'. "
                                    "ENS (.eth) and Bonfida SNS (.sol) are supported.")


class _TokenPriceBody(_BM):
    symbol: str | None = _F(
        None, description="Token symbol like 'ETH', 'BTC', 'SOL', 'USDC'. "
                          "Mutually exclusive with chain+contract.")
    chain: str | None = _F(
        None, description="Chain slug: base, ethereum, solana, polygon, arbitrum, optimism, "
                          "bsc, avalanche. Use with contract.")
    contract: str | None = _F(None, description="Token contract address. Requires chain.")


@app.post("/v1/screen", response_model=ScreenResponse, summary="Check a wallet address for sanctions and risk")
def screen_post(req: _WalletBody) -> ScreenResponse:
    return screen(req.wallet)


@app.post("/v1/resolve/name", response_model=NameResolveResponse, summary="Resolve an ENS or Solana name to a wallet address")
def resolve_name_post(req: _NameBody) -> NameResolveResponse:
    return resolve_name(req.name)


@app.post("/v1/price/token", response_model=TokenPriceResponse, summary="Look up a token's current price in US dollars")
def token_price_post(req: _TokenPriceBody) -> TokenPriceResponse:
    return token_price(symbol=req.symbol, chain=req.chain, contract=req.contract)


@app.post("/v1/intel/wallet", response_model=IntelWalletResponse, summary="Fetch bundled wallet sanctions, balance, and activity data")
def intel_wallet_post(req: _WalletBody) -> IntelWalletResponse:
    return intel_wallet(req.wallet)


# GET wrappers for the 5 POST endpoints — for function-like callers (Virtuals ACP
# Resource offerings, MCP-via-URL, agents that compose URLs from query params).
# Same x402 pricing, same response shape; just accepts inputs via query string.

@app.get("/v1/anchor", response_model=AnchorResponse)
def anchor_get(hash: str, note: str | None = None) -> AnchorResponse:
    return anchor(AnchorRequest(hash=hash, note=note))


@app.get("/v1/attest", response_model=AttestResponse)
def attest_get(
    input_hash: str,
    output_hash: str,
    decision: str,
    scheme: str,
    signature: str,
    signer_pubkey: str | None = None,
) -> AttestResponse:
    return attest(AttestRequest(
        input_hash=input_hash,
        output_hash=output_hash,
        decision=decision,
        scheme=scheme,
        signature=signature,
        signer_pubkey=signer_pubkey,
    ))


@app.get("/v1/decode/tx", response_model=TxDecodeResponse)
def decode_tx_get(chain: str, tx_hash: str) -> TxDecodeResponse:
    return decode_tx(TxDecodeRequest(chain=chain, tx_hash=tx_hash))


@app.get("/v1/decode/calldata", response_model=CalldataDecodeResponse)
def decode_calldata_get(chain: str, calldata_hex: str) -> CalldataDecodeResponse:
    return decode_calldata(CalldataDecodeRequest(chain=chain, calldata_hex=calldata_hex))


@app.get("/v1/parse/datetime", response_model=DatetimeParseResponse)
def parse_datetime_get(
    input: str,
    timezone: str = "UTC",
    base_time: str | None = None,
) -> DatetimeParseResponse:
    return parse_datetime(DatetimeParseRequest(
        input=input,
        timezone=timezone,
        base_time=base_time,
    ))


# --- /v1/investigate (async shim → risk-investigator) -----------------------
#
# The orchestrator lives in a private repo (github.com/hypeprinter007-stack/
# risk-investigator) and runs on AWS Bedrock AgentCore Runtime. This shim
# accepts $1.77 USDC, writes job_id to DynamoDB, async-invokes the worker
# Lambda, and returns the job_id immediately. Buyer polls /status until ready.

from uuid import uuid4

import boto3

from models import (
    InvestigateAcceptedResponse,
    InvestigateDeliverable,
    InvestigateRequest,
    InvestigateStatusResponse,
)

_WORKER_FN = os.environ.get("INVESTIGATOR_WORKER_FUNCTION_NAME", "risk-investigator-worker")
_JOBS_TABLE = os.environ.get("INVESTIGATOR_JOBS_TABLE", "risk-investigator-jobs")
_PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "https://api.anchor-x402.com")

_lambda = boto3.client("lambda")
_ddb = boto3.resource("dynamodb").Table(_JOBS_TABLE)


@app.post("/v1/investigate", response_model=InvestigateAcceptedResponse, summary="Create an agent-driven wallet due-diligence investigation")
def investigate_dispatch(req: InvestigateRequest, request: Request) -> InvestigateAcceptedResponse:
    """Accept payment, record job, async-dispatch to private worker."""
    from services import refund as refund_svc
    job_id = str(uuid4())
    now = int(time.time())
    buyer_wallet, buyer_network = refund_svc.parse_buyer_from_x_payment(
        request.headers.get("payment-signature") or request.headers.get("x-payment")
    )
    try:
        item: dict[str, Any] = {
            "job_id": job_id,
            "address": req.address,
            "status": "DISPATCHING",
            "source": "x402",
            "created_at": now,
            "updated_at": now,
        }
        # Captured at dispatch time so the refund path (status-poll or daily
        # cron) has a destination for FAILED-job auto-refunds. Internal-auth
        # bypass calls have no X-PAYMENT and skip this — they aren't paid jobs.
        if buyer_wallet:
            item["buyer_wallet"] = buyer_wallet
        if buyer_network:
            item["buyer_network"] = buyer_network
        _ddb.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(job_id)",
        )
    except Exception as e:  # noqa: BLE001
        logging.getLogger("investigate").exception("DDB write failed")
        raise HTTPException(status_code=502, detail=f"job init failed: {type(e).__name__}")

    payload = {
        "job_id": job_id,
        "address": req.address,
        "requester": "x402",
        "source": "x402",
    }
    # Deliver-or-die: the x402 payment is already settled by the time we get
    # here, so a transient lambda invoke failure must not leave the buyer paid
    # but undelivered. Retry with exponential backoff before marking FAILED.
    # Most observed failures are network blips or throttling, both transient.
    _DISPATCH_BACKOFF_S = [0.5, 1.0, 2.0, 4.0, 8.0]  # 5 attempts, ~15.5s worst case
    last_err: Exception | None = None
    for attempt, delay in enumerate(_DISPATCH_BACKOFF_S):
        try:
            _lambda.invoke(
                FunctionName=_WORKER_FN,
                InvocationType="Event",
                Payload=json.dumps(payload).encode(),
            )
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            logging.getLogger("investigate").warning(
                "dispatch attempt %d/%d failed: %s — retrying in %.1fs",
                attempt + 1, len(_DISPATCH_BACKOFF_S), type(e).__name__, delay,
            )
            if attempt < len(_DISPATCH_BACKOFF_S) - 1:
                time.sleep(delay)

    if last_err is not None:
        logging.getLogger("investigate").exception("worker dispatch exhausted retries")
        # Mark for buyer visibility so /status returns FAILED rather than hanging
        try:
            _ddb.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :s, error_msg = :e",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "FAILED", ":e": f"dispatch failed after {len(_DISPATCH_BACKOFF_S)} retries: {type(last_err).__name__}"},
            )
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"dispatch failed: {type(last_err).__name__}")

    return InvestigateAcceptedResponse(
        job_id=job_id,
        status="accepted",
        status_url=f"{_PUBLIC_BASE}/v1/investigate/status/{job_id}",
        eta_seconds=600,
    )


@app.get("/v1/investigate/status/{job_id}", response_model=InvestigateStatusResponse, summary="Check an investigation's status and fetch its report")
def investigate_status(job_id: str) -> InvestigateStatusResponse:
    """Poll endpoint — free, no x402 (deliberately omitted from x402_routes)."""
    try:
        resp = _ddb.get_item(Key={"job_id": job_id})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"lookup failed: {type(e).__name__}: {e}")

    item = resp.get("Item")
    if not item:
        return InvestigateStatusResponse(job_id=job_id, status="UNKNOWN")

    status = item.get("status", "UNKNOWN")

    # Refund-on-poll fast path. If the job FAILED and we haven't yet refunded,
    # do it now so the buyer's status check is also their refund. The daily
    # refund_cron is the backstop for buyers who never poll.
    if status == "FAILED" and not item.get("refund_tx") and not item.get("refund_pending"):
        try:
            from services import refund as refund_svc
            result = refund_svc.refund_failed_job(job_id)
            # Re-read to pick up the refund_tx that refund_failed_job just wrote.
            item = _ddb.get_item(Key={"job_id": job_id}).get("Item") or item
            logging.getLogger("investigate").info("inline refund for job=%s: %s", job_id, result)
        except Exception:
            logging.getLogger("investigate").exception("inline refund failed for job=%s", job_id)

    deliverable = item.get("deliverable")
    return InvestigateStatusResponse(
        job_id=job_id,
        status=status,
        deliverable=InvestigateDeliverable(**deliverable) if deliverable else None,
        eta_seconds=None if status in ("DELIVERED", "FAILED") else 600,
        error=item.get("error_msg") or item.get("error"),
        refund_tx=item.get("refund_tx"),
        refund_pending=item.get("refund_pending"),
    )


# --- /v1/ledger (x402 spend accounting) --------------------------------------
#
# Stateless: every call reconstructs from chain data + the versioned registry
# bundled at data/x402_registry.json (regenerate with scripts/build-registry.mjs
# before deploys). The async report reuses the investigate job store; the
# rendered files land in S3 and are served back through /reports/ledger/.

from models import (
    LedgerReportAccepted,
    LedgerReportRequest,
    LedgerReportStatus,
    LedgerSummaryRequest,
)
from services import ledger as ledger_svc

_LEDGER_SYNC_MAX_DAYS = 120  # longer sync scans risk the 29s API Gateway cap


@app.exception_handler(ledger_svc.LedgerError)
def _ledger_error_handler(request: Request, exc: ledger_svc.LedgerError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


@app.post("/v1/ledger/summary", summary="Summarize a wallet's ledger into accounting totals")
def ledger_summary(req: LedgerSummaryRequest) -> dict:
    """Categorized x402 spend for a wallet, computed at request time."""
    wallet = ledger_svc.validate_wallet(req.wallet)
    from_ts, to_ts = ledger_svc.parse_range(req.from_, req.to)
    if to_ts - from_ts > _LEDGER_SYNC_MAX_DAYS * 86400:
        raise ledger_svc.LedgerError(
            "range_too_long",
            f"sync summary caps at {_LEDGER_SYNC_MAX_DAYS} days; use POST /v1/ledger/report (async) for longer ranges",
            status=422,
        )
    scanned = ledger_svc.scan(
        wallet, from_ts, to_ts, req.direction,
        str(req.min_amount) if req.min_amount is not None else None,
        req.include_unfiltered,
        max_inspections=1_000,  # sync budget; the async report allows 5x
    )
    out = ledger_svc.summarize(scanned, wallet, from_ts, to_ts, req.direction, req.group_by)
    out["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return out


@app.post("/v1/ledger/report", response_model=LedgerReportAccepted, summary="Create a full accounting report job for a wallet ledger")
def ledger_report_dispatch(req: LedgerReportRequest, request: Request) -> LedgerReportAccepted:
    """Accept payment, record job, async-dispatch the report build to this
    same Lambda (self-invoke — see the ledger_job branch in handler())."""
    from services import refund as refund_svc
    wallet = ledger_svc.validate_wallet(req.wallet)
    from_ts, to_ts = ledger_svc.parse_range(req.from_, req.to)
    job_id = str(uuid4())
    now = int(time.time())
    buyer_wallet, buyer_network = refund_svc.parse_buyer_from_x_payment(
        request.headers.get("payment-signature") or request.headers.get("x-payment")
    )
    item: dict[str, Any] = {
        "job_id": job_id,
        "kind": "ledger_report",
        "address": wallet,
        "status": "DISPATCHING",
        "source": "x402",
        "price_atomic": 350_000,  # $0.35 — read by the job-aware refund path
        "created_at": now,
        "updated_at": now,
    }
    if buyer_wallet:
        item["buyer_wallet"] = buyer_wallet
    if buyer_network:
        item["buyer_network"] = buyer_network
    try:
        _ddb.put_item(Item=item, ConditionExpression="attribute_not_exists(job_id)")
    except Exception as e:  # noqa: BLE001
        logging.getLogger("ledger").exception("DDB write failed")
        raise HTTPException(status_code=502, detail=f"job init failed: {type(e).__name__}")

    payload = {
        "ledger_job": True,
        "job_id": job_id,
        "params": {
            "wallet": wallet,
            "from_ts": from_ts,
            "to_ts": to_ts,
            "direction": req.direction,
            "min_amount": str(req.min_amount) if req.min_amount is not None else None,
            "include_unfiltered": req.include_unfiltered,
            "group_by": req.group_by,
            "format": req.format,
            "title": req.title,
            "prepared_for": req.prepared_for,
        },
    }
    # Same deliver-or-die contract as /v1/investigate: payment settled, so
    # retry transient invoke failures before marking FAILED (which refunds).
    last_err: Exception | None = None
    for attempt, delay in enumerate([0.5, 1.0, 2.0, 4.0, 8.0]):
        try:
            _lambda.invoke(
                FunctionName=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", ""),
                InvocationType="Event",
                Payload=json.dumps(payload).encode(),
            )
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(delay)
    if last_err is not None:
        logging.getLogger("ledger").exception("ledger report dispatch exhausted retries")
        try:
            _ddb.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :s, error_msg = :e",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "FAILED", ":e": f"dispatch failed: {type(last_err).__name__}"},
            )
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"dispatch failed: {type(last_err).__name__}")

    return LedgerReportAccepted(
        job_id=job_id,
        status="accepted",
        status_url=f"{_PUBLIC_BASE}/v1/ledger/report/{job_id}",
        eta_seconds=120,
    )


@app.get("/v1/ledger/report/{job_id}", response_model=LedgerReportStatus, summary="Check a ledger report job and fetch the finished report")
def ledger_report_status(job_id: str) -> LedgerReportStatus:
    """Poll endpoint — free, no x402 (deliberately omitted from x402_routes)."""
    try:
        resp = _ddb.get_item(Key={"job_id": job_id})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"lookup failed: {type(e).__name__}")

    item = resp.get("Item")
    if not item or item.get("kind") != "ledger_report":
        return LedgerReportStatus(job_id=job_id, status="UNKNOWN", error="job_not_found")

    status = item.get("status", "UNKNOWN")
    # Refund-on-poll fast path, same as investigate; refund.py reads the
    # job's price_atomic so a $0.35 job refunds $0.35, not the $1.77 default.
    if status == "FAILED" and not item.get("refund_tx") and not item.get("refund_pending"):
        try:
            from services import refund as refund_svc
            refund_svc.refund_failed_job(job_id)
            item = _ddb.get_item(Key={"job_id": job_id}).get("Item") or item
        except Exception:
            logging.getLogger("ledger").exception("inline refund failed for job=%s", job_id)

    return LedgerReportStatus(
        job_id=job_id,
        status=status,
        deliverable=item.get("deliverable"),
        eta_seconds=None if status in ("DELIVERED", "FAILED") else 120,
        error=item.get("error_msg"),
        refund_tx=item.get("refund_tx"),
        refund_pending=item.get("refund_pending"),
    )


@app.get("/reports/ledger/{filename}", include_in_schema=False)
def ledger_report_file(filename: str) -> Response:
    """Serve rendered report files from S3. Unguessable job-id URLs,
    immutable once written."""
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(md|csv)", filename):
        raise HTTPException(status_code=404, detail="not found")
    import boto3 as _boto3
    try:
        obj = _boto3.client("s3").get_object(
            Bucket=ledger_svc.REPORTS_BUCKET, Key=f"ledger/{filename}"
        )
        body = obj["Body"].read()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="report not found")
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8" if filename.endswith(".md") else "text/csv; charset=utf-8",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# --- /v1/roast | /v1/oracle | /v1/tldr (universal LLM-paid endpoints) -------


@app.post("/v1/roast", response_model=RoastResponse, summary="Generate a comic roast of a wallet's trading history")
def roast(req: RoastRequest) -> RoastResponse:
    try:
        result = roast_svc.roast(req.target)
    except Exception as e:
        logging.getLogger("roast").exception("roast failed")
        raise HTTPException(status_code=502, detail=f"roast failed: {type(e).__name__}: {e}")
    return RoastResponse(**result)


@app.post("/v1/oracle", response_model=OracleResponse, summary="Generate a signed oracle verdict for a question")
def oracle(req: OracleRequest) -> OracleResponse:
    try:
        result = oracle_svc.oracle(req.question)
    except Exception as e:
        logging.getLogger("oracle").exception("oracle failed")
        raise HTTPException(status_code=502, detail=f"oracle failed: {type(e).__name__}: {e}")
    return OracleResponse(**result)


@app.post("/v1/tldr", response_model=TldrResponse, summary="Summarize long text into a short TL;DR")
def tldr(req: TldrRequest) -> TldrResponse:
    try:
        result = tldr_svc.tldr(text=req.text, url=req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.getLogger("tldr").exception("tldr failed")
        raise HTTPException(status_code=502, detail=f"tldr failed: {type(e).__name__}: {e}")
    return TldrResponse(**result)


@app.post("/v1/aura", response_model=AuraResponse, summary="Read a wallet's aura color, tier, and score")
def aura(req: AuraRequest) -> AuraResponse:
    try:
        result = aura_svc.aura(req.target)
    except Exception as e:
        logging.getLogger("aura").exception("aura failed")
        raise HTTPException(status_code=502, detail=f"aura failed: {type(e).__name__}: {e}")
    return AuraResponse(**result)


@app.post("/v1/grade", response_model=GradeResponse, summary="Generate a letter grade and margin notes for text")
def grade(req: GradeRequest) -> GradeResponse:
    try:
        result = grade_svc.grade(req.target)
    except Exception as e:
        logging.getLogger("grade").exception("grade failed")
        raise HTTPException(status_code=502, detail=f"grade failed: {type(e).__name__}: {e}")
    return GradeResponse(**result)


# GET wrappers for the LLM endpoints + investigate. Same x402 pricing,
# same response shape; accepts inputs via query string so crawler probes
# reach the 402 challenge regardless of method preference.

@app.get("/v1/investigate", response_model=InvestigateAcceptedResponse)
def investigate_dispatch_get(address: str, request: Request) -> InvestigateAcceptedResponse:
    return investigate_dispatch(InvestigateRequest(address=address), request)


@app.get("/v1/roast", response_model=RoastResponse)
def roast_get(target: str) -> RoastResponse:
    return roast(RoastRequest(target=target))


@app.get("/v1/oracle", response_model=OracleResponse)
def oracle_get(question: str) -> OracleResponse:
    return oracle(OracleRequest(question=question))


@app.get("/v1/tldr", response_model=TldrResponse)
def tldr_get(text: str | None = None, url: str | None = None) -> TldrResponse:
    return tldr(TldrRequest(text=text, url=url))


@app.get("/v1/aura", response_model=AuraResponse)
def aura_get(target: str) -> AuraResponse:
    return aura(AuraRequest(target=target))


@app.get("/v1/grade", response_model=GradeResponse)
def grade_get(target: str) -> GradeResponse:
    return grade(GradeRequest(target=target))


@app.post("/v1/roll", response_model=RollResponse, summary="Generate verifiable dice rolls with a signed result")
def roll(req: RollRequest) -> RollResponse:
    try:
        result = roll_svc.generate(
            low=req.low, high=req.high, count=req.count,
            commitment=req.commitment, label=req.label,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.getLogger("roll").exception("roll failed")
        raise HTTPException(status_code=502, detail=f"roll failed: {type(e).__name__}: {e}")
    return RollResponse(**result)


@app.get("/v1/roll", response_model=RollResponse)
def roll_get(
    low: int, high: int, count: int = 1,
    commitment: str | None = None, label: str | None = None,
) -> RollResponse:
    try:
        result = roll_svc.generate(
            low=low, high=high, count=count, commitment=commitment, label=label,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.getLogger("roll").exception("roll failed")
        raise HTTPException(status_code=502, detail=f"roll failed: {type(e).__name__}: {e}")
    return RollResponse(**result)


# --- /v1/chat (FREE, not in x402_routes) ------------------------------------
#
# Rate limit is enforced at API Gateway via Globals.HttpApi.RouteSettings in
# template.yaml — POST /v1/chat is capped at 1 RPS sustained + burst 5,
# returning 429 with Retry-After before the request ever reaches Lambda. This
# is the real cost-amplification DoS guard (Bedrock-backed, unauthenticated).


@app.post("/v1/chat", response_model=ChatResponse, include_in_schema=False)
def chat(req: ChatRequest) -> ChatResponse:
    try:
        turn = chat_svc.chat_turn(req.messages)
    except ValueError as e:
        # Abuse caps (message length, conversation length)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.getLogger("chat").exception("chat turn failed")
        raise HTTPException(status_code=502, detail=f"chat failed: {type(e).__name__}: {e}")
    return ChatResponse(**turn)


# --- /chat UI + /v1/config (free, static) -----------------------------------

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _serve_chat_html() -> FileResponse:
    path = os.path.join(_STATIC_DIR, "chat.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="chat UI not deployed")
    # Disable caching so fixes propagate immediately. The HTML is tiny (~25 KB);
    # only the (CDN-served) JS imports benefit from caching, and those are versioned.
    return FileResponse(
        path,
        media_type="text/html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _serve_icon() -> FileResponse:
    """Brand logo at the API origin. x402 directories/clients derive a service
    icon from the resource origin's favicon; without one they show a generic
    placeholder. Free, public, cached."""
    return FileResponse(
        os.path.join(_STATIC_DIR, "icon.png"),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    return _serve_icon()


@app.get("/icon.png", include_in_schema=False)
def icon_png():
    return _serve_icon()


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root(request: Request):
    """Root: chat UI on the chat.* subdomain, docs redirect on the api.* one.

    HEAD is accepted because the Facebook / Twitter / Slack / Discord link-
    preview scrapers do HEAD as a preflight before GET; if HEAD 405s they
    report the URL as unreachable and drop the preview card entirely.
    Starlette auto-strips the body for HEAD responses.
    """
    host = request.headers.get("host", "")
    if host.startswith("chat."):
        return _serve_chat_html()
    return RedirectResponse(url="/docs")


# --- Agentverse FastAPI adapter ----------------------------------------------
# Implements Fetch.ai's Chat Protocol so anchor-x402 is discoverable + invokable
# from Agentverse and ASI:One. Receives signed envelopes from Agentverse, sends
# the reply back via Agentverse's mailbox API.

def _agentverse_investigator_reply(user_text: str) -> str:
    """This Agentverse listing IS the $1.77 risk investigator. No free previews —
    the agent quotes the investigation cost + tells the caller how to invoke it."""
    return (
        "anchor-x402 risk investigator — $1.77 USDC per run.\n\n"
        "Multi-step wallet due diligence:\n"
        "• sanctions/AML screen across OFAC, Chainalysis, TRM\n"
        "• balance + activity timeline (Base + Solana mainnet)\n"
        "• identity correlation (ENS, basenames, SNS, on-chain history)\n"
        "• counterparty graph + mixer / sanctioned-chain exposure\n"
        "• final verdict + score, anchored on Base + Solana for third-party verification\n\n"
        "How to run:\n"
        "1. POST https://api.anchor-x402.com/v1/investigate\n"
        "   Body: { \"wallet\": \"<address>\" }\n"
        "   x402 USDC payment ($1.77) required\n"
        "2. Returns a job_id — poll GET /v1/jobs/{job_id} (~5–10 min)\n"
        "3. Final response includes the verdict + on-chain anchor tx\n\n"
        "Don't have an x402 client? Use the hosted chat agent that runs this tool "
        "for you from your USDC: https://chat.anchor-x402.com\n\n"
        "OpenAPI: https://api.anchor-x402.com/openapi.json"
    )


@app.get("/agentverse/status", include_in_schema=False)
def agentverse_status():
    return {"status": "ok", "agent": "anchor-x402"}


@app.post("/agentverse/chat", include_in_schema=False)
async def agentverse_chat(request: Request):
    import json as _json
    from uuid import uuid4 as _uuid4
    from uagents_core.identity import Identity
    from uagents_core.envelope import Envelope
    from uagents_core.models import Model
    from uagents_core.contrib.protocols.chat import (
        ChatMessage, TextContent, chat_protocol_spec,
    )
    from uagents_core.utils.messages import (
        parse_envelope, generate_message_envelope, send_message,
    )
    from uagents_core.utils.resolver import AlmanacResolver
    from services import secrets as _secrets

    body = await request.json()
    env = Envelope(**body)
    seed = _secrets.get("agent_seed_phrase", env_fallback="AGENT_SEED_PHRASE")
    if not seed:
        raise HTTPException(500, "agent identity not configured")
    identity = Identity.from_seed(seed, 0)

    msg = parse_envelope(env, ChatMessage)
    user_text = "".join(c.text for c in getattr(msg, "content", []) if isinstance(c, TextContent))

    reply_text = _agentverse_investigator_reply(user_text)
    reply_msg = ChatMessage(content=[TextContent(type="text", text=reply_text)])
    proto_digest = chat_protocol_spec.digest

    # Resolve where to send the reply (Agentverse's mailbox or the sender's endpoint)
    resolver = AlmanacResolver(max_endpoints=1)
    endpoints = resolver.sync_resolve(env.sender)
    if not endpoints:
        print(f"agentverse_reply no_endpoints for sender={env.sender}")
        return {"status": "no_endpoints"}

    reply_env = generate_message_envelope(
        destination=env.sender,
        message_schema_digest=Model.build_schema_digest(reply_msg),
        message_body=_json.loads(reply_msg.model_dump_json()),
        sender=identity,
        session_id=env.session or _uuid4(),
        protocol_digest=proto_digest,
    )

    delivered = False
    last_err: str | None = None
    for endpoint in endpoints:
        try:
            send_message(endpoint, reply_env, timeout=15)
            delivered = True
            print(f"agentverse_reply sent endpoint={endpoint} session={reply_env.session}")
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"agentverse_reply failed endpoint={endpoint} err={last_err}")
            continue

    return {"status": "ok" if delivered else "failed", "error": last_err}


# ── Divigent yield integration ─────────────────────────────────────────
# Lambda-native counterpart to signalfuse-divigent-router (which runs as a
# long-running Node sidecar). See services/divigent.py + services/divigent_cron.py.
# The /event/* receivers mirror SignalFuse's contract so the same dashboard
# pattern works against either integration shape.

@app.get("/divigent/dashboard", include_in_schema=False)
def divigent_dashboard():
    """Read-only snapshot of seller's Divigent position + idle USDC."""
    return divigent_svc.get_dashboard_snapshot()


_DIVIGENT_EVENT_TYPES = {
    "snapshot", "idle-deposit", "manual-deposit", "manual-withdraw",
    "sweep-failure", "non-fatal-error",
}


@app.post("/divigent/event/{event_type}", include_in_schema=False)
async def divigent_event(event_type: str, request: Request):
    """Lifecycle event sink for internal sidecars/crons. Requires x-internal-auth
    header (constant-time check via _internal_auth_matches) and an allow-listed
    event_type — was unauth previously, fixed 2026-05-26 after surface audit
    flagged log-injection + cost-amplification risk on the open path-param sink."""
    if not _internal_auth_matches(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    if event_type not in _DIVIGENT_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="unknown event_type")
    try:
        body = await request.json()
    except Exception:
        body = None
    logging.getLogger("divigent.events").info(
        "divigent_event type=%s body=%s",
        event_type,
        json.dumps(body) if body is not None else "{}",
    )
    return Response(status_code=204)


@app.post("/internal/refund/{job_id}", include_in_schema=False)
def internal_refund(job_id: str, request: Request):
    """Push-refund webhook for the worker. Called when the worker writes
    status=FAILED, so the buyer wallet gets refunded within seconds instead of
    waiting on a buyer-side poll or the daily backstop cron. Idempotent via
    refund_failed_job's existing refund_tx check."""
    if not _internal_auth_matches(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    from services import refund as refund_svc
    try:
        result = refund_svc.refund_failed_job(job_id)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("refund").exception("internal_refund failed job=%s", job_id)
        raise HTTPException(status_code=502, detail=f"refund failed: {type(e).__name__}")
    return result


@app.get("/chat", include_in_schema=False)
def chat_ui():
    return _serve_chat_html()


@app.get("/.well-known/farcaster.json", include_in_schema=False)
def farcaster_manifest():
    path = os.path.join(_STATIC_DIR, "farcaster.json")
    return FileResponse(
        path,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


_X402_DISCOVERY_PATH = os.path.join(
    os.path.dirname(__file__), "docs", ".well-known", "x402.json"
)


def _serve_x402_discovery() -> FileResponse:
    """x402 discovery doc — same content the apex site serves at /.well-known/x402.json.
    Lives on the API host too so origin-scoped crawlers (Bazaar, x402.direct) find it."""
    if not os.path.exists(_X402_DISCOVERY_PATH):
        raise HTTPException(status_code=404, detail="x402 discovery doc not bundled")
    return FileResponse(
        _X402_DISCOVERY_PATH,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/.well-known/x402", include_in_schema=False)
def x402_discovery():
    return _serve_x402_discovery()


@app.get("/.well-known/x402.json", include_in_schema=False)
def x402_discovery_json():
    return _serve_x402_discovery()


# Opportunistic indexer probes — neither path is in the spec, but both are
# being hit 276×/day each. Alias both to the same catalog content so we get
# free indexing upside instead of returning 404.
@app.get("/.well-known/x402-resources", include_in_schema=False)
def x402_resources_wellknown():
    return _serve_x402_discovery()


@app.get("/x402-resources", include_in_schema=False)
def x402_resources():
    return _serve_x402_discovery()


_DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")


# ---------------------------------------------------------------------------
# Conventional aliases. Seven days of 404s showed agents reaching for things we
# already serve, just at the paths their ecosystem expects: /status (174 hits),
# /.well-known/agent.json (136), /.well-known/agent-card without the suffix (48),
# /a2a (29), /llms-full.txt (8). Every one of those was a client that found us and
# then failed to find the document. Aliasing costs nothing and converts the miss.
#
# Deliberately NOT aliased: /agents.json and /apis.json name different formats we
# do not implement, and /.well-known/mcp.json implies an HTTP MCP transport we do
# not have. Answering those with our own documents would be a false claim.
# ---------------------------------------------------------------------------

@app.api_route("/status", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/.well-known/health", methods=["GET", "HEAD"], include_in_schema=False)
def status_alias():
    """The single most-requested missing path. Same payload as /health."""
    return {"status": "ok", "service": "anchor-x402"}


@app.api_route("/.well-known/agent.json", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/.well-known/agent-card", methods=["GET", "HEAD"], include_in_schema=False)
def agent_card_aliases():
    """Two spellings agents actually use for the A2A card."""
    return agent_card()


@app.api_route("/.well-known/x402-bazaar.json", methods=["GET", "HEAD"], include_in_schema=False)
def x402_bazaar_alias():
    return _serve_x402_discovery()


@app.get("/llms-full.txt", include_in_schema=False)
def llms_full_txt():
    """The llmstxt convention's long-form name; ours is already the full document."""
    return llms_txt()


@app.api_route("/a2a", methods=["POST", "GET", "HEAD"], include_in_schema=False)
def a2a_alias(request: Request):
    """308 preserves method and body, so a JSON-RPC POST to /a2a reaches the real
    endpoint intact rather than being downgraded to GET."""
    return RedirectResponse(url=f"{_RESOURCE_BASE}/v1/a2a", status_code=308)


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    path = os.path.join(_DOCS_DIR, "robots.txt")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="robots.txt not bundled")
    return FileResponse(
        path,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    path = os.path.join(_DOCS_DIR, "llms.txt")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="llms.txt not bundled")
    return FileResponse(
        path,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# Agent-to-agent door — POST /v1/a2a, signed JSON-RPC 2.0.
#
# Free and side-effect-free by design: identity, capability discovery, quoting,
# receipts. Money never moves through this route. A quote points the peer at the
# metered /v1/* endpoint it should pay, so there stays exactly one payment code
# path (x402_mw) and an unpaid door can never yield paid work. Trust model,
# replay defense, and the SSRF constraints on peer origins live in
# services/a2a.py.
# ---------------------------------------------------------------------------

_AGENT_CARD_PATH = os.path.join(_DOCS_DIR, ".well-known", "agent-card.json")


_AGENT_REGISTRATION_PATH = os.path.join(_DOCS_DIR, ".well-known", "agent-registration.json")


@app.api_route("/.well-known/agent-registration.json", methods=["GET", "HEAD"],
               include_in_schema=False)
def agent_registration():
    """ERC-8004 registration file, proving this domain controls agentId 60138.

    Hosting it is what flips domainVerified on the registry: the on-chain NFT
    claims a domain, and this file served from that domain claims the agentId
    back. That two-way binding is the out-of-band key distribution the agent
    card's own signature otherwise lacks — a client that trusts the registry can
    reach our card through a path we cannot forge alone.
    """
    if not os.path.exists(_AGENT_REGISTRATION_PATH):
        raise HTTPException(status_code=404, detail="agent registration file not bundled")
    return FileResponse(
        _AGENT_REGISTRATION_PATH,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.api_route("/.well-known/agent-card.json", methods=["GET", "HEAD"], include_in_schema=False)
def agent_card():
    """The A2A card on the API origin, not just the apex site. A peer that
    discovered a paid endpoint here can now resolve our signing key from the
    same host it is already talking to — no human telling it which of our two
    domains publishes the card."""
    if not os.path.exists(_AGENT_CARD_PATH):
        raise HTTPException(status_code=404, detail="agent card not bundled")
    return FileResponse(
        _AGENT_CARD_PATH,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


def _a2a_log(method: str, peer: str, ok: bool, detail: str = "", ua: str = "") -> None:
    """Same shape as the CHALLENGE / PAID_CALL funnel lines so peer traffic is
    greppable in CloudWatch without a new tool. Failures carry their own
    `A2A_FAIL` prefix: a CloudWatch JSON filter (`{ $.ok IS FALSE }`) cannot
    parse a prefixed line, and matching an embedded `"ok":false` needs escaped
    quotes in the filter pattern — a distinct prefix keeps the metric filter in
    template.yaml a plain substring match. Best-effort, never raises."""
    try:
        entry = {
            "ts": int(time.time()),
            "method": a2a_svc.log_safe(method, 32),
            "peer": a2a_svc.log_safe(peer, 120),
            "ok": ok,
        }
        if ua:
            # A peer's declared origin is self-asserted until verified; the
            # user-agent is independent evidence of what software is calling.
            entry["ua"] = a2a_svc.log_safe(ua, 120)
        if detail:
            # Our own strings, but several interpolate peer-supplied values.
            # Sanitize at the sink rather than auditing every interpolation site.
            entry["detail"] = a2a_svc.log_safe(detail, 160)
        print(("A2A " if ok else "A2A_FAIL ") + json.dumps(entry, separators=(",", ":")))
    except Exception:
        pass


def _a2a_skill(skill_id: Any) -> dict[str, Any]:
    for skill in a2a_svc.our_card().get("skills", []):
        if skill.get("id") == skill_id:
            return skill
    raise a2a_svc.A2AError(
        a2a_svc.ERR_NOT_FOUND,
        f"no skill '{a2a_svc.safe_echo(skill_id)}' — call capabilities/list for the current set",
    )


def _a2a_hello(origin: str, body: Any) -> dict[str, Any]:
    card = a2a_svc.our_card()
    return {
        "agent": card.get("name"),
        "peer_verified": origin,
        "card_url": f"{_RESOURCE_BASE}/.well-known/agent-card.json",
        "endpoint": f"{_RESOURCE_BASE}/v1/a2a",
        "namespace": a2a_svc.NAMESPACE,
        "key_id": a2a_svc.active_key_id(),
        # Full set, so a peer can verify a signature made by a key we have
        # since rotated away from without re-fetching the card.
        "keys": a2a_svc.our_keys(),
        "methods": list(a2a_svc.METHODS),
        "signature": {
            "algorithm": "ed25519",
            "digest": "sha256-canonical-json",
            "signed_fields": ["body", "exp", "method", "nonce", "origin"],
        },
        "policy_digest": a2a_svc.digest_of(a2a_svc.policy()),
        "x402": (card.get("extensions") or {}).get("anchor-x402:x402", {}),
    }


def _a2a_capabilities(origin: str, body: Any) -> dict[str, Any]:
    card = a2a_svc.our_card()
    skills = card.get("skills", [])
    return {
        "count": len(skills),
        "skills": skills,
        "x402": (card.get("extensions") or {}).get("anchor-x402:x402", {}),
        "payment_is_authorization": True,
    }


def _a2a_route_price(skill: dict[str, Any]) -> float:
    """Price from x402_routes — the table that actually charges — not from the
    agent card. The card is a published catalog and can drift; quoting from it
    means signing a price we might not charge. The card stays the source for
    discovery, and scripts/test_a2a.py asserts the two agree."""
    # urlparse, not a string replace against _RESOURCE_BASE: the card hardcodes
    # https://api.anchor-x402.com/... so any PublicBaseUrl override (staging host,
    # trailing slash, empty) made the replace a no-op and every quote failed while
    # capabilities/list kept advertising the skill.
    path = _urlparse(str(skill.get("url", ""))).path
    cfg = x402_routes.get(f"{skill.get('method')} {path}")
    # A route carries one PaymentOption per rail. Collect all USD-denominated
    # ones instead of trusting the first: if the rails ever disagree, quoting
    # whichever happens to be listed first would sign a price we may not charge.
    prices = {
        float(getattr(opt, "price")[1:])
        for opt in (getattr(cfg, "accepts", None) or [])
        if isinstance(getattr(opt, "price", None), str) and getattr(opt, "price").startswith("$")
    }
    if not prices:
        raise a2a_svc.A2AError(
            a2a_svc.ERR_NOT_FOUND,
            f"skill {a2a_svc.safe_echo(skill.get('id'))} has no payable route price",
        )
    if len(prices) > 1:
        logging.getLogger("anchor.a2a").error(
            "route %s prices disagree across rails: %s — quoting the highest",
            path, sorted(prices),
        )
    return max(prices)


def _a2a_quote(origin: str, body: Any, *, key_id: str, nonce: str) -> dict[str, Any]:
    skill = _a2a_skill((body or {}).get("skill") if isinstance(body, dict) else None)
    price_usd = _a2a_route_price(skill)
    if price_usd != skill.get("price_usd"):
        logging.getLogger("anchor.a2a").error(
            "price drift: card says %s, route charges %s for %s",
            skill.get("price_usd"), price_usd, skill.get("id"),
        )
    pol = a2a_svc.inbound_policy()
    now = int(time.time())
    # Derived from the peer's single-use nonce plus our own timestamp. The
    # timestamp is what stops receipt aliasing: nonce uniqueness expires with the
    # nonce record, but receipts live far longer, so a nonce-only derivation let a
    # peer reusing a counter value much later collide with an old exchange and be
    # handed the *previous* exchange's receipt.
    exchange_id = "ax-" + a2a_svc.digest_of(
        {"nonce": nonce, "origin": origin, "quoted_at": now, "skill": skill["id"]}
    ).split(":")[1][:24]
    quote = {
        "type": a2a_svc.TYPE_QUOTE,
        "exchange_id": exchange_id,
        "peer": origin,
        "peer_key_id": key_id,
        "skill": skill["id"],
        "url": skill.get("url"),
        "http_method": skill.get("method"),
        "price_usd": price_usd,
        "quoted_at": now,
        "expires_at": now + pol["quote_ttl_s"],
        "pay_with": "x402",
        "instructions": (
            "Call url directly with an x402 payment. The 402 challenge on that "
            "route carries the accepted rails and amounts. Then call peer/receipt "
            "with this exchange_id for a signed record of the exchange."
        ),
    }
    a2a_svc.put_quote(exchange_id, quote, pol["quote_ttl_s"])
    return a2a_svc.sign(quote)


def _a2a_spec_dispatch(method: str, params: Any) -> dict[str, Any]:
    """The A2A 0.3.0 core methods. Runs in a worker thread — the store reads are
    blocking, and the endpoint handler is async."""
    if method == "agent/getAuthenticatedExtendedCard":
        # We publish no extended card, and the public card omits
        # supportsAuthenticatedExtendedCard, so refuse with the code the spec
        # reserves rather than returning the ordinary card and implying more.
        raise a2a_svc.A2AError(
            a2a_tasks.ERR_EXTENDED_CARD_NOT_CONFIGURED,
            "No authenticated extended card is configured; the public agent card is complete.",
        )
    if method == "tasks/get":
        return a2a_tasks.get_task(params)
    if method == "tasks/cancel":
        return a2a_tasks.cancel_task(params)

    # message/send
    if not isinstance(params, dict) or "message" not in params:
        raise a2a_svc.A2AError(a2a_svc.ERR_PARAMS, "params.message is required")
    # Reply in the dialect the caller spoke. A real client was speaking v1.0 —
    # no `kind` discriminators, proto enum spellings, Task wrapped in a oneof —
    # and strict 0.3.0 validation rejected it.
    version = a2a_tasks.detect_version(params["message"])
    message = a2a_tasks.validate_message(params["message"], version)

    card = a2a_svc.our_card()
    skills = card.get("skills", [])
    skill = a2a_tasks.resolve_skill(message, skills)
    if skill is None:
        return a2a_tasks.send_result(a2a_tasks.clarify_task(message, skills, version), version)

    task = a2a_tasks.payment_task(
        message, skill, _a2a_route_price(skill),
        (card.get("extensions") or {}).get("anchor-x402:x402", {}), version,
    )
    return a2a_tasks.send_result(task, version)


def _a2a_with_anchor(receipt: dict[str, Any]) -> dict[str, Any]:
    """Attach on-chain proof as a sibling of the signed payload, never inside it.

    A receipt is signed the moment it is minted; the root that covers it is
    anchored later by the daily job, so the anchor cannot be part of the signed
    bytes without invalidating the signature. Kept outside, it stays independently
    verifiable — the reader checks the root on Base or Solana, not our word.
    """
    proof = a2a_svc.get_anchored(receipt.get("digest", ""))
    if proof is None:
        return receipt
    out = dict(receipt)   # copy: the stored receipt must not gain unsigned fields
    out["anchor"] = proof
    return out


def _a2a_receipt(origin: str, body: Any) -> dict[str, Any]:
    raw_id = (body or {}).get("exchange_id") if isinstance(body, dict) else None
    if not raw_id:
        raise a2a_svc.A2AError(a2a_svc.ERR_PARAMS, "body.exchange_id is required")
    exchange_id = a2a_svc.clean_exchange_id(raw_id)

    # Exactly one receipt per exchange. Returned before re-minting so a repeat
    # call yields the identical signed artifact rather than a second, differing
    # one — dispute evidence with two valid versions is not evidence.
    existing = a2a_svc.get_receipt(exchange_id)
    if existing is not None:
        if existing.get("peer") != origin:
            raise a2a_svc.A2AError(a2a_svc.ERR_POLICY, "receipt belongs to a different peer")
        return _a2a_with_anchor(existing)

    quote = a2a_svc.get_quote(exchange_id)
    if quote is None:
        raise a2a_svc.A2AError(
            a2a_svc.ERR_NOT_FOUND,
            f"no live quote {exchange_id} — quotes expire after "
            f"{a2a_svc.inbound_policy()['quote_ttl_s']}s; request a fresh one",
        )
    if quote.get("peer") != origin:
        # Otherwise peer B could mint receipts against peer A's quotes.
        raise a2a_svc.A2AError(a2a_svc.ERR_POLICY, "quote belongs to a different peer")

    settlement = a2a_svc.clean_settlement(
        (body or {}).get("settlement") if isinstance(body, dict) else None
    )
    receipt = {
        "type": a2a_svc.TYPE_RECEIPT,
        "exchange_id": quote["exchange_id"],
        "peer": origin,
        "peer_key_id": quote.get("peer_key_id"),
        "skill": quote.get("skill"),
        "price_usd": quote.get("price_usd"),
        "quote_digest": a2a_svc.digest_of(quote),
        "quoted_at": quote.get("quoted_at"),
        "receipted_at": int(time.time()),
        "peer_asserted_settlement": settlement,
        # State plainly what this artifact is worth, so a consuming agent does
        # not over-trust it. We witnessed the quote and the peer's identity; we
        # did not independently verify their settlement reference here.
        "proves": [
            "anchor-x402 issued this quote, for this skill, at this price, to this peer",
            "the peer controls the key published in its agent card at `peer`",
            "the peer asserted the settlement reference in peer_asserted_settlement",
        ],
        "does_not_prove": [
            "on-chain settlement — resolve peer_asserted_settlement against the rail to confirm",
        ],
    }
    signed = a2a_svc.put_receipt(
        exchange_id, a2a_svc.sign(receipt), a2a_svc.inbound_policy()["receipt_ttl_s"]
    )
    # Log the identifiers and the digest, not the artifact. The full receipt
    # carries peer-supplied fields, and at the route's throttle ceiling logging
    # it whole is a meaningful CloudWatch ingest bill.
    print("A2A_RECEIPT " + json.dumps(
        {k: signed.get(k) for k in ("exchange_id", "peer", "skill", "price_usd", "digest", "signed")},
        separators=(",", ":"), default=str))
    return _a2a_with_anchor(signed)


@app.post("/v1/a2a", include_in_schema=False)
async def a2a_rpc(request: Request):
    """JSON-RPC 2.0. Application errors return HTTP 200 with an `error` object
    per the spec; only unparseable JSON is a 400."""
    try:
        envelope = json.loads(await request.body())
        if not isinstance(envelope, dict):
            raise ValueError("envelope must be an object")
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "parse error"}},
        )

    rpc_id = envelope.get("id")
    method = envelope.get("method")
    params = envelope.get("params") or {}
    # Sanitized, not merely truncated: this is pre-verification, attacker-chosen
    # text, and it reaches a log line that the CloudWatch metric filters match on
    # by substring. Unsanitized, one unauthenticated POST naming an origin that
    # contains the literal signing-failure token forges that alarm.
    peer = str(params.get("origin", ""))[:120] if isinstance(params, dict) else ""
    ua = request.headers.get("user-agent", "")

    def fail(code: int, message: str):
        _a2a_log(str(method), peer, False, message, ua)
        return JSONResponse(
            content={"jsonrpc": "2.0", "id": rpc_id,
                     "error": {"code": code, "message": message}},
        )

    # Two auth regimes on one endpoint, deliberately. The A2A spec methods are
    # unsigned because a conformant client will not produce our envelope — the
    # card declares x402 as the security scheme, and payment happens on the
    # metered route. Our own peer/* methods keep the signature requirement.
    if method in a2a_tasks.METHODS:
        try:
            # No verified origin to key a limit on, so bound by client address.
            a2a_svc.rate_check(f"ip:{(request.headers.get('x-forwarded-for','') or 'unknown').split(',')[0].strip()[:45]}")
            result = await run_in_threadpool(_a2a_spec_dispatch, method, params)
        except a2a_svc.A2AError as e:
            return fail(e.code, e.message)
        except Exception:
            logging.getLogger("anchor.a2a").exception("a2a spec method %s failed", method)
            return fail(-32603, "Internal error")
        _a2a_log(method, "spec", True, ua=ua)
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    if method not in a2a_svc.METHODS:
        return fail(a2a_svc.ERR_METHOD,
                    f"unknown method {method!r}; supported: "
                    f"{', '.join(sorted(set(a2a_svc.METHODS) | set(a2a_tasks.METHODS)))}")
    if method not in a2a_svc.inbound_policy()["open_methods"]:
        return fail(a2a_svc.ERR_POLICY, f"method {method} is not open under the current policy")

    def _dispatch() -> tuple[str, dict[str, Any]]:
        """Returns (verified_origin, result) — the caller needs the origin for the
        success log line, which the verified value is the only correct source for."""
        origin = a2a_svc.verify(method, params)
        if method == "peer/hello":
            return origin, _a2a_hello(origin, params.get("body"))
        if method == "capabilities/list":
            return origin, _a2a_capabilities(origin, params.get("body"))
        if method == "peer/quote":
            return origin, _a2a_quote(origin, params.get("body"),
                                      key_id=str(params["key_id"]), nonce=str(params["nonce"]))
        return origin, _a2a_receipt(origin, params.get("body"))

    try:
        # Verification does blocking work — getaddrinfo plus a card fetch with a 5s
        # timeout — so it runs in a worker thread. Harmless under Lambda (one
        # request per container) but a SERVFAIL domain would otherwise stall
        # co-tenant requests on any multi-request runtime.
        origin, result = await run_in_threadpool(_dispatch)
    except a2a_svc.A2AError as e:
        return fail(e.code, e.message)
    except Exception:
        logging.getLogger("anchor.a2a").exception("a2a %s failed", method)
        return fail(-32603, "internal error")

    _a2a_log(method, origin, True, ua=ua)
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


@app.get("/icon.png", include_in_schema=False)
def chat_icon():
    return FileResponse(os.path.join(_STATIC_DIR, "icon.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/splash.png", include_in_schema=False)
def chat_splash():
    return FileResponse(os.path.join(_STATIC_DIR, "splash.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/s.png", include_in_schema=False)
def chat_splash_short():
    # Short alias for splash.png — Farcaster manifest spec caps splashImageUrl
    # at 32 characters, so we need a URL ≤ 32 chars.
    return FileResponse(os.path.join(_STATIC_DIR, "s.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/chat.bundle.js", include_in_schema=False)
def chat_bundle():
    # Serve the gzipped bundle (1.8 MB) with Content-Encoding: gzip — the
    # browser decompresses transparently. The raw bundle (6.7 MB) exceeds
    # Lambda's 6 MB response payload limit; only the gzipped form fits.
    gz_path = os.path.join(_STATIC_DIR, "chat.bundle.js.gz")
    if not os.path.exists(gz_path):
        # Fallback: maybe a small bundle still fits raw.
        raw = os.path.join(_STATIC_DIR, "chat.bundle.js")
        if os.path.exists(raw):
            return FileResponse(raw, media_type="application/javascript",
                                headers={"Cache-Control": "public, max-age=3600, immutable"})
        raise HTTPException(status_code=404, detail="chat bundle not built — run `npm run build`")
    return FileResponse(
        gz_path,
        media_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=3600, immutable",
            "Content-Encoding": "gzip",
        },
    )


@app.get("/v1/config", include_in_schema=False)
def public_config():
    """Public, non-sensitive runtime config for the static chat UI."""
    return {"wcProjectId": os.getenv("WC_PROJECT_ID", "")}


# --- MCP (Model Context Protocol) over Streamable HTTP ----------------------
# Built because agents kept knocking: ~216 404s across /mcp, /api/mcp, /v1/mcp,
# /sse and /.well-known/mcp/server-card.json before any of it existed — the
# single largest unmet demand in the access log.
#
# The npm package anchor-x402-mcp already speaks MCP, but over stdio, which
# requires handing a funded private key to a local process. Over HTTP the client
# keeps its key and pays per call with an X-PAYMENT header, so the same catalogue
# is reachable without trusting us with a wallet.
from services import mcp as mcp_svc

# Tool -> canonical paid path. Every one of the 18 services has a POST entry
# (the GET/POST wrapper pairs collapse to one here), so `arguments` maps straight
# onto the request body with no per-tool marshalling.
_MCP_TOOLS = {
    "anchor_hash": "/v1/anchor",
    "attest_decision": "/v1/attest",
    "aura_read": "/v1/aura",
    "decode_calldata": "/v1/decode/calldata",
    "decode_tx": "/v1/decode/tx",
    "grade_target": "/v1/grade",
    "intel_wallet": "/v1/intel/wallet",
    "investigate_wallet": "/v1/investigate",
    "ledger_report": "/v1/ledger/report",
    "ledger_summary": "/v1/ledger/summary",
    "oracle_verdict": "/v1/oracle",
    "parse_datetime": "/v1/parse/datetime",
    "resolve_name": "/v1/resolve/name",
    "roast_target": "/v1/roast",
    "roll_random": "/v1/roll",
    "screen_wallet": "/v1/screen",
    "tldr_text": "/v1/tldr",
    "token_price": "/v1/price/token",
}

# The five short names the published npm server uses. Accepted on tools/call but
# not advertised in tools/list, so a config written against anchor-x402-mcp keeps
# working over HTTP without agents seeing two names for one service.
_MCP_ALIASES = {
    "roast": "roast_target",
    "oracle": "oracle_verdict",
    "tldr": "tldr_text",
    "aura": "aura_read",
    "grade": "grade_target",
}

# Tools that only read: no chain write, no async job dispatch. Lets a client
# auto-approve them instead of prompting for every price lookup.
_MCP_READ_ONLY = frozenset({
    "aura_read", "decode_calldata", "decode_tx", "grade_target", "intel_wallet",
    "ledger_summary", "parse_datetime", "resolve_name", "roast_target",
    "screen_wallet", "tldr_text", "token_price",
})

# Caps a 2025-03-26 batch so one POST cannot fan out into unbounded paid
# sub-requests. Batching is gone from every later revision.
_MCP_MAX_BATCH = 20

_MCP_INSTRUCTIONS = (
    "18 pay-per-call services for agents: on-chain anchoring and attestation, wallet "
    "sanctions screening, Web3 data (tx/calldata decode, ENS/SNS, token prices), x402 "
    "spend accounting, content analysis, and verifiable randomness.\n\n"
    "No API key and no account. Every tool is priced in its description ($0.001-$1.77) and "
    "settles per call in USDC on Base or Solana via x402. Calling a tool without payment is "
    "not an error you should give up on: the result comes back with isError=true and a "
    "structuredContent.accepts array — that is the x402 challenge. Sign one of those "
    "payment options and retry the identical tools/call with a PAYMENT-SIGNATURE header "
    "(x402 V2; the V1 X-PAYMENT spelling is also accepted). Read-only "
    "tools are marked with annotations.readOnlyHint."
)


def _mcp_build_tools() -> list[dict]:
    """Tool table from the models that already validate these routes.

    inputSchema comes from each route's Pydantic body model, so a tool's schema
    cannot drift from what the endpoint actually accepts — and the per-field
    descriptions agents need come along for free. Price and prose come from
    x402_routes, the table that actually charges.
    """
    from fastapi.routing import APIRoute

    models = {
        r.path: r.body_field.field_info.annotation
        for r in app.routes
        if isinstance(r, APIRoute) and "POST" in r.methods
        and getattr(r, "body_field", None) is not None
    }
    try:
        with open(os.path.join(os.path.dirname(__file__), "data",
                               "agent-card-skills.json")) as f:
            meta = json.load(f)
    except Exception:
        meta = {}

    tools = []
    for name, path in _MCP_TOOLS.items():
        cfg, model = x402_routes.get(f"POST {path}"), models.get(path)
        if cfg is None or model is None:
            continue
        m = meta.get(name, {})
        schema = model.model_json_schema()
        schema.pop("title", None)  # Pydantic's class-name title is noise here.
        # Hand-authored prose wins, exactly as for the agent card: for the four
        # GET-canonical services the POST entry's description says "POST wrapper",
        # which describes plumbing rather than the service. test_mcp.py fails the
        # build if one of those ever leaks through.
        tool = {
            "name": name,
            "description": m.get("description") or cfg.description or "",
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": name in _MCP_READ_ONLY,
                # Everything here reaches mainnet chains or the open web.
                "openWorldHint": True,
            },
        }
        if m.get("name"):
            tool["title"] = m["name"]
        tools.append(tool)
    # Deterministic order — 2026-07-28 SHOULDs it, so clients can cache the list
    # and keep their LLM prompt-cache warm across calls.
    return sorted(tools, key=lambda t: t["name"])


_MCP_TOOL_LIST = _mcp_build_tools()


def _mcp_log(method: str, tool: str, version: str, client: str, status: str) -> None:
    """One line per MCP call. Cheap, and the thing that will answer 'is anyone
    actually using this' — the question that was unanswerable for the agent card
    until UA logging went in."""
    try:
        print("MCP " + json.dumps({
            "ts": int(time.time()), "method": method[:40], "tool": tool[:40],
            "v": version, "client": client[:80], "status": status[:40],
        }, separators=(",", ":")))
    except Exception:
        pass


# Response headers carried back out of the paid route.
_MCP_PASSTHRU_HEADERS = ("payment-required", "payment-response", "x-payment-response",
                         "payment-error")
# Request headers carried in. PAYMENT-SIGNATURE is the x402 V2 name and X-PAYMENT
# the V1 one; forwarding only the latter (as this did) meant a V2 client's payment
# was silently dropped and the route just issued a fresh challenge — a 402 loop
# with no facilitator rejection anywhere in the logs to explain it.
_MCP_FORWARD_HEADERS = ("payment-signature", "x-payment",
                        "x-forwarded-for", "authorization")


async def _mcp_invoke(tool: str, arguments: dict,
                      request: Request) -> tuple[int, dict | str, dict]:
    """Run a tool by replaying it as an in-process request to its own paid route.

    Deliberately not a direct handler call: going back in through the ASGI app
    means x402_mw verifies and settles the payment, the builder code is attached,
    and PAID_CALL telemetry fires — all identical to an external caller. A direct
    call would need a second copy of the payment path, which is exactly how the
    price on one surface drifts from the price on another.
    """
    import httpx

    path = _MCP_TOOLS[tool]
    headers = {
        "content-type": "application/json",
        # Host must satisfy CanonicalHostMiddleware or it answers 308.
        "host": _CANONICAL_HOST,
        "user-agent": request.headers.get("user-agent", "mcp-http")[:200],
        "x-mcp-tool": tool,
    }
    for h in _MCP_FORWARD_HEADERS:
        v = request.headers.get(h)
        if v:
            headers[h] = v

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url=f"https://{_CANONICAL_HOST}") as client:
        r = await client.post(path, content=json.dumps(arguments).encode(),
                              headers=headers, timeout=120.0)
    # The x402 headers are the canonical carrier in V2 — `payment-required` holds
    # the base64 challenge and `payment-response` the settlement receipt, and the
    # JSON body is only a courtesy rendering. Dropping them (as this did) left the
    # MCP surface V1-shaped: a stock V2 client had nothing to trigger on. They are
    # already in the CORS expose_headers list, so browser agents can read them too.
    passthru = {k: v for k, v in (
        (h, r.headers.get(h)) for h in _MCP_PASSTHRU_HEADERS) if v}
    try:
        return r.status_code, r.json(), passthru
    except Exception:
        return r.status_code, r.text[:2000], passthru


# _meta on a paid tool result. Asked for by a buyer runtime that wanted to separate
# discovery, capability check and payment without dropping to the HTTP layer — a
# fair request, because the settlement receipt only existed as a `payment-response`
# header and the official MCP SDK swallows headers before the caller sees them. The
# negotiated version was not echoed anywhere either, and through a real SDK client
# (which tops out at 2025-11-25) the result carried no `_meta` at all.
#
# The key prefix follows MCP's MetaObject rules: reverse-DNS labels, each starting
# with a letter and ending alphanumeric, and a second label that is neither `mcp`
# nor `modelcontextprotocol` (those prefixes are reserved).
#
# Digests reuse services/a2a.digest_of — sha256 over compact key-sorted JSON — so
# there is one canonicalization across both protocol surfaces rather than a second
# one for a buyer to learn. `canonicalization` states the rule inline so the hashes
# are verifiable without fetching our docs.
_MCP_PAYMENT_META_KEY = "com.anchor-x402/payment"


def _mcp_payment_meta(version: str, status: int, payload: Any,
                      passthru: dict, request: Request) -> dict:
    """Verifiable payment metadata for a tools/call result. Every value here is
    recomputable by the caller from bytes it already holds."""
    meta: dict[str, Any] = {
        "protocolVersion": version,
        "canonicalization": "sha256 over compact key-sorted JSON (no spaces, unescaped UTF-8)",
    }
    # Challenge path: hash the requirement set exactly as quoted, so the buyer can
    # bind the quote it received to the call it later pays for.
    if status == 402 and isinstance(payload, dict) and isinstance(payload.get("accepts"), list):
        meta["requirementHash"] = a2a_svc.digest_of(payload["accepts"])

    # Paid path: hash the single requirement the client actually presented, taken
    # from its own payment header rather than re-derived here — re-deriving could
    # drift from what was really charged, which is the whole thing being proved.
    pay = request.headers.get("payment-signature") or request.headers.get("x-payment")
    if pay:
        try:
            accepted = (json.loads(base64.b64decode(pay)) or {}).get("accepted")
            if accepted:
                meta["paidRequirement"] = a2a_svc.digest_of(accepted)
        except Exception:
            pass

    # Settlement receipt, decoded out of the header the SDK would otherwise hide.
    recv = passthru.get("payment-response") or passthru.get("x-payment-response")
    if recv:
        try:
            d = json.loads(base64.b64decode(recv))
            meta["settlement"] = {k: d[k] for k in ("success", "payer", "transaction")
                                  if k in d}
        except Exception:
            pass
    return meta


async def _mcp_tools_call(params: dict, version: str,
                          request: Request) -> tuple[dict | None, dict | None, dict]:
    """Returns (result, error). Tool *execution* failures — including 402 — come
    back as a result with isError=true rather than a JSON-RPC error, because the
    spec routes those to the model: an agent that can see the 402 challenge can
    pay it, whereas a JSON-RPC error is handled by the client and never shown."""
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return None, {"code": mcp_svc.ERR_INVALID_PARAMS,
                      "message": "params.name is required."}, {}
    tool = _MCP_ALIASES.get(name, name)
    if tool not in _MCP_TOOLS:
        return None, {"code": mcp_svc.ERR_INVALID_PARAMS,
                      "message": (f"Unknown tool {name!r}. Call tools/list for the "
                                  f"{len(_MCP_TOOL_LIST)} available tools.")}, {}

    args = params.get("arguments")
    args = args if isinstance(args, dict) else {}
    status, payload, passthru = await _mcp_invoke(tool, args, request)

    def _finish(result: dict) -> tuple[dict, None, dict]:
        # `_meta` exists on CallToolResult from 2025-06-18 — the same gate
        # structuredContent uses. Set before mcp_svc.ok(), which merges serverInfo
        # into whatever is already there on modern revisions and leaves legacy
        # results alone, so this reaches both eras.
        if version >= "2025-06-18":
            result["_meta"] = {
                _MCP_PAYMENT_META_KEY: _mcp_payment_meta(
                    version, status, payload, passthru, request)
            }
        return result, None, passthru

    if status == 200:
        result = {"content": mcp_svc.text_content(payload), "isError": False}
        # structuredContent landed in 2025-06-18; older clients ignore it, but
        # don't send a field their revision never defined.
        if isinstance(payload, dict) and version >= "2025-06-18":
            result["structuredContent"] = payload
        return _finish(result)

    if status == 402:
        lead = (f"Payment required for {tool} — this is a paid x402 service. Sign one of the "
                f"payment options in structuredContent.accepts and retry this identical "
                f"tools/call with the signed authorization in a PAYMENT-SIGNATURE header "
                f"(x402 V2; the V1 X-PAYMENT spelling also works).")
        result = {"content": mcp_svc.text_content(lead if not isinstance(payload, dict) else
                                                  {"error": "payment_required",
                                                   "guidance": lead, **payload}),
                  "isError": True}
        if isinstance(payload, dict) and version >= "2025-06-18":
            result["structuredContent"] = payload
        return _finish(result)

    result = {"content": mcp_svc.text_content(
        payload if isinstance(payload, (dict, str)) else str(payload)), "isError": True}
    if isinstance(payload, dict) and version >= "2025-06-18":
        result["structuredContent"] = payload
    return _finish(result)


async def _mcp_handle_one(body: dict,
                          request: Request) -> tuple[dict | None, int, dict]:
    """Handle one JSON-RPC message. Returns (content, http_status, headers), with
    content None for a message that takes no response (a notification).

    Split out from the endpoint so that a 2025-03-26 batch can reuse it verbatim
    — one message's handling must not depend on whether it arrived alone.
    """
    if not isinstance(body, dict):
        return mcp_svc.err(None, mcp_svc.ERR_INVALID_REQUEST,
                           "Each JSON-RPC message must be an object."), 400, {}

    method = body.get("method")
    if not isinstance(method, str):
        return mcp_svc.err(body.get("id"), mcp_svc.ERR_INVALID_REQUEST,
                           "Missing or non-string `method`."), 400, {}

    # Notifications carry no id and take no response. We hold no per-connection
    # state, so there is nothing for initialized/cancelled to do.
    if mcp_svc.is_notification(body):
        return None, 202, {}

    rpc_id = body.get("id")
    headers = request.headers  # Starlette headers are case-insensitive.
    client = mcp_svc.client_label(body) or request.headers.get("user-agent", "")[:80]

    version, verr = mcp_svc.negotiate(headers, body)
    if verr is None:
        verr = mcp_svc.validate_request(headers, body, version)
    if verr:
        _mcp_log(method, "", version, client, f"rejected:{verr['code']}")
        return (mcp_svc.err(rpc_id, verr["code"], verr["message"], verr.get("data")),
                mcp_svc.http_status(verr["code"]), {})

    params = body.get("params")
    params = params if isinstance(params, dict) else {}
    modern = mcp_svc.is_modern(version)

    def _reply(result: dict, cache=None, headers=None):
        return mcp_svc.ok(rpc_id, result, version, cache=cache), 200, (headers or {})

    def _fail(code: int, message: str):
        # Status always derived, never hardcoded — one place decides, so the
        # spec's "404 for an unimplemented method" cannot drift per call site.
        return mcp_svc.err(rpc_id, code, message), mcp_svc.http_status(code), {}

    # server/discover — mandatory in 2026-07-28. Answered for every revision:
    # it is the cheapest way for any client to learn what this server speaks.
    if method == "server/discover":
        _mcp_log(method, "", version, client, "ok")
        return mcp_svc.ok(rpc_id, {
            "resultType": "complete",
            "supportedVersions": list(mcp_svc.SUPPORTED),
            "capabilities": mcp_svc.capabilities(),
            "instructions": _MCP_INSTRUCTIONS,
            "ttlMs": mcp_svc.DISCOVER_TTL_MS,
            "cacheScope": mcp_svc.CACHE_PUBLIC,
            "_meta": {mcp_svc.META_SERVER_INFO: mcp_svc.server_info(version)},
        }, version), 200, {}

    if method == "initialize":
        if modern:
            # Unreachable via negotiate(), which never resolves initialize to a
            # modern version — kept so the invariant is stated, not assumed.
            return _fail(mcp_svc.ERR_METHOD_NOT_FOUND,
                         f"`initialize` was removed in {version}; send requests directly "
                         f"with params._meta, or call server/discover.")
        _mcp_log(method, "", version, client, "ok")
        return _reply({
            "protocolVersion": version,
            "capabilities": mcp_svc.capabilities(),
            "serverInfo": mcp_svc.server_info(version),
            "instructions": _MCP_INSTRUCTIONS,
        })

    if method == "ping":
        if modern:
            return _fail(mcp_svc.ERR_METHOD_NOT_FOUND, f"`ping` was removed in {version}.")
        return _reply({})

    if method == "tools/list":
        _mcp_log(method, "", version, client, "ok")
        return _reply({"tools": _MCP_TOOL_LIST},
                      cache=(mcp_svc.TOOLS_TTL_MS, mcp_svc.CACHE_PUBLIC))

    if method == "tools/call":
        result, cerr, passthru = await _mcp_tools_call(params, version, request)
        if cerr:
            _mcp_log(method, str(params.get("name", ""))[:40], version, client,
                     f"rejected:{cerr['code']}")
            return _fail(cerr["code"], cerr["message"])
        _mcp_log(method, str(params.get("name", ""))[:40], version, client,
                 "error" if result.get("isError") else "ok")
        return _reply(result, headers=passthru)

    # 404, not 200 — the spec uses the status to let a client distinguish a
    # modern server's -32601 from a legacy 404 with no MCP endpoint at all.
    _mcp_log(method, "", version, client, "unknown_method")
    return _fail(mcp_svc.ERR_METHOD_NOT_FOUND,
                 f"Method {method!r} is not implemented. This server offers tools only: "
                 f"server/discover, tools/list, tools/call"
                 f"{'' if modern else ', initialize, ping'}.")


@app.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request):
    """The MCP endpoint. Streamable HTTP, stateless in every protocol era.

    We mint no Mcp-Session-Id even for the revisions that permit one: there is no
    cross-invocation memory on Lambda to hold a session in, and 2026-07-28
    removes sessions outright, so statelessness is the only shape that is honest
    on both sides of that break.

    On Origin: the transport spec MUSTs Origin validation against DNS rebinding.
    That threat model is a *local* server holding ambient credentials, where a
    hostile page can drive privileged calls from the victim's browser. Here CORS
    is already deliberately allow_origins=["*"] because there is no ambient
    credential to steal — spending money requires a wallet-signed X-PAYMENT that
    a third-party page cannot produce. So an Origin check would reject legitimate
    browser agents while blocking nothing; we take the reasoned exception rather
    than pretend to satisfy the MUST.
    """
    try:
        body = json.loads(await request.body())
    except Exception:
        return JSONResponse(status_code=400, content=mcp_svc.err(
            None, mcp_svc.ERR_PARSE, "Parse error — body must be JSON."))

    # A JSON-RPC batch. Legal only before 2025-06-18, which removed batching; the
    # array itself is the tell, since 2025-03-26 predates the version header and
    # its messages carry no _meta to negotiate from.
    if isinstance(body, list):
        version = (request.headers.get("mcp-protocol-version") or "").strip() \
            or mcp_svc.DEFAULT_LEGACY
        if version >= "2025-06-18":
            return JSONResponse(status_code=400, content=mcp_svc.err(
                None, mcp_svc.ERR_INVALID_REQUEST,
                f"JSON-RPC batching was removed in 2025-06-18 and this request declares "
                f"{version}. Send one JSON-RPC message per POST."))
        if not body:
            return JSONResponse(status_code=400, content=mcp_svc.err(
                None, mcp_svc.ERR_INVALID_REQUEST, "An empty batch is not a valid request."))
        if len(body) > _MCP_MAX_BATCH:
            return JSONResponse(status_code=400, content=mcp_svc.err(
                None, mcp_svc.ERR_INVALID_REQUEST,
                f"Batch of {len(body)} exceeds the {_MCP_MAX_BATCH}-message limit."))
        out = []
        for msg in body:
            # Batch responses carry no x402 headers: a batch can hold several paid
            # calls and one header set cannot represent all of them without being
            # ambiguous about which call it belongs to. Batching exists only for
            # 2025-03-26, and the challenge is still in each result's body.
            content, _status, _hdrs = await _mcp_handle_one(msg, request)
            if content is not None:
                out.append(content)
        # All notifications (or responses, which we never solicit) — 202, no body,
        # exactly as for a lone notification.
        if not out:
            return Response(status_code=202)
        return JSONResponse(content=out)

    content, status, headers = await _mcp_handle_one(body, request)
    if content is None:
        return Response(status_code=status)
    return JSONResponse(status_code=status, content=content, headers=headers or None)


@app.api_route("/mcp", methods=["GET", "DELETE"], include_in_schema=False)
async def mcp_endpoint_rejected():
    """2026-07-28 removed both the GET notification stream and DELETE session
    teardown; a server that implements only POST answers 405 for each."""
    return JSONResponse(status_code=405, content=mcp_svc.err(
        None, mcp_svc.ERR_INVALID_REQUEST,
        "The MCP endpoint accepts POST only. The GET stream and DELETE session "
        "teardown were removed in 2026-07-28, and this server is stateless."),
        headers={"Allow": "POST"})


# The paths agents actually probed. /mcp is canonical; these keep a client that
# guessed wrong from bouncing off a 404. /api/v1/mcp was added after seeing it
# guessed in the access log.
@app.post("/v1/mcp", include_in_schema=False)
@app.post("/api/mcp", include_in_schema=False)
@app.post("/api/v1/mcp", include_in_schema=False)
async def mcp_endpoint_alias(request: Request):
    return await mcp_endpoint(request)


# The aliases need the same GET/DELETE rejection as /mcp. Without this they had no
# non-POST handler at all, so a client probing GET /api/mcp got a bare 404 while
# GET /mcp correctly got 405 + Allow — five of the six observed /api/mcp 404s.
@app.api_route("/v1/mcp", methods=["GET", "DELETE"], include_in_schema=False)
@app.api_route("/api/mcp", methods=["GET", "DELETE"], include_in_schema=False)
@app.api_route("/api/v1/mcp", methods=["GET", "DELETE"], include_in_schema=False)
async def mcp_endpoint_alias_rejected():
    return await mcp_endpoint_rejected()


# OAuth discovery. MCP's authorization spec is OAuth 2.1 + RFC 9728, but
# authorization is OPTIONAL and the "servers MUST implement Protected Resource
# Metadata" requirement applies only to servers that support it. We don't: there
# is no authorization server, and access is metered per call with x402 instead.
#
# So the honest answer is still 404 — serving an RFC 9728 document would claim an
# OAuth deployment that does not exist, which is worse than not answering. What we
# add is a body explaining why and what to do instead, so a probing client learns
# something rather than guessing. Status stays 404 so spec-following clients treat
# it exactly as they would any absent metadata document.
#
# Both orderings are enumerated because both were observed in the log; no greedy
# catch-all, so nothing else can be swallowed by accident.
_OAUTH_PROBE_PATHS = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/mcp/.well-known/oauth-protected-resource",
    "/api/mcp/.well-known/oauth-protected-resource",
    "/v1/mcp/.well-known/oauth-protected-resource",
    "/api/v1/mcp/.well-known/oauth-protected-resource",
]


def _oauth_not_used() -> JSONResponse:
    return JSONResponse(status_code=404, content={
        "error": "no_authorization_server",
        "detail": ("This server does not implement OAuth. MCP authorization is OPTIONAL and no "
                   "authorization server exists here, so there is no RFC 9728 Protected Resource "
                   "Metadata to serve. Access is metered per call with x402 instead: "
                   "server/discover, initialize and tools/list are free, and tools/call returns "
                   "the payment challenge in the result plus a payment-required header. Sign one "
                   "of the accepts entries and retry with PAYMENT-SIGNATURE."),
        "authentication": {"type": "x402", "header": "PAYMENT-SIGNATURE",
                           "free_methods": ["server/discover", "initialize", "tools/list"]},
        "mcp_endpoint": f"{_PUBLIC_BASE}/mcp",
        "server_card": "https://anchor-x402.com/.well-known/mcp/server-card.json",
    })


for _p in _OAUTH_PROBE_PATHS:
    app.add_api_route(_p, _oauth_not_used, methods=["GET", "HEAD"], include_in_schema=False)
    # RFC 9728 appends the resource path to the well-known prefix, so
    # /.well-known/oauth-protected-resource/api/mcp is the shape a conformant
    # client actually requests.
    if _p.startswith("/.well-known/"):
        app.add_api_route(_p + "/{resource_path:path}", _oauth_not_used,
                          methods=["GET", "HEAD"], include_in_schema=False)


@app.api_route("/sse", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/mcp/sse", methods=["GET", "HEAD"], include_in_schema=False)
def mcp_sse_gone():
    """The 2024-11-05 HTTP+SSE transport, which /sse probes are looking for, has
    been deprecated since 2025-03-26. Rather than implement a dead transport,
    say so and point at the endpoint that exists — a client following the
    documented fallback treats a non-`endpoint` response as 'no legacy here'."""
    return JSONResponse(status_code=404, content={
        "error": "transport_not_supported",
        "detail": ("The HTTP+SSE transport (protocol 2024-11-05) is not implemented — it has "
                   "been deprecated since 2025-03-26. Use Streamable HTTP at POST /mcp."),
        "mcp_endpoint": f"{_PUBLIC_BASE}/mcp",
        "supported_versions": list(mcp_svc.SUPPORTED),
    })


# The registry's own schema, which does resolve — unlike the mcp-server-card/v1
# URL our ERC-8004 registration was citing, which 404s. `description` is capped
# at 100 characters there, so the long guidance goes in _meta instead of being
# silently over-length.
_MCP_CARD_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
_MCP_CARD_SUMMARY = "18 x402-paid agent tools — pay per call in USDC, no API key or account."


def _mcp_server_card_doc() -> dict:
    return {
        "$schema": _MCP_CARD_SCHEMA,
        "name": mcp_svc.REGISTRY_NAME,
        "title": mcp_svc.SERVER_NAME,
        "description": _MCP_CARD_SUMMARY,
        "version": mcp_svc.SERVER_VERSION,
        "websiteUrl": "https://anchor-x402.com",
        "repository": {
            "url": "https://github.com/hypeprinter007-stack/anchor-x402",
            "source": "github",
        },
        "remotes": [{"type": "streamable-http", "url": f"{_PUBLIC_BASE}/mcp"}],
        "packages": [{
            "registryType": "npm",
            "registryBaseUrl": "https://registry.npmjs.org",
            "identifier": "anchor-x402-mcp",
            "version": "0.2.1",
            "runtimeHint": "npx",
            "transport": {"type": "stdio"},
            "environmentVariables": [{
                "name": "ANCHOR_WALLET_PRIVATE_KEY",
                "description": ("EVM private key (0x-prefixed hex) that pays for x402 calls. "
                                "Required for the stdio transport only — the streamable-http "
                                "remote takes payment per call instead."),
                "isRequired": True,
                "isSecret": True,
            }],
        }],
        "_meta": {
            "io.modelcontextprotocol.registry/publisher-provided": {
                "protocolVersions": list(mcp_svc.SUPPORTED),
                "latestProtocolVersion": mcp_svc.LATEST,
                "capabilities": mcp_svc.capabilities(),
                "instructions": _MCP_INSTRUCTIONS,
                "authentication": {
                    "type": "x402",
                    "header": "PAYMENT-SIGNATURE",
                    "headerLegacy": "X-PAYMENT",
                    "description": ("No API key or account. Each tools/call settles per request "
                                    "in USDC on Base or Solana. Call without payment to receive "
                                    "the 402 challenge in structuredContent.accepts, then retry "
                                    "with a signed PAYMENT-SIGNATURE header."),
                    "freeMethods": ["server/discover", "initialize", "tools/list"],
                },
                "tools": [t["name"] for t in _MCP_TOOL_LIST],
                "documentation": "https://anchor-x402.com/llms.txt",
                "agentCard": "https://anchor-x402.com/.well-known/agent-card.json",
            }
        },
    }


@app.api_route("/.well-known/mcp/server-card.json", methods=["GET", "HEAD"],
               include_in_schema=False)
def mcp_server_card():
    """Agents probed this path, so serve it — in the official registry
    server.json shape rather than something ad-hoc, so a tool that already reads
    server.json can consume it. `remotes[]` is what makes the HTTP transport
    discoverable; the npm package stays listed under `packages[]`."""
    return _mcp_server_card_doc()


_mangum = Mangum(app)


def handler(event: Any, context: Any) -> Any:
    # Self-invoked async path for /v1/ledger/report jobs — the dispatch route
    # re-invokes this same function with InvocationType=Event and this payload
    # shape. Everything else is normal API Gateway traffic through Mangum.
    if isinstance(event, dict) and event.get("ledger_job"):
        from services import ledger as _ledger
        return _ledger.run_report_job(event)
    # Daily EventBridge schedule — anchors a signed root over the live A2A
    # receipt set to Base + Solana. Same dispatch pattern as ledger_job above.
    if isinstance(event, dict) and event.get("a2a_root"):
        from services import a2a_cron as _a2a_cron
        return _a2a_cron.anchor_receipt_root_handler(event)
    return _mangum(event, context)
