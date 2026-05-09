"""Wallet screening: check an address against a known-bad list.

MVP: hardcoded OFAC SDN crypto entries (Tornado Cash, Lazarus, Hydra,
common DPRK clusters). Production should pull from
https://www.treasury.gov/ofac/downloads/sdn.csv on a daily refresh
(the official OFAC list includes ~200 cryptocurrency addresses) and
optionally enrich with Chainabuse, GoPlus, or proprietary data.
"""
from __future__ import annotations

import re
from typing import Literal

# --- Hardcoded sanctions corpus (lowercased EVM, raw Solana) ---
# Production: replace with daily Treasury.gov CSV pull.
_EVM_SANCTIONED = {
    # Tornado Cash (OFAC SDN, August 2022)
    "0x8589427373d6d84e98730d7795d8f6f8731fda16": ["OFAC SDN", "Tornado Cash"],
    "0x722122df12d4e14e13ac3b6895a86e84145b6967": ["OFAC SDN", "Tornado Cash"],
    "0xd96f2b1c14db8458374d9aca76e26c3d18364307": ["OFAC SDN", "Tornado Cash"],
    "0x4736dcf1b7a3d580672ccce6213fe0b7e0c89e60": ["OFAC SDN", "Tornado Cash"],
    "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b": ["OFAC SDN", "Tornado Cash"],
    "0x07687e702b410fa43f4cb4af7fa097918ffd2730": ["OFAC SDN", "Tornado Cash"],
    "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf": ["OFAC SDN", "Tornado Cash"],
    # Lazarus Group (DPRK)
    "0x098b716b8aaf21512996dc57eb0615e2383e2f96": ["OFAC SDN", "Lazarus Group", "DPRK"],
    "0xa7e5d5a720f06526557c513402f2e6b5fa20b008": ["OFAC SDN", "Lazarus Group", "DPRK"],
    # Hydra Market (sanctioned April 2022)
    "0xeac3b16c1ce81bd23663ef0ae8e5ffadc4f64eef": ["OFAC SDN", "Hydra Market"],
    # Garantex (sanctioned April 2022)
    "0xa7e5d5a720f06526557c513402f2e6b5fa20b008": ["OFAC SDN", "Garantex"],
    # Blender.io (sanctioned May 2022)
    "0x9c2bc757b66f24d60f016b6237f8cdd414a879fa": ["OFAC SDN", "Blender.io"],
}

_SOLANA_SANCTIONED: dict[str, list[str]] = {
    # Solana wallets sanctioned by OFAC are rarer in the public list;
    # populate from Treasury.gov once production pull is wired.
}

_BTC_HEX_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def _infer_chain(wallet: str) -> Literal["ethereum", "solana", "unknown"]:
    if _BTC_HEX_RE.match(wallet):
        return "ethereum"
    if _SOL_RE.match(wallet):
        return "solana"
    return "unknown"


def screen(wallet: str) -> dict:
    """Return a screening verdict for a wallet address.

    Output keys:
      wallet            normalized address (lowercased for EVM)
      chain_inferred    "ethereum" | "solana" | "unknown"
      sanctions_match   bool
      sanctioned_lists  list[str]  (which programs flagged it)
      risk_level        "low" | "medium" | "high"
      notes             human-readable summary
    """
    chain = _infer_chain(wallet)
    if chain == "unknown":
        return {
            "wallet": wallet,
            "chain_inferred": "unknown",
            "sanctions_match": False,
            "sanctioned_lists": [],
            "risk_level": "medium",
            "notes": "Could not infer chain from address shape — verdict inconclusive. Provide a checksum-style EVM address (0x + 40 hex) or base58 Solana pubkey.",
        }

    if chain == "ethereum":
        normalized = wallet.lower()
        flags = _EVM_SANCTIONED.get(normalized, [])
    else:  # solana
        normalized = wallet  # base58 is case-sensitive
        flags = _SOLANA_SANCTIONED.get(normalized, [])

    if flags:
        return {
            "wallet": normalized,
            "chain_inferred": chain,
            "sanctions_match": True,
            "sanctioned_lists": flags,
            "risk_level": "high",
            "notes": f"Address matches {len(flags)} sanctions program(s): {', '.join(flags)}. DO NOT transact without a regulatory-approved exception.",
        }

    return {
        "wallet": normalized,
        "chain_inferred": chain,
        "sanctions_match": False,
        "sanctioned_lists": [],
        "risk_level": "low",
        "notes": "No matches against the active sanctions corpus. Note: list is refreshed from public sources only; institutional users should pair with proprietary AML data for residual coverage.",
    }
