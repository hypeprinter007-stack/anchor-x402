# Security policy

## Reporting a vulnerability

**Email: [security@anchor-x402.com](mailto:security@anchor-x402.com)**

Subject line format: `[anchor-x402 security] <one-line summary>`

We acknowledge security disclosures within **5 business days** and aim to publish a fix or mitigation within **30 days** of confirmed report. Coordinated disclosure timeline is **90 days by default**; we won't pursue legal action against good-faith researchers operating within these bounds.

## Scope

In scope (we want to hear about these):

- Bypassing the x402 payment check on any of the 9 paid endpoints
- Treasury private-key extraction or compromise paths (Lambda, IAM, Secrets Manager)
- CDP facilitator JWT auth bypass
- Cross-tenant data leakage (note: by design we store no per-customer PII; report any path that disproves this)
- Domain separation bypass on the `/v1/attest` signed message format (cross-app replay)
- Sanctions screening false-clears against the published OFAC corpus
- Server-side request forgery against upstream APIs (RPC nodes, CoinGecko, openchain.xyz)
- Supply-chain attacks against the pinned dependency lockfile (`requirements.txt`)
- Auth bypass on the unprotected `/health` and `/openapi.json` endpoints (not paid, but should never leak treasury keys, secrets, or operator-internal data)

Out of scope:

- Vulnerabilities in upstream dependencies (please report to the upstream maintainer; we'll patch on next deploy after their fix lands)
- Issues that require physical access to the operator's machine
- Issues requiring leaked treasury keys we already control
- Self-DoS via paying for thousands of calls quickly (it's pay-per-use; volume costs you USDC)
- The deliberate gaps documented in [the trust portal](https://anchor-x402.com/trust/) (no SOC 2, no insurance, no DPA template, etc.) — these are operationally limited, not bugs

## Disclosure procedure

1. Email security@anchor-x402.com with the report.
2. We acknowledge receipt within 5 business days.
3. We triage and respond with severity classification + mitigation timeline within 7 days.
4. We coordinate fix + disclosure timing with you. Default is 90-day coordinated disclosure; faster if mitigation is straightforward, slower only with mutual agreement.
5. Once the fix is deployed, we publish a GitHub Security Advisory at https://github.com/hypeprinter007-stack/anchor-x402/security/advisories with credit to you (unless you prefer to remain anonymous).

## Bug bounty

Not a formal program. We pay reasonable bounties out-of-pocket for valid reports of:

- Auth bypass on the x402 payment middleware (treasury can be drained without paying)
- Treasury-key extraction (any path that exfiltrates the EVM or Solana private keys from Secrets Manager or Lambda memory)
- Cross-tenant data leakage in any paid response

Bounty range: case-by-case, generally **$50–$500 USDC** for critical reports during the early commercial phase. Full bounty matrix will be posted at [anchor-x402.com/trust/](https://anchor-x402.com/trust/) once the program is formal.

## What this codebase IS NOT certified for

We are **not** SOC 2, ISO 27001, PCI-DSS, or HIPAA certified. The trust portal at [anchor-x402.com/trust/](https://anchor-x402.com/trust/) carries the full picture — what we have, what we don't, and what we inherit from AWS infrastructure.

## Acknowledgments

Hall of fame for confirmed reports — none yet. Be the first.
