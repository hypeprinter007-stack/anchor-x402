# anchor-x402

> Dual-chain mainnet anchoring as an x402-paid service. Pay $0.005 USDC, get a 32-byte hash anchored on **both** Base and Solana mainnet, with on-chain proof URLs returned in the response.

## What it does

`POST /v1/anchor` — submit a 32-byte hex hash (or arbitrary JSON to be hashed). The server writes the hash to:

- **Base mainnet** as EIP-1559 calldata
- **Solana mainnet** via the Memo program

…in parallel, and returns both tx hashes plus block-explorer URLs. Two independent L1s, one Merkle root, one paid call.

Forging the anchor requires reorging two L1s.

## Why

There's no other primitive in the agentic API economy that gives an AI agent a cryptographic, public, multi-chain proof that *something specific happened at a specific time* — for half a cent.

Use cases:
- DAO vote receipts
- AI decision attestations
- Contract notarization
- Scientific data integrity
- Custom audit trails for any agent workflow

## Listings

This service is dual-listed for x402-native discovery:

- **CDP Bazaar** — auto-indexed via `extensions.bazaar` in the 402 response (see `app.py`).
- **pay.sh** — submission lives at `pay-skills/PAY.md`; PR'd into `solana-foundation/pay-skills`.

Same endpoint serves both. Same Solana USDC payment satisfies both.

## Pricing

| | |
|---|---|
| Per-call price | $0.005 USDC |
| Settlement | Base mainnet **or** Solana mainnet |
| Anchor cost (Base) | ~$0.0006 in ETH (paid by treasury) |
| Anchor cost (Solana) | ~$0.0008 in SOL (paid by treasury) |

Margin per call: ~$0.0036 before AWS Lambda + RPC overhead.

## Quickstart (operator)

```bash
# 1. Install
make install

# 2. Set env (treasury keys, CDP creds — see .env.example)
cp .env.example .env
$EDITOR .env

# 3. Local dev (no real anchors)
make local

# 4. Deploy to AWS
make build && make deploy-guided
```

## API

### `POST /v1/anchor` — x402-gated, $0.005 USDC

Body (one of `hash` or `data` required):

```json
{
  "hash": "ab0898397c86fbf97c99c6f8b29e55ab697315705777ee1d106e2dcb9bd686b3",
  "note": "optional 200-char note (off-chain)"
}
```

Or:

```json
{
  "data": { "any": "json", "the": "server", "will": "hash" },
  "note": "optional"
}
```

Response:

```json
{
  "merkle_root": "ab0898397c86fbf97c99c6f8b29e55ab697315705777ee1d106e2dcb9bd686b3",
  "base": {
    "tx": "0x...",
    "explorer_url": "https://basescan.org/tx/0x..."
  },
  "solana": {
    "tx": "...",
    "explorer_url": "https://solscan.io/tx/..."
  },
  "anchored_at": 1746820000,
  "note": "..."
}
```

### `GET /health` — public, no payment

Returns `{"status": "ok", "service": "anchor-x402"}`.

### `GET /docs` — public Swagger UI

Auto-generated from the FastAPI app. Browse the schema, try the unauth routes.

### `GET /openapi.json` — public OpenAPI spec

Used by both Bazaar and pay.sh discovery.

## Architecture

```
client agent ── x402 $0.005 USDC ──► anchor-x402 (AWS Lambda)
                                          │
                                          ├──► Base mainnet (calldata tx)
                                          └──► Solana mainnet (Memo tx)
                                          │
                                          ▼
                                    {merkle_root, base, solana, anchored_at}
```

Stateless. No DynamoDB, no S3, no auth (yet). Pure function of (hash, treasury keys) → tx URLs.

## Roadmap

- `screen/wallet` — sanctions + AML screening for any wallet address ($0.001/call)
- `attest/decision` — generic decision-receipt service with input/output hash + signer ($0.01/call)
- KMS-backed treasury keys
- Multi-sig treasury

## License

MIT
