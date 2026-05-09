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

from models import AnchorRequest, AnchorResponse, ChainAnchor
from services import anchor as anchor_svc
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

_accepts: list[PaymentOption] = []
if TREASURY:
    _accepts.append(PaymentOption(
        scheme="exact", pay_to=TREASURY, price="$0.005", network="eip155:8453",
    ))
if SOLANA_TREASURY:
    _accepts.append(PaymentOption(
        scheme="exact", pay_to=SOLANA_TREASURY, price="$0.005", network=SOLANA_MAINNET_CAIP2,
    ))

x402_routes = {
    "POST /v1/anchor": RouteConfig(
        accepts=_accepts,
        description="Anchor a 32-byte hash to Base + Solana mainnet — $0.005 USDC",
        extensions={**_anchor_bazaar_ext},
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


handler = Mangum(app)
