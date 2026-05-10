# Virtuals ACP — 9 Resource Offerings (paste-ready)

Open https://app.virtuals.io/agent/<your-agent-id> → ACP tab → Resources Offered → **+ Add Resource**.

For each of the 9 entries below, paste **Name**, **Description**, **URL** (the URL templating with `{{var}}` will auto-detect Query Parameters), then describe each parameter. All 9 use HTTPS GET, all paid in USDC on Base or Solana mainnet (varies — see live 402 challenge from `api.anchor-x402.com`). payTo = treasury (`0x127462e296fAc1A7F5cF33bA57bB2f0FFf5cD0B6`), enforced by anchor-x402's x402 middleware.

Naming convention: `getX` camelCase per Virtuals validator (e.g. `getMarketData`).

---

## 1. `getHashAnchor` — $0.005

**Name:** `getHashAnchor`

**Description:**
```
Anchor any 32-byte hash to Base mainnet (EIP-1559 calldata) and Solana mainnet (Memo program) in a single $0.005 USDC call. Returns both tx hashes plus block-explorer URLs as cryptographic proof a specific value existed at a known time.
```

**URL:**
```
https://api.anchor-x402.com/v1/anchor?hash={{hash}}&note={{note}}
```

**Parameters:**
- `hash` — 64-char hex SHA-256 (no `0x` prefix). Required.
- `note` — Optional 200-char label echoed in the response (NOT written on-chain).

---

## 2. `getSanctionsScreen` — $0.001

**Name:** `getSanctionsScreen`

**Description:**
```
Sanctions + AML screening for any EVM or Solana wallet. Returns sanctions match boolean, specific OFAC SDN programs flagged (Tornado Cash, Lazarus, Hydra, Garantex, Blender.io etc.), inferred chain, and a low/medium/high risk verdict.
```

**URL:**
```
https://api.anchor-x402.com/v1/screen?wallet={{wallet}}
```

**Parameters:**
- `wallet` — EVM (`0x…`) or Solana base58 address to screen against OFAC SDN sanctions lists.

---

## 3. `getDecisionAttest` — $0.010

**Name:** `getDecisionAttest`

**Description:**
```
Verify a wallet signature over (input_hash, output_hash, decision) with domain separation, then dual-chain anchor the Merkle root on Base + Solana mainnet. Returns verified signer plus on-chain proof URLs. Schemes: EVM personal_sign or Solana Ed25519.
```

**URL:**
```
https://api.anchor-x402.com/v1/attest?input_hash={{input_hash}}&output_hash={{output_hash}}&decision={{decision}}&scheme={{scheme}}&signature={{signature}}&signer_pubkey={{signer_pubkey}}
```

**Parameters:**
- `input_hash` — 64-char hex SHA-256 of the input.
- `output_hash` — 64-char hex SHA-256 of the output.
- `decision` — `APPROVED` | `REJECTED` | `ESCALATED`.
- `scheme` — `eip191` (EVM) or `ed25519` (Solana).
- `signature` — Hex (eip191) or base58 (ed25519).
- `signer_pubkey` — Required for ed25519; optional for eip191 (auto-recovered).

---

## 4. `getTxDecode` — $0.001

**Name:** `getTxDecode`

**Description:**
```
Structured decode of any Base or Ethereum mainnet transaction. Returns block number, timestamp, status, gas, decoded transfers and method signatures.
```

**URL:**
```
https://api.anchor-x402.com/v1/decode/tx?chain={{chain}}&tx_hash={{tx_hash}}
```

**Parameters:**
- `chain` — `base` | `ethereum`.
- `tx_hash` — 0x-prefixed 32-byte tx hash.

---

## 5. `getNameResolve` — $0.001

**Name:** `getNameResolve`

**Description:**
```
Cross-chain name resolution for ENS (Ethereum) and Bonfida SNS (Solana). Returns one or more addresses with chain context — useful for resolving human-readable names to wallet addresses before payments, lookups, or intel queries.
```

**URL:**
```
https://api.anchor-x402.com/v1/resolve/name?name={{name}}
```

**Parameters:**
- `name` — ENS or SNS name (e.g. `vitalik.eth`, `bonfida.sol`).

---

## 6. `getTokenPrice` — $0.001

**Name:** `getTokenPrice`

**Description:**
```
Fetch the current USD price for any major token by symbol (BTC, ETH, SOL, USDC...) or by chain + contract (Base, Ethereum, Solana, Polygon, Arbitrum). Returns USD price, 24h change %, market cap, source, cache age.
```

**URL:**
```
https://api.anchor-x402.com/v1/price/token?symbol={{symbol}}
```

**Parameters:**
- `symbol` — Token symbol (e.g. `ETH`, `SOL`, `USDC`). For chain+contract lookups, use the alternate URL: `…/v1/price/token?chain={{chain}}&contract={{contract}}` (register as a separate offering if needed).

---

## 7. `getCalldataDecode` — $0.001

**Name:** `getCalldataDecode`

**Description:**
```
Decode raw EVM calldata into a function name, canonical signature, and typed parameter values. Resolves the 4-byte selector against openchain.xyz; ABI-decodes args. Returns ambiguity candidates on selector collision.
```

**URL:**
```
https://api.anchor-x402.com/v1/decode/calldata?chain={{chain}}&calldata_hex={{calldata_hex}}
```

**Parameters:**
- `chain` — `base` | `ethereum` | `polygon` | `arbitrum` | `optimism`.
- `calldata_hex` — `0x`-prefixed hex calldata.

---

## 8. `getDatetimeParse` — $0.001

**Name:** `getDatetimeParse`

**Description:**
```
Parse freeform datetime strings (e.g. "tomorrow at noon", "2026-05-08T15:30Z", "in 2 hours") into ISO 8601, unix epoch, broken-out components, signed relative-seconds delta, human relative phrase, and a confidence label.
```

**URL:**
```
https://api.anchor-x402.com/v1/parse/datetime?input={{input}}&timezone={{timezone}}
```

**Parameters:**
- `input` — Freeform datetime string.
- `timezone` — IANA timezone (e.g. `America/New_York`). Default: `UTC`.

---

## 9. `getWalletIntel` — $0.005

**Name:** `getWalletIntel`

**Description:**
```
One call returns a unified intel bundle for any EVM or Solana wallet — native balances on Base + Ethereum, USDC balances, tx count, has-history flag, reverse ENS/SNS, and sanctions verdict. Fetched in parallel; replaces 8-10 separate RPC + API hits.
```

**URL:**
```
https://api.anchor-x402.com/v1/intel/wallet?wallet={{wallet}}
```

**Parameters:**
- `wallet` — EVM (`0x…`) or Solana base58 address.

---

## After registering all 9

1. Confirm all 9 resources appear in your agent's profile.
2. Test discovery via the SDK:
   ```python
   from virtuals_acp.client import VirtualsACP
   acp.browse_agents(keyword="anchor")
   ```
3. Other agents will discover your resources and hit the URLs with x402 payment headers — anchor-x402's existing FastAPI Lambda serves the response, payment lands in treasury.
