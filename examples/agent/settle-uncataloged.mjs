#!/usr/bin/env node
// settle-uncataloged.mjs — one paid call each to the two anchor-x402 endpoints
// that aren't yet in the CDP Bazaar index, to force them to get catalogued.
//
// Bazaar indexes a resource on its FIRST settled payment. /v1/decode/tx and
// /v1/attest have never settled, so they're invisible in discovery. This pays
// each once (Base USDC) to trigger cataloguing.
//
//   /v1/decode/tx  (~$0.001)  — decode a real Base tx
//   /v1/attest     (~$0.01)   — verify an ephemeral eip191 sig + dual-chain anchor
//
// Total: ~$0.011 USDC + the seller's own anchor gas on attest. Run:
//
//   BASE_PRIVATE_KEY=0x... node settle-uncataloged.mjs
//
// Use a funded Base buyer wallet — NOT the treasury key.

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

console.log(`buyer ${account.address}  →  ${BASE_URL}\n`);

// --- 1. /v1/decode/tx (GET) — decode a known-good Base tx ---
const TX = process.env.ANCHOR_TX || "0x9e1fd68b563cd36fbb42aa993f31762aaa7cfb876c579ccb40d36d16e178902b";
console.log(`[1/2] decode/tx(base, ${TX.slice(0, 10)}…)  ~$0.001`);
const dRes = await paidFetch(`${BASE_URL}/v1/decode/tx?chain=base&tx_hash=${TX}`);
if (!dRes.ok) { console.error("  decode/tx failed:", dRes.status, await dRes.text()); process.exit(1); }
console.log("  ok — block", (await dRes.json()).block_number, "\n");

// --- 2. /v1/attest (POST) — ephemeral eip191 attestation ---
// Domain-separated message the server reconstructs + recovers from (services/attest.py):
//   anchor-x402/attest/v1\ninput=<h>\noutput=<h>\ndecision=<d>
const inputHash  = createHash("sha256").update("anchor-x402 settle probe: input").digest("hex");
const outputHash = createHash("sha256").update("anchor-x402 settle probe: output").digest("hex");
const decision   = "APPROVED";
const message = `anchor-x402/attest/v1\ninput=${inputHash}\noutput=${outputHash}\ndecision=${decision}`;
const signature = await walletClient.signMessage({ account, message }); // eip191 personal_sign

console.log(`[2/2] attest(eip191, ${decision})  ~$0.01  + seller anchor gas`);
const aRes = await paidFetch(`${BASE_URL}/v1/attest`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ input_hash: inputHash, output_hash: outputHash, decision, scheme: "eip191", signature }),
});
if (!aRes.ok) { console.error("  attest failed:", aRes.status, await aRes.text()); process.exit(1); }
const att = await aRes.json();
console.log("  ok — signer", att.signer);
console.log("       base:", att.base?.explorer_url);
if (att.solana) console.log("       solana:", att.solana.explorer_url);

console.log("\nboth settled. CDP Bazaar indexes a resource on first settlement —");
console.log("re-check in a few minutes; tell Claude \"re-sweep the bazaar\" to confirm.");
