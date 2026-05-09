"""Register anchor-x402 on Agent Arena (ERC-8004 on-chain agent registry, Base).

Pays $0.05 USDC via x402 from the gavel CLIENT_PRIVATE_KEY wallet.
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
        "Nine x402-paid commodity services for AI agents. One AWS Lambda, one OpenAPI spec, "
        "dual-listed on CDP Bazaar and pay.sh. Pay per call in USDC on Base or Solana mainnet — "
        "no API keys, no accounts, no subscriptions. Services: hash anchoring to Base+Solana ($0.005), "
        "OFAC sanctions screening ($0.001), signed decision attestation with dual-chain anchor ($0.010), "
        "transaction decode ($0.001), ENS/SNS resolution ($0.001), USD token price ($0.001), "
        "calldata 4byte+ABI decode ($0.001), freeform datetime parsing ($0.001), bundled wallet intel ($0.005). "
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
