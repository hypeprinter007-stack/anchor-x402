# anchor-x402 agent example

The smallest useful agent that pays [anchor-x402](https://anchor-x402.com) from a real Base wallet — no API key, no account, no subscription.

Composes two paid endpoints in one run:

1. **`POST /v1/screen`** (~$0.001) — sanctions + AML check on a target wallet
2. **`POST /v1/anchor`** (~$0.005) — dual-chain SHA-256 anchor of the verdict on Base + Solana mainnet

Total spend per run: **~$0.006 USDC**. The verdict ends up cryptographically attestable from a Base tx + a Solana tx — anyone can recompute the hash later and verify the agent's call returned exactly this verdict at exactly this block height.

## Setup

```bash
cd examples/agent
npm install
```

You need a Base wallet (any EOA private key) funded with a small amount of USDC. The CDP facilitator fronts gas, so you don't need ETH on the EOA — just USDC. Fund with ~$1 to comfortably run the example many times.

```bash
export BASE_PRIVATE_KEY=0xYourPrivateKey  # NOT the treasury — use a fresh agent-only EOA
```

## Run

```bash
node agent.mjs 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045   # vitalik.eth, expected: clean
node agent.mjs 0x8589427373d6d84e98730d7795d8f6f8731fda16   # tornado-cash address, expected: sanctioned
```

Real captured output (run 2026-05-13 against vitalik.eth, with the test agent EOA elided):

```
agent 0xAGENT… → screen(0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045)  ~$0.001
{
  "wallet": "0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
  "chain_inferred": "ethereum",
  "sanctions_match": false,
  "sanctioned_lists": [],
  "risk_level": "low",
  "notes": "No matches against the active sanctions corpus. Note: list is refreshed from public sources only; institutional users should pair with proprietary AML data for residual coverage.",
  "checked_at": 1778716046
}

agent 0xAGENT… → anchor(13db67d0…)  ~$0.005
{
  "merkle_root": "13db67d09ebb4705cf7011696a756415edaa4c617995c0fa73a8331b874ce735",
  "base": {
    "tx": "0x35a9cec2ec1d16ca332a21d1a52103e7c338b60fe76a1e6add3d6c3f59ec9e6e",
    "explorer_url": "https://basescan.org/tx/0x35a9cec2ec1d16ca332a21d1a52103e7c338b60fe76a1e6add3d6c3f59ec9e6e"
  },
  "solana": {
    "tx": "3tdLus9Q9qXKECWhfaG4To7Xak8ns9rCeMPwTNgPywrvswWPJeREvH8UV3ysFYzvM5xsw627xGfxN8BE53LBMdbX",
    "explorer_url": "https://solscan.io/tx/3tdLus9Q9qXKECWhfaG4To7Xak8ns9rCeMPwTNgPywrvswWPJeREvH8UV3ysFYzvM5xsw627xGfxN8BE53LBMdbX"
  },
  "anchored_at": 1778716047,
  "note": "screen verdict for 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
}

done — proof on Base: https://basescan.org/tx/0x35a9cec2ec1d16ca332a21d1a52103e7c338b60fe76a1e6add3d6c3f59ec9e6e
        proof on Solana: https://solscan.io/tx/3tdLus9Q9qXKECWhfaG4To7Xak8ns9rCeMPwTNgPywrvswWPJeREvH8UV3ysFYzvM5xsw627xGfxN8BE53LBMdbX
```

Both explorer URLs above are real and clickable — anyone can verify the hash actually landed on-chain.

## What's happening under the hood

- `@x402/fetch` wraps the global `fetch` so any 402 response is auto-paid: the client signs an EIP-3009 `transferWithAuthorization` for the exact USDC amount to the seller, re-attaches the payment header, and replays the request. From the agent's perspective it's just `await fetch(url)` — no extra branches.
- `@x402/evm`'s `ExactEvmScheme` is the EIP-3009 signer. It plugs into the wallet via viem's `signTypedData` so the same code works with a private key, an injected browser wallet, a Coinbase Smart Wallet, a hardware wallet — anything `viem` supports.
- The server (a single AWS Lambda + FastAPI) returns 402 with payment requirements on first hit; the client retries with the payment header; the Coinbase CDP facilitator settles on Base mainnet; the server returns 200 with the actual response.

No standing approval. No agent-burns-through-your-wallet failure mode. Each paid call is one signature for one exact amount to one exact recipient.

## Extending

`agent.mjs` is 30 lines on purpose. To turn this into something useful:

- **Sanctions webhook**: HTTP server that screens + anchors any address posted to it. Useful as a pre-flight for fund managers.
- **Multi-step due diligence**: replace the simple `screen` with the bigger `/v1/investigate` ($1.77, async, signed markdown report).
- **CSV bulk-screen**: read a list of addresses, screen + anchor each, append results to a file. ~$0.006 per address.
- **Compose with other x402 services**: the [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp) package exposes all 16 endpoints as MCP tools for Claude Desktop / Cursor / Codex / any MCP client. Same auth model; one Base wallet pays for everything.

Discovery: [`/.well-known/x402.json`](https://anchor-x402.com/.well-known/x402.json) · [`/openapi.json`](https://api.anchor-x402.com/openapi.json) · [trust portal](https://anchor-x402.com/trust/) · [MIT source](https://github.com/hypeprinter007-stack/anchor-x402).
