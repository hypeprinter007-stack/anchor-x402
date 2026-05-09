"""EVM calldata decoder.

Take raw EVM calldata, peel off the 4-byte function selector (sighash),
look up the human-readable signature against openchain.xyz's free
4byte directory, then ABI-decode the remaining bytes against the
parsed parameter types.

Stateless except for a module-level sighash cache: 4byte selectors are
an immutable mapping (selector = first 4 bytes of keccak256 over the
canonical signature), so once we have a hit it's safe to cache forever.
Cache is per-Lambda-container; cold starts re-fetch on miss.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import requests as rq

log = logging.getLogger("calldata_decode")

_OPENCHAIN_URL = "https://api.openchain.xyz/signature-database/v1/lookup"
_OPENCHAIN_TIMEOUT = 6  # seconds

# Module-level cache: sighash (8 hex chars, no 0x) -> list[str] of canonical sigs.
# Keyed by lowercased sighash. None sentinel = looked up, no match.
_SIGHASH_CACHE: dict[str, list[str] | None] = {}

# Top-level type detection: split a function signature's parameter list
# at top-level commas only (don't split inside nested tuples).
_FUNC_RE = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\((.*)\)$")


def _split_top_level(params: str) -> list[str]:
    """Split a parameter list string at top-level commas, preserving tuples."""
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in params:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def parse_signature(sig: str) -> tuple[str, list[str]]:
    """Parse `transfer(address,uint256)` → ("transfer", ["address", "uint256"])."""
    m = _FUNC_RE.match(sig.strip())
    if not m:
        raise ValueError(f"unparseable signature: {sig!r}")
    name = m.group(1)
    params_str = m.group(2).strip()
    if not params_str:
        return name, []
    return name, _split_top_level(params_str)


def _normalize_value(v: Any) -> Any:
    """JSON-safe coercion: bytes → 0x-hex, large ints → str if > 2**53."""
    if isinstance(v, (bytes, bytearray)):
        return "0x" + bytes(v).hex()
    if isinstance(v, tuple):
        return [_normalize_value(x) for x in v]
    if isinstance(v, list):
        return [_normalize_value(x) for x in v]
    if isinstance(v, int) and (v > 2**53 - 1 or v < -(2**53 - 1)):
        return str(v)
    return v


def lookup_sighash(sighash: str) -> list[str]:
    """Return list of candidate canonical signatures for a 4byte selector.

    `sighash` may be 8 hex chars or `0x`-prefixed. Empty list = no match.
    Results are cached forever (module-level dict).
    """
    s = sighash.lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 8 or not all(c in "0123456789abcdef" for c in s):
        raise ValueError(f"sighash must be 8 hex chars, got {sighash!r}")

    cached = _SIGHASH_CACHE.get(s)
    if cached is not None:
        return list(cached)
    if s in _SIGHASH_CACHE:  # negative cache hit
        return []

    try:
        r = rq.get(
            _OPENCHAIN_URL,
            params={"function": "0x" + s},
            timeout=_OPENCHAIN_TIMEOUT,
        )
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        log.warning("openchain.xyz lookup failed for %s: %s: %s", s, type(e).__name__, e)
        # don't cache transient failures
        return []

    # openchain shape: {"ok": true, "result": {"function": {"0x<sig>": [{"name": "...", "filtered": false}, ...]}}}
    candidates: list[str] = []
    try:
        entries = (body.get("result") or {}).get("function") or {}
        items = entries.get("0x" + s) or []
        for item in items:
            name = item.get("name")
            if name:
                candidates.append(name)
    except Exception as e:
        log.warning("openchain.xyz response shape unexpected for %s: %s", s, e)

    if candidates:
        _SIGHASH_CACHE[s] = candidates
    else:
        _SIGHASH_CACHE[s] = None  # negative cache: prevent re-hammering on permanent miss
    return list(candidates)


def decode_calldata(calldata_hex: str) -> dict:
    """Decode EVM calldata. Pure function — does not hit the chain.

    Returns a dict matching CalldataDecodeResponse. `decoded` is True
    iff a signature was matched AND the args parsed cleanly.
    """
    raw = calldata_hex.strip()
    if raw.startswith("0x") or raw.startswith("0X"):
        raw = raw[2:]
    if len(raw) < 8:
        raise ValueError("calldata too short — need at least 4-byte selector")
    if not all(c in "0123456789abcdefABCDEF" for c in raw):
        raise ValueError("calldata must be hex")

    sighash = raw[:8].lower()
    args_hex = raw[8:]

    candidates = lookup_sighash(sighash)
    if not candidates:
        return {
            "function_selector": "0x" + sighash,
            "function_name": None,
            "function_signature": None,
            "params": [],
            "decoded": False,
            "candidates": [],
            "source": "openchain.xyz",
        }

    # First candidate is canonical per openchain ordering. Try each in order
    # until one decodes cleanly — handles the rare hash-collision case.
    from eth_abi import decode as abi_decode

    chosen_sig: str | None = None
    chosen_name: str | None = None
    params_out: list[dict] = []
    args_bytes = bytes.fromhex(args_hex)

    last_err: Exception | None = None
    for sig in candidates:
        try:
            name, types = parse_signature(sig)
            if not types and not args_bytes:
                chosen_sig, chosen_name, params_out = sig, name, []
                break
            values = abi_decode(types, args_bytes)
            params_out = [
                {"name": None, "type": t, "value": _normalize_value(v)}
                for t, v in zip(types, values)
            ]
            chosen_sig, chosen_name = sig, name
            break
        except Exception as e:
            last_err = e
            continue

    if chosen_sig is None:
        log.info("sighash %s matched %d sigs but none decoded; last err: %s",
                 sighash, len(candidates), last_err)
        return {
            "function_selector": "0x" + sighash,
            "function_name": None,
            "function_signature": None,
            "params": [],
            "decoded": False,
            "candidates": candidates,
            "source": "openchain.xyz",
        }

    others = [s for s in candidates if s != chosen_sig]
    return {
        "function_selector": "0x" + sighash,
        "function_name": chosen_name,
        "function_signature": chosen_sig,
        "params": params_out,
        "decoded": True,
        "candidates": others,
        "source": "openchain.xyz",
    }
