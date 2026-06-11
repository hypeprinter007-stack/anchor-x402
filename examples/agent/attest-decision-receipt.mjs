#!/usr/bin/env node
// attest-decision-receipt.mjs — the agent-decision-receipt demo for /v1/attest.
//
// Story: an autonomous treasury agent is asked to approve an outbound USDC
// transfer. It evaluates the request, reaches a decision, and then produces a
// *cryptographic receipt* binding that decision to the key that approved it —
// dual-anchored on Base + Solana mainnet. Later, anyone holding the 3-tuple
// (input_hash, output_hash, decision) can re-derive the on-chain Merkle root
// with NO second paid call. That's the liability record: who approved what.
//
// One paid call: POST /v1/attest (~$0.01 USDC) + the seller's own anchor gas.
//
// Run (funded Base buyer wallet — NOT the treasury key):
//   BASE_PRIVATE_KEY=0x... node attest-decision-receipt.mjs

import { createHash } from "node:crypto";
import { createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";
import { wrapFetchWithPaymentFromConfig } from "@x402/fetch";
import { ExactEvmScheme } from "@x402/evm";

const PK = process.env.BASE_PRIVATE_KEY;
if (!PK) { console.error("BASE_PRIVATE_KEY env var required (0x-prefixed, funded Base wallet)"); process.exit(2); }

const BASE_URL = process.env.ANCHOR_BASE_URL || "https://api.anchor-x402.com";
const account = privateKeyToAccount(PK);
const walletClient = createWalletClient({ account, chain: base, transport: http() });
const signer = { address: account.address, signTypedData: (m) => walletClient.signTypedData({ account, ...m }) };
const paidFetch = wrapFetchWithPaymentFromConfig(fetch, {
  schemes: [{ network: "eip155:8453", client: new ExactEvmScheme(signer) }],
});

const sha256 = (s) => createHash("sha256").update(s).digest("hex");
const rule = () => console.log("─".repeat(64));

// ── Beat 1: the agent receives a request ───────────────────────────────────
// The "input" is the task the agent was given. Hash it client-side; the server
// never sees the raw payload — only the 64-char digest crosses the wire.
const request = {
  action: "transfer",
  asset: "USDC",
  amount: 50000,
  to: "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
  memo: "Q2 vendor payment — Acme Corp",
  policy: "treasury/outbound-v3",
};

rule();
console.log("AGENT DECISION RECEIPT  ·  anchor-x402 /v1/attest");
rule();
console.log(`signer (agent decision key): ${account.address}\n`);
console.log("[1] request received by the treasury agent:");
console.log(JSON.stringify(request, null, 2));

// ── Beat 2: the agent reaches a decision ────────────────────────────────────
// In a real deployment this is your model's output. The "output" is the agent's
// reasoned recommendation; it's what the signer is attesting to.
const decision = "APPROVED";
const reasoning = {
  recommendation: decision,
  confidence: 0.97,
  checks: {
    counterparty_sanctioned: false,
    within_daily_limit: true,
    memo_matches_invoice: true,
  },
  rationale: "Counterparty clean, amount within the $100k/day treasury limit, memo reconciles to invoice INV-2207.",
};

console.log(`\n[2] agent decision: ${decision}  (confidence ${reasoning.confidence})`);
console.log(JSON.stringify(reasoning, null, 2));

const inputHash = sha256(JSON.stringify(request));
const outputHash = sha256(JSON.stringify(reasoning));

// ── Beat 3: bind the decision to the signer ─────────────────────────────────
// The signer signs the EXACT domain-separated message the server reconstructs
// (services/attest.py). Domain separation ("anchor-x402/attest/v1") means this
// signature cannot be replayed as an EVM tx or any other app's payload.
const message = `anchor-x402/attest/v1\ninput=${inputHash}\noutput=${outputHash}\ndecision=${decision}`;
console.log("\n[3] signing the decision tuple (eip191 personal_sign):");
console.log(message.split("\n").map((l) => "    " + l).join("\n"));
const signature = await walletClient.signMessage({ account, message });

// ── Beat 4: pay $0.01 to anchor the receipt on two chains ───────────────────
console.log(`\n[4] anchoring the receipt  ·  ~$0.01 USDC on Base  +  seller anchor gas`);
const res = await paidFetch(`${BASE_URL}/v1/attest`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ input_hash: inputHash, output_hash: outputHash, decision, scheme: "eip191", signature }),
});
if (!res.ok) { console.error("attest failed:", res.status, await res.text()); process.exit(1); }
const att = await res.json();

// ── Beat 5: the receipt ─────────────────────────────────────────────────────
rule();
console.log("RECEIPT");
rule();
console.log(`decision        : ${att.decision}`);
console.log(`signer verified : ${att.signer_verified ? "✓" : "✗"}  ${att.signer}`);
console.log(`merkle root     : ${att.merkle_root}`);
console.log(`signed at       : ${new Date(att.signed_at * 1000).toISOString()}`);
console.log(`Base proof      : ${att.base?.explorer_url ?? "—"}`);
console.log(`Solana proof    : ${att.solana?.explorer_url ?? "—"}`);

// ── The kicker: re-derive the proof locally, no second paid call ────────────
// The 3-tuple IS the receipt's identity. Anyone holding it reproduces the
// anchored Merkle root and cross-checks the on-chain anchor — for free.
const localRoot = sha256(message);
const matches = localRoot === att.merkle_root;
rule();
console.log("INDEPENDENT RE-VERIFICATION  (no payment — pure client-side recompute)");
rule();
console.log(`recomputed root : ${localRoot}`);
console.log(`matches anchor  : ${matches ? "✓ yes — receipt is provable from the tuple alone" : "✗ MISMATCH"}`);
console.log(`\nstore (input_hash, output_hash, decision) and you can re-prove this`);
console.log(`decision against the on-chain anchor anytime, without paying again.`);

process.exit(matches ? 0 : 1);
