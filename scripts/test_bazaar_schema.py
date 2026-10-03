#!/usr/bin/env python3
"""Every paid route's Bazaar declaration validates against its own schema.

PayAI validates `extensions.bazaar.info` against `extensions.bazaar.schema`
with AJV and silently drops a route that fails, so this checks what a buyer
actually receives: the live 402 from each paid route, GET and POST.

    .venv/bin/python scripts/test_bazaar_schema.py
"""
from __future__ import annotations

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("A2A_ALLOW_MEMORY_STORE", "1")

from fastapi.testclient import TestClient  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402

import app  # noqa: E402

c = TestClient(app.app)
fails = 0
checked = 0
no_output_schema = []
for key in sorted(app.x402_routes):
    method, path = key.split(" ", 1)
    r = c.request(method, path, json={} if method == "POST" else None)
    header = r.headers.get("payment-required")
    if r.status_code != 402 or not header:
        print(f"  FAIL  {key}: no 402 challenge ({r.status_code})")
        fails += 1
        continue
    bazaar = json.loads(base64.b64decode(header))["extensions"]["bazaar"]
    if "info" not in bazaar:
        continue  # alias route: discovery is declared once, on the canonical method
    schema = bazaar["schema"]
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(bazaar["info"]), key=str)
    if errors:
        fails += 1
        print(f"  FAIL  {key}: {errors[0].json_path}: {errors[0].message}")
        continue
    out = schema["properties"]["output"]["properties"]["example"]
    if not out.get("required"):
        no_output_schema.append(key)
    checked += 1
    print(f"  PASS  {key}")

print()
if no_output_schema:
    print("no declared output keys (expected only for ledger/summary):", ", ".join(no_output_schema))
if fails:
    print(f"{fails} FAILED")
    sys.exit(1)
if checked < 17:
    print(f"only {checked} declarations checked; expected 17")
    sys.exit(1)
print(f"all {checked} Bazaar declarations validate against their schema")
