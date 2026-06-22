# anchor-x402 — Security Questionnaire (SIG-Lite Style)

> **Purpose.** Pre-filled vendor security review for `anchor-x402`, structured for copy-paste into a customer's internal TPRM form. Mirrors the section layout of the Shared Assessments **SIG-Lite** template.
>
> **Honesty policy.** Where a control is not in place we say so and link to the roadmap. Procurement teams should treat the gaps as the most important content — they are the boundary of the current trust model.
>
> **Vendor.** Christopher Ferjo (sole proprietor, USA) — operator of `anchor-x402`, an open-source x402-paid commodity API.
> **Live URL.** https://api.anchor-x402.com
> **Source.** https://github.com/hypeprinter007-stack/anchor-x402 (MIT)
> **Last reviewed.** 2026-05-09
> **Document owner.** Christopher Ferjo

---

## A. Vendor Identity

| Field | Answer |
|---|---|
| Legal name | Christopher Ferjo (sole proprietor, dba `anchor-x402`) |
| Country | United States |
| Primary / disclosure contact | security@anchor-x402.com (general inquiries: hello@anchor-x402.com) |
| Years in operation | < 1 (launched 2026 Q2) |
| Employee / contractor count | 1 (solo; no employees, no subcontractors) |
| Customer count | 0 paying customers under contract. Public mainnet usage is paid per-call in USDC via x402; no signed-contract customer book to disclose. |
| Funding | Self-funded |
| Primary product | One AWS Lambda exposing 9 HTTP endpoints (anchor, screen, attest, decode/tx, resolve/name, price/token, decode/calldata, parse/datetime, intel/wallet) at $0.001–$0.010 USDC per call via x402. Listed on CDP Bazaar, agentic.market, and awesome-x402; MCP server indexed on Glama. (pay.sh pay-skills catalog listing in review.) |
| Intended buyers | AI agents and their developers. Sub-cent commodity calls, not enterprise-scale workloads. |
| License | MIT |
| Insurance | None (cyber-liability/E&O on the post-revenue roadmap) |

**Q1. Sole proprietor — bus-factor risk?**
Yes; bus factor is 1. Mitigated by (a) fully stateless system, (b) MIT source so any customer can self-host a fork, (c) one-command deploy (`make build && make deploy-guided` from `template.yaml`) — a successor operator stands up an equivalent stack in ~30 minutes. On-chain anchors on Base + Solana mainnet are durable independent of our infrastructure.

**Q2. Incorporated?** Not yet. Operating as sole proprietorship under personal liability. LLC formation triggered by first signed-contract customer.

**Q3. Team location?** One operator, United States. No offshore staff, no contractors.

**Q4. Where can a customer verify what the service does?**
1. Source: https://github.com/hypeprinter007-stack/anchor-x402 — every byte of server logic is in `app.py`, `models.py`, `services/`, `template.yaml`. Readable in under an hour.
2. OpenAPI: `/openapi.json`. Swagger UI: `/docs`.
3. Live mainnet evidence: every `/v1/anchor` returns Base + Solana txhashes verifiable on `basescan.org` / `solscan.io` without trusting our server.

---

## B. Governance & Risk Management

| Control | Status |
|---|---|
| Written InfoSec Policy (WISP) | **Not formalized.** Closest artifact is this questionnaire + README "Operations." Drafting on roadmap. |
| Annual risk assessment | **Not performed.** First formal risk register planned 2026 Q3. |
| Incident response plan | **Lightweight; documented in §J.** Single-operator playbook: CloudWatch alarm → SNS → operator triages → 72h customer notification on confirmed data impact. No tabletop exercises yet. |
| Third-party penetration test | **Not performed.** Compensating controls: < 4 kLOC of public Python; Pydantic-validated input; no DB, no PII path. Pentest planned with first enterprise contract. |
| SOC 2 / ISO 27001 / PCI-DSS | **No** — see §H. |
| Background checks / security training | N/A — one operator. |

**Q5. Governance roadmap.** Months 1–3: publish WISP, data-handling, AUP; start risk register on quarterly cadence. Months 4–9: SOC 2 Type I if revenue justifies (~$25–40k engagement). SOC 2 Type II earliest 2027 Q2 (6+ months of evidence required). ISO 27001 evaluated if EU/UK demand concentrates.

**Q6. Who owns security?** Christopher Ferjo, in role of "everything." Not a defensible control structure for a regulated buyer. A buyer needing segregation of duties should design their integration accordingly (independent on-chain verification of anchor txhashes; buyer-side request logging).

**Q7. Vulnerability handling.** Deps pinned in `requirements.txt`, compiled by `uv pip compile` from `requirements.in`; re-locked on every deploy. Manual `pip-audit` before each release. Critical-CVE patches deployed within 5 business days. Automated SCA in CI is a roadmap gap.

**Q8. AI-assisted development?** Codebase authored with AI pair-programming. All code is human-reviewed before commit. No customer data, secrets, or proprietary third-party material is pasted into AI tools; provider training-data sharing is disabled.

---

## C. Information Security & Data Privacy

| Question | Answer |
|---|---|
| Store PII? | **No.** No accounts, no emails, no IP-to-identity. The only persistent identifier is the wallet address that signed the x402 payment — pseudonymous on-chain data. |
| Store request bodies? | Only ephemerally in CloudWatch logs at default retention. Documented inputs are public hashes, public wallet addresses, public tx hashes, freeform datetime strings. We log a structured line per request (route, status, duration), not full bodies. |
| Store response bodies? | No, beyond CloudWatch error echoes. |
| Sell or share data? | No. |
| Caller-data caching? | Only a 60-second in-process token-price cache (`services/token_price.py`) — public CoinGecko prices, never caller-supplied data. |
| Data residency | AWS `us-east-1` (Northern Virginia), single region. |
| Encryption at rest | AWS-managed by default: Lambda code (S3-backed) SSE-KMS; CloudWatch Logs KMS-encrypted; Secrets Manager (treasury keys, CDP secret) AWS-managed KMS. No DB or S3 customer-data bucket. Future stores will use S3 Object Lock + SSE-KMS. |
| Encryption in transit | TLS 1.2+ on API Gateway (ACM-issued cert). All outbound (Base/Solana RPC, CDP, CoinGecko, openchain.xyz) is HTTPS. |
| Key management | Treasury private keys (Base + Solana) and CDP API key secret live in **AWS Secrets Manager** (`anchor-x402/runtime`). Lambda IAM role: `secretsmanager:GetSecretValue` on that single ARN only. CloudTrail logs every read. Composite secret fetched once at cold-start (`services/secrets.py`); held in process memory. **Sensitive values never appear in Lambda env vars** (which are visible to anyone with `lambda:GetFunctionConfiguration`). |
| Key rotation | Manual via `aws secretsmanager update-secret` + Lambda config-touch to force fresh cold start. Target 90 days; on-demand on suspicion of compromise. Automated rotation Lambda is roadmap (planned alongside the hot/cold split). |
| BYOK | Not supported. The customer's own wallet key never leaves the customer's environment; the customer pays into our treasury via x402. |
| Customer data deletion | Largely N/A — no customer data stored beyond ephemeral logs. Best-effort deletion against logs honored on signed wallet-operator request. |

**Q9. Lifecycle of one request.** (1) x402 client signs USDC payment authorization (EIP-3009 on Base or SPL-Token transfer auth on Solana) targeting our treasury. (2) Request lands on API Gateway over TLS 1.2+. (3) API Gateway invokes `AnchorFunction` Lambda. (4) x402 middleware verifies payment with the CDP facilitator (server-to-server, Ed25519 JWT from `services/cdp_auth.py`). (5) On verified payment, dispatch to one of nine route handlers. (6) Handler validates input via Pydantic, calls public RPC/API endpoints as needed, returns JSON. (7) CloudWatch records the invocation; no request body durably stored beyond log retention.

**Q10. Data residency outside `us-east-1`?** Not today. Customers with hard residency requirements should self-host the MIT source in their chosen region — `template.yaml` is region-agnostic.

**Q11. What customer data passes through?**
- `/v1/anchor`: 32-byte hex hash, or arbitrary JSON we hash. Body not logged in plaintext beyond truncated metadata.
- `/v1/screen`, `/v1/intel/wallet`, `/v1/resolve/name`: public wallet addresses or names.
- `/v1/attest`: input/output hashes, decision string, signature — cryptographically opaque.
- `/v1/decode/tx`, `/v1/decode/calldata`: public tx hashes / public calldata.
- `/v1/price/token`: symbol or contract address.
- `/v1/parse/datetime`: freeform strings; could in theory contain PII (e.g. "John's surgery tomorrow at noon"). **Customers should sanitize.** We do not retain.

**Q12. DPA available?** Yes, on request. Standard processor-side DPA with SCCs for EU/UK. Because we store essentially no personal data, the DPA largely covers the AWS sub-processor relationship.

---

## D. Identity & Access Management

| Question | Answer |
|---|---|
| Who can access production? | One person — the operator. |
| MFA on operator AWS account? | **Yes.** Hardware FIDO2 key on root; root locked away and not used for daily ops. Daily ops via IAM user with MFA-protected access keys. (Customers are encouraged to confirm this in writing at onboarding.) |
| AWS root for deploys? | **No.** `sam deploy` runs under a scoped IAM user. Root is used only for billing and IAM-user provisioning. |
| Lambda least-privilege? | Per `template.yaml`, the execution role has exactly `secretsmanager:GetSecretValue` on a single ARN, plus SAM-default log writes. No `s3:*`, no `dynamodb:*`, no broad wildcards. |
| SSO / SAML for customers? | N/A. No customer accounts; auth is per-call x402 payment. |
| First-party API keys? | Only the CDP facilitator key. Held in Secrets Manager and consumed as a per-request EdDSA JWT (`services/cdp_auth.py`); the secret never appears in headers. |
| Customer auth model | Per-call x402 payment is the auth. No long-lived API key to rotate. A leaked payment authorization is single-use, time-bound, scoped to a specific resource — not replayable. |
| Privileged-access reviews | N/A — one operator, one IAM user. |

**Q13. If the operator's laptop is stolen?**
Disk is FileVault-encrypted. AWS access requires both the IAM access key and a hardware FIDO2 key (not on the laptop). Treasury keys never live on the laptop in plaintext beyond the brief moment of seeding `anchor-x402/runtime` at first deploy. On detection: revoke IAM, rotate Secrets Manager values, revoke CDP key — all within hours.

**Q14. Offboarding?** One operator; offboarding equals shutdown. We say plainly that for a single-person vendor, "offboarding" is a discontinuity event for the customer, not a personnel change.

---

## E. Application Security

| Question | Answer |
|---|---|
| SDLC | Single-developer Git workflow on `main`; self-PR with self-review; every change committed to a public repo, which is its own form of accountability. CI pipeline is a roadmap item. |
| Dependency management | Direct deps in `requirements.in`; transitive lockfile `requirements.txt` (compiled by `uv pip compile`) is what Lambda installs. Re-locks happen on each deploy. |
| Dependency vuln scanning | Manual `pip-audit` before each release. Automated SCA (Dependabot or equivalent) on the immediate roadmap. |
| Static analysis | Not yet automated. Codebase is small (< 4 kLOC) so human review is the primary control today. Adding `ruff` + `bandit` to CI is roadmap. |
| Input validation | All bodies and query parameters are typed Pydantic models in `models.py`. FastAPI auto-validates and returns 422 on schema violation before the handler runs. |
| Output encoding | JSON only. No HTML rendering, so XSS is not in the threat model. |
| Auth for state-changing routes | x402 payment verification via the CDP facilitator. The middleware short-circuits unpaid requests with a 402 before the handler runs. |
| Authorization | Per-call payment authorizes one call. No multi-tenant data model; no cross-tenant access surface. |
| CSRF | N/A — no cookies, no browser-form state. |
| Rate limiting | Today: implicit, via API Gateway account throttling and Lambda concurrency caps. Per-caller (per-wallet) limits are roadmap. |
| Secrets in code | Zero. Verifiable by reading `app.py`, `services/*.py`, `template.yaml`. Sensitive values resolve at runtime through `services/secrets.py`. |
| Payment validation | Delegated to the **CDP (Coinbase Developer Platform) facilitator** at `https://api.cdp.coinbase.com/platform/v2/x402`. The facilitator independently verifies on-chain payment authorization (signature, allowance, settle-on-completion) before our Lambda admits the request. Auth to the facilitator is per-request EdDSA JWTs (`services/cdp_auth.py`), nonced and 120s-lived. |

**Q15. Threat model.**
- **Most likely:** unpaid request slipping through. Mitigated by facilitator-side payment check.
- **Next:** malformed payload exhausting Lambda CPU/memory. Mitigated by Pydantic validation, 60s timeout, 1024 MB cap.
- **Next:** treasury key compromise. Mitigated by Secrets Manager isolation, single-ARN IAM scope, manual rotation. Roadmap hot/cold split further reduces blast radius — only gas float at risk.
- **Out of scope today:** application-layer DDoS beyond what API Gateway throttling catches. AWS Shield Standard is on by default; Shield Advanced on roadmap.

**Q16. Fuzzing?** Manual adversarial testing via `scripts/test_e2e.py` and ad-hoc `curl` against malformed payloads. Given the typed Pydantic surface, fuzz attack value is bounded. `schemathesis` against the live OpenAPI spec is on the SCA roadmap.

**Q17. Dependencies pinned?** Yes, transitively, via `requirements.txt`. Lambda build uses the lockfile, not the loose `.in` file.

---

## F. Operations & Monitoring

| Component | Detail |
|---|---|
| Logging | **AWS CloudWatch Logs** at `/aws/lambda/<function-name>`. One structured line per request (route, status, duration) plus error tracebacks. We do not log full request bodies, to limit incidental PII capture. Default retention is account-default (~30 days); explicit retention configuration is roadmap. |
| Metrics | Lambda standard (Errors, Duration, Throttles, ConcurrentExecutions); API Gateway 4xx/5xx; one custom metric (`anchor-x402/SolanaAnchorFailures`) from a CloudWatch Logs metric filter. |
| Alerting | **Four CloudWatch alarms** wired to a single SNS topic (`AlarmTopic`): (1) `LambdaErrorsElevated` — Errors > 5 / 5min; (2) `LambdaDurationP95High` — p95 > 25s / 5min; (3) `ApiGateway5xxElevated` — 5xx > 3 / 5min; (4) `SolanaAnchorFailureRateHigh` — `"solana anchor failed"` log occurrences > 5 / 5min. `OK` actions also fire to SNS, so recovery is observable. |
| Alert delivery | SNS email subscription to the operator. SMS supported on request. |
| On-call | Single operator, best-effort, US business-hours-biased. We do not commit to 24/7 on-call until customer contracts justify it. |
| Uptime targets | **Best-effort today.** No SLA offered to public callers. Underlying AWS API Gateway + Lambda is documented at 99.95% AWS-side. Institutional-tier SLA (99.9%+ with credit) on roadmap, priced per-customer. |
| Patch cadence | Lambda runtime (Python 3.12) is AWS-managed. App dependencies re-locked and re-deployed on every release. Critical-CVE patches: 5 business days. |
| Backup / snapshots | None — system is stateless. Secrets Manager secret has `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain`; rotation history preserved by Secrets Manager versioning. |
| Configuration management | IaC: AWS SAM (`template.yaml`). Application: Git. Both versioned and reproducible. |
| Change management | Operator commits to `main` → `make build && make deploy-guided`. No staging today; smoke tests via paid mainnet e2e (`scripts/test_e2e.py`) post-deploy. Pre-prod stage on roadmap. |

**Q18. How do you know the service is up?** Three signals: (a) the four CloudWatch alarms above; (b) `GET /health` (external ping like UptimeRobot is roadmap); (c) any successful paid call leaves an on-chain artifact a customer can audit independently.

**Q19. Status page?** Not yet. On the immediate roadmap.

**Q20. Maintenance windows?** None announced — deploys are zero-downtime via Lambda alias updates. Change log lives at the public repo's `Releases` tab.

**Q21. CloudWatch log protection?** KMS-encrypted at rest (AWS-managed; customer-managed KMS is roadmap for paying customers). Read access requires `logs:GetLogEvents`, granted only to the operator IAM user. CloudTrail records every read.

---

## G. Business Continuity & Disaster Recovery

| Question | Answer |
|---|---|
| Single-region today? | **Yes** (`us-east-1`). |
| RTO | **~10 minutes** — wall-clock time to redeploy `template.yaml` from clean Git (verified during routine deploys). |
| RPO | **Zero** — system is stateless; no data to lose. On-chain anchor artifacts on Base + Solana mainnet are durable independent of our infrastructure. |
| Multi-AZ within `us-east-1`? | Yes — AWS Lambda + API Gateway run multi-AZ by default. |
| Regional failure plan | Today: downtime until region recovers, or a manual SAM redeploy into a backup region (`us-west-2` is operator-tested). Treasury Secrets Manager values must be re-seeded in the failover region. Estimated full failover: ~30 minutes. |
| Tested DR drill | Not formally drilled. Redeploy path is exercised in routine development. Quarterly DR drill is a roadmap item. |
| AWS vendor failure | Not in our threat model (low likelihood, high impact). Mitigation = same as regional failure plus self-hosting via the MIT source on a non-AWS host. |
| CDP facilitator failure | Possible. If down, x402 payment verification fails, API returns 5xx, no caller is charged. Recovery is automatic on facilitator restoration. Pluggable second facilitator is roadmap. |
| On-chain anchor durability | **Independent of our infrastructure.** Once `/v1/anchor` returns Base + Solana txhashes, those proofs live on the public chains and are verifiable via any RPC or block explorer. We could disappear and the proofs would remain. |

**Q22. What if you simply stop operating?** MIT source is public; a customer can fork and self-host in ~30 minutes. Existing on-chain anchors remain valid forever and need no infrastructure of ours to verify. CDP facilitator and public Base/Solana RPCs are third-party-operated and unaffected. Customers with hard continuity requirements should pre-clone the source and dry-run the deploy.

**Q23. Code escrow?** Source is public on GitHub under MIT — that is the escrow.

---

## H. Compliance

The honest answer: **none of the brand-name compliance certifications are in place yet.** Row-by-row:

| Framework | Status | Notes |
|---|---|---|
| SOC 2 Type I | Not certified | Earliest realistic: 2026 Q4, ~$25–40k engagement, if customer demand justifies |
| SOC 2 Type II | Not certified | Earliest realistic: 2027 Q2 (6+ months evidence required) |
| ISO 27001 | Not certified | Considered if EU/UK customer concentration warrants |
| ISO 27017 / 27018 | Not certified | Same as ISO 27001 |
| PCI-DSS | Not certified, **and not applicable** | We never handle card data. Payments are USDC on public chains. |
| HIPAA / HITRUST | Not certified | We do not knowingly handle PHI. Customers must not send PHI through the API. |
| GDPR | Compliant in posture; **DPA available on request** | Essentially no personal data processed; what little appears in logs is processed on legitimate-interest basis with bounded retention. EU data-subject rights honored on signed request. |
| CCPA / CPRA | Same as GDPR | We do not sell data; no targeted advertising. |
| FedRAMP | Not certified | Out of scope; we do not run on AWS GovCloud. |
| NIST 800-53 / CSF | Informal alignment | Practices align with CSF "Identify / Protect / Detect / Respond" pillars at the level a single-operator vendor can reasonably achieve. No formal control-mapping document. |
| Cloud Security Alliance CCM (CAIQ) | Not published | Available on request. |

**Q24. What about underlying infrastructure?** We inherit AWS's compliance posture for the infrastructure layer:

| AWS framework | Status |
|---|---|
| SOC 1 / SOC 2 / SOC 3 | Yes (annual) |
| ISO 27001 / 27017 / 27018 / 22301 | Yes |
| PCI-DSS Level 1 (as service provider) | Yes |
| FedRAMP Moderate / High (in GovCloud) | Yes |
| HIPAA-eligible services | Yes (Lambda, API Gateway, Secrets Manager, CloudWatch are HIPAA-eligible) |
| ENS High (ES) / G-Cloud (UK) / IRAP (AU) / C5 (DE) | Yes |

A customer can rely on AWS's controls for the infrastructure stratum (physical security, hypervisor isolation, network DDoS protection, key-management primitives) while doing their own assessment of the application stratum (this document).

**Q25. Customer-supplied DPA / BAA / MSA?**
- **DPA:** yes, our standard form or yours, on request.
- **BAA:** **no** — not on a HIPAA-compatible footing today; the application has not been engineered for or audited against HIPAA Security Rule controls. **Do not send PHI.**
- **MSA:** yes, customer-supplied or ours; expect markup on indemnification, liability cap, and SLA terms given early-stage operating posture.

**Q26. Subprocessors?** See §I.

---

## I. Vendor & Supply Chain

| Subprocessor / dependency | Role | Data shared | Geography |
|---|---|---|---|
| **Amazon Web Services** | Primary IaaS — Lambda, API Gateway, Secrets Manager, CloudWatch, SNS, KMS | All operational data flows through AWS by definition | `us-east-1` |
| **Coinbase / CDP** | x402 payment facilitator (verify + settle) | Caller wallet address, payment authorization, target resource path | Coinbase-managed (US control plane) |
| **Public Base RPC** (`mainnet.base.org`) | Base mainnet read/write | Hex hashes (already public after submission) | Coinbase-operated |
| **Public Solana RPC** (`api.mainnet-beta.solana.com`) | Solana mainnet read/write | Hex hashes (already public after submission) | Solana Foundation-operated |
| **CoinGecko (free tier)** | Backing source for `/v1/price/token` | Token symbol / contract (public) | CoinGecko-managed |
| **openchain.xyz** | 4byte/ABI directory for `/v1/decode/calldata` | Calldata first 4 bytes (already public) | openchain.xyz-managed |
| **GitHub** | Source repository | Public source only; no production secrets | Microsoft-operated |
| **PyPI** (via `uv` / `pip`) | Build-time dependency mirror | None (download-only) | PyPA-operated |

**Q27. Dependency-upgrade vetting?** Direct deps named in `requirements.in`. Major-version upgrades go through manual code review and a paid mainnet e2e (`scripts/test_e2e.py`). Transitive deps re-pinned each deploy; major transitive bumps trigger manual review.

**Q28. CDP pricing/terms change?** We pass per-call CDP fees through transparently in the published per-call price. Material CDP terms change is an operational decision: stay on CDP, swap to a self-hosted x402 facilitator, or both.

**Q29. Public RPC outage?** Today we rely on the canonical public RPC for each chain. If `mainnet.base.org` or `api.mainnet-beta.solana.com` is unhealthy, anchor calls return 5xx until recovery. The `SolanaAnchorFailureRateHigh` alarm catches the Solana side. Fallback RPC array (Alchemy, Helius, etc.) on roadmap.

---

## J. Incident Response

We treat IR as a single-person playbook with crisp obligations rather than a multi-tier escalation tree we couldn't honor.

| Step | Owner | SLA |
|---|---|---|
| Disclosure intake | security@anchor-x402.com | Acknowledge within 1 business day |
| Triage + severity classification | Operator | Within 1 business day of acknowledgment |
| Mitigation | Operator | Best-effort; high-severity mitigated or worked-around within 72 hours |
| Customer notification (confirmed breach affecting customer data) | Operator | **Within 72 hours** of confirmation, written email-of-record to all then-active contracted customers |
| Public post-mortem | Operator | Within 14 days of resolution, posted to GitHub Security advisories |
| Coordinated disclosure | Operator | Standard 90-day default; no threats to good-faith reporters |

**Q30. What counts as "breach affecting customer data" given you store none?** Largely N/A by design. Cases where it would apply: (1) treasury key compromise leading to misuse of inbound USDC — notify all callers identifiable via on-chain payment history within 72h; (2) CloudWatch log exfiltration capturing wallet addresses or freeform datetime strings beyond what's on-chain — best-effort 72h notification; (3) CDP API key compromise — rotate immediately, notify CDP per their disclosure process.

**Q31. Cyber-liability insurance?** **No.** Known gap. Customers requiring insurer-backed indemnification should treat this as a hard gap.

**Q32. Bug bounty?** Not formal. Will pay reasonable bounties (out-of-pocket) for valid reports of (a) auth bypass on the x402 payment check, (b) treasury-key extraction, (c) cross-tenant data leakage. Email security@anchor-x402.com.

**Q33. Incident history?** GitHub Security advisories on the source repo. No separate page yet.

---

## K. Customer Responsibilities (Shared Responsibility Model)

Mandatory section: tells the customer what they must do on their side so the joint posture holds together. Procurement teams: copy this into your internal vendor-integration runbook.

**1. Validate response shapes.** OpenAPI at `/openapi.json` is authoritative. Wrap all calls in a typed client or JSON Schema validator so a malformed or future-shifted response is caught at your edge.

**2. Treat output as advisory — except for on-chain artifacts.**
- `/v1/screen` is a useful first-pass against OFAC SDN crypto entries; it is **not** a substitute for your own AML/sanctions program. Your compliance team owns the final decision.
- `/v1/decode/tx` and `/v1/decode/calldata` are best-effort decoders. For high-value flows, also fetch raw chain data from a node you operate.
- `/v1/price/token` is sourced from CoinGecko; intended for orientation, not for execution pricing on financial flows.
- `/v1/anchor` and `/v1/attest` return on-chain txhashes — these **are** authoritative; verify them on Base/Solana via any RPC or explorer without further trust in our server. This is the part of the response we recommend you treat as source of truth.

**3. Verify on-chain anchors independently.** For every anchor call, take the returned `base.tx` and `solana.tx` and confirm via a public RPC or explorer that the tx is mined, was sent from our published treasury address, and contains the expected memo / calldata. Costs you one RPC call; removes us from your durability-claim trust chain.

**4. Cache where appropriate.** For data that doesn't change at sub-minute granularity (token prices, ENS resolutions, sanctions verdicts on stable wallets), cache responses on your side and avoid paying for duplicates. Our 60s in-process cache for `/v1/price/token` does not cache anything for you across sessions.

**5. Log your own requests for your own audit trail.** Because we deliberately do not retain request bodies, log your calls yourself: timestamp, endpoint, request hash, response hash, returned txhashes, x402 payment receipt id.

**6. Anchor your own decisions independently when stakes are high.** For your highest-stakes customer-facing decisions, anchor on your own infrastructure as well — either by calling `/v1/anchor` from your own service or by submitting directly to Base/Solana from a wallet you control. Defense in depth.

**7. Rate-limit your own callers.** We do not currently per-caller-rate-limit. If your application multiplexes many end-users into anchor-x402, gate them at your edge.

**8. Sanitize free-text inputs.** Specifically `/v1/parse/datetime` accepts arbitrary strings — strip PII before sending. Our log retention is bounded but we cannot retroactively unsee something you sent.

**9. Treat the treasury address as canonical.** Our published treasury address (per the `extensions.bazaar` block in the 402 response) is the only address you should pay. Confirm it on first integration; pin it in your config.

**10. Re-verify this questionnaire on a cadence.** Pin the commit hash you reviewed; re-review at contract-renewal cadence (or quarterly, whichever is shorter). Material changes are listed in the document history below.

---

## Document History

| Date | Change |
|---|---|
| 2026-05-09 | Initial publication. Mirrors current state of the codebase at `main`. |

---

## Contact

- **Operator:** Christopher Ferjo
- **Email (general):** hello@anchor-x402.com
- **Email (security disclosure):** security@anchor-x402.com
- **Source:** https://github.com/hypeprinter007-stack/anchor-x402
- **Live API:** https://api.anchor-x402.com
- **License:** MIT
