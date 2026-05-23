# Integrating Divigent from a serverless Python x402 seller

> Lambda-native reference example. Companion to [signalfuse-divigent-router](https://github.com/hypeprinter007-stack/signalfuse-divigent-router) (which integrates Divigent from a long-running Node sidecar).

A two-layer pattern that lets an x402 seller running on AWS Lambda use Divigent's full **liquidity intelligence layer** without ever holding the seller wallet's private key inside the function execution environment, and without porting Divigent's intelligence math into the seller's codebase.

---

## When to use this pattern

Use this if you answer **yes** to any of:

- Your x402 resource server runs on AWS Lambda (or any per-request serverless platform — Cloudflare Workers, Vercel Functions, etc.).
- You don't have a 24/7 host to spare for a deposit sidecar.
- You want the seller wallet key to stay cold (hardware wallet, multisig) and have a separate hot wallet do the routing work.
- You want Divigent's intelligence layer to make the deposit/recall decisions rather than running a static `idle - reserve_floor` formula yourself.

Use the [Node sidecar pattern](https://github.com/hypeprinter007-stack/signalfuse-divigent-router) instead if you already run a long-running process next to your resource server.

---

## Architecture

```
                  ┌───────────────────────────────┐
   EventBridge ─▶ │ DivigentSweepFunction (Py)    │     ┌──────────────────────┐
   rate(5min)    │ ── execution layer ───────────▶│ ──▶ │ DivigentIntelligence │
                 │                                │     │ Function (Node 22)   │
                 │ holds operator key             │ ◀── │ wraps @divigent/sdk  │
                 │ signs + broadcasts             │     │  1.0.3+ — black box  │
                 └───────────────────────────────┘     └──────────────────────┘
                              │                                    │
                              │ recommendedAction:                 │ reads:
                              │   deploy / recall / none           │   policyContext
                              │                                    │   on-chain state
                              ▼                                    │
                       ┌────────────────┐                          │
                       │ Divigent Router│ ◀────────────────────────┘
                       │ 0xE958…2A01    │   (read-only RPC)
                       └────────────────┘
```

**The intelligence stays inside the Node Lambda.** Divigent's compiled SDK runs `assessLiquidity()` over chain reads and returns a JSON decision. The Python Lambda holds the operator key and executes the decision via operator-delegated signing. Nothing about how Divigent computes the reserve appears in this codebase.

---

## The two layers, expanded

### Intelligence layer — `services/divigent-intelligence/` (Node 22)

A thin Lambda that imports `@divigent/sdk` and exposes one entrypoint:

```js
// POST { action: "assessLiquidity", wallet, policyContext, includeVenueHealth }
// Returns { ok: true, assessment: LiquidityAssessment }
```

It has **no `walletClient`** — only a `publicClient` configured against your Base RPC. It cannot sign or broadcast. It bundles to ~775KB with esbuild (`@divigent/sdk` + `viem`) and cold-starts in well under a second.

### Execution layer — `services/divigent.py` (Python)

Holds the operator key from Secrets Manager. The `assess_and_act()` function:

1. Invokes the intelligence Lambda with the configured `policyContext`.
2. Reads `recommendedAction` from the response: `'none' | 'deploy' | 'recall' | 'insufficient_liquidity'`.
3. Signs and broadcasts the corresponding transaction (`router.deposit(...)` or `router.withdraw(...)`) on behalf of the cold treasury wallet via operator delegation.

A `rate(5 minutes)` EventBridge schedule fires this loop.

---

## The key idea: operator delegation + intelligence isolation

Two orthogonal hygiene properties stack:

| Property | Mechanism |
|---|---|
| **Treasury key never enters Lambda** | `router.setOperator(operatorAddr, true)` — operator deposits/withdraws on behalf of treasury. Treasury private key stays in cold storage (hardware wallet, multisig). |
| **Divigent's intelligence math never enters our codebase** | The intelligence Lambda bundles `@divigent/sdk` as a black box. The Python execution layer consumes JSON decisions, not the math behind them. |

Comparison table:

| | SignalFuse sidecar | This pattern |
|---|---|---|
| Host | Long-running Node process | EventBridge → 2 scheduled Lambdas |
| Wallet key | In sidecar process memory | Lambda-held operator key (Secrets Manager) |
| Treasury key exposure | Key lives hot 24/7 | Key never enters Lambda |
| Intelligence layer | Calls SDK in-process | Separate Node Lambda; Python consumes JSON |
| Decision logic | Static `idle - reserve` | Divigent's `assessLiquidity()` |
| SDK language | TypeScript | TypeScript inside Node Lambda; Python execution layer has no SDK dependency |

---

## Setup

1. **Generate a fresh operator wallet:**
   ```bash
   .venv/bin/python scripts/divigent_setup.py generate-operator
   ```
   Prints a new address + private key. Save the key — Secrets Manager (production) or `.env` (local CLI).

2. **Fund the operator with ETH on Base** (~0.001 ETH covers months of gas). The operator's ETH is used by the *cron* to sign deposits/withdraws; the bootstrap step below uses the **treasury wallet** to pay its own gas. You can do this fund in parallel with step 3.

3. **Bootstrap the treasury** — sign 3 one-time txs (`router.initialize()`, `router.setOperator()`, `usdc.approve()`). All three are signed by the treasury wallet. Pick one option:

   **Option A — external signer / hardware wallet / multisig:**
   ```bash
   .venv/bin/python scripts/divigent_setup.py treasury-ops <operator-address>
   ```
   Prints the 3 transactions as calldata. Sign + broadcast via Rabby / Frame / Safe / your preferred external flow.

   **Option B — treasury key already in `.env` or Secrets Manager:**
   ```bash
   .venv/bin/python scripts/divigent_setup.py bootstrap-treasury <operator-address>
   ```
   Signs and broadcasts the same 3 transactions automatically using the treasury key. Idempotent — skips any step already done.

4. **Verify:**
   ```bash
   .venv/bin/python scripts/divigent_setup.py status
   ```
   You should see `Treasury authed: True`, `Operator approved: True`, `Oracle fresh: True`.

5. **Deploy with the policy + flag:**
   ```bash
   sam build && sam deploy --parameter-overrides \
     "DivigentEnabled=true DivigentOperatorPrivateKey=0xYOUR_KEY"
   ```
   Optional policy overrides on the same command (defaults are anchor-shaped):
   ```
   DivigentRiskPreference=balanced
   DivigentMaxDeployablePercent=95
   DivigentMinOperatingBalance=5000000
   ```

---

## Three scheduled Lambdas

### `DivigentSweepFunction` — every 5 minutes

`services.divigent_cron.sweep_handler` → `services.divigent.assess_and_act()`:

1. Safety preflight: `depositsPaused?`, `authorizedWallets?`, `isOperator?`.
2. Invoke the intelligence Lambda with `{wallet, policyContext}`.
3. Switch on `recommendedAction`:
   - `none` — return; nothing to do.
   - `deploy` — call `router.deposit(recommendedDeploymentAmount, treasury, minSharesOut)` with 50 bps slippage haircut.
   - `recall` — call `router.withdraw(recommendedRecallShares, treasury, minUsdcOut)` to pull funds back from yield into the operating reserve.
   - `insufficient_liquidity` — log; cannot fully fund target reserve.
4. Log structured event + return.

### `DivigentIntelligenceFunction` — invoked-on-demand

Internal Lambda, no public API Gateway. Called only by `DivigentSweepFunction` via `lambda:InvokeFunction`. Wraps `@divigent/sdk`.

### `DivigentOracleKeeperFunction` — every hour

Pings `oracle.recordObservation()` if the on-chain oracle is stale. Cheap insurance against the `StaleOracle()` revert that would otherwise block a sweep if Divigent's own keeper ever stalls. ~$0.50/month at Base gas rates.

---

## Policy parameters

All CloudFormation parameters; safe to update via stack update without code changes:

| Parameter | Default | Purpose |
|---|---|---|
| `DivigentEnabled` | `false` | Master kill-switch. Both crons short-circuit when `false`. |
| `DivigentOperatorPrivateKey` | — | Lambda-held operator key (lands in Secrets Manager via the composite secret). |
| `DivigentMinOperatingBalance` | `5000000` (5 USDC) | Hard reserve floor — `policyContext.minOperatingBalance`. |
| `DivigentUpcomingPayouts` | `0` | Sum of known upcoming USDC outflows. Anchor has none. |
| `DivigentMaxDeployablePercent` | `95` | Max % of wallet USDC that may be deployed at any time. |
| `DivigentRiskPreference` | `conservative` | `'conservative' \| 'balanced' \| 'capital-efficient'`. Adjusts the SDK's adaptive reserve math. |
| `BaseRpcUrl` | public Base RPC | Paid endpoint strongly recommended once crons run on a schedule. |

---

## HTTP surface

```
GET  /divigent/dashboard         → live position + idle + oracle state
POST /divigent/event/<type>      → log sink, matches signalfuse-divigent-router contract
```

The event sink lets a separate sidecar (or the cron Lambdas themselves) POST snapshot/deposit/withdraw events for dashboards. Identical contract to SignalFuse's, so the same dashboard implementation works against either integration shape.

---

## Production checklist

- **Operator wallet starts with $0 USDC and just enough ETH for gas.** Stolen operator key ≠ stolen treasury funds; attacker can only move USDC into Divigent, and the treasury can revoke them with `setOperator(operator, false)`.
- **Operator's ETH balance auto-checked** by `scripts/divigent_setup.py status` (flags ⚠ if below 0.0005 ETH).
- **Slippage protection on every deposit and withdraw** via `previewDeposit` / `previewWithdrawNet` + 50 bps tolerance. The router reverts on `SlippageExceeded()`; we never silently overspend.
- **Master kill-switch via `DivigentEnabled=false`.** Disables both crons without code changes.
- **No private keys in Lambda env vars.** Operator key lives only in Secrets Manager, fetched at cold-start.
- **Paid Base RPC.** The public `mainnet.base.org` rate-limits sustained cron traffic — `BaseRpcUrl` CFN param must point at a keyed endpoint (CDP, Alchemy, dRPC, etc.).

---

## Source

- Intelligence layer: [`services/divigent-intelligence/`](../services/divigent-intelligence/)
- Execution wrapper: [`services/divigent.py`](../services/divigent.py)
- Cron handlers: [`services/divigent_cron.py`](../services/divigent_cron.py)
- Setup CLI: [`scripts/divigent_setup.py`](../scripts/divigent_setup.py)
- Router ABI (trimmed): [`services/abis/divigent_router.json`](../services/abis/divigent_router.json)
- CloudFormation: [`template.yaml`](../template.yaml) — search for `Divigent*Function`

Built with the Divigent team. PRs welcome.
