# anchor-x402 — Threat Model

**Version:** 1.0
**Last reviewed:** 2026-05
**Audience:** institutional procurement, security review, compliance
**Author / contact:** security at the email below

---

## 1. Executive summary

`anchor-x402` is a public, anonymously-consumable, pay-per-call microservice platform on AWS Lambda. Nine endpoints cover two product families: trust infrastructure (`anchor`, `attest`, `screen`, `intel/wallet`) and stateless commodity utilities (`decode/tx`, `decode/calldata`, `resolve/name`, `price/token`, `parse/datetime`). Settlement is in USDC on Base or Solana mainnet via the x402 v2 protocol; there is no API key, no customer account, no session. What you get for the per-call price is a single signed JSON response and — for `anchor` and `attest` — two on-chain transactions whose calldata you can independently verify forever, even if `anchor-x402` is later compromised, terminated, or attempts to lie. What you do **not** get is a SOC 2 report, a DPA, per-tenant authentication, a dedicated tenancy plane, or any service-side retention of your inputs. This is by design: the platform is positioned as a commodity tier sitting one layer below a fully-managed assurance product (e.g. our sister project [Counsel](https://github.com/hypeprinter007-stack/gavel), which carries WORM evidence, officer allowlists, and Article 17 erasure).

Reviewers should treat the service as a stateless oracle with on-chain evidence as the trust anchor: if you only need a verifiable Merkle commitment that a hash existed at time `T`, the dual-chain anchor is structurally robust against operator compromise, since forging the record requires reorging two L1s plus breaking SHA-256. For services that depend on upstream data (`screen`, `price/token`, `decode/calldata`, `intel/wallet`, `resolve/name`), the trust ceiling drops to "no worse than the upstream" — you should treat results as advisory and re-derive any decision-critical fact from a primary source. Per-service threat tables, cross-cutting threats, and known limitations are below; if you find a vulnerability please follow the disclosure policy in §7.

---

## 2. Trust boundaries

### 2.1 What the consumer trusts

| # | Component | Trust posture | Why this is unavoidable |
|---|---|---|---|
| 1 | The TLS endpoint at `*.execute-api.us-east-1.amazonaws.com` | Must trust AWS to terminate TLS correctly and route to the right Lambda | Standard AWS API Gateway threat surface; mitigated by ACM certs + AWS managed cert rotation |
| 2 | The Lambda execution environment | Must trust that Lambda is running unmodified `app.py` from the deployed artifact | Mitigated by AWS code signing on the deployment and CloudTrail of `UpdateFunctionCode` |
| 3 | AWS Secrets Manager (`anchor-x402/runtime`) | Must trust the secret store to release the treasury key only to the Lambda role | Mitigated by per-ARN IAM scoping and KMS encryption at rest |
| 4 | The CDP x402 facilitator (Coinbase) | Must trust the facilitator to verify and settle the USDC payment honestly before the Lambda processes the call | Inherited from x402 protocol; payment correctness is independent of business response correctness |
| 5 | Base + Solana mainnet finality | Must trust that confirmed L1 calldata / Memo entries are not reorged | This is a chain-security property, not an `anchor-x402` property |

### 2.2 What the consumer does **not** have to trust

| # | Property | Why |
|---|---|---|
| A | `anchor-x402` operator integrity, *for `anchor` / `attest` outputs* | The Merkle root that lands on Base + Solana is independently verifiable. The operator cannot forge a past anchor without reorging both chains. |
| B | `anchor-x402` retention | The service is stateless. There is no DynamoDB, no S3, no log of your input bytes beyond CloudWatch standard request logs (which are operator-private and auto-expire). |
| C | A persistent identity | No API key. No tenant. The service cannot deny you tomorrow based on what you sent today, and cannot link your calls absent payment-side correlation by the facilitator. |
| D | A custom CA / non-AWS TLS path | All traffic terminates at API Gateway; no homegrown TLS. |

### 2.3 Boundary diagram

```
┌─────────────┐     1. HTTPS + x402 PaymentRequired handshake
│ Consumer    │  ◀──────────────────────────────────────────┐
│  (agent)    │                                              │
└──────┬──────┘                                              │
       │ 2. Signed USDC payment header                       │
       ▼                                                     │
┌──────────────────────────────────────────────────────────┐ │
│ AWS API Gateway (TLS termination, AWS-managed cert)      │ │
└──────────────────────┬───────────────────────────────────┘ │
                       │                                     │
                       ▼                                     │
┌──────────────────────────────────────────────────────────┐ │
│ AWS Lambda  (Python 3.12, FastAPI + x402 middleware)     │ │
│   - reads secret on cold start ───┐                      │ │
│   - in-process 60s cache          │                      │ │
│   - no disk / DB writes           │                      │ │
└─────┬─────────┬─────────┬─────────┼──────────────────────┘ │
      │         │         │         │                        │
      ▼         ▼         ▼         ▼                        │
┌──────────┐┌────────┐┌────────┐┌─────────────┐              │
│ AWS      ││ CDP    ││ Public ││ Public RPC  │              │
│ Secrets  ││ x402   ││ APIs   ││ (Base+Sol)  │              │
│ Manager  ││ facil. ││ (CG,   ││ + ENS / SNS │              │
│ (KMS)    ││ (set-  ││ open-  ││             │              │
│          ││  tle)  ││ chain) ││             │              │
└──────────┘└────────┘└────────┘└──────┬──────┘              │
                                       │                     │
                                       ▼                     │
                              ┌─────────────────┐  3. Signed │
                              │ Base + Solana   │     JSON   │
                              │ mainnet (anchor │  ──────────┘
                              │ + attest only)  │
                              └─────────────────┘
```

The dotted line: outputs from `anchor` and `attest` cross trust boundary 5 (chain finality) and become independently verifiable. Outputs from the other seven services do not — they are signed by AWS TLS only and have the same operator-trust ceiling as any SaaS API.

---

## 3. Per-service threat tables (STRIDE-lite)

Each row is a concrete attack scenario, the existing mitigation, and the residual risk the consumer must accept. We focus on threats that change the consumer's decision-making, not on threats to the operator's revenue.

### 3.1 `POST /v1/anchor` — $0.005 — dual-chain hash anchoring

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | Operator returns a fake `base.tx` / `solana.tx` that does not actually exist on chain | Consumer **must** verify both tx hashes against the public explorer / their own RPC. The signed JSON is not the proof — the on-chain calldata is. | Low: a fake tx hash fails any `eth_getTransactionByHash` / `getTransaction` lookup. Recommend caller verify before treating the response as anchored. |
| S | Operator returns a real tx hash that anchors a *different* hash than what was submitted | Caller can compare `merkle_root` in the response to the calldata of the returned `base.tx` (last 64 hex chars after the `0x`) and to the Solana Memo data. | Low: fully detectable. Recommend SLA: always cross-check `tx.input == "0x" + merkle_root`. |
| T | Caller-supplied `note` field tampered to inject XSS / log-injection | `note` is bounded to 200 chars and never echoed into HTML; CloudWatch logs treat it as a literal string. | Low. |
| R | Operator denies that the anchor ever happened | Once on-chain, the calldata commitment is non-repudiable; explorer URLs are public. | Effectively zero for the on-chain record itself. |
| I | Submitted `data` (pre-hashed JSON) leaks via CloudWatch | The Lambda hashes server-side and logs only the merkle root, not the source data. The body is not persisted. | Low — but submitting the *hash* directly is strictly safer for sensitive material; this is the recommended path. |
| D | Spam $0.005 anchors → exhaust treasury gas → service downgrades to Base-only or fails | x402 paywall converts attack cost ≥ Base + Solana gas + $0.005. CloudWatch alarm `SolanaAnchorFailureRateHigh` notifies operator. Best-effort Solana means Base anchor still succeeds. | Medium: a well-funded griefer can drain gas faster than the operator tops up. Mitigation roadmap: hot/master split + auto-top-up alarm. |
| E | Consumer obtains operator's treasury key via the API surface | Treasury key never crosses the API boundary; only used by `services/anchor.py` to sign. | Low; same posture as any signing service. |

**Differentiator (see §5):** for this endpoint the consumer's trust requirement on `anchor-x402` is *limited to liveness*. Correctness is enforceable independently from the chain.

### 3.2 `GET /v1/screen` — $0.001 — sanctions / AML screening

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | Operator returns `sanctions_match=false` for a wallet that *is* on the live OFAC SDN list | The hardcoded corpus in `services/screen.py` covers the well-known crypto entries (Tornado Cash, Lazarus, Hydra, Garantex, Blender). It is **not** a daily Treasury.gov pull. | **High** for institutional decisioning. **Treat this endpoint as advisory only.** Pair with a primary source (Treasury.gov SDN CSV, Chainabuse, GoPlus, or proprietary AML feed) before any onboarding decision. See §6. |
| S | Operator is silently downgraded by an attacker — corpus replaced or stripped | Code path is open-source, deployed via signed CloudFormation (SAM); CloudTrail records `UpdateFunctionCode`. | Low if the operator is monitoring; consumer cannot directly attest the running code matches the public repo. |
| T | Wallet input is malformed and bypasses the chain inference regex | Inputs not matching EVM `^0x[0-9a-fA-F]{40}$` or Solana base58 32–44 → `chain_inferred=unknown`, `sanctions_match=false`, risk_level `medium`. Caller is told the verdict is inconclusive. | Low — inconclusive flag is honest. |
| R | Operator denies issuing a "low risk" verdict for a now-sanctioned wallet | Service does not retain per-call audit logs by design. If you need durable, non-repudiable evidence, anchor the response yourself via `/v1/attest`. | Medium: explicit known limitation. Mitigation pattern: pipe screen output through attest. |
| I | Wallet address leaks sensitive routing info | Wallet addresses are public on-chain identifiers; passing one to a third party is not a confidentiality violation. | Negligible. |
| D | Spam $0.001 calls to drain treasury or measure rate limits | x402 paywall + Lambda concurrency cap. No upstream API call fan-out → no asymmetric DoS amplification. | Low. |
| E | Caller crafts wallet to inject into the corpus dictionary | Lookup is dict `get`; no eval; no SQL. | Negligible. |

**Known limitation:** the OFAC corpus is a static snapshot, not a live feed. This is documented in §6 and in the source comments. Reviewers should not rely on `/v1/screen` as a primary AML control.

### 3.3 `POST /v1/attest` — $0.010 — signature verification + dual-chain anchor

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | Caller submits a signature they did not produce (replay from another app) | Domain separation: the signed message begins with the literal `anchor-x402/attest/v1\n`. A signature over a Counsel officer message, an EIP-712 typed transaction, or any other app's payload will not validate here. | Low. |
| S | Attacker submits a bogus signature, the service "verifies" it anyway | `services/attest.py` returns `(False, "")` on any recovery / verification failure; the route returns 400. The on-chain anchor only runs after verification succeeds. | Low. |
| T | `decision` field length-extension / control-character injection | `decision` is bounded to 64 chars; canonical UTF-8 encoding before signing. | Low. |
| R | Signer later disputes the attestation | The signed message + Merkle root + on-chain anchor on two chains is a non-repudiable record. | Effectively zero for any party who signed. |
| I | Pre-hashed `input_hash` / `output_hash` reveals upstream data | Caller submits already-hashed values; the service never sees plaintext. | Negligible (caller's responsibility to hash). |
| D | Same as `/v1/anchor` — gas drain via volume | Same mitigation; price is 2× higher ($0.010), so attack cost is higher. | Same residual risk as anchor. |
| E | Attacker attempts to bypass paywall via an unsigned `attest` request | x402 middleware enforces 402 PaymentRequired before the route handler runs; no bypass path. | Low. |

**Differentiator (see §5):** like `/v1/anchor`, this endpoint produces an on-chain record that is provable without trusting `anchor-x402`. The signed message + signature + recovered signer can be re-verified offline by anyone with the response.

### 3.4 `POST /v1/decode/tx` — $0.001 — mainnet transaction decoder

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | Operator returns a decoded tx that does not match what is on-chain | Decoder is a thin wrapper over the public RPC `eth_getTransactionByHash` / `getTransaction`. Caller can re-derive in two lines of web3 code. | Low — consumer can always re-fetch from any public RPC. |
| T | Caller submits a tx_hash for a tx the upstream RPC has not yet seen | Returns 502 with `tx not found` rather than fabricating. | Low. |
| R | n/a — output is a deterministic function of public chain state | n/a | None. |
| I | Tx input is public chain data; no confidentiality concern | n/a | None. |
| D | Asymmetric DoS — $0.001 inbound triggers an arbitrarily large RPC fetch | Upstream call is a single `eth_getTransactionByHash` (constant-size). Solana decode is one `getTransaction` with `maxSupportedTransactionVersion`. No fan-out. | Low. |
| E | n/a | n/a | None. |

### 3.5 `GET /v1/resolve/name` — $0.001 — ENS / Bonfida SNS

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | Operator returns a wrong address for a name | EVM path forward-verifies (resolve `name → address`, then reverse) per ENS best practice. Solana path is best-effort against the Bonfida public proxy. | Low for ENS, medium for SNS — Bonfida outages or rebranding can produce stale or null results. Caller should treat result as advisory if the recipient is value-bearing. |
| T | Homoglyph / IDN attack — caller passes a visually-similar punycode name | Service does not normalize visually; it resolves whatever ENS / SNS resolves. | **Caller responsibility.** This is a UX risk in the consumer's wallet, not a server-side bug. |
| R | n/a | n/a | n/a |
| I | Querying a name leaks consumer interest in that name to a third party | Upstream registries log queries. Consumer should assume the name itself is non-confidential. | Low. |
| D | Spam to drain gas / hit ENS provider rate limits | Public ENS RPC is the bottleneck; if rate-limited, caller sees 502. No gas spend on this path. | Low. |
| E | n/a | n/a | n/a |

### 3.6 `GET /v1/price/token` — $0.001 — CoinGecko-backed spot price

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | Operator forwards a stale or wrong price | 60-second in-process cache; `age_seconds` returned in response so the consumer can decide whether to use it. `source: "coingecko"` is explicit. | Medium for price-sensitive logic. **Do not use this for trade execution or oracle-grade pricing** — this is a quote-display service, not a price oracle. |
| S | CoinGecko is compromised and serves manipulated prices | Any single-source price feed has this risk; we surface the source. | Inherited from upstream. |
| T | Caller passes both `symbol` and `chain+contract` | Server returns 400 — these are mutually exclusive. | None. |
| R | Operator denies returning a specific price | Not retained server-side. If you need a non-repudiable price quote, anchor it via `/v1/attest`. | Documented limitation. |
| I | n/a — token symbols are public | n/a | n/a |
| D | Asymmetric DoS — call rate exceeds CoinGecko free tier | Cache absorbs hot keys; if CoinGecko 429s, consumer sees 503. No gas spend. | Low. |
| E | n/a | n/a | n/a |

### 3.7 `POST /v1/decode/calldata` — $0.001 — 4byte / openchain.xyz + ABI decode

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | openchain.xyz returns a wrong / malicious signature for a selector | Service surfaces the upstream `function_signature` and `source: "openchain.xyz"` so the consumer knows the trust ceiling. Decoder uses `eth_abi` for typed param decoding; the signature is the only upstream-trusted artifact. | Medium. **For high-value calldata (e.g. multisig payload review) re-decode with a contract's known ABI.** This service is not authoritative for unverified contracts. |
| S | Server claims to have decoded calldata it did not | Output includes `decoded: bool` and `candidates: list` — when ambiguous (collision in the 4-byte registry), candidates are listed; the consumer must pick. | Low. |
| T | Caller submits calldata with malformed length | Server rejects with 400 and a precise error. | None. |
| R | n/a | n/a | n/a |
| I | n/a — calldata is public chain data | n/a | n/a |
| D | $0.001 inbound, openchain lookup outbound — asymmetric? | Single HTTP GET per call, bounded payload, 8s timeout. | Low. |
| E | n/a | n/a | n/a |

### 3.8 `POST /v1/parse/datetime` — $0.001 — freeform datetime parser

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | Operator returns a wrong ISO timestamp | Pure function of input + `dateparser` library; deterministic given input. Consumer can re-parse locally. | Low. |
| T | `input` contains a parser-confusing string and yields wrong result | `confidence` field returned ("low"/"medium"/"high"); caller should reject low-confidence results for any time-critical decision. | Caller responsibility. |
| R | n/a | n/a | n/a |
| I | n/a — input is freeform text supplied by caller | Caller controls confidentiality. | n/a |
| D | Pathological regex / unicode input from caller | `dateparser` has known DoS-prone inputs in some versions; we run on Lambda with a hard 30s timeout, so the worst case is one Lambda invocation burned. | Low. |
| E | n/a | n/a | n/a |

### 3.9 `GET /v1/intel/wallet` — $0.005 — bundled wallet intelligence

| Class | Threat | Mitigation | Residual risk |
|---|---|---|---|
| S | A bundle source (e.g. ENS reverse, Bonfida, public RPC) is compromised → wrong identity claim | Each source is fetched in parallel; per-source failures populate `errors[]` and leave the slot null. ENS path forward-verifies. Sanctions check inherits the limitations of `/v1/screen`. | Medium. **Treat the bundle as advisory.** Re-derive any decision-critical fact (especially sanctions) from a primary source. |
| S | Operator stitches mismatched data (wallet A's ENS with wallet B's balance) | All slots are keyed by the same input; thread-pool tasks are pure functions of `wallet`. | Low. |
| T | Cache poisoning across consumers | Cache is in-Lambda-process memory keyed by raw wallet; not cross-tenant (no tenants). 60s TTL. | Low. |
| R | Operator denies returning a specific bundle | Bundle is not retained server-side. To bind a bundle to a moment, anchor `sha256(canonical_json(bundle))` via `/v1/attest`. | Documented pattern. |
| I | Bundle fan-out leaks consumer interest in `wallet` to multiple upstreams (Base RPC, Eth RPC, Solana RPC, ENS, Bonfida) | Public RPC providers see the request. Consumer should assume any wallet they query is observable to those providers. | Inherent to multi-source enrichment. |
| D | Asymmetric DoS — $0.005 in, 6+ outbound RPC calls out | Per-source 8s timeout, max 8 concurrent in `ThreadPoolExecutor`. Lambda concurrency capped. 60s in-process cache absorbs hot keys. | Medium — see §4 cross-cutting DoS amplification row. |
| E | n/a | n/a | n/a |

---

## 4. Cross-cutting threats

These apply uniformly to all nine services and are not repeated in the per-service tables.

### 4.1 AWS account compromise

| | |
|---|---|
| **Scenario** | Attacker gains IAM access to the operator's AWS account (root, deploy role, or Lambda execution role). |
| **Impact** | (a) read treasury keys from Secrets Manager, (b) replace the deployed Lambda with a malicious version, (c) drain treasury gas, (d) issue fraudulent attest / anchor responses for the period before detection. |
| **Mitigation** | IAM least-privilege scoped to the specific secret ARN; CloudTrail on; CloudWatch alarms on errors / latency / 5xx; deployment via SAM with reviewable diffs; no human SSH into the runtime. |
| **Residual risk** | **High for that window.** Consumers cannot detect a compromised Lambda from outside; they can detect a **forged anchor** by checking the on-chain calldata, which is the structural defence (§5). For non-anchor endpoints there is no consumer-side detection. |
| **What an institutional reviewer should ask** | (1) Frequency of treasury key rotation (target: monthly); (2) MFA on root + deploy roles; (3) CloudTrail retention; (4) bug-bounty contact (see §7). |

### 4.2 Treasury key leak

| | |
|---|---|
| **Scenario** | `treasury_evm_key` or `treasury_solana_key` exfiltrated. |
| **Impact** | Attacker can drain native balance and forge anchors signed by the same address. **Important:** these are *operator* hot keys for paying gas — they are not custodying consumer funds. The maximum loss is whatever ETH/SOL is in the hot wallet (intentionally small — ~0.001 ETH + 0.05 SOL). |
| **Mitigation** | Keys are in Secrets Manager (KMS-encrypted, IAM-scoped), never in env vars or CI. Rotation is a single `aws secretsmanager update-secret` plus a Lambda cold-start nudge; no redeploy. Roadmap: hot/master split so the hot key only holds gas. |
| **Residual risk** | Forged anchors signed by the leaked key would land on chain and be indistinguishable from legitimate ones during the leak window. **A consumer can defend:** the on-chain trust is in the calldata, not the signer — verify the merkle root matches your own Merkle, not the signer address. |

### 4.3 x402 facilitator (CDP) compromise

| | |
|---|---|
| **Scenario** | The Coinbase Developer Platform x402 facilitator is compromised, briefly down, or returns wrong settlement verdicts. |
| **Impact** | (a) consumer's USDC payment is lost / double-charged; (b) Lambda accepts an unpaid request because the facilitator returned a false verify; (c) Lambda rejects a paid request because the facilitator returned a false fail. |
| **Mitigation** | Facilitator settlement is independent of business logic — even if free / overcharged calls happen briefly, the on-chain anchor record is unaffected. Per-request EdDSA JWT auth (`services/cdp_auth.py`) means a leaked CDP credential cannot be replayed beyond its 120s `exp`. |
| **Residual risk** | Inherited from CDP's own posture; no consumer-side mitigation. |

### 4.4 Upstream API compromise

| Upstream | Used by | Failure mode | Mitigation |
|---|---|---|---|
| Base public RPC (`mainnet.base.org`) | `anchor`, `attest`, `decode/tx`, `intel/wallet` | wrong tx data, downtime | Caller can re-verify against any other RPC; no retries with stale data. |
| Solana mainnet RPC | `anchor`, `attest`, `decode/tx`, `intel/wallet` | downtime, rate limits | 2x retry with 1s backoff; best-effort on `anchor` (Base anchor still succeeds). |
| Ethereum public RPC | `decode/tx`, `intel/wallet`, ENS reverse | wrong / stale data | Same as Base. |
| CoinGecko | `price/token` | wrong price, rate limit, downtime | 60s cache; explicit `source` + `age_seconds` in response. |
| openchain.xyz | `decode/calldata` | malicious signature, downtime | Explicit `source` in response; consumer should re-decode with known ABI for high-value paths. |
| Bonfida public proxy | `resolve/name`, `intel/wallet` | downtime, schema drift | Best-effort; null on failure. |
| ENS registry | `resolve/name`, `intel/wallet` | wrong reverse | Forward-verification per ENS best practice. |

### 4.5 Supply chain (pip dependencies)

| | |
|---|---|
| **Scenario** | A transitive `pip` dependency (e.g. `web3`, `solders`, `eth-account`, `dateparser`, `cryptography`) ships a malicious release. |
| **Impact** | Code execution inside Lambda → identical to AWS account compromise (§4.1). |
| **Mitigation** | `requirements.in` / `requirements.txt` are pinned with hashes; deploys go through CI from a clean checkout; `cryptography` is the only crypto-handling dep and is widely audited. |
| **Residual risk** | Real but small. Recommend periodic `pip-audit` and Dependabot review. **No SBOM artifact is currently published** — see §6. |

### 4.6 DoS amplification ($0.001 inbound → $$ outbound)

The cheapest endpoints ($0.001) are the highest amplification surface. Threat profile per endpoint:

| Endpoint | Inbound | Outbound (worst case) | Amplification |
|---|---|---|---|
| `/v1/screen` | $0.001 | 0 (in-memory) | None |
| `/v1/decode/tx` | $0.001 | 1 RPC | Low |
| `/v1/resolve/name` | $0.001 | 1–3 RPC | Low |
| `/v1/price/token` | $0.001 | 1 CoinGecko (cached 60s) | Low (cache absorbs) |
| `/v1/decode/calldata` | $0.001 | 1 openchain | Low |
| `/v1/parse/datetime` | $0.001 | 0 | None |
| `/v1/anchor` | $0.005 | Base gas + Solana gas (~$0.001–0.005 combined) | None — operator pays out of price |
| `/v1/attest` | $0.010 | same as anchor | None |
| `/v1/intel/wallet` | $0.005 | up to 6 RPC + 1 Bonfida (60s cache) | **Highest** — see §3.9 row D |

`/v1/intel/wallet` is the only endpoint where a single paid call can fan out to multiple RPC providers. Lambda concurrency is the cap. **Mitigation roadmap:** per-IP API Gateway throttling. Currently relies on the AWS API Gateway default account-wide limit (10,000 RPS) which is well above any griefing budget at $0.005 per call.

### 4.7 Single-region deployment

The service is deployed only in `us-east-1`. A regional AWS outage takes the service down. There is no failover region. Consumers requiring multi-region SLA should not adopt this service for critical-path use.

---

## 5. On-chain verifiability — the structural differentiator

For seven of the nine endpoints, `anchor-x402` is a normal SaaS API: you trust TLS, the AWS runtime, and the operator's good faith. For `/v1/anchor` and `/v1/attest`, the trust model is **fundamentally different**.

### 5.1 What lands on chain

For every successful call to `/v1/anchor`:
- **Base mainnet:** an EIP-1559 transaction whose `data` field is exactly `0x` + the 64-character merkle root. From → to is the operator's treasury address (self-send). Cost: ~21,400 gas + 64 bytes calldata.
- **Solana mainnet:** a Memo program instruction containing the merkle root as UTF-8.

For `/v1/attest`, the same dual-chain anchor is performed, but the merkle root is computed deterministically as:

```
sha256("anchor-x402/attest/v1\ninput=<hash>\noutput=<hash>\ndecision=<label>")
```

— so any third party holding `(input_hash, output_hash, decision)` can re-derive the merkle root and verify it matches the on-chain calldata.

### 5.2 What this defeats

| Threat | Why it fails against the on-chain record |
|---|---|
| Operator deletes their database | Irrelevant — we don't have one, and the chain does. |
| Operator is compromised, attacker rewrites historical responses | Attacker would have to land a new on-chain tx with the same block height and tx hash — impossible without breaking the chain. |
| Operator goes out of business | Anyone holding the response JSON can re-verify against any public Base RPC and any Solana RPC, forever. |
| Operator lies about a past decision | Cryptographic non-repudiation. |
| TLS-MITM during the call | Attacker can swap the response, but a forged tx hash will not exist on chain. The consumer's verification step (mandatory) catches this. |

### 5.3 What it does *not* defeat

| Threat | Why on-chain verification doesn't help |
|---|---|
| The hash you submit doesn't represent what you think it does | Garbage-in, garbage-anchored. Domain separation in `/v1/attest` helps here. |
| Both Base + Solana suffer simultaneous deep reorgs | Theoretically possible, vanishingly unlikely; the dual-chain design doubles the attacker cost. |
| SHA-256 is broken | Not in this threat model's threat horizon. |
| The operator silently stops providing the service tomorrow | Past anchors remain valid; new ones cannot be created. This is a liveness, not safety, concern. |

### 5.4 Verification recipe (consumer-side)

For any `anchor` or `attest` response, the consumer should run:

```python
# Python pseudo-code
from eth_utils import to_bytes
import requests

# 1. Re-derive the merkle root from your own copy of the inputs
expected = sha256(canonical_bytes(your_inputs)).hexdigest()
assert expected == response["merkle_root"]

# 2. Pull the Base tx and confirm calldata
tx = base_rpc.eth_getTransactionByHash(response["base"]["tx"])
assert tx["input"].lower() == "0x" + expected
assert tx["from"].lower() == operator_treasury_address.lower()

# 3. Pull the Solana tx and confirm Memo data
sol = solana_rpc.getTransaction(response["solana"]["tx"], maxSupportedTransactionVersion=0)
memo_data = extract_memo(sol)  # the Memo program instruction's data bytes
assert memo_data.decode("utf-8") == expected
```

If these three checks pass, the merkle root commitment is non-repudiable regardless of what `anchor-x402` does next.

---

## 6. Known limitations (explicit)

This is the deliberate, documented gap between `anchor-x402` and an institutional-tier product. None of these are bugs; all are listed so that procurement can make an informed buy/no-buy decision.

| Limitation | Consequence | Roadmap / workaround |
|---|---|---|
| **No SOC 2 / ISO 27001 / PCI** audit | Cannot satisfy procurement frameworks that mandate a third-party attestation. | Roadmap: audit if institutional demand justifies the spend. Workaround for now: pair with Counsel for the audited tier. |
| **No DPA (Data Processing Agreement)** | Cannot satisfy GDPR / UK-DPA contractual requirements for personal-data processing. | Mitigated by design: `anchor-x402` does not retain inputs and processes only public chain data + caller-supplied hashes. If you submit personal data as input, that's between you and your DPA chain. |
| **No per-tenant authentication** | Anyone with the URL and $0.001 USDC can call. No tenant isolation, no per-customer rate limits, no per-customer audit trail. | Intentional commodity tier. Customers requiring tenancy + audit should use Counsel or a future paid tier. |
| **Hardcoded OFAC corpus is incomplete** | `/v1/screen` will miss any OFAC SDN entry added after the corpus was last edited and any non-OFAC sanctions list entirely. | Documented in `services/screen.py` and §3.2. **Treat as advisory.** Roadmap: daily Treasury.gov CSV pull. |
| **Single-region deployment (us-east-1)** | A regional AWS outage = full service outage. | Roadmap: multi-region active-active behind Route 53. Consumers requiring HA today: don't put `anchor-x402` on the critical path; use it for anchoring and verify on-chain. |
| **No DoS protection beyond AWS defaults** | A determined attacker with enough USDC can cause throttling. | API Gateway default account-level throttle is in effect. Roadmap: per-IP / per-payer rate limits. |
| **No SBOM published** | Procurement cannot directly assess transitive deps. | `requirements.txt` is in the repo with pins. Roadmap: publish CycloneDX SBOM on each release. |
| **In-process cache (not shared)** | Cold Lambda containers re-fetch upstream data; cache is not durable. | This is intentional — durable cache would create a stateful surface that contradicts the "stateless oracle" posture. |
| **No webhook / push delivery** | Strictly request/response. No subscription model. | By design. |
| **No formal SLA / uptime guarantee** | Best-effort. CloudWatch alarms feed the operator's pager but there is no contractual uptime. | Use the on-chain anchor as your durable record; the API liveness can fail without your past evidence being affected. |
| **No FedRAMP / IL boundary** | Cannot serve US Gov regulated workloads. | Out of scope for commodity tier. |

### 6.1 Comparison: `anchor-x402` (commodity tier) vs Counsel/gavel (assurance tier)

| Property | anchor-x402 | Counsel (gavel) |
|---|---|---|
| Tenant auth | None (anyone with $0.001) | Customer ID + officer allowlist |
| Evidence retention | Stateless (on-chain only) | WORM S3 Object Lock vault |
| GDPR Art. 17 erasure | N/A (no PII held) | First-class `/erase` endpoint |
| AML retention | N/A | 5-year, regulatorily-aligned |
| SOC 2 path | No | On roadmap |
| Pricing model | Per-call USDC | Subscription + per-call |
| Best for | Agents needing a single signed receipt | Institutions needing a defensible audit trail |

The two products **share the same on-chain anchor primitive.** A Counsel customer's audit trail and an `anchor-x402` consumer's anchor land on the same Base + Solana, with the same Merkle commitment format. This is intentional: the commodity tier is the open, public-priced primitive; the assurance tier is the wrapper with retention, identity, and process controls institutions need.

---

## 7. Disclosure policy

If you discover a vulnerability:

- **Email:** `security@anchor-x402.com` (subject: `[anchor-x402 security] <one-line summary>`).
- **PGP:** key on request.
- **Response SLA:** acknowledgement within 72 hours; status update within 7 days; coordinated disclosure on resolution.
- **Scope:** the deployed Lambda, the services in this repository, and the published OpenAPI spec. Out of scope: third-party services we depend on (CoinGecko, openchain.xyz, public RPCs, the CDP facilitator, AWS infrastructure) — please report those upstream.
- **Safe harbor:** good-faith research that does not exfiltrate user data, drain treasury beyond the price of one paid call, or degrade availability is welcome. We will not pursue legal action against researchers acting in good faith.
- **No bounty fund yet.** Public credit on resolution is offered.

---

## 8. Change log

| Date | Version | Change |
|---|---|---|
| 2026-05 | 1.0 | Initial threat model. |
