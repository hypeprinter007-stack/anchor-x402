"""Treasury's SDN list, parsed the way anchor screens it, and a weekly drift check.

scripts/refresh_sdn.py regenerates services/ofac_sdn.py from fetch_sdn(); the
SdnWatch Lambda runs handler() weekly and logs one SDN_DRIFT line when
Treasury's addresses differ from the deployed corpus (SDN_WATCH_FAILED if it
cannot tell), which the SdnDrift alarm turns into an email. Refreshing stays a
reviewed, manual step.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request

URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"

_ADDR_RE = re.compile(r"Digital Currency Address - ([A-Za-z0-9]+) ([A-Za-z0-9]+)")
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# Below this many EVM addresses the CSV format has probably changed; treat the
# fetch as broken rather than as Treasury delisting nearly everything.
MIN_EVM = 50


def _programs(field: str) -> list[str]:
    return [p.strip(" []") for p in field.split("] [") if p.strip(" []")]


def parse(text: str) -> tuple[dict, dict]:
    """Every screenable address: 0x EVM under any ticker, base58 under SOL."""
    evm: dict[str, list[str]] = {}
    sol: dict[str, list[str]] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 12:
            continue
        name, program, remarks = row[1].strip(), row[3], row[11]
        listing = ["OFAC SDN", name, *_programs(program)]
        for ticker, addr in _ADDR_RE.findall(remarks):
            if _EVM_RE.match(addr):
                evm.setdefault(addr.lower(), listing)
            elif ticker.upper() == "SOL" and _SOL_RE.match(addr):
                sol.setdefault(addr, listing)
    return evm, sol


def fetch_sdn() -> tuple[dict, dict]:
    req = urllib.request.Request(URL, headers={"User-Agent": "anchor-x402 sdn refresh"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return parse(resp.read().decode("latin-1"))


def handler(event=None, context=None) -> dict:
    from services import ofac_sdn

    # A check that silently stops working looks the same as "no changes", so a
    # failed fetch or an implausible parse alarms too.
    try:
        evm, sol = fetch_sdn()
        if len(evm) < MIN_EVM:
            raise RuntimeError(f"only {len(evm)} EVM addresses parsed; SDN.CSV format change?")
    except Exception as exc:
        print(f"SDN_WATCH_FAILED {type(exc).__name__}: {exc}")
        raise
    drift = {
        "added": sorted(set(evm) - set(ofac_sdn.EVM)) + sorted(set(sol) - set(ofac_sdn.SOLANA)),
        "removed": sorted(set(ofac_sdn.EVM) - set(evm)) + sorted(set(ofac_sdn.SOLANA) - set(sol)),
        "relabeled": sorted(k for k in set(evm) & set(ofac_sdn.EVM) if evm[k] != ofac_sdn.EVM[k]),
    }
    if any(drift.values()):
        print("SDN_DRIFT " + json.dumps({"deployed": ofac_sdn.VERSION, **drift}, separators=(",", ":")))
    else:
        print(f"SDN_OK {ofac_sdn.VERSION}: {len(evm)} EVM, {len(sol)} Solana match Treasury")
    return {k: len(v) for k, v in drift.items()}
