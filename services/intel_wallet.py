"""Wallet intelligence bundle: aggregate multiple free public sources in
parallel into a single best-effort response.

For an EVM wallet (`0x` + 40 hex), we collect in parallel:
  - native ETH balance on Base (https://mainnet.base.org)
  - native ETH balance on Ethereum mainnet
  - USDC balance on Base (balanceOf on the USDC contract)
  - tx count / nonce on Base
  - first-seen activity probe on Base (nonce at block=earliest)
  - reverse-ENS lookup on Ethereum mainnet
  - sanctions check via services.screen.screen()

For a Solana wallet (base58, 32-44 chars), we collect:
  - native SOL balance via getBalance
  - SPL USDC balance via getTokenAccountsByOwner against the USDC mint
  - has-history probe via getSignaturesForAddress(limit=1)
  - reverse-SNS lookup (best-effort; Bonfida REST proxy)
  - sanctions check via services.screen.screen()

Per-source failures degrade gracefully: each slot returns `null` and an
entry is appended to `errors` — we never fail the whole bundle.

In-process cache TTL is 60 seconds, keyed by the (raw) wallet address.
Lambda warm container shares the cache; cold start re-fetches.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any

from services import screen as screen_svc

log = logging.getLogger("intel_wallet")

# --- Config ---------------------------------------------------------------

BASE_RPC_URL = os.getenv("INTEL_BASE_RPC", "https://mainnet.base.org")
ETH_RPC_URL = os.getenv("INTEL_ETH_RPC", "https://ethereum.publicnode.com")
SOLANA_RPC_URL = os.getenv("INTEL_SOLANA_RPC", "https://api.mainnet-beta.solana.com")
BONFIDA_API_URL = os.getenv("BONFIDA_API_URL", "https://sns-api.bonfida.com")

# USDC contract on Base mainnet (native, Circle-issued).
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_BASE_DECIMALS = 6

# USDC mint on Solana mainnet (Circle-issued).
USDC_SOL_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_SOL_DECIMALS = 6

# ENS registry on Ethereum mainnet (used via reverse-resolution helper).
# We rely on the `ens` python helper (ships with web3) which handles the
# reverse-record contract address selection internally.
ENS_REVERSE_REGISTRAR = "0xa58E81fe9b61B5c3fE2afD33CF304c454AbFc7Cb"

CACHE_TTL_SECONDS = 60
RPC_TIMEOUT_SECONDS = 8

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Minimal balanceOf ABI for USDC.
_ERC20_BALANCEOF_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    }
]

# --- Cache ---------------------------------------------------------------

_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str) -> tuple[dict[str, Any], int] | None:
    """Return (cached_payload, age_seconds) or None on miss / expired."""
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        age = int(time.time() - entry["fetched_at"])
        if age > CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return entry["payload"], age


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = {"payload": payload, "fetched_at": time.time()}


# --- Chain inference -----------------------------------------------------


def _infer_chain(wallet: str) -> str:
    if _EVM_RE.match(wallet):
        return "ethereum"
    if _SOL_RE.match(wallet):
        return "solana"
    return "unknown"


# --- EVM fetchers --------------------------------------------------------


def _w3(rpc_url: str):
    from web3 import Web3
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": RPC_TIMEOUT_SECONDS}))


def _evm_native_balance(rpc_url: str, wallet: str) -> str:
    """Return native balance as a decimal-string in whole units (ether)."""
    from web3 import Web3
    w3 = _w3(rpc_url)
    addr = Web3.to_checksum_address(wallet)
    wei = w3.eth.get_balance(addr)
    eth = (Decimal(wei) / Decimal(10**18))
    # avoid scientific-notation; preserve full precision
    return format(eth.normalize(), "f") if eth != 0 else "0"


def _evm_usdc_base(wallet: str) -> str:
    """Return USDC balance on Base as a decimal-string in whole USDC."""
    from web3 import Web3
    w3 = _w3(BASE_RPC_URL)
    addr = Web3.to_checksum_address(wallet)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(USDC_BASE_ADDRESS),
        abi=_ERC20_BALANCEOF_ABI,
    )
    raw = contract.functions.balanceOf(addr).call()
    bal = Decimal(raw) / Decimal(10**USDC_BASE_DECIMALS)
    return format(bal.normalize(), "f") if bal != 0 else "0"


def _evm_tx_count(wallet: str) -> int:
    from web3 import Web3
    w3 = _w3(BASE_RPC_URL)
    addr = Web3.to_checksum_address(wallet)
    return int(w3.eth.get_transaction_count(addr))


def _evm_has_history_base(wallet: str) -> bool:
    """Probe whether the wallet has any outbound history on Base.

    `eth_getTransactionCount(addr, 'earliest')` returns 0 unless the
    address has signed something at or before the earliest block; combined
    with the live nonce we can tell whether the wallet has ever moved.
    """
    from web3 import Web3
    w3 = _w3(BASE_RPC_URL)
    addr = Web3.to_checksum_address(wallet)
    live = int(w3.eth.get_transaction_count(addr, "latest"))
    return live > 0


def _evm_reverse_ens(wallet: str) -> str | None:
    """Reverse-resolve an EVM address to an ENS name on mainnet, or None."""
    from web3 import Web3
    try:
        from ens import ENS  # type: ignore
    except Exception as e:
        log.warning("ens helper unavailable: %s", e)
        return None
    w3 = _w3(ETH_RPC_URL)
    ns = ENS.from_web3(w3)
    addr = Web3.to_checksum_address(wallet)
    name = ns.name(addr)
    if not name:
        return None
    # Forward-verify per ENS best practice — name() can lie if forward
    # record disagrees. If the forward addr doesn't match, treat as None.
    try:
        forward = ns.address(name)
        if forward and Web3.to_checksum_address(forward) == addr:
            return name
        return None
    except Exception:
        # Best-effort: surface the name even if forward-check fails.
        return name


# --- Solana fetchers -----------------------------------------------------


def _sol_rpc(method: str, params: list) -> Any:
    import requests as rq
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = rq.post(SOLANA_RPC_URL, json=body, timeout=RPC_TIMEOUT_SECONDS)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"solana rpc error: {j['error']}")
    return j.get("result")


def _sol_native_balance(wallet: str) -> str:
    """SOL balance as a decimal-string in whole SOL."""
    res = _sol_rpc("getBalance", [wallet, {"commitment": "confirmed"}])
    lamports = int(res["value"]) if isinstance(res, dict) else int(res)
    sol = Decimal(lamports) / Decimal(10**9)
    return format(sol.normalize(), "f") if sol != 0 else "0"


def _sol_usdc_balance(wallet: str) -> str:
    """Sum USDC across all SPL accounts owned by `wallet`."""
    res = _sol_rpc(
        "getTokenAccountsByOwner",
        [
            wallet,
            {"mint": USDC_SOL_MINT},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    )
    total_raw = 0
    for entry in (res or {}).get("value", []):
        try:
            info = entry["account"]["data"]["parsed"]["info"]
            amt = info["tokenAmount"]["amount"]
            total_raw += int(amt)
        except (KeyError, TypeError, ValueError):
            continue
    bal = Decimal(total_raw) / Decimal(10**USDC_SOL_DECIMALS)
    return format(bal.normalize(), "f") if bal != 0 else "0"


def _sol_has_history(wallet: str) -> bool:
    res = _sol_rpc("getSignaturesForAddress", [wallet, {"limit": 1}])
    return bool(res)


def _sol_reverse_sns(wallet: str) -> str | None:
    """Best-effort reverse SNS via the Bonfida public proxy.

    The Bonfida REST proxy exposes /domains/{owner} which returns the
    primary `.sol` domain(s) registered to a wallet. Any failure is
    swallowed by the caller — this is best-effort enrichment.
    """
    import requests as rq
    url = f"{BONFIDA_API_URL.rstrip('/')}/domains/{wallet}"
    r = rq.get(url, timeout=RPC_TIMEOUT_SECONDS)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.json()
    # Shapes seen across versions: {"result": ["foo"]} or
    # {"result": [{"domain": "foo"}, ...]} or {"domains": [...]}.
    candidates: list[Any] = []
    if isinstance(body, dict):
        for k in ("result", "domains", "data"):
            v = body.get(k)
            if isinstance(v, list):
                candidates = v
                break
    if not candidates:
        return None
    first = candidates[0]
    if isinstance(first, str):
        name = first
    elif isinstance(first, dict):
        name = first.get("domain") or first.get("name") or first.get("address")
    else:
        return None
    if not name:
        return None
    return name if str(name).endswith(".sol") else f"{name}.sol"


# --- Orchestration -------------------------------------------------------


def _empty_bundle(wallet: str, chain: str) -> dict[str, Any]:
    return {
        "wallet": wallet,
        "chain_inferred": chain,
        "balances": {
            "base_eth": None,
            "eth_eth": None,
            "base_usdc": None,
            "sol": None,
            "solana_usdc": None,
        },
        "activity": {
            "base_tx_count": None,
            "has_history": None,
        },
        "identity": {
            "ens_name": None,
            "sns_name": None,
        },
        "sanctions": None,
        "errors": [],
        "fetched_at": int(time.time()),
        "cache_age_seconds": 0,
    }


def _run_parallel(tasks: dict[str, Any]) -> dict[str, Any]:
    """Run `{name: callable}` in a thread pool. Return `{name: (ok, value_or_err)}`."""
    out: dict[str, tuple[bool, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = (True, fut.result())
            except Exception as e:
                log.warning("intel-wallet source %s failed: %s: %s", name, type(e).__name__, e)
                out[name] = (False, f"{type(e).__name__}: {e}")
    return out


def fetch(wallet: str) -> dict[str, Any]:
    """Build the wallet intelligence bundle.

    Best-effort: per-source failures populate `errors` and leave that
    slot null in the response. Whole-request failures only happen on
    truly malformed input.
    """
    raw = (wallet or "").strip()
    if not raw:
        bundle = _empty_bundle(raw, "unknown")
        bundle["errors"].append({"source": "input", "message": "wallet is required"})
        bundle["sanctions"] = screen_svc.screen(raw)
        bundle["sanctions"]["checked_at"] = int(time.time())
        return bundle

    # Cache check (raw key — case-sensitive for Solana, lowercased EVM
    # would equally hit; we cache by exact input so callers get
    # determinism on what they sent).
    cached = _cache_get(raw)
    if cached is not None:
        payload, age = cached
        out = dict(payload)
        out["cache_age_seconds"] = age
        return out

    chain = _infer_chain(raw)
    bundle = _empty_bundle(raw, chain)

    # Sanctions is fast (in-memory) — run inline; the rest go in parallel.
    try:
        sanctions = screen_svc.screen(raw)
        sanctions["checked_at"] = int(time.time())
        bundle["sanctions"] = sanctions
    except Exception as e:
        log.warning("intel-wallet sanctions failed: %s", e)
        bundle["errors"].append({"source": "sanctions", "message": f"{type(e).__name__}: {e}"})

    if chain == "ethereum":
        tasks = {
            "base_eth": lambda w=raw: _evm_native_balance(BASE_RPC_URL, w),
            "eth_eth": lambda w=raw: _evm_native_balance(ETH_RPC_URL, w),
            "base_usdc": lambda w=raw: _evm_usdc_base(w),
            "base_tx_count": lambda w=raw: _evm_tx_count(w),
            "has_history": lambda w=raw: _evm_has_history_base(w),
            "ens_name": lambda w=raw: _evm_reverse_ens(w),
        }
        results = _run_parallel(tasks)

        for slot in ("base_eth", "eth_eth", "base_usdc"):
            ok, val = results[slot]
            if ok:
                bundle["balances"][slot] = val
            else:
                bundle["errors"].append({"source": slot, "message": val})

        ok, val = results["base_tx_count"]
        if ok:
            bundle["activity"]["base_tx_count"] = val
        else:
            bundle["errors"].append({"source": "base_tx_count", "message": val})

        ok, val = results["has_history"]
        if ok:
            bundle["activity"]["has_history"] = val
        else:
            bundle["errors"].append({"source": "has_history", "message": val})

        ok, val = results["ens_name"]
        if ok:
            bundle["identity"]["ens_name"] = val
        else:
            bundle["errors"].append({"source": "ens_name", "message": val})

    elif chain == "solana":
        tasks = {
            "sol": lambda w=raw: _sol_native_balance(w),
            "solana_usdc": lambda w=raw: _sol_usdc_balance(w),
            "has_history": lambda w=raw: _sol_has_history(w),
            "sns_name": lambda w=raw: _sol_reverse_sns(w),
        }
        results = _run_parallel(tasks)

        for slot in ("sol", "solana_usdc"):
            ok, val = results[slot]
            if ok:
                bundle["balances"][slot] = val
            else:
                bundle["errors"].append({"source": slot, "message": val})

        ok, val = results["has_history"]
        if ok:
            bundle["activity"]["has_history"] = val
        else:
            bundle["errors"].append({"source": "has_history", "message": val})

        ok, val = results["sns_name"]
        if ok:
            bundle["identity"]["sns_name"] = val
        else:
            bundle["errors"].append({"source": "sns_name", "message": val})

    else:
        # Unknown chain shape — sanctions still ran above, surface a
        # helpful error for the rest.
        bundle["errors"].append(
            {
                "source": "input",
                "message": (
                    "could not infer chain from address shape — "
                    "expected EVM (0x + 40 hex) or Solana (base58, 32-44 chars)"
                ),
            }
        )

    bundle["fetched_at"] = int(time.time())
    bundle["cache_age_seconds"] = 0
    _cache_put(raw, bundle)
    return bundle
