# Observability — public metrics + status page

> Two transparency mechanisms: a CloudWatch dashboard you can share publicly + a status page your customers can subscribe to.

## CloudWatch dashboard (created)

A public dashboard `anchor-x402-public` is provisioned in the operator's AWS account showing five panels:

| Panel | Source | What it shows |
|---|---|---|
| Request volume | `AWS/ApiGateway · Count` | Sum of all requests across the 9 services per minute |
| Lambda duration | `AWS/Lambda · Duration` (avg / p95 / p99) | Cold-start budget vs warm latency |
| Lambda errors | `AWS/Lambda · Errors` | Unhandled exceptions in the function |
| API Gateway 4xx + 5xx | `AWS/ApiGateway · 4xx / 5xx` | Includes 402 responses (paid endpoints unpaid) and real failures |
| Solana anchor failures | custom log metric `anchor-x402::SolanaAnchorFailures` | RPC drops captured from the function's log group |

**Dashboard URL (operator-private):**
`https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=anchor-x402-public`

To make it accessible to non-AWS-authenticated viewers (e.g. customers, prospects, journalists), enable CloudWatch Dashboard Sharing:

```bash
# 1. (One-time, account-level) enable dashboard sharing
aws cloudwatch put-managed-insight-rules ...   # AWS Console: CloudWatch → Settings → Dashboard sharing → "Allow this account to share dashboards"

# 2. Generate a public-shareable URL
# AWS Console: CloudWatch → Dashboards → anchor-x402-public → "Share dashboard" → "Share with everyone (public, no auth)"
# This emits a URL of the form:
#   https://cloudwatch.amazonaws.com/dashboard.html?dashboard=anchor-x402-public&context=<token>
# Copy that URL into the trust portal README.
```

Cost: dashboard sharing is free; the underlying metrics are AWS-billed at standard CloudWatch rates (~$3/month at this volume).

## Status page (recommended setup)

Two free options. Pick one and follow the matching path.

### Option A — BetterStack (Better Uptime) free tier

- Sign up at https://betterstack.com — free tier: 10 monitors, public status page, email/Slack notifications.
- Add a monitor on `https://1c09pdnrx1.execute-api.us-east-1.amazonaws.com/health` (HTTP, expect 200, JSON body contains `"status":"ok"`).
- Configure 1-minute check interval, alert on 2 consecutive failures.
- Create a public status page: `status.anchor-x402.com` (CNAME from your DNS provider) or a `<subdomain>.betteruptime.com` URL.
- Optional: add monitors on key paid endpoints — they'll show 402 Payment Required, which BetterStack treats as "failure" by default. Configure expected status code = 402 to make it green.

### Option B — Statuspage.io (Atlassian)

- Free tier exists but is less generous (1 metric, basic page). Not recommended over BetterStack for this scale.

### Option C — Hosted on AWS (Route 53 health checks + S3 static site)

- Set up Route 53 health checks against `/health`.
- Generate a static page from health-check status via Lambda + S3 — see https://github.com/awslabs/aws-status-page for a reference implementation.
- More work, less polished, but everything stays in AWS and incurs no third-party dependency.

**Recommendation:** start with BetterStack free tier. Migrate later if traffic justifies it.

## What to expose publicly

For a commodity-tier x402 service, the optimal transparency level is:

✅ **Expose:**
- Aggregate request volume per service per hour
- Aggregate error rate per service per hour
- Latency percentiles (p50, p95, p99)
- Solana RPC failure rate (this is a known flaky upstream — being transparent about it builds credibility)
- Recent incidents + resolution timelines

❌ **Don't expose:**
- Per-customer request patterns (none of our customers should be identifiable; this is a courtesy even when they're not authenticating)
- Treasury wallet balances (operational secret; revealing makes treasury a target)
- AWS account ID (informational leakage)
- Lambda function name (informational leakage; the dashboard above includes it for operator use)

When configuring a public dashboard, scrub the metrics block to drop `FunctionName` dimension labels — replace them with friendly aliases. The CloudWatch share UI doesn't make this easy; the cleaner path is to expose metrics through the status-page provider's hosted dashboard rather than via direct CloudWatch share.

## Linking from the trust portal

After the status page is live, update [docs/trust/README.md](README.md) to add a "Live status" line linking to the public URL. Update the main repo README accordingly.

## What customers should monitor on their side

A consumer of anchor-x402 should:

1. **Treat /health as the liveness probe.** It's free, returns 200, and is unauth.
2. **Track their own request error rate, not ours.** A 503 from us is your job to handle gracefully (retry, fallback, fail-open vs fail-closed depending on use case).
3. **Verify on-chain anchors independently** for any decision they're storing as evidence (see [on-chain-verifiability.md](on-chain-verifiability.md)).
4. **Subscribe to the status page** when it launches so they get incident notifications without polling.
