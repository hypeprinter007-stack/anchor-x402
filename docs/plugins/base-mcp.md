---
name: anchor-x402
version: 0.1.0
homepage: https://anchor-x402.com
description: Sixteen x402-paid services for AI agents on Base mainnet — dual-chain hash anchoring, OFAC sanctions screening, wallet intelligence, transaction & calldata decoding, async wallet due diligence, verifiable RNG, and a universal LLM suite. Pay-per-call USDC. No API keys, no accounts.
permalink: /plugins/base-mcp.md
---

# anchor-x402 — Base AI Agent plugin

anchor-x402 exposes sixteen pay-per-call HTTPS endpoints that any Base AI Agent can invoke through Base MCP's native `initiate_x402_request` / `complete_x402_request` flow. Every call settles in USDC on Base mainnet via the Coinbase x402 facilitator. There are no API keys, no signup, no rate keys to manage — the 402 challenge + EIP-3009 authorization handled by Base MCP is the entire auth surface.

This document is the canonical capability map. Drop it into your assistant's context (custom plugin / skill) so the model knows what anchor-x402 offers, when each capability is appropriate, and how to set safe `maxPayment` caps.

## Prerequisites

The user's assistant must already have:

1. **Base MCP installed and connected.** See https://docs.base.org/ai-agents/quickstart.
2. **A Base Account with USDC balance** on Base mainnet. The cheapest call (`/v1/screen`, `/v1/roll`, `/v1/decode/*`, `/v1/resolve/name`, `/v1/price/token`, `/v1/parse/datetime`) costs **$0.001 USDC**. Holding **$2.00 USDC** covers every endpoint at least once, including the $1.77 async investigator.
3. **No additional credentials** — anchor-x402 has no API keys, no allowlist, no per-user accounts. The 402 challenge IS the auth.

## Onboarding gate

Before the first paid call in a session:

1. Call `get_wallets` to confirm the user is on a Base account.
2. Surface the price of the chosen endpoint to the user verbatim from this catalog.
3. Recommend a `maxPayment` cap equal to the listed price — anchor-x402 pricing is fixed; no need to add headroom.

If the user's USDC balance is below the chosen endpoint's price, halt with a clear message: *"This call costs $X USDC on Base. Your Base account is below that balance — top up at https://anchor-x402.com or use a smaller endpoint first."*

## What's included

| # | Endpoint | Method | Price | Category | One-liner |
|---|----------|--------|-------|----------|-----------|
| 1 | `/v1/anchor` | POST / GET | $0.005 | security | Dual-chain hash anchoring (Base + Solana) |
| 2 | `/v1/screen` | GET / POST | $0.001 | security | OFAC + AML wallet screening |
| 3 | `/v1/attest` | POST / GET | $0.010 | security | Verify signature → dual-chain anchor |
| 4 | `/v1/decode/tx` | POST / GET | $0.001 | devtools | Structured mainnet tx decode (Base/Eth/Sol) |
| 5 | `/v1/resolve/name` | GET / POST | $0.001 | identity | ENS + Bonfida SNS resolver |
| 6 | `/v1/price/token` | GET / POST | $0.001 | finance | USD spot price (CoinGecko-backed) |
| 7 | `/v1/decode/calldata` | POST / GET | $0.001 | devtools | EVM calldata → function + typed params |
| 8 | `/v1/parse/datetime` | POST / GET | $0.001 | devtools | Freeform datetime → ISO 8601 + relative |
| 9 | `/v1/intel/wallet` | GET / POST | $0.005 | data | Unified wallet intel (8-10 sources, one call) |
| 10 | `/v1/investigate` | POST / GET | $1.77 | security (async) | Multi-step agent wallet due diligence |
| 11 | `/v1/roast` | POST / GET | $0.050 | ai_ml | LLM roast of any target |
| 12 | `/v1/oracle` | POST / GET | $0.050 | ai_ml | Yes/no oracle with anchored verdict |
| 13 | `/v1/tldr` | POST / GET | $0.010 | ai_ml | URL or text → 3-5 bullet summary |
| 14 | `/v1/aura` | POST / GET | $0.010 | ai_ml | Color + tier + 0-9999 score read |
| 15 | `/v1/grade` | POST / GET | $0.010 | ai_ml | Letter grade + red-pen marginalia |
| 16 | `/v1/roll` | POST / GET | $0.001 | security | Verifiable signed RNG (drop-in VRF) |

Base URL: **`https://api.anchor-x402.com`**

## Payment flow

For every paid call, use Base MCP's standard two-step pattern:

```json
{
  "server": "base-mcp",
  "action": "initiate_x402_request",
  "args": {
    "url": "https://api.anchor-x402.com/v1/<endpoint>",
    "method": "POST",
    "maxPayment": "<exact price from catalog>",
    "body": { /* endpoint-specific */ }
  }
}
```

User receives a Base Account approval modal showing the URL, amount, and network. After approval:

```json
{
  "server": "base-mcp",
  "action": "complete_x402_request",
  "args": { "requestId": "<from step 1>" }
}
```

The framework handles the 402 challenge, EIP-3009 transferWithAuthorization signature, facilitator settlement, and request replay automatically. The response is the paid endpoint's normal JSON body — no payment-handling code needed in the plugin.

## Service catalog

### 1. `/v1/anchor` — Dual-chain hash anchoring · $0.005

Anchor a 32-byte hash to **Base + Solana mainnet in parallel**. Returns both transaction URLs as cryptographic proof of when the hash existed.

**Use when:** the user wants a timestamp-proof record on-chain (e.g. agent decision audit, document hash, AI output hash). Two chains anchored ≈ defense in depth — no single L1 risk.

**Body:**
```json
{ "hash": "<64-char hex, no 0x>" }
```
*or* pass `{"data": <any JSON>}` to let the server canonicalize + SHA-256 it. Optional `note` (≤200 chars) returned in response, NOT stored on-chain.

**Returns:** `{ merkle_root, base: {tx, explorer_url}, solana: {tx, explorer_url}, anchored_at }`.

**Example:**
```json
{
  "server": "base-mcp",
  "action": "initiate_x402_request",
  "args": {
    "url": "https://api.anchor-x402.com/v1/anchor",
    "method": "POST",
    "maxPayment": "0.005",
    "body": { "data": { "decision": "approve_loan", "agent_id": "underwriter-v3" } }
  }
}
```

---

### 2. `/v1/screen` — OFAC + AML wallet screening · $0.001

Returns sanctions match boolean, flagged programs (Tornado Cash, Lazarus, etc.), and a risk level.

**Use when:** the agent needs a fast pre-trade / pre-disbursement compliance check on an EVM or Solana address.

**Query / body:** `wallet=<EVM 0x… or Solana base58>`

**Returns:** `{ wallet, sanctioned: bool, matches: [<program names>], chain_inferred, risk_level }`.

**Example (GET form for consumer-surface compatibility):**
```json
{
  "server": "base-mcp",
  "action": "initiate_x402_request",
  "args": {
    "url": "https://api.anchor-x402.com/v1/screen?wallet=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "method": "GET",
    "maxPayment": "0.001"
  }
}
```

---

### 3. `/v1/attest` — Signature verify + dual-chain anchor · $0.010

Verify a wallet signature over `(input_hash, output_hash, decision)` with domain separation, then anchor the resulting Merkle root to Base + Solana. Returns verified signer + on-chain proof.

**Use when:** building auditable agent provenance — anchoring a signed claim like "agent X decided Y on input Z" with cryptographic verifiability.

**Body:**
```json
{
  "input_hash": "<64-hex SHA-256>",
  "output_hash": "<64-hex SHA-256>",
  "decision": "<≤64 char label>",
  "scheme": "eip191 | ed25519",
  "signature": "<0x-hex for eip191, base58 for ed25519>",
  "signer_pubkey": "<required for ed25519>"
}
```

**Returns:** verified signer address, anchored merkle root, both chain tx URLs.

---

### 4. `/v1/decode/tx` — Structured mainnet tx decode · $0.001

Multi-chain transaction decode. EVM (Base, Ethereum): `from / to / value / gas / status / calldata`. Solana: `slot / fee / signers / program_calls`.

**Body:** `{ "chain": "base" | "ethereum" | "solana", "tx_hash": "..." }`

**Use when:** explaining what a transaction did, or extracting parameters from a tx hash without the agent needing chain-specific RPC code.

---

### 5. `/v1/resolve/name` — ENS + SNS resolver · $0.001

Cross-chain name resolution: `.eth` (ENS) and `.sol` (Bonfida SNS). Returns resolved address(es) per registry.

**Query / body:** `name=<human-readable>`

**Use when:** the user references an address as a name (`vitalik.eth`, `solana.sol`) and the agent needs the underlying pubkey.

---

### 6. `/v1/price/token` — USD spot price · $0.001

CoinGecko-backed token price by symbol or by `chain + contract`. 60s cache.

**Query / body:** `symbol=ETH` *or* `chain=base&contract=0x...`

**Use when:** quoting a USD value for a token denomination, calculating portfolio value, or showing price context.

---

### 7. `/v1/decode/calldata` — EVM calldata decoder · $0.001

Raw EVM calldata → function name + typed parameters. Powered by openchain.xyz signature directory + `eth_abi`.

**Body:** `{ "chain": "ethereum" | "base", "calldata_hex": "0x..." }`

**Use when:** decoding what an EVM tx is *attempting* before submission, or explaining a tx in a wallet UI.

---

### 8. `/v1/parse/datetime` — Freeform datetime parser · $0.001

Freeform datetime string → structured ISO 8601 + components + relative time + parser confidence.

**Body:**
```json
{ "input": "next Thursday at 3pm ET", "base_time": "<optional ISO>", "timezone": "<optional IANA>" }
```

**Use when:** normalizing user-provided times into deterministic ISO 8601 for scheduling, reminders, or on-chain commits.

---

### 9. `/v1/intel/wallet` — Unified wallet intelligence · $0.005

**One call returns:** balances on Base + Ethereum + Solana, USDC across chains, tx counts, ENS/SNS reverse, sanctions verdict — aggregated from 8-10 parallel sources.

**Query / body:** `wallet=<EVM or Solana>`

**Use when:** building a counterparty profile, KYB check, or pre-flight before sending value. Replaces 8-10 separate RPC calls with one $0.005 fetch.

---

### 10. `/v1/investigate` — Agent-driven wallet due diligence · $1.77 (async, 5-10 min)

Multi-step LLM investigation: sanctions screen, on-chain history, identity correlation, counterparty graph. Delivered as a **signed markdown report + JSON sidecar with dual-chain anchor proof on Base + Solana**. ETA 5-10 minutes.

**Body:** `{ "address": "<EVM or Solana>" }`

**Returns immediately:** `{ job_id, status: "accepted", status_url, eta_seconds }`.

**Polling:** `GET /v1/investigate/status/{job_id}` is **free** (not x402-paid). Poll every 30-60s. When `status == "DELIVERED"`, the response includes `deliverable: { reportUrl, reportJsonUrl, verdict, score, signedBy, signature, merkleRoot, baseAnchorTx, solanaAnchorTx, disclaimer }`.

**Use when:** the agent needs a substantive human-reviewable wallet investigation — counterparty diligence, fraud signals, deep history. Not for fast preflight (use `/v1/screen` + `/v1/intel/wallet` instead).

**Example dispatch:**
```json
{
  "server": "base-mcp",
  "action": "initiate_x402_request",
  "args": {
    "url": "https://api.anchor-x402.com/v1/investigate",
    "method": "POST",
    "maxPayment": "1.77",
    "body": { "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045" }
  }
}
```

**Status polling is free.** Do not use `initiate_x402_request` for `/v1/investigate/status/{job_id}` — it's not x402-paid. Approach depends on your surface:

- **Claude Code / Cursor / Codex / Claude Desktop with MCP:** use the harness's HTTP fetch (`bash curl`, the host's `fetch`/`web_fetch` tool, etc.) to GET `https://api.anchor-x402.com/v1/investigate/status/<job_id>`. Poll every 30-60s.
- **Claude.ai / ChatGPT consumer:** `web_request` rejects non-allowlisted hosts on these surfaces. Ask the user to paste the status URL into the chat when they want an update; the assistant will fetch it via standard link-following.

---

### 11. `/v1/roast` — LLM roast · $0.050

Witty roast of any target — wallet, tweet, idea, code, anything. Universal text input. Powered by Claude on AWS Bedrock.

**Body:** `{ "target": "<≤8000 chars>" }`

**Use when:** the user explicitly asks for a roast / dunking on something. **Do not invoke unsolicited** — this endpoint produces blunt, sometimes harsh language and burns $0.05.

---

### 12. `/v1/oracle` — Yes/no oracle with anchored verdict · $0.050

Yes/no question → MAYBE / YES / NO + explanation + dual-chain anchored proof (`merkle_root`, `base_tx`, `solana_tx`).

**Body:** `{ "question": "<≤1000 chars, a yes/no question>" }`

**Use when:** the user wants a decision artifact they can reference later — the verdict is anchored on-chain on both Base and Solana, making it tamper-evident.

---

### 13. `/v1/tldr` — Summarizer · $0.010

URL (fetched server-side) or pasted text → 3-5 concise bullet summary.

**Body:** `{ "url": "<https URL>" }` *or* `{ "text": "<pasted content>" }`.

**Use when:** the user wants a tight summary and the source is too long to paste; or wants a server-side fetch (e.g. paywalled, behind a redirect, or to avoid disclosing the URL to the assistant's host).

---

### 14. `/v1/aura` — Aura read · $0.010

Returns color (free-form), tier (S/A/B/C/D/F), score 0-9999, description. Screenshot-friendly viral output.

**Body:** `{ "target": "<≤4000 chars>" }`

**Use when:** the user explicitly asks for a vibe check / aura read. **Do not invoke unsolicited** — output is screenshot-friendly viral content that can read as mocking; only run on a target the user named with clear intent.

---

### 15. `/v1/grade` — Letter grade + marginalia · $0.010

Academic letter grade (A-F) + red-pen marginalia bullets + summary.

**Body:** `{ "target": "<≤6000 chars>" }`

**Use when:** critique / feedback on a piece of work — code, essay, pitch, design doc.

---

### 16. `/v1/roll` — Verifiable signed RNG · $0.001

Cryptographically-random integer(s) over a caller-chosen range, **signed by the treasury EOA (EIP-191)**. Drop-in VRF for game studios, raffles, DAO voter selection, NFT mint reveals.

**Body:**
```json
{
  "low": 1,
  "high": 100,
  "count": 1,
  "commitment": "<optional 32-byte hex pre-commitment>",
  "label": "<optional ≤200 char tag>"
}
```

The optional `commitment` is included in the signed payload to close front-running windows: commit to your inputs first, then call `/v1/roll`, and verifiers can prove the inputs were fixed before randomness was sampled.

**Returns:** `{ low, high, values: [n,...], signed_payload, signature, signer_address }`.

**Use when:** producing a result the user (or third party) can later cryptographically verify came from anchor-x402 without trusting anchor-x402's server log.

## Surface compatibility

| Surface | POST endpoints | GET endpoints | Notes |
|---------|----------------|---------------|-------|
| Claude Code / Codex / Cursor | ✅ Full | ✅ Full | Harness HTTP tool; use POST forms as documented |
| Claude Desktop with MCP | ✅ Full | ✅ Full | Same as above |
| Claude.ai (web) | ❌ web_request rejects POST to non-allowlisted hosts | ✅ Via user-paste pattern | Use the **GET form** of each endpoint; user pastes the URL with query string |
| ChatGPT consumer apps | ❌ Same restriction | ✅ Via user-paste | Same as above |

Every endpoint in this plugin has a **GET form** with query-string parameters for consumer-surface compatibility. See `https://api.anchor-x402.com/openapi.json` for the full GET parameter shapes per endpoint.

## Multi-rail availability (outside Base MCP)

Base MCP's `initiate_x402_request` settles on **Base mainnet USDC only**. anchor-x402 itself accepts three rails:

- **Base USDC** (this plugin) — `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- **Solana USDC** — `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`. Use a Solana x402 client outside Base MCP.
- **JPYC on Polygon** — yen-denominated pricing (e.g. ¥1 for `/v1/anchor`). Use a Polygon x402 client outside Base MCP.

If the user holds USDC on Solana or JPYC on Polygon and wants to use those rails, point them at the direct API + an x402 client SDK; Base MCP itself can't route those settlements today.

## Troubleshooting

**"Payment required" loops without an approval modal.** The `maxPayment` cap is below the listed price. Match it exactly from the catalog above.

**Hostname not allowlisted on web_request.** You're on a consumer surface (Claude.ai, ChatGPT). Use the GET form of the endpoint and let the user paste the URL into the chat so the assistant fetches it as a regular link-following action.

**`/v1/investigate/status/{job_id}` returns 402.** It shouldn't — the status route is intentionally unpaid. If you see this, file an issue at https://github.com/hypeprinter007-stack/anchor-x402/issues.

**Investigation stuck at `DISPATCHING` or `IN_PROGRESS` past 10 minutes.** Normal ceiling is ~10 min. If exceeded, the job will eventually transition to `FAILED` with an `error_msg` field. Refund policy: out of band; contact `hello@anchor-x402.com`.

**A call settles but the response is empty / malformed.** anchor-x402 logs every paid request server-side. Include the `requestId` from `complete_x402_request` + the timestamp when contacting `hello@anchor-x402.com`.

## Resources

- **Homepage:** https://anchor-x402.com
- **OpenAPI spec (interactive):** https://api.anchor-x402.com/docs
- **OpenAPI JSON:** https://api.anchor-x402.com/openapi.json
- **Canonical x402 discovery doc:** https://anchor-x402.com/.well-known/x402.json
- **Agent card:** https://anchor-x402.com/.well-known/agent-card.json
- **Trust portal:** https://anchor-x402.com/trust/
- **Status page:** https://anchor-x402.betteruptime.com
- **MCP server (npm):** https://www.npmjs.com/package/anchor-x402-mcp
- **GitHub:** https://github.com/hypeprinter007-stack/anchor-x402
- **Hosted chatbot demo:** https://chat.anchor-x402.com
- **Support:** `hello@anchor-x402.com` · Security: `security@anchor-x402.com`
