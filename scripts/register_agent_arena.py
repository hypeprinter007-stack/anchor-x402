"""Register anchor-x402 on Agent Arena (ERC-8004 on-chain agent registry, Base).

WARNING: POST /api/register MINTS A NEW AGENT. It does not update an existing
one. Running this against an already-listed agent creates a duplicate listing —
which is how agentId 60138 came to exist alongside the older 47261 on
2026-07-29. There is no DELETE on the API (OPTIONS reports only POST, PUT), so a
duplicate cannot be undone without registry-side help. PUT /api/register is the
documented update path, but it returns the same $0.05 402 challenge with the
same "Register a new AI agent" description, so whether it updates or mints again
is unverified — do not assume.

Canonical listing is agentId 60138. Pays $0.05 USDC via x402 from the gavel
CLIENT_PRIVATE_KEY wallet (EIP-3009 signed authorization, so the payer needs
USDC but no ETH — the facilitator covers gas).
Mints an ERC-8004 NFT at 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432.

Usage:
    /Users/cferjoair/gavel/.venv/bin/python scripts/register_agent_arena.py
"""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

load_dotenv("/Users/cferjoair/gavel/.env")

from eth_account import Account
from x402 import x402ClientSync
from x402.http.clients.requests import x402_requests
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner

CLIENT_KEY = os.environ["CLIENT_PRIVATE_KEY"]
TREASURY = os.environ.get("TREASURY_ADDRESS", "")
ENDPOINT = "https://agentarena.site/api/register?a2a=true&mcp=true"

PAYLOAD = {
    "name": "anchor-x402",
    "description": (
        "Eighteen x402-paid services for AI agents. One AWS Lambda, one OpenAPI spec. Pay per "
        "call in USDC on Base or Solana mainnet — no API keys, no accounts, no subscriptions. "
        "Hash anchoring to Base+Solana ($0.005), OFAC sanctions screening ($0.001), signed "
        "decision attestation with dual-chain anchor ($0.010), transaction and calldata decode "
        "($0.001), ENS/SNS resolution ($0.001), token price ($0.001), datetime parsing ($0.001), "
        "bundled wallet intel ($0.005), verifiable signed RNG ($0.001), x402 spend accounting "
        "($0.01), agent-driven wallet due diligence ($1.77), and five LLM endpoints ($0.01-$0.05). "
        "Also exposes a free agent-to-agent door at POST /v1/a2a: signed JSON-RPC 2.0, no API "
        "key, authenticate with an Ed25519 key in your own agent card. Quotes, receipts and "
        "daily receipt roots anchored to Base + Solana. "
        "Sources: github.com/hypeprinter007-stack/anchor-x402. MCP: anchor-x402-mcp on npm."
    ),
    "capabilities": [
        "anchoring",
        "compliance",
        "sanctions",
        "screening",
        "attestation",
        "tx-decode",
        "ens",
        "sns",
        "name-resolution",
        "price-oracle",
        "calldata-decode",
        "datetime-parse",
        "wallet-intel",
        "a2a",
        "agent-to-agent",
        "signed-receipts",
        "verifiable-rng",
        "spend-accounting",
        "due-diligence",
        "llm",
        "x402",
        "base",
        "solana",
        "evm",
    ],
    "services": [
        {"name": "x402", "endpoint": "https://api.anchor-x402.com"},
        {
            "name": "A2A",
            "endpoint": "https://anchor-x402.com/.well-known/agent-card.json",
            "version": "0.3.0",
            # The card is the discovery document per spec; name the JSON-RPC door
            # too so a registry consumer does not have to fetch to find it.
            "rpcEndpoint": "https://api.anchor-x402.com/v1/a2a",
            "transport": "json-rpc-2.0",
            "signedCard": True,
        },
        {
            "name": "MCP",
            "endpoint": "https://www.npmjs.com/package/anchor-x402-mcp",
            "version": "2025-06-18",
        },
        {"name": "web", "endpoint": "https://anchor-x402.com"},
    ],
    "pricing": {"per_task": 0.005, "currency": "USDC", "chain": "base"},
    "x402Support": True,
    "preferredChain": "base",
    "agentWallet": TREASURY or Account.from_key(CLIENT_KEY).address,
    "supportedTrust": ["reputation", "crypto-economic"],
    "image": "https://anchor-x402.com/og.png",
}


def main():
    payer = EthAccountSigner(Account.from_key(CLIENT_KEY))
    cli = x402ClientSync()
    cli.register("eip155:8453", ExactEvmClientScheme(signer=payer))
    s = x402_requests(cli)

    print(f"Payer: {Account.from_key(CLIENT_KEY).address}")
    print(f"Receiver wallet (agentWallet): {PAYLOAD['agentWallet']}")
    print(f"POST {ENDPOINT}  (full bundle — $0.25 USDC on Base)\n")

    r = s.post(ENDPOINT, json=PAYLOAD, timeout=120)
    print(f"HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        body = r.json()
        print(json.dumps(body, indent=2))
    else:
        print(r.text[:2000])

    if r.status_code != 200:
        sys.exit(1)

    body = r.json()
    print("\n=== SAVE THESE ===")
    print(f"globalId:   {body.get('globalId')}")
    print(f"agentId:    {body.get('agentId')}")
    print(f"chainId:    {body.get('chainId')}")
    print(f"txHash:     {body.get('txHash')}")
    print(f"agentUri:   {body.get('agentUri')}")
    print(f"profileUrl: {body.get('profileUrl')}")


if __name__ == "__main__":
    main()
