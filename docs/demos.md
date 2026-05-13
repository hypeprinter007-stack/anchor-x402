---
layout: default
title: anchor-x402 demos — five paid endpoints, end-to-end
description: "30-second walkthroughs of five anchor-x402 endpoints — aura, sanctions screen, dual-chain anchor, verifiable RNG, and the $7.77 wallet due-diligence investigator. See the chat → approve → sign → result loop with the real Coinbase Smart Wallet signing view."
permalink: /demos/
---

# Five demos. One protocol. No accounts.

Each demo is the same five-beat flow: chat free, agent quotes a price, approval card with the exact endpoint + body, sign one EIP-3009 USDC authorization, see the result. The signing screen is the real Coinbase Smart Wallet typed-data view — not a mockup.

<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 28px; margin: 36px 0;">

<div>
<video controls playsinline preload="metadata" poster="/og.png" style="width:100%; border-radius:6px; background:#0c0d10;">
  <source src="/demos/aura.mp4" type="video/mp4">
</video>
<h3 style="margin:14px 0 6px;">aura — $0.010</h3>
<p style="margin:0 0 8px; color:#cfd2da; font-size:14px;">Color + tier (S/A/B/C/D/F) + score (0–9999) + 2-sentence description. Universal text input. Powered by Claude on AWS Bedrock.</p>
<p style="margin:0; font-size:13px;"><code>POST /v1/aura {target}</code></p>
</div>

<div>
<video controls playsinline preload="metadata" poster="/og.png" style="width:100%; border-radius:6px; background:#0c0d10;">
  <source src="/demos/screen.mp4" type="video/mp4">
</video>
<h3 style="margin:14px 0 6px;">screen — $0.001</h3>
<p style="margin:0 0 8px; color:#cfd2da; font-size:14px;">OFAC SDN + sanctions screening for any wallet. Returns sanctions match, flagged programs (Tornado Cash, Lazarus, etc.), risk tier.</p>
<p style="margin:0; font-size:13px;"><code>GET /v1/screen?wallet=…</code></p>
</div>

<div>
<video controls playsinline preload="metadata" poster="/og.png" style="width:100%; border-radius:6px; background:#0c0d10;">
  <source src="/demos/anchor.mp4" type="video/mp4">
</video>
<h3 style="margin:14px 0 6px;">anchor — $0.005</h3>
<p style="margin:0 0 8px; color:#cfd2da; font-size:14px;">Anchor any 32-byte hash to Base + Solana mainnet in parallel. Returns both tx URLs as cryptographic proof of when the hash existed.</p>
<p style="margin:0; font-size:13px;"><code>POST /v1/anchor {hash, note}</code></p>
</div>

<div>
<video controls playsinline preload="metadata" poster="/og.png" style="width:100%; border-radius:6px; background:#0c0d10;">
  <source src="/demos/roll.mp4" type="video/mp4">
</video>
<h3 style="margin:14px 0 6px;">roll — $0.001</h3>
<p style="margin:0 0 8px; color:#cfd2da; font-size:14px;">Verifiable signed RNG. Cryptographically-random integers signed by the treasury EOA. Drop-in VRF for game studios, raffles, NFT mint reveals.</p>
<p style="margin:0; font-size:13px;"><code>POST /v1/roll {low, high, count, label?}</code></p>
</div>

<div>
<video controls playsinline preload="metadata" poster="/og.png" style="width:100%; border-radius:6px; background:#0c0d10;">
  <source src="/demos/investigate.mp4" type="video/mp4">
</video>
<h3 style="margin:14px 0 6px;">investigate — $7.77 <span style="color:#9ea3b0; font-weight:400;">· 90 s</span></h3>
<p style="margin:0 0 8px; color:#cfd2da; font-size:14px;">Multi-step wallet due-diligence. Agent runs 4–6 anchor-x402 sub-calls (sanctions, intel, name resolve, tx decode), synthesizes a verdict via Claude on AWS Bedrock, signs the deliverable, and anchors the report hash on Base + Solana. Async 5–10 min in production; this demo compresses the polling.</p>
<p style="margin:0; font-size:13px;"><code>POST /v1/investigate {address}</code></p>
</div>

</div>

<div style="text-align:center; margin: 48px 0 24px;">
  <a href="https://chat.anchor-x402.com" style="display:inline-block; padding:16px 36px; background:#d97954; color:#0c0d10; font-weight:600; border-radius:6px; text-decoration:none; font-size:18px;">Run any of these →</a>
  <p style="margin-top:12px; color:#7a7c84; font-size:14px;">Bring USDC on Base. Cheapest call is $0.001.</p>
</div>

## What every demo shows

**The agent never spends without your signature.** Each paid call shows the approval card with the exact endpoint, the exact body, and the exact USDC amount before the wallet popup. You sign one EIP-3009 `transferWithAuthorization` per call — no standing approval, no agent-burns-through-your-wallet failure mode.

**The signing screen is real.** That's the actual Coinbase Smart Wallet sign view at `keys.coinbase.com` with the EIP-712 typed-data tree expanded. The price line is rendered per-endpoint to match what you'd actually see; nothing else is mocked.

**Results are structured.** Each endpoint returns a small JSON object that an agent can route on — sanctions verdict, tier, anchor tx hashes, verifiable signature. The result cards in these demos are the same components the hosted chat at [chat.anchor-x402.com](https://chat.anchor-x402.com) renders post-payment.

## Looking for the canonical 30-second story?

The original [/demo/](/demo/) page walks through the full flow on the `aura` endpoint with five-beat narration. Use it as the lead-in; come back here for the per-endpoint variations.

## Eleven more endpoints not pictured

The catalog has 16 paid endpoints total — these 5 are a representative cross-section. The rest:

- **Decode** `/v1/decode/tx`, `/v1/decode/calldata` ($0.001 each) — structured tx + EVM calldata decode across Base/Ethereum/Solana
- **Resolve + price** `/v1/resolve/name`, `/v1/price/token` ($0.001 each) — ENS+SNS, USD spot price
- **Intel** `/v1/intel/wallet` ($0.005) — bundled wallet intelligence in one call
- **Attest** `/v1/attest` ($0.010) — verify a signature + dual-chain anchor the result
- **Parse** `/v1/parse/datetime` ($0.001) — freeform → ISO 8601
- **LLM** `/v1/roast`, `/v1/oracle`, `/v1/tldr`, `/v1/grade` ($0.01–$0.05) — universal text inputs, anchored verdicts where it makes sense

Discoverable at [`/.well-known/x402.json`](https://anchor-x402.com/.well-known/x402.json), [`/openapi.json`](https://api.anchor-x402.com/openapi.json), and the MCP server [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp).
