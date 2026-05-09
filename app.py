"""anchor-x402: dual-chain mainnet anchoring as an x402-paid service.

POST /v1/anchor — accept a hash (or arbitrary JSON to be hashed),
write the resulting 32-byte digest to Base mainnet (calldata) and
Solana mainnet (Memo program) in parallel, return both tx hashes.

Pay-per-call: $0.005 USDC, settle on Base or Solana.
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from mangum import Mangum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

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
    CalldataDecodeRequest,
    CalldataDecodeResponse,
    ChainAnchor,
    DatetimeParseRequest,
    DatetimeParseResponse,
    NameResolveResponse,
    ScreenResponse,
    TokenPriceResponse,
    TxDecodeRequest,
    TxDecodeResponse,
)
from services import anchor as anchor_svc
from services import attest as attest_svc
from services import calldata_decode as calldata_decode_svc
from services import datetime_parse as datetime_parse_svc
from services import name_resolve as name_resolve_svc
from services import screen as screen_svc
from services import token_price as token_price_svc
from services import tx_decode as tx_decode_svc
from services.cdp_auth import build_cdp_auth_provider

TREASURY = os.getenv("TREASURY_ADDRESS", "")
SOLANA_TREASURY = os.getenv("SOLANA_TREASURY_ADDRESS", "")
SOLANA_MAINNET_CAIP2 = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

app = FastAPI(
    title="anchor-x402",
    description="Dual-chain mainnet anchoring as an x402-paid service. Anchor any 32-byte hash to Base + Solana for $0.005.",
    version="0.1.0",
)

facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(
        url="https://api.cdp.coinbase.com/platform/v2/x402",
        auth_provider=build_cdp_auth_provider(),
    )
)

x402_server = x402ResourceServer(facilitator_clients=facilitator)
x402_server.register("eip155:8453", ExactEvmServerScheme())
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

def _accepts_at(price: str) -> list[PaymentOption]:
    out: list[PaymentOption] = []
    if TREASURY:
        out.append(PaymentOption(scheme="exact", pay_to=TREASURY, price=price, network="eip155:8453"))
    if SOLANA_TREASURY:
        out.append(PaymentOption(scheme="exact", pay_to=SOLANA_TREASURY, price=price, network=SOLANA_MAINNET_CAIP2))
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
        accepts=_accepts_at("$0.0005"),
        description="Structured decode of any mainnet tx (base | ethereum | solana) — $0.0005 USDC",
        extensions={**_tx_decode_bazaar_ext},
    ),
    "GET /v1/resolve/name": RouteConfig(
        accepts=_accepts_at("$0.0005"),
        description="Cross-chain name resolver (ENS, Bonfida SNS) — $0.0005 USDC",
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
        accepts=_accepts_at("$0.0001"),
        description="Parse any freeform datetime string into a structured normalized form — $0.0001 USDC",
        extensions={**_datetime_parse_bazaar_ext},
    ),
}


@app.middleware("http")
async def x402_mw(request, call_next):
    return await payment_middleware(x402_routes, x402_server)(request, call_next)


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


handler = Mangum(app)
