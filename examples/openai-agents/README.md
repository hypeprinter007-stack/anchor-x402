# OpenAI Agents SDK + x402: Compliance Agent

A working example of an OpenAI agent calling paid HTTP APIs **without an API key**. The agent has three tools — sanctions screening, SHA-256, and dual-chain anchoring — and uses the x402 v2 protocol to auto-pay the paid tools from a Base mainnet wallet.

Total cost per agent run: **~$0.006 USDC** (one screen + one anchor).

This example is structured to drop into [openai/openai-cookbook](https://github.com/openai/openai-cookbook) as a tutorial — see [Submitting as a cookbook PR](#submitting-as-a-cookbook-pr) at the end.

## Why this exists

OpenAI's Agents SDK is great at deciding when to call tools, but each tool typically needs its own API-key wiring: stripe keys, alchemy keys, twilio keys, etc. The x402 protocol replaces that with a single Base wallet — any 402 response from a paid tool gets auto-paid with an EIP-3009 USDC authorization, signed in-memory, and the agent gets the response back transparently.

```
agent.run("Is 0xXYZ sanctioned?")
  ↓
  tool: screen_wallet("0xXYZ")
  ↓
  GET https://api.anchor-x402.com/v1/screen?wallet=0xXYZ
    → 402 Payment Required + { price: $0.001, payTo: 0x..., network: eip155:8453 }
  ↓
  x402 client: sign EIP-3009 transferWithAuthorization, retry with X-Payment header
  ↓
  → 200 { sanctions_match: false, risk_level: "low", ... }
  ↓
agent gets the result, decides next step
```

No API key. No subscription. No account creation. The agent pays per call from a wallet you fund once.

## Setup

```bash
cd examples/openai-agents
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

You need:
- An **OpenAI API key** (`OPENAI_API_KEY`) — Agents SDK calls `gpt-4o-mini` by default.
- A **Base wallet** (`BASE_PRIVATE_KEY`, 0x-prefixed) funded with a small amount of USDC. The Coinbase CDP facilitator fronts gas, so you don't need ETH on the EOA — just USDC. Fund with ~$1 to run the example many times.

⚠️ Use a **fresh agent-only EOA**, never your treasury / main wallet. The script signs from `BASE_PRIVATE_KEY` directly.

```bash
export OPENAI_API_KEY=sk-...
export BASE_PRIVATE_KEY=0xYourAgentEOA
```

## Run

```bash
# A wallet known to be on the OFAC SDN list (Tornado Cash)
python agent.py 0x8589427373d6d84e98730d7795d8f6f8731fda16

# A wallet known to be clean (vitalik.eth)
python agent.py 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
```

The agent will:
1. Call `screen_wallet(<address>)` — auto-pays $0.001 via x402, returns the OFAC verdict
2. Call `sha256_of(<json>)` — free, client-side
3. Call `anchor_hash(<hex>, note=...)` — auto-pays $0.005 via x402, dual-chain anchors
4. Print a plain-English summary + the two explorer URLs

Real captured output (run 2026-05-13 against vitalik.eth):

```
Verdict: no sanctions match found. Risk level: low.

Anchored audit:
- Base: https://basescan.org/tx/0x1d0e1c3475d2a7b3b592cb8743a4ad5beb94dbbdd728ecbf7b140610d751f841
- Solana: https://solscan.io/tx/TKyw7zxXZCs4uGoFnunhTbpkAUVrD6p3J3X5BRZm1QWKeNeUAwvoqodM9VXzHPfWTgf5T1ZAamf7QLvWvdiKedG

Hash: 9533cd8f3893f8748a9af77941dbb10ce1dd074be22c8d6ee28f9db285692977
```

The agent's three tool calls fire in order: `screen_wallet` → `sha256_of` → `anchor_hash`. Total spend per run: **~$0.006 USDC + ~$0.001 in OpenAI tokens** (gpt-4o-mini). Both explorer URLs above are real and clickable — the on-chain anchor is independently verifiable.

## How the wiring works

The interesting bit is in `agent.py`:

```python
account = Account.from_key(os.environ["BASE_PRIVATE_KEY"])
signer  = EthAccountSigner(account)
client  = x402Client().register("eip155:8453", ExactEvmScheme(signer))

@function_tool
async def screen_wallet(wallet: str) -> dict:
    async with x402HttpxClient(client=client) as http:
        r = await http.get(f"{ANCHOR_X402}/v1/screen?wallet={wallet}")
        return r.json()
```

`x402HttpxClient` is a drop-in `httpx.AsyncClient` with an x402 transport. The transport intercepts 402 responses, signs the payment payload, and retries with `X-Payment` set. From the tool's perspective it's just `await http.get(...)` — no extra branches, no try/except for payment.

The same pattern works for any number of paid endpoints. anchor-x402 has 16; see [`/.well-known/x402.json`](https://api.anchor-x402.com/.well-known/x402.json) for the full catalog.

## What x402 actually does

The protocol is short: a server that wants payment returns `402 Payment Required` with a JSON body listing accepted prices, schemes, and recipient addresses. The client picks one it can satisfy, signs the corresponding payment payload (EIP-3009 `transferWithAuthorization` for the EVM exact scheme), and replays the request with the signed payload in the `X-Payment` header. The server forwards the signature to a facilitator (Coinbase CDP is the default), the facilitator submits the on-chain settlement, and the server returns the actual 200 response once settlement succeeds.

For the agent, that whole roundtrip is invisible — it's one tool call.

## Submitting as a cookbook PR

If you want to merge this into [openai/openai-cookbook](https://github.com/openai/openai-cookbook), here's the conversion path:

1. **Convert `agent.py` to a Jupyter notebook.** The cookbook uses `.ipynb`:
   ```bash
   pip install jupytext
   jupytext --to notebook agent.py
   ```
   You'll get `agent.ipynb`. Open it, add markdown cells before each function for narration, and run the cells against a funded wallet so the outputs are captured.

2. **Place it under** `examples/agents_sdk/x402_paid_apis/`. Path conventions vary; check the cookbook's existing structure when filing the PR.

3. **PR body should include:** a one-paragraph description of what x402 is, the cost-per-run, a working notebook with captured outputs, and a link to the live x402 server (this repo's `/.well-known/x402.json`).

4. **Disclosures:** the example uses a third-party seller (anchor-x402.com, MIT-licensed). Note in the PR that the seller is independent of OpenAI and the agent only spends what's in the wallet you give it.

A real working example with captured run output tends to merge faster than a feature-request issue — concrete > requested.

## Related

- **anchor-x402 main repo**: https://github.com/hypeprinter007-stack/anchor-x402
- **Live discovery doc**: https://api.anchor-x402.com/.well-known/x402.json
- **Per-endpoint demo videos**: https://anchor-x402.com/demos/
- **x402 protocol spec**: https://x402.org
- **MCP server with the same 16 tools**: [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp)
- **Minimal TS sibling example**: [`examples/agent/`](../agent/) — same idea in 30 lines of Node
