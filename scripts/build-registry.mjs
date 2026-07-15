#!/usr/bin/env node
// Regenerates data/x402_registry.json from CDP Bazaar + our own catalog.
// Run manually before deploys: node scripts/build-registry.mjs
// Registry updates ship with the next deploy — runtime stays stateless.

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BAZAAR = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources";
const PAGE = 100;
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "data", "x402_registry.json");

const OWN = {
  service_id: "anchor-x402.com",
  name: "anchor-x402",
  recipients: ["0x127462e296fAc1A7F5cF33bA57bB2f0FFf5cD0B6"],
  recipients_solana: ["6apuZvJQ51Led9iEjnHw6f5jfnXL4qjt8S1h58PeXzuR"],
  category: "onchain-data",
  source: "own",
};

function hostOf(resource) {
  try {
    return new URL(resource).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

const services = new Map(); // host -> {name, recipients:Set, recipients_solana:Set, category, source}

let offset = 0, total = Infinity;
while (offset < total) {
  const res = await fetch(`${BAZAAR}?limit=${PAGE}&offset=${offset}`);
  if (!res.ok) throw new Error(`bazaar ${res.status} at offset ${offset}`);
  const page = await res.json();
  total = page.pagination.total;
  for (const item of page.items) {
    const host = hostOf(item.resource);
    if (!host) continue;
    const svc = services.get(host) ?? {
      name: item.serviceName || host,
      recipients: new Set(),
      recipients_solana: new Set(),
      category: item.tags?.[0] ?? null,
      source: "bazaar",
    };
    if (item.serviceName && svc.name === host) svc.name = item.serviceName;
    for (const a of item.accepts ?? []) {
      if (a.scheme !== "exact" || !a.payTo) continue;
      const net = a.network ?? "";
      if (net === "eip155:8453" || net === "base") svc.recipients.add(a.payTo.toLowerCase());
      else if (net.startsWith("solana:")) svc.recipients_solana.add(a.payTo);
    }
    if (svc.recipients.size || svc.recipients_solana.size) services.set(host, svc);
  }
  offset += PAGE;
  process.stderr.write(`\r${Math.min(offset, total)}/${total}`);
}
process.stderr.write("\n");

services.delete(OWN.service_id);
services.delete("api.anchor-x402.com");

const rows = [
  OWN,
  ...[...services.entries()]
    .map(([host, s]) => ({
      service_id: host,
      name: s.name,
      recipients: [...s.recipients].sort(),
      recipients_solana: [...s.recipients_solana].sort(),
      category: s.category,
      source: s.source,
    }))
    .sort((a, b) => a.service_id.localeCompare(b.service_id)),
];

const registry = {
  registry_version: new Date().toISOString().slice(0, 10),
  services: rows,
};

writeFileSync(OUT, JSON.stringify(registry, null, 2) + "\n");
const nRecipients = rows.reduce((n, r) => n + r.recipients.length, 0);
console.log(`wrote ${OUT}: ${rows.length} services, ${nRecipients} Base recipients`);
