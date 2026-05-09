---
layout: default
title: anchor-x402
---

# anchor-x402

> Nine x402-paid commodity services for AI agents. One AWS Lambda, one OpenAPI spec, dual-listed for [CDP Bazaar](https://docs.cdp.coinbase.com/x402/bazaar) and [pay.sh](https://pay.sh). Pay per call in USDC on Base or Solana mainnet — no API keys, no accounts, no subscriptions.

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

## For agents and developers

- **MCP server:** [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp) on npm — drop one config block into Claude Desktop, Cursor, or any MCP client and the 9 services become callable tools that auto-pay from your Base wallet. Install: `npx anchor-x402-mcp`. Source: [github.com/hypeprinter007-stack/anchor-x402-mcp](https://github.com/hypeprinter007-stack/anchor-x402-mcp).
- **Server source:** [github.com/hypeprinter007-stack/anchor-x402](https://github.com/hypeprinter007-stack/anchor-x402)
- **License:** MIT — fork it, audit it, deploy your own
- **x402 client SDK:** [github.com/coinbase/x402](https://github.com/coinbase/x402)
- **Quickstart:** see the [repo README](https://github.com/hypeprinter007-stack/anchor-x402#quickstart-agent--consumer)

## Contact

Christopher Ferjo · [hello@anchor-x402.com](mailto:hello@anchor-x402.com) · security disclosures: [security@anchor-x402.com](mailto:security@anchor-x402.com)
