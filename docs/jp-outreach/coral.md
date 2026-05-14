# anchor-x402 — first x402 service accepting JPYC

**One-line:** APIs that AI agents pay per call. Until last week, every
x402 service on the internet settled exclusively in USDC. anchor-x402
ships the **first x402-compatible service accepting JPYC**, the FSA-
licensed yen stablecoin — opening Japan's regulated stablecoin to the
agent-payments economy.

**Why now**
x402 (Coinbase, Cloudflare-backed) is the emerging standard for
HTTP 402 payments by autonomous agents. USDC has been the only serious
settlement asset. That's a problem for every JP fintech conversation
I've had — "USDC-only is a non-starter" came up four times in three
months. JPYC has 2.6B tokens outstanding but zero agent-native
distribution. We're closing that gap.

**Honest scoping:** anchor-x402 is the 67th caller of JPYC v2's
`transferWithAuthorization`. The prior 66 are 16 distinct addresses
making test-pattern transfers over the past 7 months — none was wired
to a paying service. anchor-x402 is the first **production service
rail**, not the first transfer.

**What I shipped**
- In-process EIP-3009 facilitator on Polygon for JPYC v2
- 16 paid endpoints, all accept JPYC alongside Base USDC and Solana USDC
- `/v1/anchor` priced at **¥1 per call** — the press-friendly entry point
- End-to-end paid tx hashes verifiable on Polygonscan
- Discoverable in CDP Bazaar + 7 other agent registries

**Live proof:** https://api.anchor-x402.com
Example settle: https://polygonscan.com/tx/0x8c465c282e336bb389a992b47fe9370ba6b5d68d51e73705706f09b096b24a14

**What I'm not asking:** not raising. Solo built, self-funded.

**What I am asking:** 30 minutes to talk distribution. Specifically:
1. Are there Coral portfolio companies (AI agents, automation, RPA)
   that would benefit from a JP-priced, agent-payable API surface — to
   use, or to white-label?
2. You almost certainly have warmer paths to JPYC Inc.'s commercial
   team than I do. Who's the right contact?
3. Who's the right person at Progmat for the bank-stablecoin track,
   once those go live?

**About me:** Christopher Ferjo, solo builder, US-based.
Background: $LARGECO. Shipping anchor-x402 daily for ~4 months.
Twitter: @hypeprinter.

Happy to do a 5-minute live demo — Claude Desktop paying anchor-x402
¥1 over MCP. Bring popcorn.

— Chris
cferjo@gmail.com · anchor-x402.com
