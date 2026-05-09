# Learnings — building anchor-x402

> Concrete things I learned shipping a 9-service x402 commodity API + MCP server + trust portal + custom domain in ~24 hours, post-Counsel hackathon. Mostly the non-obvious gotchas; the obvious stuff (FastAPI, AWS Lambda, Solana RPC) is well-documented elsewhere.

## Protocol-layer findings

### CDP facilitator enforces a $0.001 USDC minimum payment

Three of the original services were priced at $0.0005 (`/v1/decode/tx`, `/v1/resolve/name`) and $0.0001 (`/v1/parse/datetime`). All three returned 500 to clients during paid e2e tests. CloudWatch logs showed `ValueError: Facilitator verify failed (400): {"invalidReason":"invalid_payload","isValid":false}`.

Pattern: every failing service was priced **below 1000 raw units** (USDC has 6 decimals — $0.001 = 1000). Bumping all three to $0.001 fixed it immediately.

This is undocumented in CDP's public materials. If you're tempted to price below $0.001, don't — the facilitator silently rejects it pre-execution.

### CDP Bazaar v2 indexing isn't deterministic from outside

Counsel emitted a wire-correct `extensions.bazaar` block (verified by base64-decoding the `payment-required` header) matching SignalFuse's exact shape (`{info, schema}`). Multiple settlements over ~3 hours; never appeared in the discovery API.

SignalFuse's indexed entries all carry `quality.l30DaysTotalCalls` in the dozens or hundreds. There's likely a volume gate or async batch process before a resource surfaces in the public catalog.

For new resources expecting auto-indexing: **ship and wait days, not minutes.** Don't iterate on extension shape based on short-window absence — you can't see what's failing on their side.

### pay.sh CI prober treats `401` as indeterminate

If your service has customer-auth-before-x402 ordering (anonymous request returns 401 before the 402 even fires), the pay-skills CI doesn't fail your PR. It logs an indeterminate status and passes you through. Important for institutional services that gate behind a customer key.

## Library / runtime gotchas

### `dateparser` 1.4.0 can't parse "next Tuesday at 3pm"

Returns `None`. Library limitation, not Lambda-side. Other inputs work fine:

| Input | dateparser result |
|---|---|
| `"tomorrow at noon"` | ✓ parsed correctly |
| `"in 2 hours"` | ✓ parsed correctly |
| `"2026-05-13"` | ✓ ISO fast path |
| `"march 15 2026"` | ✓ parsed correctly |
| `"next Tuesday at 3pm"` | ✗ returns `None` |

If you wrap `dateparser` for an x402 service, expect occasional 400s on inputs that look human-natural but trip the parser's relative-weekday-plus-time logic.

### npm publish silently hangs through non-TTY contexts

`npm publish` with 2FA enabled blocks waiting for an OTP prompt that can't render in a non-TTY shell (Claude Code's `!` prefix, CI pipelines, etc.). Output is empty; exit hangs forever.

Fixes (ordered by ease):
1. **Granular Access Token with "Bypass 2FA" checkbox** + write to `~/.npmrc` (`//registry.npmjs.org/:_authToken=npm_...`). Once configured, publishes work through any context.
2. Pass OTP inline: `npm publish --access public --otp=123456`.
3. Switch account-level 2FA mode from "Authorization and writes" to "Authorization only".

### Cloudflare proxy must be OFF (gray cloud) when provisioning GitHub Pages SSL

Orange cloud (proxied) blocks Let's Encrypt's domain-validation challenge for the apex domain. GitHub Pages can't issue a cert; the site loads but HTTPS is broken.

**Sequence that works:**
1. Set DNS records, **proxy OFF** (gray cloud)
2. Enable GitHub Pages → branch + folder
3. Wait for "Your site is live at..." banner (~1–2 min)
4. Wait for HTTPS provisioning (~5–10 min)
5. Click "Enforce HTTPS"
6. *Now* you can flip records to proxied (orange) if you want CDN/WAF, with Cloudflare SSL/TLS mode = "Full"

### GitHub Pages + Jekyll skips dotdirectories by default

`/.well-known/x402.json` and `/.well-known/agent-card.json` won't be served unless you add to `_config.yml`:

```yaml
include:
  - .well-known
```

Cayman theme has an undocumented hook: `_includes/head_custom.html` auto-included in `<head>`. Use it for OG meta, JSON-LD, etc. — much cleaner than a custom layout override.

## Operational gotchas

### Wide `grep` on `.env` files is a credential leak waiting to happen

Twice during this work I leaked secrets into the conversation transcript by grepping too broadly:

| Bad | Good |
|---|---|
| `grep TREASURY .env` | `grep '^TREASURY_ADDRESS=' .env` |
| `cat .env` | `python3 -c "...select-only-public-fields..."` |

The treasury private keys leaked once (cost ~30 min of rotation: sweep funds → generate new keys → update Secrets Manager → redeploy). The npm token leaked once (lower stakes — revoke + regenerate).

**Rule:** never grep `.env` without pinning the prefix. Never `cat` a secrets file. Treat .env files like wallet keys: read with structured tools that filter to public fields only.

### CFN for API Gateway HTTP API custom domain — three resources, in concert

```yaml
ApiCustomDomainName:
  Type: AWS::ApiGatewayV2::DomainName
  Properties:
    DomainName: api.example.com
    DomainNameConfigurations:
      - CertificateArn: !Ref ApiCustomDomainCertArn
        EndpointType: REGIONAL
        SecurityPolicy: TLS_1_2

ApiCustomDomainMapping:
  Type: AWS::ApiGatewayV2::ApiMapping
  Properties:
    DomainName: !Ref ApiCustomDomainName
    ApiId: !Ref ServerlessHttpApi
    Stage: $default          # ← not "ServerlessHttpApi.Stage" or similar
```

Got the Stage wrong on first pass. The auto-deployed stage for SAM-managed HTTP APIs is **literally** `$default` — quote it as a string.

### Solana CDP facilitator requires the treasury USDC ATA to exist

The Coinbase facilitator simulates Solana inbound payments before settling. If the treasury's USDC ATA doesn't exist yet, simulation fails with `InvalidAccountData` and the facilitator returns an error.

**Fix:** create the ATA once, before first settlement, paying ~0.002 SOL rent from the treasury wallet's SOL balance. Counsel had a `scripts/create_treasury_usdc_ata.py` script for this; same pattern needed for any new Solana treasury.

### CloudWatch alarm log metric filter targets need the log group to exist

`AWS::Logs::MetricFilter` requires `/aws/lambda/${AnchorFunction}` to exist when the stack is being created. Lambda auto-creates the log group on first invocation, so on the *first* deploy the metric filter creation can race ahead and fail.

**Fix:** invoke the function once after deploy (e.g., hit `/health`) before checking that the alarm reaches `OK` state. Or pre-create the log group as a CFN resource.

## Strategic / product findings

### Two-tier strategy for x402 services has clear shape

| Tier | Examples | Posture |
|---|---|---|
| **Public utility commodity** | anchor-x402 (this repo) | No auth, no contract, $0.001–$0.010/call, open source, anyone-can-call. High volume, thin margin per call. |
| **Institutional** | Counsel-style (planned, separate product) | Auth + retention + SLA + DPA + WORM evidence vault. $0.05–$0.50/call, signed agreement, regulated buyers. |

Same primitives. Same on-chain anchoring + signing infra. Different operational posture for different buyer. Clear upgrade path: an anchor-x402 customer who needs more posture moves to the institutional tier without changing their business logic.

### Agentic traffic optimization ≠ SEO

Agents don't render HTML or follow Open Graph. They probe **machine-readable surfaces**:

| Surface | Who reads it |
|---|---|
| `/openapi.json` | x402 client SDKs, MCP servers, custom agents |
| `/.well-known/x402.json` | Bazaar-style discovery, agentic.market, x402.direct |
| `/.well-known/agent-card.json` | Google A2A protocol, AP2 discovery |
| `llms.txt` | Anthropic's, OpenAI's, Perplexity's training/search crawlers |
| `robots.txt` (with explicit AI-bot allow) | Same crawlers + general SEO |
| MCP server in npm + Glama + Smithery | Claude Desktop, Cursor, Codex, ChatGPT Desktop, Continue, OpenAI Agents SDK |
| GitHub repo + topics + README | Code-search agents, awesome-list maintainers |

OG/Twitter Card meta + JSON-LD help when humans share the URL on social media. They don't help an autonomous agent decide to call your API.

### The gap between "code shipped" and "discoverable to agents" is bigger than expected

Rough breakdown of post-hackathon work hours:

- ~10% — actual API code (extracted from Counsel, stripped, redeployed)
- ~15% — operational hardening (Secrets Manager migration, CloudWatch alarms, custom domain, branded email)
- ~20% — paid e2e testing, debugging facilitator issues, treasury rotation
- ~30% — distribution (MCP server, npm publish, awesome-x402 PR, pay-skills PR)
- ~25% — discovery surfaces (well-known files, llms.txt, trust portal, status page, JSON-LD)

For a commodity-tier x402 service, **discovery + distribution + trust > additional features**. Until agents can find the service, paying margin is zero.

## Process findings

### Subagent parallelization works for non-overlapping documentation work

For the trust portal: 4 subagents wrote ~22,000 words across `threat-model.md`, `security-questionnaire.md`, `self-audit.md`, `regulated-deployment.md` in parallel. Each agent wrote one file; no integration conflicts because no file collisions.

It does **not** work as well for code that touches shared files (`app.py`, `models.py`). Tried this with the 5 commodity services — agents each wrote their own `services/<name>.py` and PAY.md, then returned integration notes for me to merge into shared files. That worked, but the merge step was the bottleneck.

**Heuristic:** parallelize when the work product is one isolated file per agent. Sequence (or have agents return notes for a single integrator) when shared files are involved.

### `gh` CLI eliminates almost all manual GitHub steps

After installing partway through the session, repo creation + PR opening dropped to one shell command each. Things that became frictionless:

- `gh repo create org/repo --public --source=. --remote=origin --push` — fork, init remote, push, all in one
- `gh pr create --repo upstream/repo --head fork:branch --title ... --body ...` — open PR from a forked branch
- `gh auth status` — verifies SSH + token are healthy

Should have been first-day setup. Going forward: install `gh` first thing on any new machine.

## Process findings — wallet-secret hygiene

For any project handling mainnet keys + LLM tooling:

1. **Never grep `.env` without pinning specific field names.** `grep '^FIELD_NAME=' .env`, never `grep KEYWORD .env`.
2. **Never `cat` a secrets file** in any context where the output goes to a logged tool result.
3. **Use AWS Secrets Manager** instead of Lambda env vars for production. `services/secrets.py` pattern (lazy load, in-memory cache, env-var fallback for local dev) is solid.
4. **Use Granular Access Tokens** scoped to single packages for npm. Bypass 2FA + restrict to a known IP range if possible.
5. **Treat treasury wallets like hot wallets.** Even after rotation, assume the old keys are compromised forever. Sweep + abandon.
6. **For agent / MCP wallets**, use a wallet generated specifically for agent use, top up with $5–$50 at a time, watch balance.

The two near-misses this session (treasury keys, npm token) both came from grep patterns that were too loose. The fix is mechanical: pin field-name prefixes always.

---

*This file is a record of decisions and gotchas, not a status doc. For current state see [README.md](README.md). For trust posture see [docs/trust/](docs/trust/README.md).*
