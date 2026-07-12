---
layout: page
title: "Pay an x402 API from Node.js — complete working example"
description: "A copy-paste Node.js client that pays any x402 v2 API with USDC on Base: parse the 402 challenge, sign an EIP-3009 authorization, retry, get the result. ~30 lines, tested against a live endpoint."
permalink: /guides/pay-x402-api-node/
---

# Pay an x402 API from Node.js

This is the complete client. It calls a paid endpoint, gets the `402 Payment Required`
challenge, signs a gasless USDC authorization from your wallet, retries, and returns the
result — one round-trip, no API keys, no account signup. This exact pattern runs in
production; you can test it against a live `$0.001` endpoint below.

## Install

```bash
npm install @x402/fetch @x402/evm viem
```

## The client

```js
// pay.mjs — node pay.mjs
import { createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";
import { wrapFetchWithPaymentFromConfig } from "@x402/fetch";
import { ExactEvmScheme } from "@x402/evm";

// Any Base wallet holding a little USDC. Never hardcode the key.
const account = privateKeyToAccount(process.env.PRIVATE_KEY);
const walletClient = createWalletClient({ account, chain: base, transport: http() });
const signer = {
  address: account.address,
  signTypedData: (msg) => walletClient.signTypedData({ account, ...msg }),
};

// fetch, but it pays 402s automatically.
const paidFetch = wrapFetchWithPaymentFromConfig(fetch, {
  schemes: [{ network: "eip155:8453", client: new ExactEvmScheme(signer) }],
});

// A live $0.001 endpoint: OFAC sanctions screen for any wallet.
const res = await paidFetch(
  "https://api.anchor-x402.com/v1/screen?address=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
);
console.log(res.status, await res.json());
```

That's the whole integration. `paidFetch` behaves exactly like `fetch` for free
endpoints and transparently handles the payment dance for paid ones.

## What actually happens

1. **First request** → the server replies `402` with a JSON challenge listing
   `accepts[]`: network (`eip155:8453` = Base mainnet), asset (USDC), amount, and `payTo`.
2. **Sign** → the client signs an [EIP-3009](https://eips.ethereum.org/EIPS/eip-3009)
   `transferWithAuthorization` — an off-chain signature, so **you pay no gas**; the
   facilitator settles it on-chain.
3. **Retry** → the same request goes out again with a `PAYMENT-SIGNATURE` header.
   The server verifies, settles, and returns the result plus a `payment-response`
   header containing the settlement details.

## Funding the wallet

The wallet only needs USDC on Base (no ETH — settlement is gasless for the buyer).
For agents, [Coinbase's Agentic Wallet](https://docs.cdp.coinbase.com/agentic-wallet/welcome)
(`npx awal@latest`) is the fastest path; any EOA whose key you hold works the same.

## Troubleshooting

- **`402` again after paying, body `error: "payment_invalid"`** — the authorization was
  rejected: insufficient USDC balance, expired, or already used. The body includes a
  `retry_hint`; sign a *fresh* authorization and retry (don't resubmit the old one).
- **Client throws before ever paying** — check the challenge's `accepts[].network`.
  Current `@x402/evm` expects CAIP-2 (`eip155:8453`). Some older servers still emit the
  legacy label `"base"`; those need a v1 client or a server-side fix.
- **Want to inspect a challenge without paying?** `curl -i` the endpoint — the full
  challenge JSON is in the body and the `payment-required` header (base64).

## Try it against 16 live endpoints

Everything in the [anchor-x402 catalog](/.well-known/x402.json) works with the client
above — sanctions screening, wallet intel, tx decoding, token prices, on-chain anchoring,
from $0.001/call. Machine-readable index: [llms.txt](/llms.txt) ·
[OpenAPI](https://api.anchor-x402.com/openapi.json) ·
selling instead? [Accept x402 payments in Python →](/guides/accept-x402-payments-python/)
