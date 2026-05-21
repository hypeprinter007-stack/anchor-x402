# Integrating Divigent from a serverless Python x402 seller

> Lambda-native reference example. Companion to [signalfuse-divigent-router](https://github.com/hypeprinter007-stack/signalfuse-divigent-router) (which integrates Divigent from a long-running Node sidecar).

A small, hardened AWS Lambda + EventBridge pattern that sweeps an x402 seller's idle USDC into [Divigent](https://divigent.ai) yield on Base mainnet — without ever holding the seller wallet's private key inside the function execution environment.

---

## When to use this pattern

Use this if you answer **yes** to any of:

- Your x402 resource server runs on AWS Lambda (or any per-request serverless platform — Cloudflare Workers, Vercel Functions, etc.).
- You don't have a 24/7 host to spare for a deposit sidecar.
- You want the seller wallet key to stay cold (hardware wallet, multisig) and have a separate hot wallet do the routing work.

Use the [Node sidecar pattern](https://github.com/hypeprinter007-stack/signalfuse-divigent-router) instead if you already run a long-running process next to your resource server.

---

## Architecture

```
                                     EventBridge
                                    ┌──────────────────┐
                                    │ rate(5 minutes)  │  divigent-sweep-cron
                                    │ rate(1 hour)     │  divigent-oracle-keeper
                                    └──────┬───────────┘
                                           │
              ┌─────────────────┐          │
   x402 ──▶  │ anchor-x402      │         ▼
   USDC ──▶  │  FastAPI         │   ┌──────────────────┐
   on Base   │  (Lambda)        │   │ sweep_handler.py │       ┌──────────────────┐
              │ + cold treasury  │   │ ─ read idle USDC ├──────▶│ Divigent Router  │
              │   wallet 0xS…    │   │ ─ check oracle   │       │ 0xE958…2A01      │
              └────────┬─────────┘   │ ─ deposit on     │       │  (Base mainnet)  │
                       │             │   behalf of 0xS… │       └──────────────────┘
                       │             └────────┬─────────┘                ▲
                       │                      │                          │
                       │              Operator wallet 0xO…               │
                       │              (Secrets Manager)                  │
                       │                      │ ETH for gas              │
                       │                      │                          │
                       │ /divigent/dashboard ─────────────────── reads ──┘
                       ▼                                       (position, yield)
                  ┌───────────┐
                  │ React UI  │  ── Yield panel on /seller dashboard
                  └───────────┘
```

---

## The key idea: operator delegation

The whole pattern hinges on Divigent's `setOperator()` primitive:

```solidity
function setOperator(address operator, bool approved) external;
function deposit(uint256 amount, address wallet, uint256 minSharesOut) external returns (uint256);
```

`deposit()`'s second argument is the wallet on whose behalf the position is opened — **not** the `msg.sender`. So once the seller (cold) calls `setOperator(operatorAddr, true)` once on-chain, a separate Lambda-held operator wallet can deposit/withdraw on the seller's behalf without ever seeing the seller's key.

| | SignalFuse sidecar | This pattern |
|---|---|---|
| Host | Long-running Node process | EventBridge → Lambda cron |
| Wallet key location | Sidecar process memory | Lambda-held operator key (Secrets Manager) |
| Seller wallet exposure | Key lives hot 24/7 | Key never enters Lambda |
| SDK | `@divigent/sdk` | None — direct `web3.py` contract calls |
| Sweep mechanism | In-process 5-min ticker | EventBridge `rate(5 minutes)` |

---

## Setup

1. **Generate a fresh operator wallet:**
   ```bash
   python scripts/divigent_setup.py generate-operator
   ```
   Prints a new `address` + `private_key`. Save the key to Secrets Manager under `divigent_operator_key` inside the `anchor-x402/runtime` secret.

2. **Fund the operator with ETH on Base** (~0.001 ETH covers months of cron gas at current rates).

3. **Bootstrap the treasury** — emit calldata for the three one-time transactions:
   ```bash
   python scripts/divigent_setup.py treasury-ops <operator-address>
   ```
   Sign these from the treasury wallet via Rabby / Frame / Safe / hardware wallet:
   - `router.initialize()` — registers the treasury with Divigent
   - `router.setOperator(operator, true)` — authorizes the Lambda operator
   - `usdc.approve(router, MAX_UINT256)` — one-time USDC allowance

4. **Verify the setup:**
   ```bash
   python scripts/divigent_setup.py status
   ```
   You should see `Treasury authed: True`, `Operator approved: True`, `Oracle fresh: True`.

5. **Enable and deploy:**
   ```bash
   DIVIGENT_ENABLED=true
   sam build && sam deploy
   ```
   The two cron Lambdas start firing immediately. Watch CloudWatch.

---

## Two scheduled Lambdas

### `DivigentSweepFunction` — every 5 minutes

`services.divigent_cron.sweep_handler` →

1. Read `usdc.balanceOf(treasury)` and subtract `MIN_HOT_USDC` (the reserve floor for payment-buffer liquidity).
2. If `idle - reserve < MIN_DEPOSIT` (10 USDC): return `{swept: false, reason: "below_min_deposit"}`.
3. Call `router.previewDeposit(amount)` to get expected dvUSDC shares.
4. Apply slippage tolerance: `minSharesOut = expected * (10_000 - 50) / 10_000` (50 bps default).
5. Sign `router.deposit(amount, treasury, minSharesOut)` from the operator wallet and broadcast.
6. Log the result.

Idempotent across re-invocations — a re-firing finds nothing fresh to sweep and returns silently.

### `DivigentOracleKeeperFunction` — every hour

`services.divigent_cron.oracle_keeper_handler` →

1. Read `router.oracleStatus()`.
2. If the oracle is already fresh (last observation within ~2h), return.
3. Otherwise call `oracle.recordObservation()` and broadcast.

Cheap insurance against the `StaleOracle()` revert that would otherwise block a sweep if Divigent's own keeper ever stalls. Costs ~$0.50/month at Base gas rates.

---

## Configuration

All in `.env` or Lambda env vars:

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `TREASURY_ADDRESS` | — | yes | Seller's wallet address (registered with Divigent via `initialize()`). |
| `DIVIGENT_OPERATOR_PRIVATE_KEY` | — | yes | Lambda-held operator key (stored in Secrets Manager in prod). |
| `DIVIGENT_ENABLED` | `false` | yes | Master kill-switch. Both crons short-circuit when `false`. |
| `BASE_RPC_URL` | `https://mainnet.base.org` | recommended | Paid RPC endpoint. Public Base RPC will rate-limit a 5-min ticker over time. |
| `DIVIGENT_MIN_HOT_USDC` | `5` | no | Reserve USDC kept liquid in the wallet (NOT swept). |
| `DIVIGENT_SLIPPAGE_BPS` | `50` | no | Slippage tolerance on previewDeposit / previewWithdrawNet. |

---

## HTTP surface (optional)

```
GET  /divigent/dashboard         → live position + idle + oracle state
POST /divigent/event/<type>      → log sink, matches signalfuse-divigent-router contract
```

The event sink lets a separate sidecar (or the cron Lambdas themselves, via a callback) POST snapshot/deposit/withdraw events for dashboards. Identical contract to SignalFuse's, so the same dashboard implementation works against either integration shape.

---

## Production checklist

- **Operator wallet starts with $0 USDC and just enough ETH for gas.** It never holds funds — it only signs deposit/withdraw calls. Stolen operator key ≠ stolen treasury funds; attacker can only move USDC into Divigent (and the treasury can revoke them with `setOperator(operator, false)`).
- **Operator's ETH balance auto-checked** by `scripts/divigent_setup.py status` (flags ⚠ if below 0.0005 ETH).
- **Slippage protection on every deposit** via `previewDeposit` + 50 bps tolerance. The contract reverts on `SlippageExceeded()`; we never silently overspend.
- **Master kill-switch via `DIVIGENT_ENABLED=false`.** Disables both crons without redeploying.
- **No private keys in Lambda env vars.** Operator key lives only in Secrets Manager, fetched at cold-start.

---

## Source

- Contract wrapper: [`services/divigent.py`](../services/divigent.py)
- Cron handlers: [`services/divigent_cron.py`](../services/divigent_cron.py)
- Setup CLI: [`scripts/divigent_setup.py`](../scripts/divigent_setup.py)
- Router ABI (trimmed): [`services/abis/divigent_router.json`](../services/abis/divigent_router.json)
- CloudFormation: [`template.yaml`](../template.yaml) — search for `DivigentSweepFunction`

Built with the Divigent team. PRs welcome.
