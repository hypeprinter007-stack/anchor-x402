# Divigent integration — CloudWatch Insights queries

> CloudWatch Insights queries for inspecting Divigent integration telemetry. The queries themselves are non-sensitive; the **output** can include live wallet balances and tx hashes — share query outputs (not the queries) under appropriate confidentiality.

All queries filter for the `DIVIGENT_EVENT` prefix, which is emitted by `services/divigent.py` (anchor seller) and `investigator/divigent.py` (risk-investigator buyer).

## Log groups

| Role | Group |
|---|---|
| Seller sweep cron | `/aws/lambda/anchor-x402-DivigentSweepFunction-*` |
| Seller oracle keeper | `/aws/lambda/anchor-x402-DivigentOracleKeeperFunction-*` |
| Buyer (investigations) | `/aws/bedrock-agentcore/runtimes/risk_investigator-*-DEFAULT` |

## Quick CLI export

```bash
.venv/bin/python scripts/divigent_metrics.py --hours 24
.venv/bin/python scripts/divigent_metrics.py --hours 168 --json > divigent-week.json
```

## Saved Insights queries

Paste any of these into CloudWatch Logs Insights → select the log group(s) above → Run.

### 1. Action distribution (last 24h)

```
fields @timestamp, @message
| filter @message like /DIVIGENT_EVENT/
| parse @message /DIVIGENT_EVENT (?<json>\{.*\})/
| parse json /"role"\s*:\s*"(?<role>[^"]+)"/
| parse json /"action"\s*:\s*"(?<action>[^"]+)"/
| stats count() by role, action
| sort role, action
```

### 2. Recall/deploy amounts over time

```
fields @timestamp, @message
| filter @message like /DIVIGENT_EVENT/
| parse @message /DIVIGENT_EVENT (?<json>\{.*\})/
| parse json /"action"\s*:\s*"(?<action>[^"]+)"/
| parse json /"amount_atomic"\s*:\s*(?<amount_atomic>\d+)/
| filter action in ["deploy", "recall"]
| stats sum(amount_atomic) as total_atomic by bin(1h), action
```

### 3. Latest liquidity status per wallet

```
fields @timestamp, @message
| filter @message like /DIVIGENT_EVENT/
| parse @message /DIVIGENT_EVENT (?<json>\{.*\})/
| parse json /"wallet"\s*:\s*"(?<wallet>[^"]+)"/
| parse json /"liquidity_status"\s*:\s*"(?<liquidity_status>[^"]+)"/
| parse json /"wallet_balance_atomic"\s*:\s*"(?<wallet_balance_atomic>[^"]+)"/
| parse json /"position_current_value_atomic"\s*:\s*"(?<position_current_value_atomic>[^"]+)"/
| sort @timestamp desc
| stats latest(liquidity_status) as status, latest(wallet_balance_atomic) as idle, latest(position_current_value_atomic) as position by wallet
```

### 4. Cycle frequency (assessment cadence)

```
fields @timestamp, @message
| filter @message like /DIVIGENT_EVENT/
| parse @message /DIVIGENT_EVENT (?<json>\{.*\})/
| parse json /"event_type"\s*:\s*"(?<event_type>[^"]+)"/
| stats count() by bin(1h), event_type
```

### 5. Average duration per cycle (operational health)

```
fields @timestamp, @message
| filter @message like /DIVIGENT_EVENT/
| parse @message /DIVIGENT_EVENT (?<json>\{.*\})/
| parse json /"event_type"\s*:\s*"(?<event_type>[^"]+)"/
| parse json /"duration_ms"\s*:\s*(?<duration_ms>\d+)/
| stats avg(duration_ms), max(duration_ms) by event_type
```

### 6. Operator-approval revoked (alarm candidate)

Treasury revoked `setOperator(operator, true)` — sweeps will stop until it's reauthorized.

```
fields @timestamp, @message
| filter @message like /DIVIGENT_EVENT/
| filter @message like /divigent.operator.revoked/
| parse @message /DIVIGENT_EVENT (?<json>\{.*\})/
| parse json /"treasury"\s*:\s*"(?<treasury>[^"]+)"/
| parse json /"operator"\s*:\s*"(?<operator>[^"]+)"/
| sort @timestamp desc
```

## Event schema

```jsonc
{
  "event_type": "divigent.seller.cycle" | "divigent.buyer.preflight" | "divigent.buyer.postflight" | "divigent.keeper.cycle" | "divigent.operator.revoked",
  "ts": "2026-05-23T00:36:42.272Z",
  "role": "seller" | "buyer" | "keeper",
  "wallet": "0x...",          // wallet being assessed
  "action": "none" | "deploy" | "recall" | "skipped" | "insufficient_liquidity",
  "acted": true | false,        // whether an on-chain tx was sent
  "reason": "...",              // skip reason, if applicable
  "tx_hash": "0x...",           // present if acted=true
  "amount_atomic": "N",         // USDC atomic units (6 decimals) of the action
  "wallet_balance_atomic": "N", // current idle USDC at assessment time
  "position_current_value_atomic": "N",  // current Divigent position value
  "required_reserve_atomic": "N",        // policy + adaptive reserve target
  "pending_payment_atomic": "N",         // buyer-side only — expected investigation spend
  "risk_preference": "conservative" | "balanced" | "capital-efficient",
  "liquidity_status": "healthy" | "reserve_low" | "needs_recall" | "partial_recall_only" | "insufficient_liquidity",
  "duration_ms": N
}
```
