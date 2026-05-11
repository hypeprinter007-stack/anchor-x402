---
layout: default
title: anchor-x402
---

# anchor-x402

> Fifteen x402-paid services for AI agents — nine commodity primitives, one agent-driven wallet investigator, and five universal LLM endpoints (roast, oracle, tldr, aura, grade). Plus a hosted-agent chatbot at [chat.anchor-x402.com](https://chat.anchor-x402.com) for users without their own agent. One AWS Lambda, one OpenAPI spec, indexed across 8 agent-discovery surfaces — [CDP Bazaar](https://docs.cdp.coinbase.com/x402/bazaar), [agentic.market](https://agentic.market), [Agent Arena](https://agentarena.site/api/agent/8453/47261) (ERC-8004 NFT), [Virtuals ACP](https://app.virtuals.io), the [Official MCP Registry](https://registry.modelcontextprotocol.io), [Glama](https://glama.ai/mcp/servers/hypeprinter007-stack/anchor-x402-mcp), [mcp.so](https://mcp.so/server/anchor-x402-mcp), and [npm](https://www.npmjs.com/package/anchor-x402-mcp). Pay per call in USDC on Base or Solana mainnet — no API keys, no accounts, no subscriptions.

## Try it without an agent

**→ [chat.anchor-x402.com](https://chat.anchor-x402.com)** — connect a wallet, chat with our hosted Claude agent, pay per call from your own USDC on Base. The agent quotes the price aloud before every call and never spends beyond a cap you set.

Watch a [30-second walkthrough →](/demo/)

## Live API

- **Base URL:** `https://api.anchor-x402.com`
- **Swagger UI:** [/docs](https://api.anchor-x402.com/docs)
- **OpenAPI spec:** [/openapi.json](https://api.anchor-x402.com/openapi.json)
- **Health:** [/health](https://api.anchor-x402.com/health)
- **Status page:** [anchor-x402.betteruptime.com](https://anchor-x402.betteruptime.com)

## Services

| Endpoint | Method | Price | Purpose |
|---|---|---|---|
| `/v1/anchor` | POST | $0.005 | Anchor a 32-byte hash to Base + Solana mainnet in parallel |
| `/v1/screen` | GET | $0.001 | Sanctions + AML screening for any wallet address |
| `/v1/attest` | POST | $0.010 | Verify a wallet signature, dual-chain anchor the result |
| `/v1/decode/tx` | POST | $0.001 | Structured decode of any mainnet tx |
| `/v1/resolve/name` | GET | $0.001 | Cross-chain name resolution (ENS, Bonfida SNS) |
| `/v1/price/token` | GET | $0.001 | USD spot price by symbol or chain+contract |
| `/v1/decode/calldata` | POST | $0.001 | 4byte selector + ABI param decode |
| `/v1/parse/datetime` | POST | $0.001 | Freeform datetime → structured ISO 8601 |
| `/v1/intel/wallet` | GET | $0.005 | Bundled wallet intelligence (balances + activity + identity + sanctions) |

## For institutional reviewers

The full security posture lives at [/trust/](trust/) — threat model, pre-filled SIG-Lite security questionnaire, code-level self-audit guide, regulated deployment guide, on-chain verifiability primer, and observability setup. Start with [trust/](trust/).

If you need an **institutional tier** beyond what anchor-x402's commodity tier provides — per-tenant authentication, signed MSA / DPA / SLA contracts, WORM evidence vault, GDPR Article 17 erasure, dedicated support — email [hello@anchor-x402.com](mailto:hello@anchor-x402.com). Available $499–$5,000+/mo depending on volume and posture.

## For agents and developers

- **MCP server:** [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp) on npm — drop one config block into Claude Desktop, Cursor, or any MCP client and the 9 services become callable tools that auto-pay from your Base wallet. Install: `npx anchor-x402-mcp`. Source: [github.com/hypeprinter007-stack/anchor-x402-mcp](https://github.com/hypeprinter007-stack/anchor-x402-mcp).
- **Server source:** [github.com/hypeprinter007-stack/anchor-x402](https://github.com/hypeprinter007-stack/anchor-x402)
- **License:** MIT — fork it, audit it, deploy your own
- **x402 client SDK:** [github.com/coinbase/x402](https://github.com/coinbase/x402)
- **Quickstart:** see the [repo README](https://github.com/hypeprinter007-stack/anchor-x402#quickstart-agent--consumer)

## Contact

Christopher Ferjo · [hello@anchor-x402.com](mailto:hello@anchor-x402.com) · security disclosures: [security@anchor-x402.com](mailto:security@anchor-x402.com)
