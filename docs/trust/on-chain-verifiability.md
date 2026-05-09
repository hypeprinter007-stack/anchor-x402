# On-chain verifiability

> **The defining property of `/v1/anchor` and `/v1/attest`: the answer is verifiable independent of anchor-x402.** You don't trust us. You read the chain.

## Why this matters

Most SaaS trust models reduce to "we promise we did X, here's our SOC 2 report." The customer trusts the vendor's word, backed by audit attestations.

`anchor-x402`'s anchor and attest services break that pattern: every output is a **public, immutable, dual-chain cryptographic record** that the customer can verify themselves at any time, with no involvement from us, forever.

**Concretely:** if anchor-x402 the service is compromised tomorrow, taken offline, sued out of existence, sold to a hostile party, or replaced by a malicious clone — the receipts you obtained from us yesterday continue to be cryptographically valid against Base and Solana mainnet. We can't retroactively change them. We can't claim something different than what we anchored. We can't fake new ones with old timestamps.

This is the institutionally meaningful primitive. It changes the question from *"is the vendor trustworthy?"* to *"is SHA-256 still unbroken and is at least one of Base or Solana still censorship-resistant?"* — and the answer to both is yes.

## What gets anchored

For `POST /v1/anchor`:

```
merkle_root = SHA-256(canonical(client-supplied data))    # OR
merkle_root = client-supplied 32-byte hex
```

The 32 bytes are written to:

| Chain | Encoding | Cost (paid by treasury) | Finality |
|---|---|---|---|
| Base mainnet | EIP-1559 self-tx, calldata field carries the bytes | ~$0.0006 in ETH | ~12s |
| Solana mainnet | Memo program instruction data carries the hex | ~$0.0008 in SOL | ~400ms |

For `POST /v1/attest`:

```
merkle_root = SHA-256(
  "anchor-x402/attest/v1\n"
  "input=" + input_hash + "\n"
  "output=" + output_hash + "\n"
  "decision=" + decision
)
```

The same bytes go to both chains. The attest service additionally verifies the customer's signature over the same domain-separated message — the verified signer is returned in the response and is reproducible by anyone who has the same `(input_hash, output_hash, decision, signature, scheme)` tuple.

## How a customer verifies an anchor (free, no payment, no API call)

Given any anchor-x402 response carrying `{ merkle_root, base.tx, solana.tx }`:

**1. Verify Base anchor.**

```bash
# Use any public Base RPC
curl -sX POST https://mainnet.base.org \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_getTransactionByHash","params":["<base.tx>"]}' \
  | jq -r '.result.input'
```

The response field `input` will contain `0x<merkle_root>` exactly. If it doesn't match, the receipt was tampered with after issuance — drop it.

**2. Verify Solana anchor.**

```bash
curl -sX POST https://api.mainnet-beta.solana.com \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"getTransaction","params":["<solana.tx>",{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}' \
  | jq -r '.result.transaction.message.instructions[].parsed'
```

Look at the Memo program instruction. Its `data` field will contain the hex string of the merkle root. Same check — must match exactly.

**3. (Attest only) Verify the signer.**

Reconstruct the signed message:
```
anchor-x402/attest/v1
input=<input_hash>
output=<output_hash>
decision=<decision>
```

For `eip191`: standard `personal_sign` recovery via any Ethereum library (eth-account, ethers.js, viem). The recovered address must equal `signer` in the response.

For `ed25519`: standard Ed25519 verification using the supplied `signer_pubkey`. Any Solana SDK does this in 2 lines.

**4. Confirm dual-chain consensus.**

Both `base.input` and `solana.memo.data` must contain the same 64-char hex. They come from independent L1s with different consensus algorithms (proof-of-stake EVM rollup vs proof-of-stake on Solana). If both agree, forging requires either:
- Breaking SHA-256 (no known attack)
- Reorging both chains simultaneously (independently astronomically unlikely)
- Compromising your signer's key (your own concern, not ours)

## What this DOESN'T cover

We're transparent about the boundaries of the guarantee:

| Property | Guarantee |
|---|---|
| The hash you supplied was anchored at the timestamp on-chain | ✓ verifiable forever |
| The hash represents what you think it represents | ✗ that's your job — we just anchored opaque bytes |
| Your signature was valid at the time of submission | ✓ verifiable forever |
| The signer is who they claim to be (key-to-identity binding) | ✗ that's your job — we don't run a PKI |
| The decision was correct or wise | ✗ off-topic; we just timestamped what you signed |
| The off-chain context (the actual input/output) is preserved | ✗ only the hashes are anchored — you must retain the originals |

You bring the originals + the human-meaning. We bring the cryptographic timestamp.

## Comparison to traditional audit trails

| | Traditional audit log (Splunk, Datadog, S3 Object Lock) | anchor-x402 dual-chain receipts |
|---|---|---|
| Tamper-evident | requires trust in the log vendor's WORM guarantees | mathematically tamper-evident |
| Verifiable by third parties | only with vendor cooperation | by anyone, with no vendor involvement |
| Survives vendor going out of business | usually no (data deleted with account) | yes (chains are independent) |
| Cost per receipt | $0–$0.10 depending on storage class | $0.005 + ~$0.0014 in chain gas |
| Retention | typically 1–7y configurable | permanent (until both chains die) |
| Cross-jurisdiction admissibility | depends on vendor cooperation + venue | self-contained cryptographic evidence |
| Confidentiality of the underlying data | preserved (you store the data, log the metadata) | preserved (only the hash is on-chain) |

## The institutional positioning

For regulated decision flows where audit-trail integrity matters legally — SOX-scoped IT controls, model risk management, AI Act high-risk system records, FATF Travel Rule R.16 — the typical pattern is:

1. The institution generates the decision artifact and a hash of it
2. They sign that hash with a compliance officer's wallet
3. They submit the signature + hashes to `/v1/attest`
4. They receive the dual-chain receipt
5. They store the originals (decision artifact, signed message, anchor-x402 response) in their existing evidence vault — no migration required
6. At audit time: regulators verify the on-chain anchors directly, no need to subpoena anchor-x402

This pattern lets the institution add cryptographic evidence integrity to their existing workflow without replacing any existing system. anchor-x402 is purely additive infrastructure — never load-bearing for the institution's primary records.

## What changes if anchor-x402 disappears

If our AWS account is suspended, our domain expires, our GitHub repo is taken down, and our team is hit by a bus tomorrow morning — every receipt issued before that moment remains valid, verifiable, and admissible. The receipts have no dependency on our continued existence beyond the moment of anchoring.

This is the asymmetry: the customer paid for the anchoring transaction once, and they got a permanent record. There is no ongoing trust relationship with us required to preserve the value of what they bought.

For most institutional purchasing committees, this asymmetry is the point.

## Reference: the live evidence chain

Counsel (anchor-x402's predecessor project) published a worked example with real on-chain anchors:
- Merkle root: `7646dda1564bde0ef3f3971f4c002962df64246da4aa1d8c47247e7632494710`
- Base tx: [`0xf2908400…2c1fa7`](https://basescan.org/tx/0xf2908400d45af03d8c1b65b33851434c6fd178b682a143904a2bfa89ff2c1fa7)
- Solana tx: [`u8rqU4oS…ZMPKW2`](https://solscan.io/tx/u8rqU4oSQkkHQw93nCCtQym5crh4UjuoNvYhspLcUTEny59asrNNffpmxpgzRuZ4MXQnN5UEoCKQuDS16ZMPKW2)

Open both block explorers. Find the Input Data (Base) and the Memo program instruction (Solana). The 64-char hex appears verbatim on both. This is the entire trust model for `/v1/anchor` — **on-chain bytes are the receipt; everything else is convenience tooling around them.**
