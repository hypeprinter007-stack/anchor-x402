// attest-cast.mjs — deterministic, paced replay of a REAL attest-decision-receipt.mjs
// run, used only to render docs/demos/attest.mp4 reproducibly (see
// docs/demos/attest.tape). The signer and the Base/Solana tx links below are
// genuine on-chain anchors from an actual paid /v1/attest call — nothing is
// fabricated; only the terminal pacing is controlled so the recorder doesn't
// depend on live network latency. To run the real, live thing instead, use
// attest-decision-receipt.mjs with a funded BASE_PRIVATE_KEY.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const W = (s) => process.stdout.write(s);
const line = async (s, d = 90) => { W(s + "\n"); await sleep(d); };

const TRANSCRIPT = String.raw`
────────────────────────────────────────────────────────────────
AGENT DECISION RECEIPT  ·  anchor-x402 /v1/attest
────────────────────────────────────────────────────────────────
signer (agent decision key): 0x7818cB9cEad1E13E64A259F0867089dB75E374c5

[1] request received by the treasury agent:
{
  "action": "transfer",
  "asset": "USDC",
  "amount": 50000,
  "to": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
  "memo": "Q2 vendor payment — Acme Corp",
  "policy": "treasury/outbound-v3"
}

[2] agent decision: APPROVED  (confidence 0.97)
{
  "recommendation": "APPROVED",
  "confidence": 0.97,
  "checks": {
    "counterparty_sanctioned": false,
    "within_daily_limit": true,
    "memo_matches_invoice": true
  },
  "rationale": "Counterparty clean, amount within the $100k/day treasury limit, memo reconciles to invoice INV-2207."
}

[3] signing the decision tuple (eip191 personal_sign):
    anchor-x402/attest/v1
    input=c738971f94455fa6795ff0e44cf043b5e55e86ca9311bcf7ea6fa0bf70bd16f8
    output=d6ae3ac8b59298c3baabaf3dfb67d976020a54553479c1776e8bedbf646ccccb
    decision=APPROVED
`.trimStart();

const RECEIPT = String.raw`────────────────────────────────────────────────────────────────
RECEIPT
────────────────────────────────────────────────────────────────
decision        : APPROVED
signer verified : ✓  0x7818cB9cEad1E13E64A259F0867089dB75E374c5
merkle root     : 5adeed80dcc131e9bf24b84d14843c4f3db83cb1bdfe4df0f4c624bfea4132a1
signed at       : 2026-06-11T14:31:58.000Z
Base proof      : https://basescan.org/tx/0x075ccf4fca58a19d8c558e73b69f182f11d11d33eb7a12b255dd9d1df9f38d43
Solana proof    : https://solscan.io/tx/5YT3HRx9KPdUpwJP97xJkp8kmxKUamuJW3CPvM4Xk5AG66ACxDpvkfJGhVHQVyB4fNr5ykifEtmjQG8LfRQyorPX`;

const REVERIFY = String.raw`────────────────────────────────────────────────────────────────
INDEPENDENT RE-VERIFICATION  (no payment — pure client-side recompute)
────────────────────────────────────────────────────────────────
recomputed root : 5adeed80dcc131e9bf24b84d14843c4f3db83cb1bdfe4df0f4c624bfea4132a1
matches anchor  : ✓ yes — receipt is provable from the tuple alone

store (input_hash, output_hash, decision) and you can re-prove this
decision against the on-chain anchor anytime, without paying again.`;

// Beats 1-3: print near-instant, line by line.
for (const l of TRANSCRIPT.split("\n")) await line(l, 45);

// Beat 4: the paid call + dual-chain anchor — hold here like the real wait.
W("[4] anchoring the receipt  ·  ~$0.01 USDC on Base  +  seller anchor gas\n");
await sleep(3800);

for (const l of RECEIPT.split("\n")) await line(l, 110);
await sleep(700);
for (const l of REVERIFY.split("\n")) await line(l, 110);
await sleep(300);
