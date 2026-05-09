"""Cross-chain name service resolver.

Resolves human-readable names to on-chain addresses across multiple
naming systems:

  - `*.eth`  -> ENS (Ethereum). Calls the canonical ENS Registry at
                0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e on mainnet
                via web3.py against a public RPC. Walks Registry ->
                Resolver -> addr(node).
  - `*.sol`  -> Solana Name Service / Bonfida. Tries the public Bonfida
                REST proxy first (https://sns-api.bonfida.com), falls
                back to a "not resolved" answer if the proxy is down.

Other TLDs (.crypto, .x, .nft, .blockchain, …) are deliberately not
supported in this build — the response advertises which TLDs *are*
supported so callers can make a typed decision.

Resolutions are memoized in-process for 1 hour. The cache key is the
exact lowercased name; entries carry their own `expires_at` epoch and
are evicted lazily on read.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger("name_resolve")

# --- Config ---------------------------------------------------------------

ETH_RPC_URL = os.getenv("ETH_RPC_URL", "https://ethereum.publicnode.com")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
BONFIDA_API_URL = os.getenv("BONFIDA_API_URL", "https://sns-api.bonfida.com")

ENS_REGISTRY_ADDRESS = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"

SUPPORTED_TLDS: list[str] = [".eth", ".sol"]

# 1 hour TTL. Names re-resolve roughly hourly — long enough to soak
# the bulk of repeat traffic, short enough to catch real updates.
CACHE_TTL_SECONDS = 3600

# Per-call TTL hints surfaced in the response so downstream callers
# can layer their own caches on top.
ENS_TTL_HINT = 3600
SOL_TTL_HINT = 3600

# --- Cache ---------------------------------------------------------------

_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> dict[str, Any] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        if entry["expires_at"] < time.time():
            _cache.pop(key, None)
            return None
        return entry["result"]


def _cache_put(key: str, result: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = {
            "result": result,
            "expires_at": time.time() + CACHE_TTL_SECONDS,
        }


# --- ENS ------------------------------------------------------------------

# Minimal ABIs — only what we actually call.
_ENS_REGISTRY_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "node", "type": "bytes32"}],
        "name": "resolver",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
    },
]

_ENS_RESOLVER_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "node", "type": "bytes32"}],
        "name": "addr",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function",
    },
]

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _namehash(name: str) -> bytes:
    """Compute the EIP-137 namehash for an ENS name."""
    from eth_utils import keccak

    node = b"\x00" * 32
    if not name:
        return node
    labels = name.lower().split(".")
    for label in reversed(labels):
        label_hash = keccak(text=label)
        node = keccak(node + label_hash)
    return node


def _resolve_ens(name: str) -> dict[str, Any]:
    """Resolve `*.eth` to a 0x address. Returns {address, notes}.

    On any failure (RPC down, malformed name, no resolver, no addr
    record) returns address=None with a notes string explaining why.
    """
    try:
        from web3 import Web3
    except Exception as e:  # pragma: no cover
        return {"address": None, "notes": f"web3 unavailable: {e}"}

    # Prefer the ENS helper if installed — it handles wildcard / CCIP
    # resolvers, Unicode normalization, etc. Fall back to direct
    # registry/resolver calls otherwise.
    try:
        from ens import ENS  # type: ignore

        w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL, request_kwargs={"timeout": 8}))
        ns = ENS.from_web3(w3)
        addr = ns.address(name)
        if addr and addr != _ZERO_ADDRESS:
            return {"address": Web3.to_checksum_address(addr), "notes": None}
        return {"address": None, "notes": f"ENS name `{name}` has no addr record on mainnet."}
    except ImportError:
        pass
    except Exception as e:
        log.warning("ENS helper failed for %s: %s; falling back to direct calls", name, e)

    # Direct path: Registry.resolver(node) -> Resolver.addr(node).
    try:
        w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL, request_kwargs={"timeout": 8}))
        node = _namehash(name)
        registry = w3.eth.contract(
            address=Web3.to_checksum_address(ENS_REGISTRY_ADDRESS),
            abi=_ENS_REGISTRY_ABI,
        )
        resolver_addr = registry.functions.resolver(node).call()
        if not resolver_addr or resolver_addr == _ZERO_ADDRESS:
            return {"address": None, "notes": f"ENS name `{name}` is not registered or has no resolver."}
        resolver = w3.eth.contract(
            address=Web3.to_checksum_address(resolver_addr),
            abi=_ENS_RESOLVER_ABI,
        )
        addr = resolver.functions.addr(node).call()
        if not addr or addr == _ZERO_ADDRESS:
            return {"address": None, "notes": f"ENS name `{name}` has a resolver but no addr record."}
        return {"address": Web3.to_checksum_address(addr), "notes": None}
    except Exception as e:
        log.warning("ENS direct resolution failed for %s: %s", name, e)
        return {"address": None, "notes": f"ENS resolution failed: {type(e).__name__}: {e}"}


# --- Solana Name Service (Bonfida) ---------------------------------------


def _resolve_sns(name: str) -> dict[str, Any]:
    """Resolve `*.sol` to a base58 Solana address via Bonfida's REST proxy.

    Implementing the full Bonfida PDA derivation in pure Python requires
    porting their JS/Rust SDK; the public REST endpoint is the fast path
    and what most agents use in practice.
    """
    try:
        import requests as rq
    except Exception as e:  # pragma: no cover
        return {"address": None, "notes": f"requests unavailable: {e}"}

    # Bonfida exposes /resolve/<domain>; accepts with or without the
    # trailing `.sol`. We strip it for safety.
    domain = name[:-4] if name.endswith(".sol") else name
    url = f"{BONFIDA_API_URL.rstrip('/')}/resolve/{domain}"
    try:
        r = rq.get(url, timeout=8)
        if r.status_code == 404:
            return {"address": None, "notes": f"SNS name `{name}` is not registered."}
        r.raise_for_status()
        body = r.json()
        # Bonfida response shapes have varied across versions; accept
        # either {"result": "<addr>"} or {"s": "ok", "result": "<addr>"}.
        addr = body.get("result") if isinstance(body, dict) else None
        if isinstance(addr, dict):
            addr = addr.get("owner") or addr.get("address")
        if not addr:
            return {"address": None, "notes": f"SNS name `{name}` returned no owner from Bonfida."}
        return {"address": str(addr), "notes": None}
    except Exception as e:
        log.warning("SNS resolution failed for %s: %s", name, e)
        return {"address": None, "notes": f"SNS resolution failed via Bonfida: {type(e).__name__}: {e}"}


# --- Public entrypoint ---------------------------------------------------


def resolve(name: str) -> dict[str, Any]:
    """Resolve `name` across supported TLDs.

    Returns a dict shaped like:
        {
          "name": <input>,
          "addresses": [{"chain": ..., "address": ..., "ttl_hint_seconds": ...}],
          "resolved_at": <epoch>,
          "registry_used": "ENS" | "SNS" | None,
          "supported_tlds": [...],
          "notes": <str | None>,
        }

    Names that don't resolve return 200-shaped output with empty
    `addresses` and a populated `notes` field.
    """
    raw = (name or "").strip()
    key = raw.lower()
    now = int(time.time())

    if not key:
        return {
            "name": raw,
            "addresses": [],
            "resolved_at": now,
            "registry_used": None,
            "supported_tlds": list(SUPPORTED_TLDS),
            "notes": "Empty name — pass `?name=<value>`.",
        }

    cached = _cache_get(key)
    if cached is not None:
        # Refresh the timestamp to reflect when the caller got it,
        # but keep cached payload otherwise.
        out = dict(cached)
        out["resolved_at"] = now
        out["notes"] = (cached.get("notes") or "") + (
            " (served from in-process cache)" if cached.get("notes") else "served from in-process cache"
        )
        return out

    if key.endswith(".eth"):
        registry = "ENS"
        chain = "ethereum"
        ttl = ENS_TTL_HINT
        res = _resolve_ens(key)
    elif key.endswith(".sol"):
        registry = "SNS"
        chain = "solana"
        ttl = SOL_TTL_HINT
        res = _resolve_sns(key)
    else:
        # Unknown / unsupported TLD — graceful 200 with explanation.
        result = {
            "name": raw,
            "addresses": [],
            "resolved_at": now,
            "registry_used": None,
            "supported_tlds": list(SUPPORTED_TLDS),
            "notes": (
                f"TLD not supported. Supported TLDs: {', '.join(SUPPORTED_TLDS)}. "
                "Other naming systems (Unstoholdings .crypto/.x, Lens, Farcaster) "
                "may be added in a future build."
            ),
        }
        # Cache miss results too — saves repeated work on bot traffic.
        _cache_put(key, result)
        return result

    addresses: list[dict[str, Any]] = []
    if res.get("address"):
        addresses.append(
            {
                "chain": chain,
                "address": res["address"],
                "ttl_hint_seconds": ttl,
            }
        )

    result = {
        "name": raw,
        "addresses": addresses,
        "resolved_at": now,
        "registry_used": registry,
        "supported_tlds": list(SUPPORTED_TLDS),
        "notes": res.get("notes"),
    }
    _cache_put(key, result)
    return result
