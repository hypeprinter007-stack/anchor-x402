# anchor-x402

> Eighteen x402-paid services for AI agents — nine commodity primitives, one agent-driven wallet investigator, five universal LLM endpoints (roast, oracle, tldr, aura, grade), a signed on-chain RNG (roll), and a two-endpoint x402 spend-accounting ledger. Plus a hosted-agent chatbot at [chat.anchor-x402.com](https://chat.anchor-x402.com) for users without their own agent. One AWS Lambda, one OpenAPI spec, indexed across a dozen-plus agent-discovery surfaces ([CDP Bazaar](https://docs.cdp.coinbase.com/x402/bazaar), [agentic.market](https://agentic.market), [x402scan](https://www.x402scan.com), [PayAPI Market](https://payapi.market), [Poncho](https://tryponcho.com), [MCP Registry](https://registry.modelcontextprotocol.io), [Glama](https://glama.ai) — see [Listings](#listings)). Pay per call in **USDC on Base or Solana**, or in **JPYC on Polygon** (¥1 per anchor call) — no API keys, no accounts, no subscriptions.

**Site:** https://anchor-x402.com
**Trust portal:** https://anchor-x402.com/trust/
**Live API:** `https://api.anchor-x402.com`
**Status:** https://anchor-x402.betteruptime.com
**MCP server:** [`anchor-x402-mcp`](https://www.npmjs.com/package/anchor-x402-mcp) on npm — `npx anchor-x402-mcp` plugs all 14 tools into Claude Desktop / Cursor / Continue
**Swagger UI:** [/docs](https://api.anchor-x402.com/docs)
**OpenAPI:** [/openapi.json](https://api.anchor-x402.com/openapi.json)

## Services

| Endpoint | Method | Price | Purpose |
|---|---|---|---|
| `/v1/anchor` | POST | $0.005 | Anchor a 32-byte hash to Base + Solana mainnet in parallel |
| `/v1/screen` | GET | $0.001 | Sanctions + AML screening for any wallet address |
| `/v1/attest` | POST | $0.010 | Verify a wallet signature, dual-chain anchor the result |
| `/v1/decode/tx` | POST | $0.001 | Structured decode of any mainnet tx (Base / Ethereum / Solana) |
| `/v1/resolve/name` | GET | $0.001 | Cross-chain name resolution (ENS, Bonfida SNS) |
| `/v1/price/token` | GET | $0.001 | USD spot price by symbol or chain+contract |
| `/v1/decode/calldata` | POST | $0.001 | 4byte selector + ABI param decode for EVM calldata |
| `/v1/parse/datetime` | POST | $0.001 | Freeform datetime string → structured ISO 8601 |
| `/v1/intel/wallet` | GET | $0.005 | Bundled wallet intelligence: balances + activity + identity + sanctions |
| `/v1/investigate` | POST | $1.77 | Agent-driven wallet due diligence — async, signed report + JSON sidecar, dual-chain anchored |
| `/v1/roast` | POST | $0.050 | LLM roast of a target |
| `/v1/oracle` | POST | $0.050 | Yes/no verdict on a question, dual-chain anchored |
| `/v1/tldr` | POST | $0.010 | Summarize a URL or block of text |
| `/v1/aura` | POST | $0.010 | Aura/vibe tier score |
| `/v1/grade` | POST | $0.010 | Graded feedback on text |
| `/v1/roll` | POST | $0.001 | Signed verifiable RNG (dice/range roll) |
| `/v1/ledger/summary` | POST | $0.010 | x402 spend accounting for any Base wallet — reconstructed from chain data at request time |
| `/v1/ledger/report` | POST | $0.350 | Signed + dual-chain-anchored x402 expense report (markdown + CSV) — async job |

All endpoints accept payment on **Base** or **Solana** mainnet in USDC, and — when a Polygon treasury is configured — in **JPYC** on Polygon (Japan's first FSA-licensed yen stablecoin, settled via an in-process EIP-3009 facilitator; `/v1/anchor` is priced at ¥1 per call on this rail). All return v2 `PaymentRequired` with `extensions.bazaar` so they're auto-indexed by the CDP facilitator on settlement.

## Why

Two product theses live in this repo:

1. **Trust infrastructure** (`anchor`, `attest`, `screen`, `intel-wallet`) — agents pay for cryptographic, multi-chain, signed receipts that an AI's decision happened the way it claims. Nothing else in the agentic API economy provides this primitive.

2. **Commodity utilities** (`decode/tx`, `resolve/name`, `price/token`, `decode/calldata`, `parse/datetime`) — agents pay sub-cent prices to skip orchestration code, cache misses, and rate-limited free APIs. Each call replaces 2–10 lines of boilerplate.

3. **LLM + signed RNG** (`investigate`, `roast`, `oracle`, `tldr`, `aura`, `grade`, `roll`) — premium agent-driven due diligence, universal text endpoints, and verifiable on-chain randomness, paid per call without an LLM key or RNG oracle of your own.

## Quickstart (operator)

```bash
# 1. Install deps in a venv
make install

# 2. Configure (treasury keys, CDP creds — see .env.example)
cp .env.example .env
$EDITOR .env

# 3. Local dev (no real anchors — just import smoke-test)
make local

# 4. Deploy to AWS
make build && make deploy-guided

# 5. End-to-end paid tests (costs ~$0.026 USDC total)
.venv/bin/python scripts/test_e2e.py

# 6. Test a single service
.venv/bin/python scripts/test_e2e.py --only anchor
```

## Quickstart (agent / consumer)

If you're an AI agent (or building one) using the [x402 client SDK](https://github.com/coinbase/x402):

```python
from x402 import x402ClientSync
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.mechanisms.evm.signers import EthAccountSigner
from x402.http.clients.requests import x402_requests
from eth_account import Account

cli = x402ClientSync()
cli.register("eip155:8453", ExactEvmClientScheme(signer=EthAccountSigner(Account.from_key("0xYOUR_KEY"))))
session = x402_requests(cli)

# Anchor any hash for $0.005
r = session.post("https://api.anchor-x402.com/v1/anchor",
                 json={"hash": "ab0898397c86fbf97c99c6f8b29e55ab697315705777ee1d106e2dcb9bd686b3"})
print(r.json())  # {merkle_root, base.tx, solana.tx, anchored_at}
```

The client handles x402 payment negotiation automatically — intercepts the 402, signs a USDC payment, retries.

## API

For each endpoint, the schema is auto-generated by FastAPI and served at [`/openapi.json`](https://api.anchor-x402.com/openapi.json). Try Swagger UI at [`/docs`](https://api.anchor-x402.com/docs).

### `POST /v1/anchor` — dual-chain anchor

```json
{ "hash": "<64-hex>", "note": "optional" }
```
or
```json
{ "data": { "any": "json" }, "note": "optional" }
```

Returns: `{merkle_root, base: {tx, explorer_url}, solana: {tx, explorer_url}, anchored_at, note}`.

### `GET /v1/screen?wallet=<address>` — sanctions screening

Returns: `{wallet, chain_inferred, sanctions_match, sanctioned_lists, risk_level, notes, checked_at}`.

Active corpus: OFAC SDN crypto entries (Tornado Cash, Lazarus Group, Hydra Market, Garantex, Blender.io, etc.).

### `POST /v1/attest` — signed decision attestation

```json
{
  "input_hash": "<64-hex>",
  "output_hash": "<64-hex>",
  "decision": "APPROVED",
  "scheme": "eip191" | "ed25519",
  "signature": "<hex or base58>",
  "signer_pubkey": "<solana pubkey, required for ed25519>"
}
```

Verifies the signature over the domain-separated message:
```
anchor-x402/attest/v1
input=<input_hash>
output=<output_hash>
decision=<decision>
```

Anchors the SHA-256 of that message on Base + Solana. Returns the verified signer + on-chain proof URLs.

### `POST /v1/decode/tx`

```json
{ "chain": "base" | "ethereum" | "solana", "tx_hash": "..." }
```

Returns structured tx receipt (from, to, value, gas, status, calldata for EVM; slot, signers, program_calls for Solana).

### `GET /v1/resolve/name?name=<name>` — name → addresses

Supports `.eth` (ENS) and `.sol` (Bonfida SNS).

### `GET /v1/price/token`

`?symbol=ETH` or `?chain=base&contract=0x...`. Returns USD spot, 24h change, market cap. CoinGecko-backed, 60s in-process cache.

### `POST /v1/decode/calldata`

```json
{ "chain": "ethereum", "calldata_hex": "0xa9059cbb..." }
```

Decodes via openchain.xyz signature directory + eth_abi. Returns function name + typed params.

### `POST /v1/parse/datetime`

```json
{ "input": "tomorrow at noon", "timezone": "America/New_York" }
```

Returns ISO 8601 + components + relative_seconds + confidence.

### `GET /v1/intel/wallet?wallet=<address>` — bundle play

ONE call, 8–10 parallel sources fetched, signed bundle returned: balances on Base + Ethereum + Solana, USDC across chains, tx counts, ENS/SNS reverse, sanctions verdict.

### `GET /health` — public, no payment

`{"status": "ok", "service": "anchor-x402"}`.

## Architecture

```
                          ┌───────────────────────────────────┐
client agent              │  AWS Lambda (Python 3.12)         │
   │                      │    ↓                              │
   │ x402 USDC ─────────▶ │  FastAPI + x402 middleware        │
   │ (Base or Solana)     │    ↓                              │
   │                      │  18 routes, 1 OpenAPI spec        │
   │                      │    ↓                              │
   │                      │  services/*.py                    │
   │                      │    │                              │
   │                      │    ├──► Base RPC (anchor txs)     │
   │                      │    ├──► Solana RPC (Memo txs)     │
   │                      │    ├──► CoinGecko (price)         │
   │                      │    ├──► openchain.xyz (calldata)  │
   │                      │    └──► CDP facilitator (settle)  │
   │                      └───────────────────────────────────┘
   │                                │
   ◄────────  signed JSON  ─────────┘
```

The commodity, trust, and LLM/RNG routes are stateless. The one exception is `/v1/investigate`, which records async jobs in DynamoDB and writes signed deliverables to S3, with an auto-refund-on-failure flow (push webhook + poll + daily cron). Treasury keys live in AWS Secrets Manager (`anchor-x402/runtime`); fetched at cold-start and cached in process memory. The JPYC/Polygon rail settles via an in-process EIP-3009 facilitator alongside the CDP facilitator.

## Repo layout

```
anchor-x402/
├── app.py                              # FastAPI + x402 + 18 routes
├── models.py                           # Pydantic request/response schemas
├── services/
│   ├── anchor.py                       # dual-chain hash anchoring
│   ├── attest.py                       # sig verification + anchor wrapper
│   ├── calldata_decode.py              # 4byte + ABI decode
│   ├── cdp_auth.py                     # CDP facilitator JWT auth
│   ├── cdp_heartbeat.py                # daily Bazaar keepalive probes
│   ├── datetime_parse.py               # dateparser + dateutil
│   ├── intel_wallet.py                 # parallel wallet intel bundle
│   ├── jpyc_facilitator.py             # in-process EIP-3009 facilitator (Polygon/JPYC)
│   ├── llm.py                          # Bedrock LLM client (roast/oracle/tldr/aura/grade)
│   ├── name_resolve.py                 # ENS + Bonfida SNS
│   ├── oracle.py, roast.py, tldr.py    # LLM endpoints
│   ├── aura.py, grade.py, roll.py      # LLM tiers + signed RNG
│   ├── refund.py, refund_cron.py       # /v1/investigate auto-refund (push + cron)
│   ├── screen.py                       # OFAC SDN screening
│   ├── secrets.py                      # AWS Secrets Manager helper
│   ├── token_price.py                  # CoinGecko proxy + cache
│   └── tx_decode.py                    # tx receipt decoder
├── pay-skills/
│   └── anchor-x402/                    # mirrors solana-foundation/pay-skills tree
│       ├── dual-chain/PAY.md           # one PAY.md per service for the catalog PR
│       ├── wallet-screen/PAY.md
│       ├── decision-attest/PAY.md
│       ├── tx-decode/PAY.md
│       ├── name-resolve/PAY.md
│       ├── token-price/PAY.md
│       ├── calldata-decode/PAY.md
│       ├── datetime-parse/PAY.md
│       └── intel-wallet/PAY.md
├── scripts/
│   ├── test_e2e.py                     # paid e2e across the catalog
│   ├── test_jpyc_e2e.py                # JPYC/Polygon rail e2e
│   └── gen_og.py                       # regenerate the social card (docs/og.png)
├── template.yaml                       # SAM (Lambda + APIGW + Secrets Manager + CloudWatch)
├── Makefile                            # install / lock / build / deploy / local
├── requirements.in / .txt              # pinned dependency lockfile
└── .env.example                        # treasury + CDP + RPC config
```

## Operations

**Treasury keys.** Stored in AWS Secrets Manager (`anchor-x402/runtime` — `treasury_evm_key`, `treasury_solana_key`, `cdp_api_key_secret`). The Lambda IAM role can `GetSecretValue` on this ARN only. Rotate without a redeploy:

```bash
aws secretsmanager update-secret \
  --secret-id anchor-x402/runtime \
  --secret-string '{"treasury_evm_key":"...","treasury_solana_key":"...","cdp_api_key_secret":"..."}'
# Force a fresh cold start to pick it up:
aws lambda update-function-configuration \
  --function-name $(aws lambda list-functions --query 'Functions[?starts_with(FunctionName,`anchor-x402`)].FunctionName' --output text) \
  --description "rotate $(date +%s)"
```

**Monitoring.** CloudWatch alarms publish to the `AlarmTopic` SNS topic:
- `LambdaErrorsElevated` — Errors > 5 / 5min
- `LambdaDurationP95High` — Duration p95 > 25s / 5min
- `ApiGateway5xxElevated` — 5xx > 3 / 5min
- `SolanaAnchorFailureRateHigh` — log metric filter on `"solana anchor failed"` > 5 / 5min

Subscribe an email or phone post-deploy:
```bash
aws sns subscribe \
  --topic-arn $(aws cloudformation describe-stack-resource --stack-name anchor-x402 --logical-resource-id AlarmTopic --query 'StackResourceDetail.PhysicalResourceId' --output text) \
  --protocol email --notification-endpoint you@example.com
```

**Treasury balance.** Anchor + attest + intel-wallet pay native gas (Base ETH + Solana SOL). Fund the treasury with ~0.001 ETH and ~0.05 SOL to cover several hundred anchors.

## Trust portal

For institutional review: **https://anchor-x402.com/trust/** carries the full security posture — threat model, pre-filled SIG-Lite security questionnaire, code-level self-audit guide, regulated deployment guide, on-chain verifiability primer, and observability/status setup. Source files live at [docs/trust/](docs/trust/).

## Institutional tier

anchor-x402 is the public-utility commodity tier. An **institutional tier** with per-tenant authentication, WORM evidence vault on S3 Object Lock, GDPR Article 17 erasure with AML retention reconciliation, signed MSA / DPA / SLA contracts, and dedicated support is available on request — pricing $499–$5,000+/mo depending on volume and posture. Reach out to [hello@anchor-x402.com](mailto:hello@anchor-x402.com) with your use case, expected call volume, and any compliance certifications you require.

## Listings

| Catalog | Type | Status |
|---|---|---|
| **CDP Bazaar** | x402 service registry (auto-indexed via `extensions.bazaar`) | 18 services live |
| **agentic.market** | x402 service search API | 18 services live |
| **x402scan** | x402 explorer + marketplace | auto-indexed from on-chain activity |
| **PayAPI Market** | curated x402 + MCP marketplace | live (approved 2026-06) |
| **Poncho / AgentCash** | buyer-side x402 agent (tool catalog) | listed — [tryponcho.com/m/api.anchor-x402.com](https://tryponcho.com/m/api.anchor-x402.com/) |
| **x402 List** | agent-first x402 directory | submitted, in review |
| **awesome-x402** | curated GitHub list | listed (PR #350) |
| **Agent Arena / Base ERC-8004** | on-chain agent identity (Base) | live — agentId 47261 |
| **Solana Agent Registry** | on-chain agent identity (Metaplex MPL Agent) | registered (MPL Core asset) |
| **Virtuals ACP** | Agent Commerce Protocol | resources registered |
| **Official MCP Registry** | Anthropic-maintained MCP server registry | `io.github.hypeprinter007-stack/anchor-x402` |
| **Glama** | MCP server marketplace | License A / Quality A |
| **mcp.so** | MCP server directory | live |
| **npm** | Node package registry | [`anchor-x402-mcp@0.2.1`](https://www.npmjs.com/package/anchor-x402-mcp) |

## Roadmap

- **Master / hot-wallet split.** Receive USDC into a cold master wallet; pay anchor gas from a small hot wallet in Secrets Manager. If hot key leaks, only pennies of gas float at risk.
- **Multi-sig treasury** (Safe on Base + Squads on Solana) for production-grade key custody.
- **Auto top-up** from master to hot wallet when a CloudWatch low-balance alarm fires.
- **More services** based on real agent demand: web search aggregator, PDF extraction, transactional email send.

## License

MIT — see [LICENSE](LICENSE).

## Author

Christopher Ferjo — solo build, post-[Counsel](https://github.com/hypeprinter007-stack/gavel) (EasyA Consensus 2026 hackathon project).
