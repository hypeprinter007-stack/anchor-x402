#!/usr/bin/env node
// mcp-paid-call.mjs — call a paid tool on the MCP HTTP endpoint, end to end.
//
//   BASE_PRIVATE_KEY=0x... node mcp-paid-call.mjs [tool] [jsonArgs]
//   BASE_PRIVATE_KEY=0x... node mcp-paid-call.mjs token_price '{"symbol":"ETH"}'
//
// Use a funded Base buyer wallet — NOT the treasury key.
//
// Why this file exists: /mcp answers an unpaid tools/call with HTTP 200 and the
// x402 challenge inside the MCP result (isError + structuredContent.accepts).
// That is deliberate — MCP routes tool failures to the model, so an agent can
// read the challenge and decide to pay, whereas an HTTP 402 would be swallowed
// by the MCP client as a transport error before the model ever saw it.
//
// The cost is that a stock x402 fetch wrapper, which triggers on HTTP 402, will
// not auto-pay here. The adapter below bridges the two: it makes the unpaid call,
// re-presents the challenge to the wrapper as a synthetic 402, and lets the
// wrapper sign and retry as usual. Copy it if you already have a paidFetch.

import { createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base } from "viem/chains";
import { wrapFetchWithPaymentFromConfig } from "@x402/fetch";
import { ExactEvmScheme } from "@x402/evm";

const PK = process.env.BASE_PRIVATE_KEY;
if (!PK) {
  console.error("BASE_PRIVATE_KEY env var required (0x-prefixed, funded Base wallet)");
  process.exit(2);
}

const BASE_URL = process.env.ANCHOR_BASE_URL || "https://api.anchor-x402.com";
const MCP = `${BASE_URL}/mcp`;
const VERSION = "2026-07-28";
const tool = process.argv[2] || "token_price";
const args = JSON.parse(process.argv[3] || '{"symbol":"ETH"}');

const account = privateKeyToAccount(PK);
const walletClient = createWalletClient({ account, chain: base, transport: http() });
const signer = {
  address: account.address,
  signTypedData: (m) => walletClient.signTypedData({ account, ...m }),
};

// One JSON-RPC request, with the per-request metadata 2026-07-28 requires. There
// is no initialize and no session: this is the whole handshake.
function rpc(method, params) {
  return {
    jsonrpc: "2.0",
    id: `call-${method}`,
    method,
    params: {
      ...params,
      _meta: {
        "io.modelcontextprotocol/protocolVersion": VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": { name: "mcp-paid-call", version: "1.0.0" },
      },
    },
  };
}

// The mirrored headers are mandatory and must agree with the body, or the server
// answers 400 / -32020 (HeaderMismatch).
function headers(method, name, extra = {}) {
  return {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": VERSION,
    "Mcp-Method": method,
    ...(name ? { "Mcp-Name": name } : {}),
    ...extra,
  };
}

async function mcpPost(method, params, name, extra) {
  const res = await fetch(MCP, {
    method: "POST",
    headers: headers(method, name, extra),
    body: JSON.stringify(rpc(method, params)),
  });
  const body = await res.json();
  if (body.error) throw new Error(`${method} → ${body.error.code}: ${body.error.message}`);
  return body.result;
}

// The adapter: re-present the MCP-embedded challenge to the wrapper as a real
// 402. The `payment-required` header is carried over verbatim — in x402 V2 that
// base64 header is the canonical challenge and what the client actually parses;
// the JSON body is a courtesy rendering. Rebuilding a 402 from the body alone is
// not enough, which is exactly how the first version of this failed.
const payingFetch = wrapFetchWithPaymentFromConfig(
  async (a, b) => {
    // The wrapper hands us a Request as the first argument with the second
    // undefined. Fetch it via a clone: fetching the Request itself consumes its
    // body, and the wrapper needs to re-issue that same request once it has
    // signed the payment. Without the clone the retry goes out bodyless.
    const req = a instanceof Request && b === undefined ? a : new Request(a, b);
    const res = await fetch(req.clone());
    const challengeHeader = res.headers.get("payment-required");
    if (!challengeHeader) return res;
    const body = await res.clone().json().catch(() => null);
    if (!body?.result?.isError) return res;
    return new Response(JSON.stringify(body.result.structuredContent ?? {}), {
      status: 402,
      headers: {
        "content-type": "application/json",
        "payment-required": challengeHeader,
      },
    });
  },
  { schemes: [{ network: "eip155:8453", client: new ExactEvmScheme(signer) }] },
);

console.log(`buyer ${account.address}`);

const list = await mcpPost("tools/list", {});
const found = list.tools.find((t) => t.name === tool);
if (!found) {
  console.error(`no tool ${tool}. available: ${list.tools.map((t) => t.name).join(", ")}`);
  process.exit(1);
}
console.log(`${list.tools.length} tools advertised; calling ${tool} — ${found.description}`);

const res = await payingFetch(MCP, {
  method: "POST",
  headers: headers("tools/call", tool),
  body: JSON.stringify(rpc("tools/call", { name: tool, arguments: args })),
});

const body = await res.json();
if (body.error) {
  console.error("rpc error:", body.error);
  process.exit(1);
}
const result = body.result;
if (result.isError) {
  console.error("tool returned isError — not settled:");
  console.error(result.content?.[0]?.text?.slice(0, 800));
  process.exit(1);
}
console.log(`\nsettled. resultType=${result.resultType} isError=${result.isError}`);
console.log(result.content[0].text.slice(0, 600));

const paid = res.headers.get("x-payment-response") || res.headers.get("payment-response");
if (paid) console.log("\npayment-response header:", paid.slice(0, 200));
