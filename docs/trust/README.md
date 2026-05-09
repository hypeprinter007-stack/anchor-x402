# Trust portal · `anchor-x402`

> Signal the thinking, not the certifications.

This portal is for security reviewers, compliance officers, and procurement teams evaluating whether to adopt anchor-x402's services in regulated workflows. The intent is to give you everything you'd normally extract from a vendor over weeks of email — questionnaires, threat models, code-level audit guides, deployment guidance — in one place, all up-front, all current.

We do **not** hold SOC 2, ISO 27001, or PCI certifications. We are explicit about that gap below and on the next page. The compensating controls — open-source codebase, on-chain verifiability, deliberately-stateless architecture, AWS infrastructure inheritance — are intended to give institutional reviewers enough material to make an evidence-based decision without those certifications.

## What's here

| Document | Purpose | Audience | Length |
|---|---|---|---|
| [Threat model](threat-model.md) | STRIDE-lite per-service threat enumeration with mitigations and residual risk | Security reviewers | ~5,400 words |
| [Security questionnaire](security-questionnaire.md) | Pre-filled SIG-Lite-style vendor security response across 11 sections | Procurement / vendor management | ~4,300 words |
| [Self-audit guide](self-audit.md) | 15 compliance concerns mapped to specific files and line ranges in the codebase | Code-level auditors | ~4,600 words |
| [Regulated deployment guide](regulated-deployment.md) | Trust boundaries, compliance inheritance from AWS, customer-side responsibilities, reference architectures | Integrators in regulated institutions | ~5,300 words |
| [On-chain verifiability](on-chain-verifiability.md) | The cryptographic primitive, how customers verify anchors independently of the service | Anyone evaluating audit-trail integrity | ~1,800 words |
| [Observability](observability.md) | CloudWatch dashboard + status page setup, what to expose publicly, what customers should monitor on their side | Operators + consumers | ~1,000 words |

Every document is current as of the most recent commit on `main`. The codebase is at https://github.com/hypeprinter007-stack/anchor-x402 and these docs sit beside it.

## Quick orientation

**If you have 5 minutes:** read [on-chain verifiability](on-chain-verifiability.md). It's the unique trust property that distinguishes this service category from regular SaaS.

**If you have 30 minutes:** add [regulated deployment guide](regulated-deployment.md) — it tells you what the service is appropriate for, what it isn't, and how to integrate it without compliance violations.

**If you're doing a vendor security review:** start with [security questionnaire](security-questionnaire.md). Many institutional procurement forms map 1:1 to its sections; you can copy-paste answers into your internal form.

**If you're a code-level auditor:** [self-audit guide](self-audit.md) is structured as a reading procedure — file paths and line ranges in the order you should review them.

**If you're modeling threats:** [threat model](threat-model.md) covers all 9 services with STRIDE-lite tables.

## What's deliberately not here (yet)

We're transparent about the gaps:

- **No SOC 2 / ISO 27001 / PCI / HIPAA certification.** Roadmap items, not blockers for the commodity tier. The architecture supports an institutional-tier service that would carry these.
- **No insurance.** Cyber liability + tech E&O — we'll obtain when first contractually required by an institutional customer.
- **No formal incident response runbook.** A disclosure email exists; written runbook is on the roadmap.
- **No status page.** The service is observable via direct probes (`/health`) and via CloudWatch alarms (operator-internal). Public status page is on the roadmap.
- **No DPA template.** GDPR Article 28 controller-processor agreement; available on customer request, not yet pre-published.
- **No third-party penetration test.** On the roadmap; the codebase is open-source so independent review is possible today.

These are concrete deficiencies relative to what a Fortune 500 vendor would offer. The compensating story is the open codebase and the on-chain verifiability — together they give a reviewer more direct evidence than most certifications would provide.

## Getting in touch

- **Source code:** https://github.com/hypeprinter007-stack/anchor-x402
- **Live API:** https://1c09pdnrx1.execute-api.us-east-1.amazonaws.com
- **Security disclosure / questions:** security@anchor-x402.com
- **General inquiries:** hello@anchor-x402.com

We respond to security disclosures within 5 business days and aim to acknowledge institutional review requests within 2 business days.

## Updating this portal

These documents live in the same repo as the code. Each commit that materially changes the security posture should land alongside an update to one of these documents. If you find drift between a doc and the code, file a GitHub issue or email — we treat trust-portal accuracy as a security defect.
