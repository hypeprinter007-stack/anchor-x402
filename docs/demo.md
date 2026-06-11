---
layout: default
title: anchor-x402 demo — 30 seconds, end-to-end
description: "Watch the full anchor-x402 flow in 30 seconds — chat free, type a target, see the price the agent quotes, approve with passkey, see the result. No accounts, no API keys, USDC settlement on Base mainnet."
permalink: /demo/
---

# 30 seconds, end-to-end

<video controls playsinline preload="metadata" poster="/og.png" style="width:100%; max-width:480px; display:block; margin:0 auto 32px; border-radius:6px; background:#0b1220;">
  <source src="/demo.mp4" type="video/mp4">
  Your browser can't play HTML5 video. <a href="/demo.mp4">Download the MP4</a>.
</video>

<div style="text-align:center; margin: 24px 0 40px;">
  <a href="https://chat.anchor-x402.com" style="display:inline-block; padding:14px 28px; background:#7dd3fc; color:#0b1220; font-weight:600; border-radius:6px; text-decoration:none;">Try it now →</a>
  <span style="display:inline-block; margin:0 12px; color:#94a3b8;">·</span>
  <a href="https://github.com/hypeprinter007-stack/anchor-x402">source</a>
  <span style="color:#94a3b8;"> · </span>
  <a href="https://api.anchor-x402.com/docs">api docs</a>
</div>

## What you just saw

**Beat 1 — chat free.** The agent introduces itself; the page suggests a few one-tap prompts. Nothing has cost anything yet.

**Beat 2 — quote the price.** You ask for an aura check on Elon Musk. The agent says it'll run `aura` for $0.01 and shows an approval card with the exact endpoint (`POST /v1/aura`), the input (`Elon Musk`), and the cost in USDC.

**Beat 3 — connect.** Tap *connect*. A Coinbase Smart Wallet popup appears. Touch ID / Face ID creates a passkey-based wallet in your browser — no app install, no seed phrase, no email. The wallet is non-custodial and bound to your device.

**Beat 4 — approve.** The button relabels to *approve $0.010*. Tap again. Your wallet signs **one** EIP-3009 USDC `transferWithAuthorization` for exactly that amount, to exactly the anchor-x402 treasury, on Base mainnet. The Coinbase CDP facilitator settles the payment; gas is on them.

**Beat 5 — the result.** Aura returns a color, a tier (S → F), a 0–9999 score, and a punchy description. The agent translates the JSON into natural language. Your spend pill in the corner ticks up to $0.010 / $5.00.

## What you didn't see

- An account creation form. There isn't one.
- An API key. None required.
- A subscription. Nothing recurring.
- A custodial wallet. Your private key lives in your device's secure enclave.

Each paid call is its own EIP-3009 signature for a single amount to a single address — there is no standing approval, no "agent burns through your wallet while you sleep" failure mode.

## Want to see the rest?

[Five demos, one per endpoint →](/demos/) — aura, sanctions screen, dual-chain anchor, verifiable RNG, and the $1.77 wallet due-diligence investigator with its 90-second async flow.

## 15 services in this same flow

`aura` ($0.01) is one of fifteen. Same approval pattern works for:

- **Compliance** — OFAC sanctions screen ($0.001), bundled wallet intel ($0.005), full async due-diligence investigator ($1.77)
- **Chain utilities** — dual-chain hash anchoring ($0.005), signed decision attestations ($0.01), tx decode, calldata decode, ENS / SNS resolution, token price, datetime parsing ($0.001 each)
- **Fun / shareable** — roast ($0.05), oracle with on-chain anchored verdict ($0.05), tldr ($0.01), aura ($0.01), grade ($0.01)

All on the same Lambda, same x402 v2 protocol, same CDP facilitator. The MCP server [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp) gives any agent (Claude Desktop / Cursor / Codex / ChatGPT Desktop) the same 14 tools (everything except the async investigator and the hosted chat surface).

## Specifically for the wallet-curious

- **Protocol**: x402 v2, payment requirements in the `payment-required` response header, EIP-3009 `transferWithAuthorization` settlement on Base or SPL-USDC on Solana
- **Facilitator**: official Coinbase CDP (`api.cdp.coinbase.com/platform/v2/x402`), which handles ERC-6492 unwrapping so smart-wallet signatures verify correctly
- **Treasury**: [`0x127462e296fAc1A7F5cF33bA57bB2f0FFf5cD0B6`](https://basescan.org/address/0x127462e296fAc1A7F5cF33bA57bB2f0FFf5cD0B6) — on-chain receipts are independent of the service
- **Source**: [MIT-licensed](https://github.com/hypeprinter007-stack/anchor-x402) — fork it, audit it, deploy your own

The full security posture lives at [/trust/](/trust/) — STRIDE threat model, SIG-Lite questionnaire, regulated deployment guide.

<div style="text-align:center; margin: 48px 0 24px;">
  <a href="https://chat.anchor-x402.com" style="display:inline-block; padding:16px 36px; background:#7dd3fc; color:#0b1220; font-weight:600; border-radius:6px; text-decoration:none; font-size:18px;">Run a paid call →</a>
  <p style="margin-top:12px; color:#94a3b8; font-size:14px;">Bring USDC on Base. Cheapest call is $0.001.</p>
</div>
