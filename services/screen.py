"""Wallet screening: a payment pre-flight risk verdict for agents.

An agent about to send USDC to a counterparty wants one machine-actionable
answer — allow / review / block — backed by why. This composes three layers,
cheapest-and-most-authoritative first:

  1. OFAC SDN floor (local, instant, critical). Hardcoded corpus below;
     production should refresh from treasury.gov/ofac/downloads/sdn.csv daily.
  2. GoPlus address-security (drainer / phishing / mixer / laundering labels
     and contract-vs-EOA). One cached HTTP call; degrades to `partial` on
     timeout rather than failing the verdict — this call sits in the payment
     loop, so a 502 here would block the agent's payment.

Solana addresses get the OFAC floor only for now (GoPlus address-security is
keyed by EVM chain_id); the verdict says so via `partial`.
"""
from __future__ import annotations

import os
import re
import time
from typing import Literal

import requests

# --- Hardcoded sanctions corpus (lowercased EVM, raw Solana) ---
# Production: replace with daily Treasury.gov CSV pull.
_OFAC_CORPUS_VERSION = "2026-08-04-static"

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
    # Blender.io (sanctioned May 2022)
    "0x9c2bc757b66f24d60f016b6237f8cdd414a879fa": ["OFAC SDN", "Blender.io"],
}

_SOLANA_SANCTIONED: dict[str, list[str]] = {
    # Solana wallets sanctioned by OFAC are rarer in the public list;
    # populate from Treasury.gov once production pull is wired.
}

_BTC_HEX_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# --- GoPlus address-security label map: key -> (severity, human detail) ---
# Values in the API are "1"/"0" flags; we surface only the ones that flip.
_GOPLUS_FLAGS: dict[str, tuple[str, str]] = {
    "sanctioned": ("critical", "On a sanctions list"),
    "stealing_attack": ("critical", "Linked to token-stealing attacks"),
    "phishing_activities": ("critical", "Linked to phishing activities"),
    "honeypot_related_address": ("critical", "Associated with honeypot contracts"),
    "financial_crime": ("critical", "Linked to financial crime"),
    "money_laundering": ("critical", "Linked to money laundering"),
    "darkweb_transactions": ("critical", "Linked to darkweb transactions"),
    "blackmail_activities": ("critical", "Linked to blackmail activity"),
    "cybercrime": ("high", "Linked to cybercrime"),
    "mixer": ("high", "Associated with a mixer"),
    "fake_kyc": ("high", "Linked to fake-KYC activity"),
    "malicious_mining_activities": ("high", "Linked to malicious mining"),
    "blacklist_doubt": ("medium", "Appears on community blacklists"),
    "gas_abuse": ("medium", "Linked to gas-abuse activity"),
}

_SEVERITY_SCORE = {"critical": 90, "high": 60, "medium": 30, "low": 0}
_GOPLUS_URL = "https://api.gopluslabs.io/api/v1/address_security/{addr}"
_GOPLUS_TIMEOUT_S = 2.5
_GOPLUS_CACHE_TTL_S = 3600
_goplus_cache: dict[str, tuple[float, dict | None]] = {}


def _infer_chain(wallet: str) -> Literal["ethereum", "solana", "unknown"]:
    if _BTC_HEX_RE.match(wallet):
        return "ethereum"
    if _SOL_RE.match(wallet):
        return "solana"
    return "unknown"


def _goplus_lookup(address: str) -> dict | None:
    """Return GoPlus address-security `result` dict, or None on any failure.

    Cached by address; the reputation DB is address-based and cross-chain, so
    we query with chain_id=1 (Ethereum canonical) regardless of EVM origin.
    Never raises — callers treat None as "layer unavailable".
    """
    key = address.lower()
    hit = _goplus_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    headers = {}
    token = os.getenv("GOPLUS_ACCESS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = token
    try:
        resp = requests.get(
            _GOPLUS_URL.format(addr=address),
            params={"chain_id": "1"},
            headers=headers,
            timeout=_GOPLUS_TIMEOUT_S,
        )
        resp.raise_for_status()
        result = resp.json().get("result") or None
    except Exception:
        result = None
    # Cache successes only; a transient failure should be retried next call.
    if result is not None:
        _goplus_cache[key] = (time.time() + _GOPLUS_CACHE_TTL_S, result)
    return result


def _from_ofac(normalized: str, chain: str) -> list[str]:
    if chain == "ethereum":
        return _EVM_SANCTIONED.get(normalized, [])
    return _SOLANA_SANCTIONED.get(normalized, [])


def screen(wallet: str) -> dict:
    """Return a payment-risk verdict for a wallet address.

    Keys (v2 superset — the original low/medium/high fields are retained):
      wallet, chain_inferred, sanctions_match, sanctioned_lists, risk_level, notes
      address_type    "eoa" | "contract" | "unknown"
      recommendation  "allow" | "review" | "block"   <- agents branch on this
      risk_score      0-100
      signals         [{code, severity, source, detail}]
      labels          benign context labels, when known
      corpus_version  OFAC corpus stamp for provenance
      partial         true if the GoPlus layer was unavailable / not applicable
    """
    chain = _infer_chain(wallet)
    checked_at = int(time.time())
    if chain == "unknown":
        return {
            "wallet": wallet,
            "chain_inferred": "unknown",
            "sanctions_match": False,
            "sanctioned_lists": [],
            "risk_level": "medium",
            "notes": "Could not infer chain from address shape — verdict inconclusive. Provide a checksum-style EVM address (0x + 40 hex) or base58 Solana pubkey.",
            "address_type": "unknown",
            "recommendation": "review",
            "risk_score": 30,
            "signals": [],
            "labels": [],
            "corpus_version": _OFAC_CORPUS_VERSION,
            "partial": True,
            "checked_at": checked_at,
        }

    normalized = wallet.lower() if chain == "ethereum" else wallet
    signals: list[dict] = []
    score = 0

    # Layer 1 — OFAC floor.
    ofac = _from_ofac(normalized, chain)
    if ofac:
        signals.append({"code": "ofac_sdn", "severity": "critical", "source": "treasury.gov", "detail": ", ".join(ofac)})
        score = 100

    # Layer 2 — GoPlus (EVM only; Solana falls through as partial).
    partial = chain != "ethereum"
    goplus = _goplus_lookup(normalized) if chain == "ethereum" else None
    address_type = "unknown"
    labels: list[str] = []
    if goplus is not None:
        address_type = "contract" if str(goplus.get("contract_address", "0")) == "1" else "eoa"
        for code, (severity, detail) in _GOPLUS_FLAGS.items():
            if str(goplus.get(code, "0")) == "1":
                signals.append({"code": code, "severity": severity, "source": "goplus", "detail": detail})
                score = max(score, _SEVERITY_SCORE[severity])
        try:
            if int(goplus.get("number_of_malicious_contracts_created", 0) or 0) > 0:
                signals.append({"code": "malicious_contracts_created", "severity": "high", "source": "goplus", "detail": "Deployed malicious contracts"})
                score = max(score, _SEVERITY_SCORE["high"])
        except (TypeError, ValueError):
            pass
    elif chain == "ethereum":
        partial = True  # GoPlus layer failed — verdict rests on OFAC only.

    sanctions_match = bool(ofac) or any(s["code"] == "sanctioned" for s in signals)

    if score >= 80:
        risk_level, recommendation = "critical", "block"
    elif score >= 50:
        risk_level, recommendation = "high", "review"
    elif score >= 20:
        risk_level, recommendation = "medium", "review"
    else:
        risk_level, recommendation = "low", "allow"

    if ofac:
        notes = f"Address matches {len(ofac)} sanctions program(s): {', '.join(ofac)}. DO NOT transact without a regulatory-approved exception."
    elif signals:
        notes = f"{len(signals)} risk signal(s) found — recommendation: {recommendation}. Review `signals` before transacting."
    elif partial:
        notes = "OFAC floor clear; enrichment layer unavailable for this address, so residual risk is unscored. Treat `allow` as provisional."
    else:
        notes = "No sanctions or address-reputation flags across the active corpus. Enrichment is public-source only; institutions should pair with proprietary AML data."

    return {
        "wallet": normalized,
        "chain_inferred": chain,
        "sanctions_match": sanctions_match,
        "sanctioned_lists": ofac,
        "risk_level": risk_level,
        "notes": notes,
        "address_type": address_type,
        "recommendation": recommendation,
        "risk_score": score,
        "signals": signals,
        "labels": labels,
        "corpus_version": _OFAC_CORPUS_VERSION,
        "partial": partial,
        "checked_at": checked_at,
    }
