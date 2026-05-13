#!/usr/bin/env node
// agent.mjs — minimal anchor-x402 agent example.
//
// Composes two paid endpoints from a real Base wallet:
//   1. POST /v1/screen   (~$0.001)  — sanctions + AML check
//   2. POST /v1/anchor   (~$0.005)  — dual-chain hash anchor of the verdict
//
// Total cost per run: ~$0.006 USDC. Pays via x402 v2 — no API key, no
// account, no subscription. Just a Base wallet with USDC.
//
// Run:
//   BASE_PRIVATE_KEY=0x... node agent.mjs 0xWalletToScreen

import { createHash } from "node:crypto";
import { createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";
import { wrapFetchWithPaymentFromConfig } from "@x402/fetch";
import { ExactEvmScheme } from "@x402/evm";

const PK = process.env.BASE_PRIVATE_KEY;
const target = process.argv[2];
if (!PK)     { console.error("BASE_PRIVATE_KEY env var required (0x-prefixed)"); process.exit(2); }
if (!target) { console.error("usage: node agent.mjs <wallet-to-screen>");        process.exit(2); }

const account = privateKeyToAccount(PK);
const walletClient = createWalletClient({ account, chain: base, transport: http() });
const signer = { address: account.address, signTypedData: (m) => walletClient.signTypedData(m) };
const paidFetch = wrapFetchWithPaymentFromConfig(fetch, {
  schemes: [{ network: "eip155:8453", client: new ExactEvmScheme(signer) }],
});

console.log(`agent ${account.address} → screen(${target})  ~$0.001`);
const screenRes = await paidFetch(`https://api.anchor-x402.com/v1/screen?wallet=${target}`);
if (!screenRes.ok) { console.error("screen failed:", screenRes.status, await screenRes.text()); process.exit(1); }
const screen = await screenRes.json();
console.log(JSON.stringify(screen, null, 2));

const verdict = { wallet: screen.wallet, sanctions_match: screen.sanctions_match, risk_level: screen.risk_level, checked_at: screen.checked_at };
const hash = createHash("sha256").update(JSON.stringify(verdict)).digest("hex");

console.log(`\nagent ${account.address} → anchor(${hash.slice(0,8)}…)  ~$0.005`);
const anchorRes = await paidFetch("https://api.anchor-x402.com/v1/anchor", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ hash, note: `screen verdict for ${target}` }),
});
if (!anchorRes.ok) { console.error("anchor failed:", anchorRes.status, await anchorRes.text()); process.exit(1); }
const anchored = await anchorRes.json();
console.log(JSON.stringify(anchored, null, 2));

console.log(`\ndone — proof on Base: ${anchored.base.explorer_url}`);
if (anchored.solana) console.log(`        proof on Solana: ${anchored.solana.explorer_url}`);
