# anchor-x402 — Institutional Self-Audit Guide

This document is a guided code review for institutional security reviewers. Walk it top to bottom: each section names a concrete claim the service makes, points at the exact files (with line ranges) where the claim is implemented, describes what you will see, and notes the residual risk that the cited code does *not* cover.

Everything below references files at `https://github.com/<owner>/anchor-x402` and on disk under the repo root `/Users/cferjoair/anchor-x402`. The repo is MIT-licensed and public source — there is no closed component to audit. Open the files in your editor in the order each section specifies; nothing requires running the service.

---

## 1. Treasury private keys are not in Lambda environment variables at runtime

**Concern.** A reviewer with `lambda:GetFunctionConfiguration` on the AWS account must not be able to read the Base treasury private key, the Solana treasury keypair, or the CDP API secret out of the function's env block.

**What to read.**
- `template.yaml:44-76` — the `AnchorFunction` resource and its `Environment.Variables` block.
- `template.yaml:77-95` — the `AnchorRuntimeSecret` (AWS Secrets Manager) resource.
- `services/secrets.py:1-81` — the runtime fetcher.
- `services/anchor.py:22-28` — call sites that go through the secrets helper rather than reading env directly.
- `services/cdp_auth.py:18-23` — same pattern for the CDP secret.

**What you'll see.** The Lambda's `Environment.Variables` block (template.yaml:50-61) lists only non-sensitive inputs: the treasury *addresses* (public), `CDP_API_KEY_ID` (public identifier — see CDP docs), the two RPC URLs, and `ANCHOR_SECRET_ARN`. The CFN comment at lines 57-60 explicitly states "Sensitive runtime values (treasury keys, CDP secret) are NOT in env vars — they live in Secrets Manager." A separate Secrets Manager resource (`AnchorRuntimeSecret`, template.yaml:83-95) holds a single composite JSON document with three keys: `treasury_evm_key`, `treasury_solana_key`, `cdp_api_key_secret`. At cold-start, `services/secrets.py` calls `secretsmanager:GetSecretValue` once and caches the parsed JSON in module-level memory (lines 48-63); every subsequent fetch is in-process. The call sites in `services/anchor.py:22-28` and `services/cdp_auth.py:22-23` use `secrets.get(key, env_fallback="...")` — Secrets Manager is the source of truth, env vars are a local-dev fallback that is never reached in production because `ANCHOR_SECRET_ARN` is always set by CloudFormation (template.yaml:61).

**Residual risk.** A privileged AWS principal (the Lambda's own role, the CFN deployer, a root user) can still read the secret value through Secrets Manager itself — there is no way around that for any live system. The protection here is against the much larger circle of principals who hold `lambda:GetFunctionConfiguration` but not `secretsmanager:GetSecretValue` on this specific ARN. The composite secret is also a single blast-radius object; rotating one of the three values requires writing all three.

---

## 2. Lambda IAM is scoped, not wildcarded

**Concern.** The Lambda execution role must not grant `secretsmanager:*` or `Resource: "*"` style policies that would let a compromised function read unrelated secrets in the account.

**What to read.**
- `template.yaml:68-75` — the `Policies` block on `AnchorFunction`.

**What you'll see.** A single inline statement: `Effect: Allow`, `Action: secretsmanager:GetSecretValue` (only — no `PutSecretValue`, no `DescribeSecret`, no list/tag actions), `Resource: !Ref AnchorRuntimeSecret`. The `!Ref` resolves at deploy time to the ARN of *this* secret only; SAM does not permit it to expand to a wildcard. There is no `AWSLambdaBasicExecutionRole` managed policy attached, no implicit `*` resource grant, and no separate role attachment elsewhere in the template. The remaining permissions the function holds are the SAM-default CloudWatch Logs writer for its own log group (added implicitly by SAM and visible after deploy via `aws iam get-role-policy`).

**Residual risk.** SAM's implicit policies for CloudWatch Logs are not visible in the template; reviewers should run `aws iam list-attached-role-policies --role-name <generated>` post-deploy to confirm they are not over-broad. The Lambda's outbound network access is unrestricted (no VPC, no egress filter) — by design, since the service must reach Base RPC, Solana RPC, the CDP facilitator, CoinGecko, and openchain.xyz. An institutional deployment that needs egress allow-listing should re-house the function inside a VPC with a NAT and an outbound proxy.

---

## 3. All paid endpoints emit a valid v2 `PaymentRequired` response and refuse to execute without a settled payment

**Concern.** No endpoint in the price table can be called for free; the x402 middleware must intercept every paid route *before* the FastAPI route handler runs.

**What to read.**
- `app.py:299-345` — the `x402_routes` table that names every paid endpoint with its price and accepted networks.
- `app.py:75-85` — facilitator and resource-server wiring.
- `app.py:348-350` — the FastAPI middleware adapter that routes every request through `payment_middleware`.
- `app.py:116-122` — `_accepts_at(price)` builds the `PaymentOption` list for both Base and Solana mainnet.
- `app.py:353-355` — the only unpaid route, `/health`, which returns a static dict with no business logic.

**What you'll see.** `x402_routes` (lines 299-345) declares all nine paid endpoints — `/v1/anchor`, `/v1/screen`, `/v1/attest`, `/v1/decode/tx`, `/v1/resolve/name`, `/v1/price/token`, `/v1/decode/calldata`, `/v1/parse/datetime`, `/v1/intel/wallet` — each with a `RouteConfig(accepts=_accepts_at("$X"), …)` entry. `_accepts_at` (lines 116-122) emits one `PaymentOption` for Base (`eip155:8453`) and one for Solana mainnet (`solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp`). The middleware (`app.py:348-350`) calls `payment_middleware(x402_routes, x402_server)` — this is the official `x402.http.middleware.fastapi.payment_middleware` from the Coinbase x402 SDK, which intercepts the request *before* the FastAPI dispatcher and returns a 402 JSON body with the v2 `accepts` array if no `X-PAYMENT` header is present. Only after the facilitator (line 75-80, pointed at `https://api.cdp.coinbase.com/platform/v2/x402`) verifies and settles the payment does the request fall through to the route handler (`app.py:358-499`). The single unpaid path (`/health`, lines 353-355) returns a constant dict and touches no business logic.

**Residual risk.** This relies on the upstream `x402` library implementing the middleware correctly. The library is open source (Coinbase) and pinned at `x402==2.9.0` (requirements.txt:331); reviewers can independently audit the SDK's middleware. There is no in-repo test that proves "no payment ⇒ 402" for every route; the live e2e tests (`scripts/test_e2e.py`) instead exercise the *paid* path through the official client, which itself first observes the 402 then settles. Reviewers who want explicit "fail-closed" coverage can add a curl test against `/v1/anchor` with no `X-PAYMENT` header and expect HTTP 402.

---

## 4. CDP facilitator authentication is per-request EdDSA-signed; no plaintext API key crosses the wire

**Concern.** Settlement requests to the Coinbase CDP facilitator must not include a long-lived bearer token or a plaintext API secret in the request body or headers.

**What to read.**
- `services/cdp_auth.py:1-57` — the entire file.
- Specifically `services/cdp_auth.py:30-44` — `_build_cdp_jwt`.
- `services/cdp_auth.py:47-57` — `build_cdp_auth_provider`.
- `app.py:75-80` — wiring into the `HTTPFacilitatorClient`.

**What you'll see.** Every facilitator call (`verify`, `settle`, `supported`) is authenticated by a freshly minted EdDSA JWT with a 120-second `exp` (line 39). The signing key is the CDP API secret loaded via `secrets.get("cdp_api_key_secret", …)` (line 23) — that is, fetched from Secrets Manager per concern #1. The JWT header (line 37) carries `alg: EdDSA`, `typ: JWT`, `kid` set to the (non-sensitive) `CDP_API_KEY_ID`, and a fresh 16-byte hex `nonce` (`secrets.token_hex(16)`). The payload (line 38-39) binds the token to the specific `METHOD path` URI being called, so a captured token cannot be replayed against a different endpoint. The Ed25519 private key bytes never leave Lambda memory; only the signed compact JWS travels in the `Authorization: Bearer …` header (line 53-55).

**Residual risk.** The CDP secret is still a symmetric-equivalent (Ed25519 seed) — anyone who reads it from Secrets Manager can mint valid tokens. Token lifetime is 120s, which is short, but a stolen secret is fully exploitable until rotated. The 2-minute clock skew tolerance is implicit; a clock-skewed Lambda would fail to authenticate (a *fail-closed* property, not a vulnerability).

---

## 5. No PII is stored anywhere — by construction

**Concern.** Customer wallet addresses, request payloads, response bundles, and decision attestations must not be persisted to any datastore the operator could later be compelled to disclose.

**What to read.**
- `template.yaml:1-206` — the entire CFN template; search for `DynamoDB`, `S3`, `RDS`, `EFS`, `DocumentDB`, `Bucket`. None exist.
- `services/*.py` — search every service file for `boto3`, `open(`, `sqlite`, `psycopg`, `sqlalchemy`. The only `boto3` reference is `services/secrets.py:28` (Secrets Manager — read-only, scoped to the runtime secret).
- `services/screen.py:14-39` — the sanctions corpus is a hardcoded Python dict in module memory, not a database read.
- `services/intel_wallet.py:1-50` — the file's docstring explicitly states "Per-source failures degrade gracefully" and the cache is "in-process … keyed by the (raw) wallet address", with TTL 60 seconds.

**What you'll see.** The CloudFormation template declares exactly four resource types: `AWS::Serverless::Function`, `AWS::SecretsManager::Secret`, `AWS::SNS::Topic`/`Subscription`, and `AWS::CloudWatch::Alarm`/`AWS::Logs::MetricFilter`. There is no DynamoDB table, S3 bucket, RDS instance, EFS mount, ElastiCache cluster, or any other persistence resource. A grep across `services/` for persistence calls returns one hit — `services/secrets.py:28: import boto3` — used only to read the runtime secret. Wallet caches in `services/intel_wallet.py` and `services/token_price.py` live in process memory, are bounded by the Lambda container's lifetime, and disappear on cold-start. CloudWatch Logs do receive request lines for debugging, but the request bodies themselves (payloads passed to `/v1/anchor`, `/v1/attest`, etc.) are not logged unless an exception fires (and the exception logger emits the exception class and message, not the body — see `app.py:362-366, 422-424, 443-445, 482-484`).

**Residual risk.** CloudWatch Logs do capture some request metadata (path, method, latency) by default. If a route handler throws, the traceback can include local-variable hints depending on the Python logging configuration. Operators concerned about log-side data leakage should set a CloudWatch Logs retention policy (default is "never expire" if not set) and review the post-deploy log group. The `wallet` query parameter on `/v1/screen` and `/v1/intel/wallet` is technically present in the API Gateway access log; institutions that consider a wallet address PII should disable access logging or filter it.

---

## 6. Input validation is enforced via Pydantic before any route handler runs

**Concern.** A malformed or oversized payload must be rejected with HTTP 422 before reaching service-layer code; types must be strictly validated, not coerced.

**What to read.**
- `models.py:1-50` — `AnchorRequest` and its `model_validator`.
- `models.py:82-101` — `AttestRequest` with type-strict `scheme: Literal["eip191", "ed25519"]` and `_check_hashes` validator.
- `models.py:117-119` — `TxDecodeRequest` with `chain: Literal[...]` and explicit `tx_hash` length bound.
- `models.py:179-183` — `CalldataDecodeRequest` with `min_length=8`.
- `models.py:204-208` — `DatetimeParseRequest` with `min_length=1, max_length=500`.
- `app.py:358-499` — every route handler accepts a typed `req: <Model>` parameter, so FastAPI runs Pydantic validation before the body of the handler executes.

**What you'll see.** `_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")` (models.py:16) defines the canonical 32-byte-hex shape that gets enforced on `hash`, `input_hash`, and `output_hash`. `AnchorRequest._check_exclusive` (lines 34-40) requires *exactly one* of `hash` or `data` and runs after field parsing, so a payload supplying both — or neither — is rejected with a 422. `AttestRequest._check_hashes` (lines 93-101) enforces the hex shape on both hashes and additionally requires `signer_pubkey` when `scheme == "ed25519"`. The `Literal[...]` types on `scheme`, `chain`, and `confidence` make Pydantic refuse to coerce strings outside the enumeration. Because every `@app.<method>(...)` route in `app.py` declares the model as the parameter type (e.g. `def anchor(req: AnchorRequest) -> AnchorResponse`, line 359), FastAPI invokes the Pydantic validator *before* the handler body runs; an invalid body never reaches `anchor_svc.anchor_dual_chain`.

**Residual risk.** The `data` field on `AnchorRequest` (models.py:24-27) is typed `Any` — by design, since the contract is "anything you send gets canonicalized + SHA-256'd." A reviewer wanting deeper input policy (max depth, max size, whitelisted JSON shapes) would need to add a custom validator. FastAPI's default request size limit is process-level (uvicorn / API Gateway 6 MB request quota); there is no in-app cap.

---

## 7. Domain separation prevents cross-app signature replay on `/v1/attest`

**Concern.** A signature submitted to `/v1/attest` must not be reusable as authorization for any other application — for example, an EVM transaction, a different signing protocol, or a Counsel officer authorization.

**What to read.**
- `services/attest.py:28-36` — `build_message`.
- `services/attest.py:39-46` — `attest_merkle_root`.
- `services/attest.py:78-102` — `verify`, the top-level entry point.
- `services/attest.py:1-21` — file header explaining the rationale.

**What you'll see.** The signed bytes always begin with the literal prefix `anchor-x402/attest/v1\n` (services/attest.py:31). This is unambiguous: it is not a valid Ethereum RLP-encoded transaction, not a valid `personal_sign` payload that any wallet UI would render as a financial intent, and not a known prefix of any other major protocol. `verify_eip191` (lines 49-61) wraps the message with `encode_defunct` from `eth-account`, which prepends the standard `\x19Ethereum Signed Message:\n` magic string before hashing — so the actual digest the EVM signer produces is `keccak256("\x19Ethereum Signed Message:\n<len>" + "anchor-x402/attest/v1\nrest…")`, which cannot collide with a real transaction hash. `verify_ed25519` (lines 64-75) signs the raw domain-separated bytes directly. The Merkle root anchored on-chain (line 39-46) is `SHA-256` over the same domain-separated bytes — so an on-chain observer can independently recompute the root from `(input_hash, output_hash, decision)` and verify it matches the calldata in the Base anchor tx. Cross-app replay would require an attacker to find a payload that *also* starts with `anchor-x402/attest/v1\n` in some other protocol's signing domain — by construction none does.

**Residual risk.** The version suffix is `v1`; a `v2` schema must use a distinct prefix or risk crossing the same name space. There is no on-chain registry of allowed signers — `/v1/attest` reports the recovered EVM address or supplied Solana pubkey but does not check it against any allow-list (this is intentional; the consumer is expected to enforce its own authorization on top). For a more institutional posture, see Counsel/gavel's officer allow-list as a comparative pattern.

---

## 8. The sanctions screening corpus is reproducible from public sources

**Concern.** A reviewer must be able to verify every entry in the screening list against an authoritative public source (OFAC) — no proprietary, undisclosed, or arbitrary additions.

**What to read.**
- `services/screen.py:1-39` — the `_EVM_SANCTIONED` and `_SOLANA_SANCTIONED` dicts with inline comments.
- `services/screen.py:14-15` — explicit comment: "Production: replace with daily Treasury.gov CSV pull."
- `services/screen.py:53-99` — the `screen()` function, which is pure and stateless.

**What you'll see.** Every EVM entry is annotated with the OFAC sanctions program name and (in the case of Tornado Cash, Hydra, Garantex, Blender.io) the publication date — e.g. `# Tornado Cash (OFAC SDN, August 2022)` (line 17). Reviewers can cross-check each address against `https://www.treasury.gov/ofac/downloads/sdn.csv` or the SDN search UI. The Solana dict (lines 36-39) is intentionally empty with a comment that production should populate it from the Treasury feed. The `screen()` function (lines 53-99) only does a dict lookup against the lowercased EVM address (line 76-77) or raw base58 Solana pubkey (line 80) — there is no fuzzy match, no scoring, no proprietary data.

**Residual risk.** This is a static MVP corpus, not a daily pull. An address sanctioned after this code was last edited will not be flagged. The README at the file head (lines 1-7) is candid that production should pair this with a daily Treasury.gov CSV refresh and ideally Chainabuse / GoPlus / proprietary AML data. There is no clustering or transaction-graph analysis — a wallet that *received* funds from a sanctioned address but is not itself listed will not match.

---

## 9. Every secret read is logged via CloudTrail

**Concern.** A reviewer must be able to reconstruct every access to the runtime secret — when it happened, which principal made it, and from which source IP.

**What to read.**
- `template.yaml:68-75` — the IAM policy granting `secretsmanager:GetSecretValue` on the runtime secret ARN.
- AWS feature reference: every API call against AWS Secrets Manager is recorded by CloudTrail by default, including the calling principal, source IP (or VPC endpoint), and the secret ARN.

**What you'll see.** The Lambda function calls `GetSecretValue` once per cold-start container (`services/secrets.py:57`). Each call is captured in the AWS-account CloudTrail event history as a `secretsmanager.amazonaws.com / GetSecretValue` event with `eventName=GetSecretValue`, `userIdentity.type=AssumedRole`, and the secret ARN in `requestParameters.secretId`. CloudTrail's management-events stream is enabled by default in every AWS account and retains 90 days of history at no cost; long-term retention requires a CloudTrail trail with an S3 destination, which the operator should set up at the account level.

**Residual risk.** CloudTrail does not log the secret *value*, only the access metadata — which is what you want, but it means a reviewer cannot tell from CloudTrail alone whether the secret content is correct. CloudTrail event delivery is best-effort and not real-time. If the account has not enabled an organization-level trail, the audit history is bounded to the 90-day default. None of this is anchor-x402-specific; it is a property of AWS.

---

## 10. The dual-chain anchor is independently verifiable, with or without the service

**Concern.** A customer holding `(input_hash, output_hash, decision)` (or any pre-image) must be able to verify the on-chain anchor without trusting the operator's logs or response.

**What to read.**
- `services/anchor.py:30-53` — `anchor_to_base`: posts the 32-byte digest as EIP-1559 calldata.
- `services/anchor.py:56-104` — `anchor_to_solana`: posts the digest via the Memo program.
- `services/anchor.py:107-122` — `anchor_dual_chain`: the parallel orchestrator.
- `services/attest.py:39-46` — the deterministic Merkle root construction.
- `scripts/test_e2e.py:54-58` — live e2e proof: each run posts a fresh `sha256("anchor-x402 test " + os.urandom(8))` digest and prints the response, which contains both `base.tx` and `solana.tx` hashes.

**What you'll see.** The Base side (`anchor.py:40-50`) constructs an EIP-1559 transaction whose `to` is the treasury's own address, `value` is 0, and `data` is `0x` + the 64-hex Merkle root. After submission, the calldata is permanently recorded in the Base block and is decodable by any Base RPC node — `eth_getTransactionByHash` returns the raw input. The Solana side (`anchor.py:96-100`) builds a Memo-program instruction whose `data` is the UTF-8 bytes of the hex root; Solana's transaction history on `mainnet-beta` is similarly immutable and queryable via `getTransaction`. Both txs are returned to the caller (`anchor.py:121-122`) along with the input root. To verify *without* the service: take the response's `merkle_root`, pull the Base tx via `https://basescan.org/tx/<base.tx>`, decode the input field, confirm it equals `0x` + `merkle_root`. Repeat for Solana via `https://solscan.io/tx/<solana.tx>`. For `/v1/attest`, also recompute `SHA-256` over `anchor-x402/attest/v1\ninput=<…>\noutput=<…>\ndecision=<…>` (per services/attest.py:28-46) and confirm it matches the on-chain root.

**Residual risk.** The Solana side is best-effort: `anchor_dual_chain` (lines 107-122) catches Solana failures and returns `solana_tx=None` while still surfacing the Base tx (line 119-121). A customer needing strict dual-chain proof should treat a `solana=None` response as a partial success, not a full anchor. RPC providers (`https://mainnet.base.org`, `https://api.mainnet-beta.solana.com`) are public and unauthenticated — the service depends on them for liveness but not for correctness; the on-chain proof, once written, is independent.

---

## 11. The dependency lockfile is pinned and reproducible

**Concern.** A reviewer must be able to rebuild the exact same dependency tree months from now — no transitive drift, no untracked floating versions.

**What to read.**
- `requirements.in:1-15` — the human-readable list of direct deps.
- `requirements.txt:1-335` — the auto-generated, fully resolved lockfile.
- `Makefile:9-10` — the `lock` target: `uv pip compile requirements.in -o requirements.txt`.
- `Makefile:5-7` — the `install` target uses the *resolved* `requirements.txt`, not `requirements.in`.

**What you'll see.** `requirements.in` lists 14 direct dependencies (fastapi, uvicorn, mangum, boto3, x402[…], cryptography, pydantic, python-dotenv, web3, solders, base58, requests, eth-abi, dateparser, python-dateutil) with no version pins — only the names. `requirements.txt` is the `uv pip compile` output, with every transitive dependency pinned to an exact version (e.g. `x402==2.9.0`, `cryptography==48.0.0`, `pydantic==2.13.4`, `boto3==1.43.6`) and annotated with the upstream source (`# via fastapi`, etc.). The header at line 1-2 of requirements.txt records the exact uv command that produced it. The deploy pipeline (`make build && make deploy`, Makefile:12-16) bundles only what `requirements.txt` resolves to — SAM `pip install -r requirements.txt` against this lockfile produces a byte-identical wheel set on any subsequent build (modulo platform-specific wheels for `cffi`, `pycryptodome`, `solders`).

**Residual risk.** There is no SHA hash pin (uv supports it via `--generate-hashes` but the project does not use it), so a malicious upload to PyPI under an existing version-name could substitute a different artifact. There is no SBOM file (CycloneDX / SPDX) committed to the repo. There is no automated CVE scan in CI. Reviewers concerned about supply-chain attacks should run `pip-audit -r requirements.txt` and `osv-scanner` against the lockfile; both will read the file as-is.

---

## 12. CloudWatch alarms cover the failure modes a customer would care about

**Concern.** A reviewer must be able to confirm that the operator will be notified if the service starts erroring, slowing, or failing on the Solana side.

**What to read.**
- `template.yaml:97-108` — `AlarmTopic` and the optional email subscription.
- `template.yaml:110-129` — `LambdaErrorsAlarm` (Errors > 5 over 5 min).
- `template.yaml:131-150` — `LambdaDurationP95Alarm` (P95 > 25s over 5 min).
- `template.yaml:152-171` — `ApiGateway5xxAlarm` (5xx > 3 over 5 min).
- `template.yaml:173-200` — the metric filter on the literal log string `"solana anchor failed"` and its alarm (> 5 / 5 min).
- `services/anchor.py:118-121` — the corresponding log line: `log.warning("solana anchor failed: %s: %s", …)`.

**What you'll see.** Four CloudWatch alarms attach to the same `AlarmTopic` SNS topic, each with both `AlarmActions` and `OKActions` so subscribers see both fire and recover events. The Solana failures alarm is the most interesting: it is a *log-derived metric* — the `SolanaAnchorFailuresMetricFilter` (lines 173-182) pattern-matches the literal string `"solana anchor failed"` in the function log group, increments a custom CloudWatch metric, and the alarm (lines 184-200) triggers when that metric exceeds 5 in 5 minutes. The producer of that log line is `services/anchor.py:120` — a single, exact-string call site. The README operations section (lines 226-250) documents how to subscribe an additional endpoint to the topic post-deploy.

**Residual risk.** The thresholds are tuned for the current scale (single-Lambda, sub-cent payments); a higher-volume deployment must re-tune. No alarm covers low-balance treasury (`treasury runs out of ETH/SOL` would surface only as elevated Errors). No alarm covers the CDP facilitator returning unexpected verification failures — a partial-availability customer-impact event that is not separately tracked. There is no AWS Cost Anomaly alarm.

---

## 13. No customer-data persistence means GDPR data-subject rights are largely N/A

**Concern.** Verify that there is no stored personal data that would obligate the operator to honor GDPR Article 15 (access), Article 16 (rectification), or Article 17 (erasure) requests.

**What to read.**
- All of section #5 above.
- `template.yaml` in full — search for any persistence resource. None exists.
- `services/intel_wallet.py:23-24` and `services/token_price.py` — confirm caches are in-process only.

**What you'll see.** Because no datastore exists, there is no record of a request after the Lambda container that handled it terminates (typically minutes-to-hours of warm-cache lifetime). The only data that crosses a persistence boundary is the dual-chain anchor itself (Base calldata + Solana Memo) — and that is, by deliberate design, an immutable, public, non-PII 32-byte digest of *whatever the customer chose to anchor*. If the customer hashes a payload that contains personal data and submits the resulting hash to `/v1/anchor`, the on-chain artifact is the hash, not the payload. The payload itself is never stored.

**Residual risk.** GDPR Art. 17 ("right to be forgotten") cannot be honored on the on-chain anchor itself — that's a property of public blockchains, not anchor-x402. The CloudWatch Logs and API Gateway access logs may briefly contain the wallet address used for screening / intel queries; institutional customers should treat that as the residual surface and apply log-retention controls. Anyone who treats anchored hashes as personal data should pre-image-protect their inputs before calling the service. This is not a substitute for a Data Protection Impact Assessment for the operator's specific use case. For a more institutional posture (WORM vault, GDPR Art. 17 wired into an erasure flow, customer auth, officer allow-list), see the Counsel/gavel reference at `https://github.com/<owner>/gavel`.

---

## 14. Source code is fully open and auditable

**Concern.** No part of the runtime is closed-source or "trust me" — a reviewer can read every line of the production code path.

**What to read.**
- `LICENSE:1-22` — the full MIT License.
- The repo on GitHub (public).
- `requirements.txt` — every dependency is a public PyPI package.

**What you'll see.** The entire FastAPI app, x402 wiring, Pydantic models, CDP auth provider, sanctions corpus, secrets helper, dual-chain anchorer, and attest verifier are in 12 Python files (`app.py`, `models.py`, and 10 files in `services/`) totaling under 2,000 lines. Every function used at runtime resolves to a file in this repo or a pinned wheel from PyPI. There is no compiled blob, no closed-source plugin, no managed-service back-end besides AWS Lambda itself, AWS Secrets Manager, AWS CloudWatch, and the Coinbase CDP facilitator (which is itself a public, documented HTTP service whose request/response contract is described in the [x402 spec](https://github.com/coinbase/x402)).

**Residual risk.** "Open source" means a reviewer *can* audit, not that one *has*. The code is short enough for a single reviewer to read end-to-end in an afternoon. The author is identified in `LICENSE:3` (Christopher Ferjo) and `README.md:272-274`. There is no external code-signing of releases; reviewers should pin to a specific git commit SHA when integrating, not to `main`.

---

## 15. CFN parameters seed Secrets Manager only; rotation is independent of redeploy

**Concern.** A reviewer must understand that the deploy-time parameters (`TreasuryPrivateKey`, `SolanaTreasuryKey`, `CdpApiKeySecret`) are *not* the long-term storage of the secret — they are a one-time seeding mechanism, and rotation does not require a new deploy.

**What to read.**
- `template.yaml:12-39` — the `Parameters` block; the three sensitive parameters are marked `NoEcho: true`.
- `template.yaml:83-95` — the `AnchorRuntimeSecret` resource and its `SecretString` literal.
- `template.yaml:77-82` — the inline comment block describing the rotation flow.
- `README.md:226-237` — the operator-facing rotation procedure.

**What you'll see.** Each sensitive parameter has `NoEcho: true` (template.yaml:18, 27, 34), meaning the CloudFormation console will mask it and `aws cloudformation describe-stacks` will not return it. The parameters' descriptions explicitly state "Consumed at deploy-time only to seed the runtime secret; never lands in Lambda env" (lines 19, 28, 35). The `AnchorRuntimeSecret.SecretString` (lines 90-95) is a `!Sub`-rendered JSON template that bakes the parameters into the Secrets Manager resource on first deploy. After that, the comment at lines 77-82 directs operators to rotate via `aws secretsmanager update-secret --secret-id <arn> --secret-string '{...}'` — which mutates the secret directly without touching CloudFormation. The README's Operations section (lines 226-237) documents the same flow with a copy-pasteable command, plus the `aws lambda update-function-configuration --description "rotate $(date +%s)"` trick to force a cold-start so the new secret is picked up. The `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain` directives (lines 85-86) mean a stack delete or replacement does *not* destroy the secret — so a botched redeploy cannot lose the keys.

**Residual risk.** The CFN parameters are visible to anyone with `cloudformation:GetTemplate` *during* the deploy (briefly, until the deploy completes) — not after, because `NoEcho` strips them from the stored template. The seeding pattern means the *initial* private keys are typed once at the operator's terminal during the first `sam deploy --guided` (Makefile:18-19); operators with strict key-handling policies should generate the keys on a separate offline machine, paste them into the one-time deploy prompt, and rotate them out before going live. There is no automated rotation schedule (e.g. AWS Lambda rotation function) wired up — rotation is operator-driven.

---

## How to use this document end-to-end

1. **Clone the repo and check out a tagged commit.** Pin to a specific SHA, not `main`.
2. **Read sections 1, 2, 4, 9, 15 in one sitting** — they form the "key custody and access" story.
3. **Read sections 3, 6, 7, 10** — the "request integrity and on-chain proof" story.
4. **Read sections 5, 13, 14** — the "what's *not* there" story (no PII, no closed source).
5. **Read sections 8, 11, 12** — the "supply chain and operations" story.
6. **Run `pip-audit -r requirements.txt`** as a first-pass external check on dependency CVEs.
7. **Run `scripts/test_e2e.py --only anchor`** against the live deployment; observe the response printing real Base + Solana mainnet tx hashes you can independently look up on basescan.org and solscan.io.
8. **Verify the on-chain proof yourself** for one of those tx hashes per section #10.

If, after walking the doc this way, you find a claim the code does not back up — that's the bug we want to know about. File an issue or contact the maintainer named in `LICENSE`.
