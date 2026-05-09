# Regulated Deployment Guide for `anchor-x402`

> A vendor-due-diligence and integration document for compliance engineers, vendor risk teams, and platform integrators at regulated institutions. This document describes the trust boundary of `anchor-x402`, the compliance posture an integrator inherits from its underlying AWS infrastructure, the categorical boundaries of what `anchor-x402` is and is not appropriate for, and the customer-side controls required to deploy it inside a regulated workflow.

---

## 1. Audience and scope

This document is written for:

- **Compliance engineers and vendor-risk officers** at banks, money services businesses (MSBs), broker-dealers, registered investment advisers (RIAs), payment institutions, regulated AI vendors, and fintech platforms who are evaluating whether `anchor-x402` can be embedded in a workflow that touches a regulated obligation (BSA/AML, SOC 2, PCI-DSS, HIPAA, GDPR, MiCA, DORA, NYDFS Part 500, FFIEC).
- **Platform integrators** at those institutions writing the code that calls `anchor-x402` endpoints.
- **Internal audit and second-line-of-defense reviewers** who need to understand the limits of evidence produced by `anchor-x402` calls.

This document is **not** intended for:

- End-users of an institution's product. They should never directly hold a relationship with `anchor-x402`.
- Hobbyist developers. The `README.md` at the repository root is sufficient for general use.
- Workflows that have no regulatory hook at all. If your workflow is fully unregulated, you do not need this document — read the README instead.

The scope of this document is the public `anchor-x402` Lambda deployment in AWS `us-east-1` at `https://api.anchor-x402.com`. Any private deployment, any forked deployment, any deployment in a different region, and any future "institutional tier" is **outside the scope** of this document. If you are running a forked deployment in your own AWS account, you inherit the source code's behavior but you own the operational posture entirely.

---

## 2. Trust boundary diagram

The diagram below shows where data crosses trust boundaries when an institution calls `anchor-x402`. Each leg is annotated with what data crosses it and at what privilege level.

```
+-----------------------------------------+      +---------------------------------+      +----------------------------------+
|        Customer regulated env           |      |      anchor-x402 (us-east-1)    |      |        External dependencies      |
|                                         |      |                                 |      |                                  |
|  - Production system of record          |  A   |  AWS Lambda (Python 3.12)       |  C   |  Base mainnet RPC                |
|  - Internal audit log (immutable)       | ---> |  FastAPI + x402 middleware      | ---> |  Solana mainnet RPC              |
|  - PII / KYC / transaction data         |      |  Stateless: no DB, no S3        |      |  CoinGecko (price)               |
|  - Decision engine, AI agent, traders   | <--- |  Treasury keys in Secrets Mgr   | <--- |  openchain.xyz (4byte registry) |
|                                         |  B   |  CloudWatch logs (request meta) |  D   |  CDP facilitator (settlement)    |
|  - Caller's wallet (USDC on Base/SVM)   |      |                                 |      |  ENS / Bonfida SNS resolvers     |
+-----------------------------------------+      +---------------------------------+      +----------------------------------+
```

**Legs and what crosses them:**

- **Leg A — Customer → `anchor-x402` (HTTPS over TLS 1.2+):**
  Carries: the request body (a hash, a wallet address, a tx hash, a calldata hex string, a freeform datetime, etc.), an `X-Payment` header containing a signed x402 USDC authorization, and standard HTTP metadata. The request body is always sub-1KB for these endpoints. **Sensitivity:** customer-controlled. The customer should **never** put PII, customer identifiers, or material non-public information in the request body. `anchor-x402` is designed to operate over hashes, public addresses, and public on-chain identifiers only.
- **Leg B — `anchor-x402` → Customer (HTTPS):**
  Carries: a signed JSON response (transaction hashes, sanctions verdicts, decoded tx structures, prices, etc.). Plus, in the 402 challenge case, a `PaymentRequired` payload with the price and accepted networks. **Sensitivity:** the response is informational. It is **advisory output**, not authoritative record.
- **Leg C — `anchor-x402` → on-chain RPCs (HTTPS):**
  Carries: signed transactions (for the anchor / attest paths) and read RPC calls (for tx-decode, price, name-resolve, intel). The signed transactions are constructed inside the Lambda using the treasury keys held in AWS Secrets Manager; the customer's wallet never signs an on-chain transaction here. The hash being anchored is publicly visible on the chain. **Sensitivity:** anything a hash anchors is permanently public. This is by design — the anchor is the public timestamp.
- **Leg D — `anchor-x402` → external read APIs (HTTPS):**
  CoinGecko (price), openchain.xyz (4byte selector lookup), public ENS/SNS resolvers, and the CDP facilitator (settlement validation). These are public APIs. The customer's payload is forwarded only to the extent needed (e.g. a wallet address goes to a public block explorer's RPC). No credentials, no PII, no auth tokens of the customer ever leave the Lambda.

**Privilege levels.** The customer is the owner of leg A (they choose what to send) and is the consumer of leg B (they choose what to do with the response). `anchor-x402` is the operator of legs C and D — it owns the treasury keys, the RPC endpoint configuration, and the external API contracts. The customer **never** delegates an authority to `anchor-x402` that could move customer funds, write to a customer system, or sign on behalf of a customer principal.

The trust boundary, in plain language: **everything inside the customer's regulated environment is the customer's responsibility. `anchor-x402` is a stateless function call across the boundary that returns informational output.** It is not a system of record, not a custodian, and not a regulated service.

---

## 3. What `anchor-x402` IS appropriate for

The following uses are categorically appropriate for `anchor-x402` at a regulated institution, provided the customer-side responsibilities in section 6 are followed:

1. **Pre-production and sandbox testing.** All of the public-data endpoints are excellent for build-time validation and integration testing. There is no regulatory exposure to running `anchor-x402` calls from a non-production environment against synthetic or public test data.
2. **Internal proofs of concept on non-production data.** `anchor-x402` is well-suited for an institution's internal innovation team to wire up a working demo of "AI agent pays per-call for trust primitives." When the demo proves out, the team can budget a build of an institutional-grade equivalent or upgrade to a formal agreement.
3. **Public-data lookups.** Endpoints that operate purely on public on-chain or public-internet data are categorically appropriate. This includes:
    - `/v1/price/token` — public token price from CoinGecko.
    - `/v1/resolve/name` — public ENS / SNS resolution.
    - `/v1/decode/calldata` — public 4byte selector lookup.
    - `/v1/decode/tx` — public on-chain transaction decode.
    - `/v1/parse/datetime` — pure deterministic string parsing.
   None of these touch any customer data beyond a public identifier the customer chooses to look up.
4. **Anchoring non-personal hashes for non-binding audit purposes.** `/v1/anchor` and `/v1/attest` are appropriate when the institution wants a public, dual-chain timestamp on top of a hash they have already produced inside their own system of record. The institution's primary audit trail is internal; the anchor is supplemental, tamper-evidence for the institution's own evidence retention.
5. **Build-time tooling for AI agents.** During development of an autonomous agent, `anchor-x402` provides a low-friction commodity bus for `name → address`, `address → sanctions hint`, `tx hash → structured decode`, etc. This is a faster development loop than wiring eight free APIs each with their own auth and rate limits.
6. **Light-touch decision attestation where the institution maintains its own primary audit trail.** If the institution already produces a complete, retained, signed log of every decision its agents make, `anchor-x402`'s attest endpoint can layer a public dual-chain commitment on top — without `anchor-x402` becoming the system of record. The institution's log is the legal artifact; the anchor is the public commitment to it.
7. **First-pass triage / "pre-screen" before invoking a paid enterprise tool.** Use `/v1/screen` or `/v1/intel/wallet` as a low-cost first pass to filter the obvious cases, then escalate the remainder to a full Chainalysis / TRM / Elliptic query. This is appropriate provided the production decision is gated on the enterprise tool, not on `anchor-x402`.

---

## 4. What `anchor-x402` is NOT appropriate for

These are categorical exclusions. If the institution needs any of the below, it must use a vendor with the appropriate certifications, contractual data-protection agreements, and regulatory standing.

1. **Live AML / sanctions decisions for production transactions.** The OFAC SDN corpus inside `/v1/screen` is a static, point-in-time snapshot of the public OFAC SDN list and selected high-risk address sets. It is **not** sourced from a continuously-updated commercial AML database, it does not include behavioral risk scoring, and it has no SLA. Use **Chainalysis KYT, TRM Labs, or Elliptic** for any live AML decision on a production transaction.
2. **KYC / KYB verification of natural persons or legal entities.** `anchor-x402` does not collect identity documents, does not perform liveness checks, does not do address verification, does not check sanctions against natural-person lists, and does not produce a KYC record. Use **Onfido, Persona, Jumio, Trulioo, or Sumsub** for natural-person KYC.
3. **Material trading decisions where the audit trail is legally probative.** If a trader, an asset manager, or a clearing function needs to produce evidence to a regulator that a decision was made on a particular input at a particular time, that evidence must come from a system the institution controls — not from a third-party stateless call. `anchor-x402` can supplement such an audit trail, but it cannot be the primary evidence.
4. **HIPAA-scoped workflows.** No protected health information should ever cross leg A. AWS supports a Business Associate Agreement (BAA) on its infrastructure, but `anchor-x402` does not operate under a BAA at the application layer. There is no path to making `anchor-x402` HIPAA-compliant in its public deployment.
5. **SOX / financial-reporting workflows where vendor non-availability is a Material Weakness.** A free public utility has no SLA. If an outage of `anchor-x402` would cause a financial reporting control to fail, do not depend on it.
6. **GDPR-scoped workflows that require a full Data Processing Agreement.** `anchor-x402` stores no PII at the application layer (it is stateless), but no DPA is offered for the public deployment. If the institution requires a signed DPA listing `anchor-x402` as a sub-processor, request the institutional tier (section 10).
7. **Workflows where the institution would need to inspect the vendor's evidence retention.** `anchor-x402` does not retain per-customer records. CloudWatch logs at the infrastructure layer record request metadata for operational debugging only and are not curated for evidentiary use. There is nothing to subpoena, audit, or inspect on the vendor side beyond the public on-chain anchor itself.
8. **Real-time operational decisions with hard latency budgets below ~150ms p95.** Latency from the US east coast to `us-east-1` is 50-150ms; transcontinental is +200ms. `anchor-x402` is appropriate for asynchronous and human-supervised flows. It is not appropriate as a hot-path dependency in a low-latency trading system.
9. **Custody, fund movement, or settlement of customer assets.** `anchor-x402` only ever holds its own treasury wallet (which it uses to pay for its own anchor gas). It never holds, controls, or moves a customer's funds. Do not architect any flow that depends on `anchor-x402` to custody value.

---

## 5. Compliance inheritance

Because `anchor-x402` is hosted on AWS Lambda + API Gateway in `us-east-1`, the customer inherits AWS's IaaS-layer compliance posture for the legs of the call that traverse AWS infrastructure. The customer does **not** automatically inherit any application-layer compliance at the `anchor-x402` boundary, because `anchor-x402` does not currently hold any application-layer certifications. The table below makes this explicit.

| Framework | What AWS provides at the IaaS layer | What `anchor-x402` provides at the app layer | What the customer must do |
|---|---|---|---|
| **SOC 1 / SOC 2 / SOC 3** | AWS is independently audited for SOC 1, SOC 2 Type II, and SOC 3 across its core compute and networking services (including Lambda, API Gateway, Secrets Manager, CloudWatch). The reports are downloadable from AWS Artifact under NDA. | None. There is no SOC 2 report scoped to the `anchor-x402` application. | Treat `anchor-x402` as a non-SOC-2-attested vendor in your vendor risk register. Compensate with control activities on your side: log every call, validate every response, do not delegate authority to it. |
| **ISO 27001 / 27017 / 27018** | AWS holds ISO 27001, ISO 27017 (cloud-specific controls), and ISO 27018 (cloud PII protection) certifications across its in-scope services. | None. | If your institution requires ISO 27001 alignment of all sub-processors, do not use `anchor-x402` for any data subject to your ISO scope. Use it only for non-scoped exploration. |
| **PCI-DSS** | AWS is a PCI-DSS Level 1 service provider for its in-scope infrastructure services. | **Not in scope.** `anchor-x402` does not process, store, or transmit cardholder data. Do not send cardholder data over leg A under any circumstance. | Make sure no part of your integration causes cardholder data to cross leg A. If your integration is part of a CDE (cardholder data environment), `anchor-x402` must be outside it. |
| **HIPAA** | AWS makes a Business Associate Agreement (BAA) available, and a defined set of services are HIPAA-eligible. Lambda is HIPAA-eligible under AWS's BAA. | **Not HIPAA-eligible at the application layer.** `anchor-x402` does not have a BAA with you. | Do not send PHI over leg A. Period. |
| **FedRAMP** | AWS GovCloud (US) is FedRAMP High; standard commercial regions including `us-east-1` carry FedRAMP Moderate authorizations for many services. | **Not FedRAMP-authorized.** `anchor-x402` runs in commercial `us-east-1` with no ATO. | If your workload requires FedRAMP, do not use `anchor-x402`. |
| **GDPR** | AWS offers a Data Processing Addendum (DPA) covering its processing of customer data on AWS infrastructure, with EU data centers and Standard Contractual Clauses. | No DPA at the application layer. `anchor-x402` is stateless and stores no PII, so the practical surface area for a DPA is very small — but no contractual instrument is offered. | Do not send personal data over leg A. Treat any inadvertent personal data in a request as an incident that must be logged and remediated on your side. |
| **MiCA / Travel Rule** | n/a | Not applicable. `anchor-x402` is not a CASP, is not a VASP, does not transmit value on behalf of customers, and is not subject to the Travel Rule. | If your workflow IS subject to MiCA / Travel Rule (because YOU are a CASP), `anchor-x402` is not a substitute for your VASP-to-VASP messaging stack. |
| **NYDFS Part 500 / DORA / FFIEC** | AWS provides infrastructure controls that map to many of the technical-control expectations in these frameworks. | No application-layer mapping. | Document `anchor-x402` in your third-party register exactly as you would document any non-attested public utility — e.g. a public block explorer or a public price feed. The institution's own governance over its use is what makes the use compliant. |

**Bottom line on inheritance.** The customer inherits a clean, well-documented IaaS posture by virtue of `anchor-x402` running on AWS. The customer does not inherit any application-layer attestation, because the public deployment does not have any. The customer's compliance team must close the gap with controls of their own — the responsibilities in section 6 below are the minimum.

---

## 6. Customer-side responsibilities

The institution closes the compliance gap from section 5 by implementing the following controls on its side of the trust boundary. None of these are unusual; together they make `anchor-x402` safe to use as a non-authoritative dependency in a regulated workflow.

1. **Log every request.** For each call to an `anchor-x402` endpoint, the institution must record in its own immutable audit log:
    - The endpoint called, with its full URL.
    - The exact request body the institution sent (over leg A).
    - The exact response body the institution received (over leg B).
    - The HTTP status code and the elapsed time.
    - The institution's internal correlation ID (request ID, trace ID, decision ID) tying the call back to the workflow step that made it.
   The institution's audit log, not `anchor-x402`'s ephemeral CloudWatch trail, is the legal record. `anchor-x402` may rotate or expire its CloudWatch retention without notice.
2. **Treat outputs as advisory, not authoritative.** Every response from `anchor-x402` is informational. Do not gate a regulated decision solely on a value returned over leg B. If a downstream control (e.g. an AML hold, a trade halt, a customer block) depends on the result, the result must also be confirmed by an authoritative source the institution has under a regulatory-grade contract.
3. **Cache responses on your side, validate against your primary source where decisions are material.** For the public-data endpoints (`/v1/price/token`, `/v1/resolve/name`, `/v1/decode/calldata`, `/v1/decode/tx`), the institution should keep its own short-lived cache of recent responses, both for cost and for integrity. Where a decision is material, validate the value against a primary source (an EVM RPC the institution operates, the canonical price feed in the institution's pricing engine, an in-house ABI registry).
4. **Anchor the institution's own derived decisions independently.** If the institution wants tamper-evident timestamps over its decisions, the institution should compute its own Merkle root from its own data, sign it with the institution's own key, and call `/v1/attest` to get the dual-chain anchor. The cryptographic root is the institution's; `anchor-x402` only timestamps it. The institution must retain the signed payload alongside its on-chain anchor URLs in its audit log.
5. **Monitor `anchor-x402`'s health from the institution's side.** Implement client-side monitoring on the institution's integration: error rate, latency p95, anchor-success rate (for `/v1/anchor` and `/v1/attest`), HTTP 402 vs 200 ratio (high 402s indicate a payment-stack regression on the institution's side, not on `anchor-x402`'s). The public deployment does not currently have a status page; do not rely on a vendor-side signal for liveness.
6. **Implement client-side rate limiting and budget caps.** `anchor-x402` does not currently enforce per-caller rate limits. The institution must self-throttle to avoid runaway costs. The institution's wallet is the only spending control: configure a low USDC balance and a per-day top-up cap, and monitor depletion.
7. **Validate the on-chain anchors when receiving an attest response.** When `/v1/attest` returns a `base.tx` and `solana.tx`, the institution should fetch those transactions from its own RPC providers and verify that the calldata / memo on-chain matches the `merkle_root` returned. This is a one-time, cheap step that closes the trust loop: the institution trusts the chains, not the vendor.
8. **Document this dependency in your third-party register.** List `anchor-x402` as a non-attested public utility with the operational characteristics described in this document. Mark it as advisory-only, classify any data sent to it as PUBLIC, and assign a vendor-tier rating that matches "no SLA, no DPA, no attestation, but stateless and well-isolated."
9. **Review section 4 quarterly.** The categorical exclusions in section 4 are durable. The categorical appropriateness in section 3 is also durable. But the institution's own use cases evolve; what was a sandbox use last quarter may be drifting toward production. Re-confirm that no production decision has come to depend on `anchor-x402` without the institution noticing.

---

## 7. Reference architecture: regulated AML pre-screen workflow

**Scenario.** An institution operates a counterparty risk function that screens incoming wallet addresses before allowing the institution's customers to interact with them. The institution holds a Chainalysis KYT license that it uses for all definitive AML decisions, but Chainalysis calls cost more than the institution wants to spend on the long tail of low-risk inquiries. The institution wants to use `/v1/intel/wallet` as a low-cost first pass that filters out the obvious-cleans and routes only the ambiguous cases to Chainalysis.

```
+---------------------------------------------+
|  Customer-facing app                        |
|  Customer asks: "Can I send to 0xABC...?"   |
+---------------------------------------------+
                 |
                 v
+---------------------------------------------+
|  Counterparty Risk Service (institution)    |
|                                             |
|  Step 1: Internal allow/deny list cache     |
|     hit -> done.                            |
|     miss -> continue.                       |
+---------------------------------------------+
                 |
                 v
+---------------------------------------------+
|  Step 2: Call anchor-x402 /v1/intel/wallet  |
|  - bundle: balances + activity + identity   |
|    + sanctions hint                         |
|  - cost: $0.005 USDC                        |
|  - SLA: none (advisory)                     |
+---------------------------------------------+
                 |
        +--------+--------+
        |                 |
        v                 v
+---------------+  +-------------------+
|  Clear hint   |  | Risky / ambiguous |
|  (no hits,    |  | hint (sanctioned, |
|  short tx     |  | high tx volume,   |
|  history,     |  | brand-new wallet, |
|  active, etc) |  | etc.)             |
+---------------+  +-------------------+
        |                 |
        v                 v
+---------------+  +-------------------+
|  Internal     |  | Step 3: Call      |
|  policy:      |  | Chainalysis KYT   |
|  ALLOW with   |  | for definitive    |
|  routine      |  | AML risk score.   |
|  monitoring.  |  | This is the       |
|  Log decision |  | regulatory-grade  |
|  + anchor-    |  | call.             |
|  x402 inputs  |  +-------------------+
|  + outputs    |           |
|  to internal  |           v
|  audit trail. |  +-------------------+
+---------------+  |  Use Chainalysis  |
                   |  output as the    |
                   |  authoritative    |
                   |  AML decision.    |
                   |  Anchor-x402 hint |
                   |  is logged for    |
                   |  context only.    |
                   +-------------------+
```

**Why this architecture is compliant.**
- The authoritative AML decision is always made by the regulatory-grade vendor (Chainalysis) under a real contract with real coverage.
- `anchor-x402` is used only for cost-saving triage. If it were unavailable, the system would degrade to "send everything to Chainalysis" — i.e. expensive, but still safe.
- The institution's audit log records both the `anchor-x402` hint and (where invoked) the Chainalysis verdict, with the Chainalysis verdict as the binding decision.
- No customer PII ever crosses leg A. Only the public counterparty wallet address is sent.

**Operational notes.**
- Do not cache `anchor-x402` sanctions hints for longer than 24 hours; the OFAC SDN corpus inside `anchor-x402` is best-effort, not real-time.
- If the institution receives a positive sanctions hint from `anchor-x402`, the institution must independently verify against the live OFAC SDN list before acting on it. Do not rely on `anchor-x402` alone to claim a sanctions match.

---

## 8. Reference architecture: cryptographic decision receipts

**Scenario.** An institution operates an internal AI agent that recommends actions to a human officer (e.g. recommended trades, recommended treasury moves, recommended counterparty risk decisions). Regulators and internal audit want a tamper-evident receipt that ties the agent's input to its output to the officer's sign-off, with public timestamps that cannot be backdated. The institution wants the receipt to survive a single-vendor compromise.

```
+--------------------------------------------------+
|  Institution AI agent                            |
|  - reads internal context (input)                |
|  - produces recommendation (output)              |
|  - writes both to internal evidence store        |
+--------------------------------------------------+
                 |
                 v
+--------------------------------------------------+
|  Evidence store (institution-controlled, WORM)   |
|  - input_blob, output_blob, decision_label,      |
|    decision_id, agent_version, prompt_template,  |
|    timestamp                                     |
|  - SHA-256 each blob                             |
|  - input_hash = SHA256(input_blob)               |
|  - output_hash = SHA256(output_blob)             |
+--------------------------------------------------+
                 |
                 v
+--------------------------------------------------+
|  Human officer sign-off                          |
|  - officer reviews (input, output, decision)     |
|  - officer signs message:                        |
|      "anchor-x402/attest/v1\n                    |
|       input=<input_hash>\n                       |
|       output=<output_hash>\n                     |
|       decision=<decision>"                       |
|    with eip191 (EVM key) or ed25519 (Solana key) |
+--------------------------------------------------+
                 |
                 v
+--------------------------------------------------+
|  POST /v1/attest                                 |
|  - body: {input_hash, output_hash, decision,     |
|           scheme, signature, [signer_pubkey]}    |
|  - cost: $0.01 USDC                              |
|                                                  |
|  anchor-x402:                                    |
|  - verifies signature (eip191 or ed25519)        |
|  - computes attest_merkle_root                   |
|  - anchors to Base + Solana in parallel          |
|  - returns {merkle_root, signer, base.tx,        |
|             solana.tx}                           |
+--------------------------------------------------+
                 |
                 v
+--------------------------------------------------+
|  Institution verifies and stores receipt         |
|  - re-derives attest_merkle_root locally,        |
|    must match returned merkle_root               |
|  - fetches base.tx and solana.tx from its own    |
|    RPCs, confirms calldata/memo == merkle_root   |
|  - writes the verified receipt next to the       |
|    input/output/decision in the evidence store  |
+--------------------------------------------------+
```

**Why dual-chain anchoring is institutionally meaningful.** Forging a receipt after the fact would require simultaneously reorging both Base and Solana so that the on-chain anchors of the forged Merkle root land at the original block height. Reorging Base requires defeating Coinbase's sequencer and Ethereum L1 checkpointing; reorging Solana requires defeating Solana's leader schedule and slot-by-slot confirmations. The two systems are independent; an attacker who can defeat one cannot necessarily defeat the other. A single-chain anchor is a valuable timestamp; a dual-chain anchor is a cross-validator timestamp that makes the forgery cost visibly larger than the value of any institutional decision worth attacking.

**Why this architecture is compliant.**
- The legal record is the institution's evidence store. The chain anchors are public commitments to that record, not replacements for it.
- The signature is produced by an institution-controlled key over an institution-controlled hash. `anchor-x402` cannot forge a signature because it never sees the signing key.
- The institution verifies independently (by re-deriving the Merkle root and reading the on-chain anchors from its own RPCs) that `anchor-x402` did not lie about what it anchored.
- The institution's audit retention requirements are met by the evidence store, not by `anchor-x402`. `anchor-x402`'s retention window is irrelevant after the receipt is written into the store.

---

## 9. Operational considerations

These are the concrete properties the institution should expect from the public deployment.

- **Region.** `us-east-1` (N. Virginia). All requests terminate at API Gateway in `us-east-1` and execute in Lambda in the same region. There is no multi-region deployment today. If `us-east-1` has an outage, `anchor-x402` is down.
- **Latency.** Round-trip from a client in US East is typically 50-150ms p95 (excluding the on-chain anchor latency on `/v1/anchor` and `/v1/attest`, which adds 1-3 seconds for Base inclusion and 1-2 seconds for Solana inclusion). Transcontinental clients add about 200ms one-way. Do not place `anchor-x402` in a hot trading loop.
- **Cold-start.** Lambda cold-start adds 200-800ms on the first request after an idle period. The institution's client should retry once on a sub-second timeout.
- **Rate limits.** No application-layer rate limit is enforced today. API Gateway has account-level throttling defaults (~10,000 RPS account-wide). The institution must self-throttle. A sensible production cap is 5 RPS per integration.
- **Cost ceilings.** The only spending control is the institution's own wallet balance. Configure a low USDC balance (e.g. $50 of working capital) and refill from a treasury wallet on a manual schedule. Do not connect a master wallet directly. Watch for runaway loops in client code.
- **Treasury balance and gas.** `anchor` and `attest` and `intel-wallet` cost real native gas (Base ETH, Solana SOL) on the `anchor-x402` side. The operator funds this wallet, but expect occasional Solana RPC failures (Solana is more flaky than Base under load). When Solana fails, `/v1/anchor` and `/v1/attest` still complete with a Base anchor and a `null` Solana anchor — the response shape is stable. The institution's verification logic should treat `solana == null` as a degradation, not a failure.
- **Endpoint stability.** The base URL `https://api.anchor-x402.com` is the stable handle. Future deployments may add a custom domain; the API Gateway URL will continue to redirect / serve.
- **Versioning.** The path prefix `/v1/...` is the stable contract. Breaking changes will land under `/v2/...`. Field additions are non-breaking. The institution should pin to `/v1/`.
- **Authentication.** None at the IAM / API key layer. The only authentication is the x402 USDC payment itself, validated by the CDP facilitator. The institution's wallet **is** its identity for billing purposes; treat the wallet's private key with operational-key sensitivity.
- **Logs.** CloudWatch logs are kept for operational debugging. Log retention is not contractually defined and should not be relied on for the institution's audit. Re-emphasizing: the institution's own log is the legal record.
- **Incident response.** There is no 24/7 on-call. The operator monitors CloudWatch alarms (errors, p95, 5xx, Solana anchor failure rate) and responds best-effort. Do not architect a workflow that requires a human at `anchor-x402` to be reachable.

---

## 10. When to upgrade to a Counsel-style institutional tier

The public `anchor-x402` deployment is the **public-utility tier**: open to anyone, paid per call in USDC, no auth, no SLA, no DPA, no per-tenant data, no contractual support relationship. It is well-suited for the appropriate uses in section 3 and the reference architectures in sections 7 and 8.

For institutional integrators whose use is drifting toward production-critical, regulator-facing, or customer-data-touching workflows, the natural upgrade path is a **Counsel-style institutional tier**. This tier is planned but not built today; describing it here is meant to give the integrator a clear horizon and to help frame the conversation with internal vendor management. The institutional tier would offer:

- **Customer authentication and per-tenant isolation.** API keys or signed JWTs scoped to the institution. Per-tenant logical separation of CloudWatch logs, metrics, and alarms.
- **Per-tenant retention.** A contractually-defined retention window for request/response logs, with the option to extend or to forward into the institution's SIEM.
- **Signed SLAs.** Uptime, latency p95, and on-chain anchor success rate, with credits for breach.
- **A formal DPA.** GDPR sub-processor language, sub-processor list maintenance, breach-notification timelines, and SCCs for cross-border transfers.
- **Real AML data.** A commercial relationship with one of the regulatory-grade AML vendors so the screen / intel-wallet endpoints return decisions instead of hints.
- **A multi-tenant treasury split.** Master / hot wallet split, multi-sig (Safe on Base, Squads on Solana) on the master, per-tenant gas budgeting and reporting.
- **24/7 on-call and named technical contacts.** PagerDuty / Statuspage / customer-success integration.
- **A clean compliance attestation path.** SOC 2 Type II for the application layer, ISO 27001 alignment, and on-request third-party penetration test reports.
- **Optional VPC endpoint or PrivateLink termination** so the institution's calls never traverse the public internet.

If the institution's workflow today is exploratory, sandboxed, or layered on top of an existing regulator-grade vendor as in sections 7 and 8, the public tier is the right place to be. If the workflow is migrating into a path where the institution would need to claim `anchor-x402` as a sub-processor of regulated data, where downtime would breach a regulatory or customer commitment, or where the institution would need to subpoena evidence retained by `anchor-x402` itself, that is the signal to start the conversation about the institutional tier.

---

## Appendix A: One-page summary for vendor-management intake

| Attribute | Value |
|---|---|
| Vendor name | `anchor-x402` |
| Service type | Public, stateless, x402-paid AI-agent commodity API |
| Deployment | AWS Lambda + API Gateway, region `us-east-1` |
| Base URL | `https://api.anchor-x402.com` |
| Endpoints | 9 (anchor, screen, attest, decode/tx, resolve/name, price/token, decode/calldata, parse/datetime, intel/wallet) |
| Pricing | $0.001 - $0.010 USDC per call, paid on Base or Solana |
| Authentication | x402 payment authorization only (no API keys) |
| Data classification of leg A | Customer-controlled. Should be PUBLIC only. |
| Data retention at vendor | None at app layer. CloudWatch operational logs only at IaaS layer. |
| SLA | None offered |
| DPA | None offered |
| BAA | None |
| SOC 2 | Inherited at AWS IaaS layer only. None at app layer. |
| PCI-DSS | Not in scope. Do not send cardholder data. |
| HIPAA | Not eligible. Do not send PHI. |
| FedRAMP | Not authorized. |
| GDPR | Stateless app; no DPA. AWS DPA covers infrastructure only. |
| Recommended classification | Non-attested public utility. Advisory-only output. |
| Recommended use | Sandboxes, public-data lookups, supplemental anchoring, first-pass triage. |
| Disqualified use | Live AML / KYC / PCI / HIPAA / FedRAMP / SOX-control workflows. |
| Upgrade path | Counsel-style institutional tier (planned, contact owner). |

---

## Appendix B: Document maintenance

This document describes the public deployment as of its publication date. The deployment is stateless, the surface area is small, and the categorical statements in sections 3, 4, and 5 are durable across point releases. Section 9 (operational considerations) is the section most likely to drift; integrators should re-read it before each material rollout. For written confirmation of a specific item for vendor-onboarding paperwork, contact the repository owner; confirmation is provided best-effort and does not constitute an SLA.
