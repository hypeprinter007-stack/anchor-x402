---
layout: page
title: "Accept x402 payments in Python (FastAPI) — minimal working server"
description: "Charge USDC per API call with the x402 Python SDK and FastAPI: one middleware, one route config, Coinbase's facilitator settles on Base. No accounts, no API keys, no payment UI. This is the exact pattern behind a production 16-endpoint service."
permalink: /guides/accept-x402-payments-python/
---

# Accept x402 payments in Python

Charge per request in USDC without issuing API keys, running a billing system, or
touching private keys at request time. A buyer hits your endpoint, gets a `402`
challenge, signs a gasless USDC authorization, retries, and Coinbase's facilitator
settles it on-chain to your wallet. This page is the minimal version of the exact
pattern serving [api.anchor-x402.com](https://api.anchor-x402.com/docs) in production.

## Install

```bash
pip install "x402[evm,fastapi]" fastapi uvicorn
```

You need two things: an EVM address you control (to receive USDC on Base) and a
free [CDP API key](https://portal.cdp.coinbase.com/) — the facilitator's `/settle`
endpoint requires it (a JWT auth header built from the key; the
[reference implementation](https://github.com/hypeprinter007-stack/anchor-x402/blob/main/services/cdp_auth.py)
is ~50 lines). No ETH, no node, no hot key on the server — settlement is done by
the facilitator, and funds land at your address.

## The server

```python
# server.py — uvicorn server:app
import os

from fastapi import FastAPI
from x402.http import HTTPFacilitatorClient, FacilitatorConfig, PaymentOption
from x402.http.middleware.fastapi import payment_middleware, RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

TREASURY = os.environ["TREASURY_ADDRESS"]  # your 0x… address on Base

# Coinbase's hosted facilitator verifies + settles payments for you.
# For settlement you must pass auth_provider= (a JWT built from your free
# CDP API key) — see the reference implementation linked above.
facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url="https://api.cdp.coinbase.com/platform/v2/x402")
)
server = x402ResourceServer(facilitator_clients=[facilitator])
server.register("eip155:8453", ExactEvmServerScheme())  # Base mainnet USDC

app = FastAPI()

# Which routes cost what. Everything not listed here stays free.
routes = {
    "GET /quote": RouteConfig(
        accepts=[PaymentOption(
            scheme="exact",
            network="eip155:8453",
            pay_to=TREASURY,
            price="$0.001",
        )],
        description="A paid quote — $0.001 USDC",
    ),
}


@app.middleware("http")
async def x402_mw(request, call_next):
    return await payment_middleware(routes, server)(request, call_next)


@app.get("/quote")
def quote():
    # Only runs after payment is verified. Write normal handlers.
    return {"quote": "anything worth computing is worth charging $0.001 for"}
```

Run it, then `curl -i localhost:8000/quote` — you'll see the `402` challenge your
buyers' clients consume. Any x402 client
([Node example →](/guides/pay-x402-api-node/)) can now pay it with zero coordination
from you.

## What the middleware does

- **No payment header** → replies `402` with a challenge: price, asset (USDC),
  network, and your `payTo` address.
- **Valid `PAYMENT-SIGNATURE`** → the facilitator verifies the buyer's EIP-3009
  authorization, settles USDC on-chain to your address, and your handler runs.
- **Invalid/expired payment** → `402` again. Return a machine-readable hint so
  agent clients recover (we return `retry_hint: {action: "sign_fresh_authorization"}`).

Production notes from running this at scale (16 endpoints, three settlement rails):

- **CORS ordering (Starlette):** register `CORSMiddleware` *last* so it wraps the
  payment middleware — otherwise short-circuited `402`s go out without CORS headers
  and browser wallets can't read the challenge.
- **Put the challenge in the body too.** The SDK defaults to a header-only challenge
  (base64 in `payment-required`); strict clients and validators expect the JSON body.
- **Log your funnel.** Emit one structured line per challenge and per settled call —
  402s never reach middleware inside the payment layer, so log from outside it.

## Getting discovered by agents

Payments only matter if buyers find you:

- Serve a machine catalog at `/.well-known/x402.json` and an
  [`llms.txt`](https://llmstxt.org/) index.
- Declare [Bazaar discovery extensions](https://docs.cdp.coinbase.com/x402/bazaar)
  on each route — CDP's Bazaar catalogs your endpoint on its first settled payment.
- Expect probes: a dozen x402 monitors (uptime trackers, trust oracles, explorers)
  will start hammering your 402s within days. That's free listing coverage — keep
  your challenge responses fast and well-formed.

## Working reference

anchor-x402 runs this exact stack in production — 16 paid endpoints from $0.001 to
$1.77 on Base, Solana, and Polygon (JPYC). Source:
[github.com/hypeprinter007-stack/anchor-x402](https://github.com/hypeprinter007-stack/anchor-x402) ·
live challenges: `curl -i https://api.anchor-x402.com/v1/screen?address=0x0` ·
buying instead? [Pay an x402 API from Node.js →](/guides/pay-x402-api-node/)
