#!/usr/bin/env python3
"""Offline tests for the weekly SDN drift check.

    .venv/bin/python scripts/test_sdn_watch.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import ofac_sdn, sdn_watch  # noqa: E402

_fails = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        _fails += 1


def run(fetch) -> tuple[str, object]:
    sdn_watch.fetch_sdn = fetch
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            result = sdn_watch.handler()
        except Exception as exc:  # noqa: BLE001
            result = exc
    return out.getvalue(), result


# parse: a row shaped like SDN.CSV (12 columns, addresses in remarks)
row = ('1,"LAZARUS GROUP",-0-,"[DPRK3] [CYBER2]",-0-,-0-,-0-,-0-,-0-,-0-,-0-,'
       '"Digital Currency Address - ETH 0x098B716B8Aaf21512996dC57EB0615e2383E2f96; '
       'Digital Currency Address - SOL 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM; '
       'Digital Currency Address - XBT 1BoatSLRHtKNngkdXEeobR76b53LETtpyT."\n')
evm, sol = sdn_watch.parse(row)
ok("EVM address parsed and lowercased", evm == {"0x098b716b8aaf21512996dc57eb0615e2383e2f96": ["OFAC SDN", "LAZARUS GROUP", "DPRK3", "CYBER2"]}, str(evm))
ok("SOL address parsed", list(sol) == ["9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"], str(sol))
ok("BTC address ignored", len(evm) + len(sol) == 2)

same = (dict(ofac_sdn.EVM), dict(ofac_sdn.SOLANA))
out, result = run(lambda: same)
ok("matching list → SDN_OK, no drift line", out.startswith("SDN_OK") and "SDN_DRIFT" not in out, out)

new_addr = "0x" + "ab" * 20
added = ({**same[0], new_addr: ["OFAC SDN", "NEW ENTITY", "CYBER2"]}, same[1])
out, result = run(lambda: added)
ok("new Treasury address → SDN_DRIFT naming it", out.startswith("SDN_DRIFT ") and new_addr in out, out)

removed_key = sorted(same[0])[0]
out, result = run(lambda: ({k: v for k, v in same[0].items() if k != removed_key}, same[1]))
ok("delisted address → SDN_DRIFT naming it", "SDN_DRIFT" in out and removed_key in out, out)

relabel = dict(same[0]); relabel[removed_key] = ["OFAC SDN", "RENAMED", "CYBER2"]
out, result = run(lambda: (relabel, same[1]))
ok("relabeled entry → SDN_DRIFT", "SDN_DRIFT" in out and '"relabeled":["' + removed_key in out, out)

out, result = run(lambda: ({}, {}))
ok("implausible parse → SDN_WATCH_FAILED and raises", out.startswith("SDN_WATCH_FAILED") and isinstance(result, RuntimeError), out)


def boom():
    raise OSError("network down")


out, result = run(boom)
ok("fetch failure → SDN_WATCH_FAILED and raises", out.startswith("SDN_WATCH_FAILED OSError") and isinstance(result, OSError), out)

print()
if _fails:
    print(f"{_fails} FAILED")
    sys.exit(1)
print("all sdn-watch checks OK")
