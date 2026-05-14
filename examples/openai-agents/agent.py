"""OpenAI Agents SDK + x402: a Compliance Agent that pays per call.

Demonstrates an OpenAI agent calling paid HTTP APIs without an API key.
The agent has two tools — sanctions screening and dual-chain anchoring —
both served by anchor-x402.com over the x402 v2 protocol. Each tool call
returns HTTP 402, the x402 httpx client signs an EIP-3009 USDC
authorization in-memory, retries the request, and the agent gets the
response back transparently. Total spend per agent run: ~$0.006 USDC.

Run:
    OPENAI_API_KEY=sk-...   \\
    BASE_PRIVATE_KEY=0x...  \\
    python agent.py 0x8589427373d6d84e98730d7795d8f6f8731fda16
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys

from agents import Agent, Runner, function_tool
from eth_account import Account
from x402.client import x402Client
from x402.http.clients.httpx import x402HttpxClient
from x402.mechanisms.evm.exact.client import ExactEvmScheme
from x402.mechanisms.evm.signers import EthAccountSigner

ANCHOR_X402 = "https://api.anchor-x402.com"
BASE_NETWORK = "eip155:8453"  # Base mainnet


def _build_x402_client() -> x402Client:
    """Build a Base-mainnet x402 client from BASE_PRIVATE_KEY in env."""
    pk = os.environ.get("BASE_PRIVATE_KEY")
    if not pk:
        raise SystemExit("BASE_PRIVATE_KEY env var required (0x-prefixed)")
    account = Account.from_key(pk)
    signer = EthAccountSigner(account)
    client = x402Client()
    client.register(BASE_NETWORK, ExactEvmScheme(signer))
    return client


_x402 = _build_x402_client()


@function_tool
async def screen_wallet(wallet: str) -> dict:
    """Sanctions + AML screen for a Base or Ethereum wallet address.

    Costs ~$0.001 USDC, auto-paid via the x402 protocol. Returns
    `sanctions_match`, `sanctioned_lists`, and a `risk_level` tier.
    """
    async with x402HttpxClient(_x402) as http:
        r = await http.get(f"{ANCHOR_X402}/v1/screen?wallet={wallet}")
        r.raise_for_status()
        return r.json()


@function_tool
async def anchor_hash(hex_hash: str, note: str | None = None) -> dict:
    """Dual-chain anchor a 32-byte hex hash on Base + Solana mainnet.

    Costs ~$0.005 USDC, auto-paid via the x402 protocol. Returns the
    resulting `base.tx`, `solana.tx`, and explorer URLs — anyone can
    later re-compute the same hash client-side and verify it matches.
    """
    body = {"hash": hex_hash, "note": note} if note else {"hash": hex_hash}
    async with x402HttpxClient(_x402) as http:
        r = await http.post(f"{ANCHOR_X402}/v1/anchor", json=body)
        r.raise_for_status()
        return r.json()


@function_tool
def sha256_of(data: str) -> str:
    """Compute SHA-256 of a UTF-8 string. Free, client-side."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


compliance_agent = Agent(
    name="Compliance Agent",
    instructions=(
        "You vet wallet addresses for sanctions risk. For each address the user "
        "gives you: (1) call `screen_wallet` to get a sanctions verdict; "
        "(2) compute a SHA-256 of the verdict JSON via `sha256_of`; "
        "(3) call `anchor_hash` with that hash + a short `note` so the "
        "verdict is dual-chain anchored on Base + Solana for later audit. "
        "Report the verdict in plain English plus the two on-chain explorer "
        "URLs so the user can verify independently."
    ),
    tools=[screen_wallet, sha256_of, anchor_hash],
)


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python agent.py <wallet-address>", file=sys.stderr)
        sys.exit(2)
    wallet = sys.argv[1]
    result = await Runner.run(compliance_agent, f"Vet this wallet: {wallet}")
    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
