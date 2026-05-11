"""Hosted-agent /v1/chat: one Anthropic tool-use turn.

Tools are NOT executed server-side — they're returned to the client which pays
each call via x402 and feeds tool_result back on the next chat_turn invocation.
This keeps custody with the user (their wallet, their funds).
"""
from __future__ import annotations

from services.llm import MODEL_REASON, get_client

_MODEL = MODEL_REASON
_MAX_TOKENS = 2048

_SYSTEM = """You are the anchor-x402 hosted agent. You help users run paid x402 endpoints from their own wallet.

Rules:
1. Never call a tool unless the user's intent is clear. If ambiguous, ask first.
2. Prefer cheaper tools when sufficient. For wallet checks, escalate gradually: screen_wallet ($0.001) -> wallet_intel ($0.005) -> investigate_wallet ($7.77, only if the user explicitly asks for a full investigation, or for compliance / OTC due-diligence contexts).
3. State each tool call's price aloud before requesting it (e.g. "I'll run screen_wallet ($0.001) - pulls OFAC sanctions match.").
4. investigate_wallet is async - it returns a job_id and you'll get the report 5-10 min later. Set expectations.
5. roast, oracle, tldr, aura, grade work on any topic; encourage users to try them with anything (a wallet, a tweet, their own code, a project, a meme).

Tone: direct, knowledgeable, slightly playful. No hedging filler."""


_TOOLS = [
    {
        "name": "anchor_hash",
        "description": "Anchor a 32-byte hash to Base + Solana mainnet. Returns both tx hashes plus block-explorer URLs as cryptographic proof. $0.005 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hash": {"type": "string", "description": "64-char hex SHA-256, no 0x prefix"},
                "note": {"type": "string", "description": "Optional 200-char label echoed in response (NOT written on-chain)"},
            },
            "required": ["hash"],
        },
    },
    {
        "name": "screen_wallet",
        "description": "Sanctions + AML screening for any EVM or Solana wallet. Returns match boolean, OFAC SDN programs flagged, risk verdict. $0.001 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"wallet": {"type": "string", "description": "EVM 0x... or Solana base58 address"}},
            "required": ["wallet"],
        },
    },
    {
        "name": "attest_decision",
        "description": "Verify a wallet signature over (input_hash, output_hash, decision) and dual-chain anchor the result. $0.01 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "input_hash": {"type": "string"},
                "output_hash": {"type": "string"},
                "decision": {"type": "string", "enum": ["APPROVED", "REJECTED", "ESCALATED"]},
                "scheme": {"type": "string", "enum": ["eip191", "ed25519"]},
                "signature": {"type": "string"},
                "signer_pubkey": {"type": "string", "description": "Required for ed25519; optional for eip191"},
            },
            "required": ["input_hash", "output_hash", "decision", "scheme", "signature"],
        },
    },
    {
        "name": "decode_tx",
        "description": "Structured decode of any Base or Ethereum mainnet transaction. Returns block, status, gas, transfers, method signatures. $0.001 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "enum": ["base", "ethereum"]},
                "tx_hash": {"type": "string", "description": "0x-prefixed 32-byte tx hash"},
            },
            "required": ["chain", "tx_hash"],
        },
    },
    {
        "name": "decode_calldata",
        "description": "Decode raw EVM calldata into function name, canonical signature, and typed param values via openchain.xyz. $0.001 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "enum": ["base", "ethereum", "polygon", "arbitrum", "optimism"]},
                "calldata_hex": {"type": "string"},
            },
            "required": ["chain", "calldata_hex"],
        },
    },
    {
        "name": "resolve_name",
        "description": "Cross-chain name resolver: ENS (Ethereum) and Bonfida SNS (Solana). $0.001 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "ENS or SNS name, e.g. vitalik.eth, bonfida.sol"}},
            "required": ["name"],
        },
    },
    {
        "name": "token_price",
        "description": "Current USD price for any major token by symbol (BTC, ETH, SOL, USDC, etc.). Returns price, 24h change, market cap. $0.001 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "Token symbol, e.g. ETH, SOL, USDC"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "parse_datetime",
        "description": "Parse any freeform datetime string (e.g. 'tomorrow at noon', 'in 2 hours') into ISO 8601 + unix epoch + components. $0.001 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "input": {"type": "string"},
                "timezone": {"type": "string", "description": "IANA timezone, default UTC"},
            },
            "required": ["input"],
        },
    },
    {
        "name": "wallet_intel",
        "description": "Unified wallet intelligence bundle: native + USDC balances on Base + Ethereum, tx count, reverse ENS/SNS, sanctions verdict. $0.005 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"wallet": {"type": "string"}},
            "required": ["wallet"],
        },
    },
    {
        "name": "investigate_wallet",
        "description": "Agent-driven multi-step wallet due diligence. ASYNC - returns job_id + status_url; poll until ready (5-10 min). Delivers signed markdown report + JSON sidecar with verdict, score, dual-chain anchor proof. $7.77 USDC. Use only when the user explicitly requests a full investigation, or for compliance / OTC due-diligence.",
        "input_schema": {
            "type": "object",
            "properties": {"address": {"type": "string", "description": "EVM 0x... or Solana base58 address"}},
            "required": ["address"],
        },
    },
    {
        "name": "roast",
        "description": "Witty roast of any target - a wallet, tweet, startup pitch, code, idea. $0.05 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    },
    {
        "name": "oracle",
        "description": "Yes/no oracle with dual-chain anchored verdict. Ask any yes/no question; the answer + question hash + timestamp are anchored on Base + Solana for cryptographic receipt. $0.05 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "tldr",
        "description": "Summarize a URL or pasted text into 3-5 concise bullets. Provide EXACTLY ONE of `text` or `url`. $0.01 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Pasted text to summarize. Omit if you are providing `url`."},
                "url": {"type": "string", "description": "URL to fetch and summarize. Omit if you are providing `text`."},
            },
        },
    },
    {
        "name": "aura",
        "description": "Read the aura of anything — wallet, tweet, project, person, code, idea, meme. Returns color, tier (S/A/B/C/D/F), score 0-9999, and a punchy description. $0.01 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Anything to read the aura of."}},
            "required": ["target"],
        },
    },
    {
        "name": "grade",
        "description": "Academic letter grade (A+ ... F) with red-pen marginalia and a summary, for any target — code, pitch, tweet, wallet, idea. $0.01 USDC.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string", "description": "Anything to grade."}},
            "required": ["target"],
        },
    },
]

_PRICES = {
    "anchor_hash": 0.005,
    "screen_wallet": 0.001,
    "attest_decision": 0.01,
    "decode_tx": 0.001,
    "decode_calldata": 0.001,
    "resolve_name": 0.001,
    "token_price": 0.001,
    "parse_datetime": 0.001,
    "wallet_intel": 0.005,
    "investigate_wallet": 7.77,
    "roast": 0.05,
    "oracle": 0.05,
    "tldr": 0.01,
    "aura": 0.01,
    "grade": 0.01,
}


def chat_turn(messages: list[dict]) -> dict:
    """Run one Bedrock turn. Tools are returned (not executed) — client pays + executes."""
    resp = get_client().messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM,
        tools=_TOOLS,
        messages=messages,
    )

    text_parts: list[str] = []
    tool_uses: list[dict] = []
    for block in resp.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_uses.append(
                {
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                    "price_usd": _PRICES.get(block.name, 0.0),
                }
            )

    return {
        "assistant_text": "\n".join(text_parts).strip() or None,
        "tool_uses": tool_uses or None,
        "stop_reason": resp.stop_reason,
    }
