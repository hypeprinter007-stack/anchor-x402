"""Token price lookup via the CoinGecko free public API.

Two resolution paths:
  - by symbol  (`BTC`, `ETH`, `SOL`, `USDC`, …) — mapped to a CoinGecko
    coin id through a small static table covering ~30 top tokens.
  - by contract (chain + address) — uses CoinGecko's per-chain
    /simple/token_price endpoint.

In-process cache, 60s TTL, keyed by ("sym", symbol_upper) or
("addr", chain_slug, contract_lower). On cache hit, the response carries
`age_seconds` measured from the moment the upstream fetch returned.

CoinGecko's free tier is rate-limited (~10–30 req/min). This cache
keeps us comfortably under that for typical agent workloads, and any
upstream 429 is surfaced to the caller as 503.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 8  # seconds
_CACHE_TTL = 60  # seconds

# Symbol → CoinGecko coin id. Top ~30 tokens by market cap / agent
# relevance. Lowercased on lookup.
_SYMBOL_TO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "TRX": "tron",
    "TON": "the-open-network",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "LINK": "chainlink",
    "SHIB": "shiba-inu",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "XLM": "stellar",
    "ETC": "ethereum-classic",
    "FIL": "filecoin",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "PEPE": "pepe",
    "WBTC": "wrapped-bitcoin",
    "WETH": "weth",
}

# CoinGecko platform slugs for /simple/token_price/<slug>
_CHAIN_SLUGS: dict[str, str] = {
    "base": "base",
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "polygon": "polygon-pos",
    "polygon-pos": "polygon-pos",
    "matic": "polygon-pos",
    "arbitrum": "arbitrum-one",
    "arbitrum-one": "arbitrum-one",
    "arb": "arbitrum-one",
    "optimism": "optimistic-ethereum",
    "op": "optimistic-ethereum",
    "bsc": "binance-smart-chain",
    "binance-smart-chain": "binance-smart-chain",
    "avalanche": "avalanche",
}


class TokenPriceError(Exception):
    """Caller-visible error: not_found / upstream_error / bad_request."""

    def __init__(self, kind: str, message: str, supported: list[str] | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.supported = supported


_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> dict | None:
    with _cache_lock:
        hit = _cache.get(key)
    if not hit:
        return None
    fetched_at, payload = hit
    if time.time() - fetched_at > _CACHE_TTL:
        return None
    out = dict(payload)
    out["fetched_at"] = int(fetched_at)
    out["age_seconds"] = max(0, int(time.time() - fetched_at))
    return out


def _cache_put(key: tuple, payload: dict) -> dict:
    fetched_at = time.time()
    stored = dict(payload)
    stored.pop("fetched_at", None)
    stored.pop("age_seconds", None)
    with _cache_lock:
        _cache[key] = (fetched_at, stored)
    out = dict(stored)
    out["fetched_at"] = int(fetched_at)
    out["age_seconds"] = 0
    return out


def supported_symbols() -> list[str]:
    return sorted(_SYMBOL_TO_ID.keys())


def supported_chains() -> list[str]:
    # Canonical (deduped) slugs only.
    return sorted(set(_CHAIN_SLUGS.values()))


def _coingecko_get(path: str, params: dict[str, Any]) -> dict:
    url = f"{_COINGECKO_BASE}{path}"
    try:
        r = requests.get(url, params=params, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise TokenPriceError("upstream_error", f"coingecko request failed: {type(e).__name__}: {e}")
    if r.status_code == 429:
        raise TokenPriceError("upstream_error", "coingecko rate-limited (429); retry shortly")
    if r.status_code >= 500:
        raise TokenPriceError("upstream_error", f"coingecko {r.status_code}")
    if r.status_code != 200:
        raise TokenPriceError("upstream_error", f"coingecko {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        raise TokenPriceError("upstream_error", "coingecko returned non-JSON body")


def by_symbol(symbol: str) -> dict:
    sym_up = symbol.strip().upper()
    if not sym_up:
        raise TokenPriceError("bad_request", "symbol must be non-empty")
    coin_id = _SYMBOL_TO_ID.get(sym_up)
    if not coin_id:
        raise TokenPriceError(
            "not_found",
            f"unsupported symbol '{symbol}'. Use ?chain=&contract= for arbitrary tokens.",
            supported=supported_symbols(),
        )

    cached = _cache_get(("sym", sym_up))
    if cached is not None:
        return cached

    body = _coingecko_get(
        "/simple/price",
        {
            "ids": coin_id,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_market_cap": "true",
        },
    )
    row = body.get(coin_id)
    if not row or "usd" not in row:
        raise TokenPriceError("upstream_error", f"coingecko returned no price for '{coin_id}'")

    payload = {
        "symbol": sym_up,
        "name": coin_id.replace("-", " ").title(),
        "contract": None,
        "chain": None,
        "usd": float(row["usd"]),
        "usd_24h_change_pct": (
            float(row["usd_24h_change"]) if row.get("usd_24h_change") is not None else None
        ),
        "market_cap_usd": (
            float(row["usd_market_cap"]) if row.get("usd_market_cap") is not None else None
        ),
        "source": "coingecko",
    }
    return _cache_put(("sym", sym_up), payload)


def by_contract(chain: str, contract: str) -> dict:
    chain_in = chain.strip().lower()
    addr_in = contract.strip()
    if not chain_in or not addr_in:
        raise TokenPriceError("bad_request", "chain and contract are both required")
    slug = _CHAIN_SLUGS.get(chain_in)
    if not slug:
        raise TokenPriceError(
            "bad_request",
            f"unsupported chain '{chain}'. Supported: {', '.join(supported_chains())}",
        )
    # EVM addresses are case-insensitive; Solana is base58 (case-sensitive).
    addr_key = addr_in.lower() if slug != "solana" else addr_in

    cached = _cache_get(("addr", slug, addr_key))
    if cached is not None:
        return cached

    body = _coingecko_get(
        f"/simple/token_price/{slug}",
        {
            "contract_addresses": addr_in,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_market_cap": "true",
        },
    )
    # CoinGecko echoes the address back lowercased for EVM chains.
    row = body.get(addr_in.lower()) or body.get(addr_in) or (
        next(iter(body.values())) if isinstance(body, dict) and body else None
    )
    if not row or "usd" not in row:
        raise TokenPriceError(
            "not_found",
            f"no price found for contract {addr_in} on chain {slug}",
        )

    payload = {
        "symbol": None,
        "name": None,
        "contract": addr_in,
        "chain": slug,
        "usd": float(row["usd"]),
        "usd_24h_change_pct": (
            float(row["usd_24h_change"]) if row.get("usd_24h_change") is not None else None
        ),
        "market_cap_usd": (
            float(row["usd_market_cap"]) if row.get("usd_market_cap") is not None else None
        ),
        "source": "coingecko",
    }
    return _cache_put(("addr", slug, addr_key), payload)
