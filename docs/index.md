---
layout: home
title: "anchor-x402 — 18 x402-paid services for AI agents"
description: "Eighteen x402-paid endpoints on three rails — Base USDC, Solana USDC, JPYC on Polygon. Hosted agent chatbot at chat.anchor-x402.com. Pay-per-call, no API keys, no accounts."
---

## What this is, in one paragraph

anchor-x402 is **eighteen stateless x402 endpoints** that any AI agent can call and pay for in a single round-trip. Each call returns an x402 v2 PaymentRequired challenge; the agent signs an EIP-3009 authorization from its own wallet, replays the request, and gets the result. Settlement happens on **Base** or **Solana** (USDC via Coinbase's CDP facilitator) or **Polygon** (JPYC via an in-process facilitator). Live at `https://api.anchor-x402.com` — see the [/health probe](https://api.anchor-x402.com/health) or the [Swagger UI](https://api.anchor-x402.com/docs).

If you don't have your own agent, **[chat.anchor-x402.com](https://chat.anchor-x402.com)** is a hosted Claude that runs the same services on your behalf. Connect a wallet (Coinbase Smart Wallet with a passkey, MetaMask, Rabby — any of them), chat for free, and approve each paid tool call one EIP-3009 signature at a time.

## Have an x402 endpoint?

If you run a paid x402 service and want it exposed through this agent — listed in the chat surface, included in the [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp) npm package, cross-linked from this site, and discoverable through our existing listings — email **[hello@anchor-x402.com](mailto:hello@anchor-x402.com?subject=x402%20endpoint%20listing)**. Include the endpoint URL, one-line description, and price. First listing is free if it covers something we don't already do well.

White-label / custom-bot tier — your branding, your system prompt, your tool subset, your tenant-scoped subdomain, revenue share on tool calls — same address.

## For institutional reviewers

The full security posture lives at [/trust/](trust/) — STRIDE threat model, pre-filled SIG-Lite questionnaire, code-level self-audit guide, regulated deployment guide, on-chain verifiability primer, and observability docs.

No SOC 2 / ISO 27001 / PCI / HIPAA — this is the commodity tier, fit for sandboxes, POCs, and non-binding workflows. An **institutional tier** ($499–$5,000+/mo) is available on request: per-tenant authentication, signed MSA/DPA/SLA, WORM evidence vault on S3 Object Lock, GDPR Article 17 erasure reconciled with AML retention. Email [hello@anchor-x402.com](mailto:hello@anchor-x402.com).

## For agents and developers

- **Guides:** [Pay an x402 API from Node.js](/guides/pay-x402-api-node/) (complete ~30-line client) · [Accept x402 payments in Python](/guides/accept-x402-payments-python/) (the FastAPI pattern behind this service)
- **MCP server:** [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp) on npm. One config block in Claude Desktop, Claude Code, Codex CLI, ChatGPT Desktop, Cursor, or OpenAI Agents SDK and the services become callable tools that auto-pay from your Base wallet. `npx anchor-x402-mcp`.
- **Direct HTTP:** any x402 v2 client SDK works — [`@x402/fetch`](https://www.npmjs.com/package/@x402/fetch) for TypeScript, the Python x402 SDK, or Rust. Same dance: 402 → sign → retry.
- **Source:** [github.com/hypeprinter007-stack/anchor-x402](https://github.com/hypeprinter007-stack/anchor-x402) (MIT licensed — fork it, audit it, deploy your own)
- **Discovery surfaces:** [CDP Bazaar](https://docs.cdp.coinbase.com/x402/bazaar), [agentic.market](https://api.agentic.market/v1/services/search?q=api.anchor-x402.com), [Agent Arena](https://agentarena.site/api/agent/8453/47261) (ERC-8004), [Virtuals ACP](https://app.virtuals.io), the [Official MCP Registry](https://registry.modelcontextprotocol.io/v0/servers?search=anchor-x402), [Glama](https://glama.ai/mcp/servers/hypeprinter007-stack/anchor-x402-mcp), [mcp.so](https://mcp.so/server/anchor-x402-mcp).

## On-chain verifiability

`/v1/anchor`, `/v1/attest`, `/v1/oracle`, and `/v1/investigate` reports each write a 32-byte hash to **both Base and Solana mainnet**. The on-chain bytes are independent of this service — anyone can verify a receipt by reading the chains directly. Tampering would require breaking SHA-256 or simultaneously reorganizing two L1s. Live examples at [/trust/on-chain-verifiability](/trust/on-chain-verifiability).

## Contact

Christopher Ferjo · [hello@anchor-x402.com](mailto:hello@anchor-x402.com) · security disclosures: [security@anchor-x402.com](mailto:security@anchor-x402.com) · [@thexferj](https://x.com/thexferj)
