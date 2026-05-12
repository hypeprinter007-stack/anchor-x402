"""Register the anchor-x402 risk-investigator as an Agent on Agentverse.

Listing the $7.77 /v1/investigate endpoint so it shows up in Agentverse /
ASI:One discovery. Invocation still happens via x402 USDC directly against
api.anchor-x402.com — Agentverse here is a marketplace pointer, not a
payment proxy.

Setup:
    1. Create an account at https://agentverse.ai
    2. Go to Settings → API Keys, create one, copy
    3. Pick any string as your AGENT_SECRET_KEY (deterministic seed for the
       Fetch identity tied to this agent — never share, but doesn't need to
       hold funds)
    4. export AGENTVERSE_KEY=<paste>
       export AGENT_SECRET_KEY=<any_long_random_string>
    5. python3 scripts/register_agentverse.py

The same secret can be reused if you re-register; the identity stays stable.

Requirements:
    pip install fetchai
"""
from __future__ import annotations

import os
import sys

try:
    from uagents_core.identity import Identity
    from fetchai.registration import register_with_agentverse
except ImportError:
    sys.stderr.write("missing deps. run: pip install fetchai\n")
    sys.exit(1)


AGENTVERSE_KEY = os.environ.get("AGENTVERSE_KEY", "")
AGENT_SECRET_KEY = os.environ.get("AGENT_SECRET_KEY", "")

if not AGENTVERSE_KEY:
    sys.stderr.write("AGENTVERSE_KEY env var not set\n")
    sys.exit(1)
if not AGENT_SECRET_KEY:
    sys.stderr.write("AGENT_SECRET_KEY env var not set (any long random string is fine)\n")
    sys.exit(1)


NAME = "anchor-x402"

# FastAPI Chat Protocol adapter endpoint — receives signed Agentverse envelopes,
# parses the user query, replies via Agentverse mailbox.
WEBHOOK = "https://api.anchor-x402.com/agentverse/chat"

README = """![domain:risk-intel](https://img.shields.io/badge/risk--intel-cc6e47)
![chain:base](https://img.shields.io/badge/base-mainnet-0052ff)
![chain:solana](https://img.shields.io/badge/solana-mainnet-9945ff)
![pay:x402](https://img.shields.io/badge/pay-x402%20USDC-3aa66b)

**Risk investigator agent** — multi-step due diligence for any wallet address.

Routes through an internal LLM orchestrator that runs sanctions screening,
balance and activity analysis, identity correlation, counterparty graph
expansion, and cross-chain pattern matching across Base + Solana mainnet,
then anchors the final verdict on-chain so the result is independently
verifiable.

## Pricing
- **$7.77 USDC** per investigation
- Paid via x402 (EIP-3009 `transferWithAuthorization` on Base, or SPL-USDC
  on Solana)
- No subscription, no account, no API key

## How to call

```http
POST https://api.anchor-x402.com/v1/investigate
Content-Type: application/json
X-PAYMENT: <x402 payment payload>

{ "wallet": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045" }
```

Returns a `job_id` immediately. Poll `GET /v1/jobs/{job_id}` for the result
(typically 5–10 minutes — the agent runs ~30 sub-tools).

Final response includes:
- Verdict (clean / caution / high-risk) with score 0–100
- Evidence list (sanctions matches, suspicious counterparties, mixer
  interactions, sanctioned-chain exposure)
- Dual-chain on-chain anchor (Base tx + Solana memo) so the verdict is
  third-party verifiable

## See also
- [Hosted chat agent](https://chat.anchor-x402.com) — Claude that runs this
  tool + 13 others from your USDC
- [MCP server](https://www.npmjs.com/package/anchor-x402-mcp): `npx anchor-x402-mcp`
- [Source (MIT)](https://github.com/hypeprinter007-stack/anchor-x402)
- [OpenAPI spec](https://api.anchor-x402.com/openapi.json)
"""


def main() -> None:
    identity = Identity.from_seed(AGENT_SECRET_KEY, 0)
    print(f"agent address: {identity.address}")
    print(f"registering '{NAME}' with webhook {WEBHOOK}...")
    register_with_agentverse(
        identity,
        WEBHOOK,
        AGENTVERSE_KEY,
        NAME,
        README,
    )
    print("ok. visit https://agentverse.ai/agents and search for the agent.")


if __name__ == "__main__":
    main()
