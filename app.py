"""anchor-x402: dual-chain mainnet anchoring as an x402-paid service.

POST /v1/anchor — accept a hash (or arbitrary JSON to be hashed),
write the resulting 32-byte digest to Base mainnet (calldata) and
Solana mainnet (Memo program) in parallel, return both tx hashes.

Pay-per-call: $0.005 USDC, settle on Base or Solana.
"""
from __future__ import annotations

import json
import logging
import os
import time

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from mangum import Mangum

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
from services import anchor as anchor_svc
from services import attest as attest_svc
from services import aura as aura_svc
from services import calldata_decode as calldata_decode_svc
from services import chat as chat_svc
from services import datetime_parse as datetime_parse_svc
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
    description="Dual-chain mainnet anchoring as an x402-paid service. Anchor any 32-byte hash to Base + Solana for $0.005.",
    version="0.1.0",
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


@app.middleware("http")
async def _access_log(request, call_next):
    """One-line access log per request → Lambda CloudWatch.
    Enables host-level traffic split (chat.* vs api.*) via Insights."""
    response = await call_next(request)
    host = request.headers.get("host", "").split(":")[0]
    print(f"access host={host} method={request.method} path={request.url.path} status={response.status_code}")
    return response

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
    "$0.05":   10 * 10**18,    # ¥10
    "$5.00":   1000 * 10**18,  # ¥1000
    "$7.77":   1500 * 10**18,  # ¥1500
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

x402_routes = {
    "POST /v1/anchor": RouteConfig(
        accepts=_accepts_at("$0.005"),
        description="Anchor a 32-byte hash to Base + Solana mainnet — $0.005 USDC",
        extensions={**_anchor_bazaar_ext},
    ),
    "GET /v1/screen": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Sanctions + AML screening for any wallet address — $0.001 USDC",
        extensions={**_screen_bazaar_ext},
    ),
    "POST /v1/attest": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Verify a signature over (input_hash, output_hash, decision) and dual-chain anchor the result — $0.01 USDC",
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
        extensions={**_name_resolve_bazaar_ext},
    ),
    "GET /v1/price/token": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="USD price for any major token by symbol or chain+contract — $0.001 USDC",
        extensions={**_token_price_bazaar_ext},
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
        extensions={**_intel_wallet_bazaar_ext},
    ),
    "POST /v1/investigate": RouteConfig(
        accepts=_accepts_at("$7.77"),
        description="Agent-driven wallet due diligence — multi-step investigation, signed markdown report + JSON sidecar, dual-chain anchored. Async — returns job_id; poll /v1/investigate/status/{job_id} for the deliverable. ETA 5-10 min. $7.77 USDC.",
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
    # GET wrappers for function-like callers (Virtuals ACP, etc.) — same price, no
    # bazaar extensions to avoid duplicate listings (POST is the canonical entry).
    "GET /v1/anchor": RouteConfig(
        accepts=_accepts_at("$0.005"),
        description="Anchor a 32-byte hash to Base + Solana mainnet (GET wrapper) — $0.005 USDC",
    ),
    "GET /v1/attest": RouteConfig(
        accepts=_accepts_at("$0.01"),
        description="Verify a signature and dual-chain anchor the result (GET wrapper) — $0.01 USDC",
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
        accepts=_accepts_at("$0.001"),
        description="Sanctions + AML screening (POST wrapper, body: {wallet}) — $0.001 USDC",
    ),
    "POST /v1/resolve/name": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="Cross-chain name resolver (POST wrapper, body: {name}) — $0.001 USDC",
    ),
    "POST /v1/price/token": RouteConfig(
        accepts=_accepts_at("$0.001"),
        description="USD token price (POST wrapper, body: {symbol} or {chain, contract}) — $0.001 USDC",
    ),
    "POST /v1/intel/wallet": RouteConfig(
        accepts=_accepts_at("$0.005"),
        description="Unified wallet intelligence (POST wrapper, body: {wallet}) — $0.005 USDC",
    ),
    # GET wrappers for POST-only endpoints (LLM + investigate). Same price, no
    # bazaar extensions to avoid duplicate listings.
    "GET /v1/investigate": RouteConfig(
        accepts=_accepts_at("$7.77"),
        description="Agent-driven wallet due diligence (GET wrapper, query: address) — $7.77 USDC",
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


@app.middleware("http")
async def x402_mw(request, call_next):
    if _INTERNAL_AUTH and request.headers.get("x-internal-auth") == _INTERNAL_AUTH:
        return await call_next(request)
    return await payment_middleware(x402_routes, x402_server)(request, call_next)


# CORS registered LAST so it ends up outermost in the Starlette stack — must
# wrap x402_mw so that short-circuited 402 responses still receive ACAO +
# expose-headers. Allow * origin (payment auth via X-PAYMENT replaces origin-
# based security; no cookies involved). Expose the x402 response headers so
# browser-resident agents can read 402 challenges + settle confirmations.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-payment", "authorization"],
    expose_headers=["payment-response", "x-payment-response", "payment-required"],
    max_age=86400,
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "anchor-x402"}


@app.post("/v1/anchor", response_model=AnchorResponse)
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


@app.post("/v1/attest", response_model=AttestResponse)
def attest(req: AttestRequest) -> AttestResponse:
    ok, recovered = attest_svc.verify(
        scheme=req.scheme,
        input_hash=req.input_hash,
        output_hash=req.output_hash,
        decision=req.decision,
        signature=req.signature,
        signer_pubkey=req.signer_pubkey,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="signature verification failed")

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
        base=base_anchor,
        solana=solana_anchor,
        decision=req.decision,
        signed_at=started,
    )


@app.post("/v1/decode/tx", response_model=TxDecodeResponse)
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


@app.post("/v1/decode/calldata", response_model=CalldataDecodeResponse)
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


@app.post("/v1/parse/datetime", response_model=DatetimeParseResponse)
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
from pydantic import BaseModel as _BM


class _WalletBody(_BM):
    wallet: str


class _NameBody(_BM):
    name: str


class _TokenPriceBody(_BM):
    symbol: str | None = None
    chain: str | None = None
    contract: str | None = None


@app.post("/v1/screen", response_model=ScreenResponse)
def screen_post(req: _WalletBody) -> ScreenResponse:
    return screen(req.wallet)


@app.post("/v1/resolve/name", response_model=NameResolveResponse)
def resolve_name_post(req: _NameBody) -> NameResolveResponse:
    return resolve_name(req.name)


@app.post("/v1/price/token", response_model=TokenPriceResponse)
def token_price_post(req: _TokenPriceBody) -> TokenPriceResponse:
    return token_price(symbol=req.symbol, chain=req.chain, contract=req.contract)


@app.post("/v1/intel/wallet", response_model=IntelWalletResponse)
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
# accepts $7.77 USDC, writes job_id to DynamoDB, async-invokes the worker
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


@app.post("/v1/investigate", response_model=InvestigateAcceptedResponse)
def investigate_dispatch(req: InvestigateRequest) -> InvestigateAcceptedResponse:
    """Accept payment, record job, async-dispatch to private worker."""
    job_id = str(uuid4())
    now = int(time.time())
    try:
        _ddb.put_item(
            Item={
                "job_id": job_id,
                "address": req.address,
                "status": "DISPATCHING",
                "source": "x402",
                "created_at": now,
                "updated_at": now,
            },
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
    try:
        _lambda.invoke(
            FunctionName=_WORKER_FN,
            InvocationType="Event",
            Payload=json.dumps(payload).encode(),
        )
    except Exception as e:  # noqa: BLE001
        logging.getLogger("investigate").exception("worker dispatch failed")
        # Mark for buyer visibility so /status returns FAILED rather than hanging
        try:
            _ddb.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :s, error_msg = :e",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "FAILED", ":e": f"dispatch failed: {type(e).__name__}"},
            )
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"dispatch failed: {type(e).__name__}")

    return InvestigateAcceptedResponse(
        job_id=job_id,
        status="accepted",
        status_url=f"{_PUBLIC_BASE}/v1/investigate/status/{job_id}",
        eta_seconds=600,
    )


@app.get("/v1/investigate/status/{job_id}", response_model=InvestigateStatusResponse)
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
    deliverable = item.get("deliverable")
    return InvestigateStatusResponse(
        job_id=job_id,
        status=status,
        deliverable=InvestigateDeliverable(**deliverable) if deliverable else None,
        eta_seconds=None if status in ("DELIVERED", "FAILED") else 600,
        error=item.get("error_msg") or item.get("error"),
    )


# --- /v1/roast | /v1/oracle | /v1/tldr (universal LLM-paid endpoints) -------


@app.post("/v1/roast", response_model=RoastResponse)
def roast(req: RoastRequest) -> RoastResponse:
    try:
        result = roast_svc.roast(req.target)
    except Exception as e:
        logging.getLogger("roast").exception("roast failed")
        raise HTTPException(status_code=502, detail=f"roast failed: {type(e).__name__}: {e}")
    return RoastResponse(**result)


@app.post("/v1/oracle", response_model=OracleResponse)
def oracle(req: OracleRequest) -> OracleResponse:
    try:
        result = oracle_svc.oracle(req.question)
    except Exception as e:
        logging.getLogger("oracle").exception("oracle failed")
        raise HTTPException(status_code=502, detail=f"oracle failed: {type(e).__name__}: {e}")
    return OracleResponse(**result)


@app.post("/v1/tldr", response_model=TldrResponse)
def tldr(req: TldrRequest) -> TldrResponse:
    try:
        result = tldr_svc.tldr(text=req.text, url=req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.getLogger("tldr").exception("tldr failed")
        raise HTTPException(status_code=502, detail=f"tldr failed: {type(e).__name__}: {e}")
    return TldrResponse(**result)


@app.post("/v1/aura", response_model=AuraResponse)
def aura(req: AuraRequest) -> AuraResponse:
    try:
        result = aura_svc.aura(req.target)
    except Exception as e:
        logging.getLogger("aura").exception("aura failed")
        raise HTTPException(status_code=502, detail=f"aura failed: {type(e).__name__}: {e}")
    return AuraResponse(**result)


@app.post("/v1/grade", response_model=GradeResponse)
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
def investigate_dispatch_get(address: str) -> InvestigateAcceptedResponse:
    return investigate_dispatch(InvestigateRequest(address=address))


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


@app.post("/v1/roll", response_model=RollResponse)
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


@app.post("/v1/chat", response_model=ChatResponse)
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


@app.get("/")
def root(request: Request):
    """Root: chat UI on the chat.* subdomain, docs redirect on the api.* one."""
    host = request.headers.get("host", "")
    if host.startswith("chat."):
        return _serve_chat_html()
    return RedirectResponse(url="/docs")


# --- Agentverse FastAPI adapter ----------------------------------------------
# Implements Fetch.ai's Chat Protocol so anchor-x402 is discoverable + invokable
# from Agentverse and ASI:One. Receives signed envelopes from Agentverse, sends
# the reply back via Agentverse's mailbox API.

def _agentverse_investigator_reply(user_text: str) -> str:
    """This Agentverse listing IS the $7.77 risk investigator. No free previews —
    the agent quotes the investigation cost + tells the caller how to invoke it."""
    return (
        "anchor-x402 risk investigator — $7.77 USDC per run.\n\n"
        "Multi-step wallet due diligence:\n"
        "• sanctions/AML screen across OFAC, Chainalysis, TRM\n"
        "• balance + activity timeline (Base + Solana mainnet)\n"
        "• identity correlation (ENS, basenames, SNS, on-chain history)\n"
        "• counterparty graph + mixer / sanctioned-chain exposure\n"
        "• final verdict + score, anchored on Base + Solana for third-party verification\n\n"
        "How to run:\n"
        "1. POST https://api.anchor-x402.com/v1/investigate\n"
        "   Body: { \"wallet\": \"<address>\" }\n"
        "   x402 USDC payment ($7.77) required\n"
        "2. Returns a job_id — poll GET /v1/jobs/{job_id} (~5–10 min)\n"
        "3. Final response includes the verdict + on-chain anchor tx\n\n"
        "Don't have an x402 client? Use the hosted chat agent that runs this tool "
        "for you from your USDC: https://chat.anchor-x402.com\n\n"
        "OpenAPI: https://api.anchor-x402.com/openapi.json"
    )


@app.get("/agentverse/status")
def agentverse_status():
    return {"status": "ok", "agent": "anchor-x402"}


@app.post("/agentverse/chat")
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


@app.get("/chat")
def chat_ui():
    return _serve_chat_html()


@app.get("/.well-known/farcaster.json")
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


@app.get("/.well-known/x402")
def x402_discovery():
    return _serve_x402_discovery()


@app.get("/.well-known/x402.json")
def x402_discovery_json():
    return _serve_x402_discovery()


_DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")


@app.get("/robots.txt")
def robots_txt():
    path = os.path.join(_DOCS_DIR, "robots.txt")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="robots.txt not bundled")
    return FileResponse(
        path,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/llms.txt")
def llms_txt():
    path = os.path.join(_DOCS_DIR, "llms.txt")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="llms.txt not bundled")
    return FileResponse(
        path,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/icon.png")
def chat_icon():
    return FileResponse(os.path.join(_STATIC_DIR, "icon.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/splash.png")
def chat_splash():
    return FileResponse(os.path.join(_STATIC_DIR, "splash.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/s.png")
def chat_splash_short():
    # Short alias for splash.png — Farcaster manifest spec caps splashImageUrl
    # at 32 characters, so we need a URL ≤ 32 chars.
    return FileResponse(os.path.join(_STATIC_DIR, "s.png"), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/chat.bundle.js")
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


@app.get("/v1/config")
def public_config():
    """Public, non-sensitive runtime config for the static chat UI."""
    return {"wcProjectId": os.getenv("WC_PROJECT_ID", "")}


handler = Mangum(app)
