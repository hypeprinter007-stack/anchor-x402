import express from "express";
import serverless from "serverless-http";
import { createGatewayMiddleware } from "@circle-fin/x402-batching/server";

const UPSTREAM = process.env.UPSTREAM_URL || "https://api.anchor-x402.com";
const INTERNAL_SECRET = process.env.INTERNAL_AUTH_SECRET;
const SELLER_ADDRESS = process.env.SELLER_ADDRESS;

if (!INTERNAL_SECRET) throw new Error("INTERNAL_AUTH_SECRET env var required");
if (!SELLER_ADDRESS) throw new Error("SELLER_ADDRESS env var required");

const app = express();
app.use(express.json({ limit: "256kb" }));

const gateway = createGatewayMiddleware({ sellerAddress: SELLER_ADDRESS });

// Catalog priced for Circle Gateway batched/gas-free settlement —
// 10x lower than anchor's CDP-rail prices since per-call gas is free.
const catalog = [
  { method: "post", path: "/v1/anchor",          price: "$0.0005" },
  { method: "get",  path: "/v1/anchor",          price: "$0.0005" },
  { method: "get",  path: "/v1/screen",          price: "$0.0001" },
  { method: "post", path: "/v1/attest",          price: "$0.001"  },
  { method: "get",  path: "/v1/attest",          price: "$0.001"  },
  { method: "post", path: "/v1/decode/tx",       price: "$0.0001" },
  { method: "get",  path: "/v1/decode/tx",       price: "$0.0001" },
  { method: "get",  path: "/v1/resolve/name",    price: "$0.0001" },
  { method: "get",  path: "/v1/price/token",     price: "$0.0001" },
  { method: "post", path: "/v1/decode/calldata", price: "$0.0001" },
  { method: "get",  path: "/v1/decode/calldata", price: "$0.0001" },
  { method: "post", path: "/v1/parse/datetime",  price: "$0.0001" },
  { method: "get",  path: "/v1/parse/datetime",  price: "$0.0001" },
  { method: "get",  path: "/v1/intel/wallet",    price: "$0.0005" },
  { method: "post", path: "/v1/investigate",     price: "$5.00"   },
  { method: "post", path: "/v1/roast",           price: "$0.005"  },
  { method: "post", path: "/v1/oracle",          price: "$0.005"  },
  { method: "post", path: "/v1/tldr",            price: "$0.001"  },
  { method: "post", path: "/v1/aura",            price: "$0.001"  },
  { method: "post", path: "/v1/grade",           price: "$0.001"  },
  { method: "post", path: "/v1/roll",            price: "$0.0001" },
  { method: "get",  path: "/v1/roll",            price: "$0.0001" },
  // Bidirectional method wrappers — mirror api host so crawlers hitting
  // gateway.* with the wrong method reach the 402 challenge instead of 405.
  { method: "post", path: "/v1/screen",          price: "$0.0001" },
  { method: "post", path: "/v1/resolve/name",    price: "$0.0001" },
  { method: "post", path: "/v1/price/token",     price: "$0.0001" },
  { method: "post", path: "/v1/intel/wallet",    price: "$0.0005" },
  { method: "get",  path: "/v1/investigate",     price: "$5.00"   },
  { method: "get",  path: "/v1/roast",           price: "$0.005"  },
  { method: "get",  path: "/v1/oracle",          price: "$0.005"  },
  { method: "get",  path: "/v1/tldr",            price: "$0.001"  },
  { method: "get",  path: "/v1/aura",            price: "$0.001"  },
  { method: "get",  path: "/v1/grade",           price: "$0.001"  },
];

async function forward(req, res) {
  const target = new URL(req.originalUrl, UPSTREAM);
  const init = {
    method: req.method,
    headers: {
      "content-type": "application/json",
      "x-internal-auth": INTERNAL_SECRET,
    },
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = JSON.stringify(req.body || {});
  }
  const r = await fetch(target, init);
  const body = await r.text();
  res.status(r.status);
  res.set("content-type", r.headers.get("content-type") || "application/json");
  res.send(body);
}

for (const { method, path, price } of catalog) {
  app[method](path, gateway.require(price), forward);
}

// Free passthroughs — no x402, just proxy.
const free = ["/openapi.json", "/docs", "/.well-known/x402", "/.well-known/x402.json", "/llms.txt", "/robots.txt"];
for (const p of free) app.get(p, forward);
app.get("/v1/investigate/status/:job_id", forward);
app.get("/health", (_req, res) =>
  res.json({ status: "ok", service: "anchor-x402-gateway" })
);

if (process.env.LOCAL) {
  app.listen(3000, () => console.log("anchor-x402-gateway listening on :3000"));
}

export const handler = serverless(app);
