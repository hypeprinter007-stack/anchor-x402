#!/usr/bin/env python3
"""Local test for the agent-to-agent door (POST /v1/a2a).

Runs entirely offline: a throwaway Ed25519 keypair stands in for a peer, and
its agent card is injected straight into the card cache so nothing is fetched
over the network. Covers the happy path for all four methods plus the refusals
that matter — replay, tampered body, wrong key, expired and over-long windows,
denied origin, and SSRF-shaped origins.

  .venv/bin/python scripts/test_a2a.py
"""

import base64
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)

from fastapi.testclient import TestClient

from app import app, _a2a_route_price
from services import a2a as a2a_svc

PEER = "https://peer.example"
PEER_KEY_ID = "peer-2026-01"

_key = Ed25519PrivateKey.generate()
_other = Ed25519PrivateKey.generate()

failures: list[str] = []


def der_b64(k) -> str:
    return base64.b64encode(
        k.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode()


def prime_card(shape: str = "flat", status: str = "active") -> None:
    """Publish the peer's key the way a real peer would — under its own vendor
    namespace, to prove the reader is namespace-agnostic. `shape` covers both
    accepted card layouts: a flat key_id pair (what most peers publish today,
    including the Agoragentic pilot block) and a `keys` array (rotation-ready).
    """
    entry = {"key_id": PEER_KEY_ID, "public_key_der_base64": der_b64(_key), "status": status}
    block = {"keys": [entry]} if shape == "keys" else entry
    a2a_svc._cards[PEER] = (time.time() + 3600,
                            {"name": "peer", "extensions": {"peer.example:federation": block}})


def _serve_card_once(pub_der_b64: str, close_conn: bool) -> dict | None:
    """Serve one agent card over real TLS on loopback and fetch it through
    peer_card(). A self-signed cert is generated in-memory and trusted via
    REQUESTS_CA_BUNDLE; getaddrinfo and the address classifier are stubbed so the
    loopback address is treated as public for the duration. Exercises the real
    requests/urllib3 code path, which is the only way to catch socket-lifetime
    bugs in _assert_connected_peer.
    """
    import datetime, http.server, ssl, tempfile, threading
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    host = "cardhost.example"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now_utc - datetime.timedelta(days=1))
            .not_valid_after(now_utc + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .sign(key, hashes.SHA256()))
    tmp = tempfile.mkdtemp()
    cert_path, key_path = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))

    body = json.dumps({"name": "cardhost", "extensions": {"c:fed": {
        "key_id": PEER_KEY_ID, "public_key_der_base64": pub_der_b64}}}).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0" if close_conn else "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if close_conn:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    port = srv.server_address[1]
    threading.Thread(target=srv.handle_request, daemon=True).start()

    real_dns, real_public, real_port = socket.getaddrinfo, a2a_svc._is_public_address, 443
    os.environ["REQUESTS_CA_BUNDLE"] = cert_path
    try:
        socket.getaddrinfo = lambda h, p, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        a2a_svc._is_public_address = lambda addr: True   # loopback is the test server
        a2a_svc._cards.clear(); a2a_svc._card_failures.clear(); a2a_svc._fetch_times.clear()
        # peer_card builds the URL from the origin, so the port must ride along.
        return a2a_svc.peer_card(f"https://{host}")
    finally:
        socket.getaddrinfo, a2a_svc._is_public_address = real_dns, real_public
        os.environ.pop("REQUESTS_CA_BUNDLE", None)
        srv.server_close()


_n = 0


def envelope(method, body=None, *, key=None, exp_delta=60, nonce=None, origin=PEER, tamper=None,
             aud=None, key_id=PEER_KEY_ID):
    global _n
    _n += 1
    nonce = nonce or f"test-nonce-{_n:04d}-{method.replace('/', '-')}"
    exp = int(time.time()) + exp_delta
    aud = a2a_svc.AUDIENCE if aud is None else aud
    d = a2a_svc.request_digest(method=method, origin=origin, nonce=nonce, exp=exp, body=body,
                               aud=aud, key_id=key_id)
    sig = (key or _key).sign(d.encode("ascii"))
    params = {
        "aud": aud, "origin": origin, "key_id": key_id, "nonce": nonce, "exp": exp,
        "signature_algorithm": "ed25519", "signature": base64.b64encode(sig).decode(),
        "body": tamper if tamper is not None else body,
    }
    return {"jsonrpc": "2.0", "id": f"t{_n}", "method": method, "params": params}


def call(client, env):
    return client.post("/v1/a2a", json=env).json()


def ok(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def expect_error(client, env, code, label):
    r = call(client, env)
    got = (r.get("error") or {}).get("code")
    ok(label, got == code, f"expected {code}, got {got}: {json.dumps(r)[:180]}")


def main() -> None:
    a2a_svc.reset_cache_for_testing()
    os.environ.pop("A2A_STATE_TABLE", None)     # exercise the in-memory store
    os.environ["A2A_ALLOW_MEMORY_STORE"] = "1"  # which now requires explicit opt-in
    # The suite sends well over the real per-peer ceiling in one window, so raise
    # the limits for the run. F11 lowers them deliberately to exercise the real
    # thing; leaving production values here would make every later test flaky.
    a2a_svc.inbound_policy()["rate_limit_per_peer"] = 100_000
    a2a_svc.inbound_policy()["rate_limit_global"] = 100_000
    prime_card()
    client = TestClient(app)

    print("\nhappy path")
    r = call(client, envelope("peer/hello"))
    res = r.get("result") or {}
    ok("peer/hello verifies and echoes the peer", res.get("peer_verified") == PEER, json.dumps(r)[:200])
    ok("peer/hello advertises our own namespace", res.get("namespace") == "anchor-x402:a2a")
    ok("peer/hello publishes a policy digest", str(res.get("policy_digest", "")).startswith("sha256:"))

    r = call(client, envelope("capabilities/list"))
    skills = (r.get("result") or {}).get("skills") or []
    ok("capabilities/list returns the skill catalog", len(skills) > 0, json.dumps(r)[:200])
    ok("skills carry a payable url + price",
       all(s.get("url") and s.get("price_usd") is not None for s in skills))

    first = skills[0]["id"]
    r = call(client, envelope("peer/quote", {"skill": first}))
    quote = r.get("result") or {}
    ok("peer/quote issues an exchange_id", str(quote.get("exchange_id", "")).startswith("ax-"),
       json.dumps(r)[:200])
    ok("quote prices the requested skill", quote.get("skill") == first)
    ok("quote points at the metered route", str(quote.get("url", "")).startswith("https://"))
    ok("quote carries a digest", str(quote.get("digest", "")).startswith("sha256:"))

    xid = quote.get("exchange_id")
    r = call(client, envelope("peer/receipt", {"exchange_id": xid,
                                              "settlement": {"tx": "0xabc", "rail": "base"}}))
    receipt = r.get("result") or {}
    ok("peer/receipt mints against the stored quote", receipt.get("exchange_id") == xid,
       json.dumps(r)[:200])
    ok("receipt states what it proves", len(receipt.get("proves") or []) >= 3)
    ok("receipt states what it does not prove", len(receipt.get("does_not_prove") or []) >= 1)
    ok("receipt binds the peer's assertion",
       (receipt.get("peer_asserted_settlement") or {}).get("tx") == "0xabc")

    print("\nrefusals")
    replay = envelope("peer/hello")
    ok("first use of a nonce succeeds", "result" in call(client, replay))
    expect_error(client, replay, a2a_svc.ERR_REPLAY, "same nonce replayed is rejected")

    expect_error(client, envelope("peer/hello", {"a": 1}, tamper={"a": 2}),
                 a2a_svc.ERR_SIGNATURE, "tampered body breaks the signature")
    expect_error(client, envelope("peer/hello", key=_other),
                 a2a_svc.ERR_SIGNATURE, "signature from an unpublished key is rejected")
    expect_error(client, envelope("peer/hello", exp_delta=-10),
                 a2a_svc.ERR_PARAMS, "expired envelope is rejected")
    expect_error(client, envelope("peer/hello", exp_delta=99999),
                 a2a_svc.ERR_PARAMS, "exp beyond the nonce window is rejected")
    expect_error(client, envelope("peer/hello", nonce="short"),
                 a2a_svc.ERR_PARAMS, "too-short nonce is rejected")

    # A signature for one method must not carry over to another.
    cross = envelope("peer/hello")
    cross["method"] = "capabilities/list"
    expect_error(client, cross, a2a_svc.ERR_SIGNATURE, "signature is bound to its method")

    expect_error(client, envelope("peer/quote", {"skill": "no-such-skill"}),
                 a2a_svc.ERR_NOT_FOUND, "quote for an unknown skill is refused")
    expect_error(client, envelope("peer/receipt", {"exchange_id": "ax-" + "0" * 24}),
                 a2a_svc.ERR_NOT_FOUND,
                 "well-formed but unknown exchange_id is refused as not-found")

    r = client.post("/v1/a2a", content=b"{not json")
    ok("unparseable body is a 400 parse error", r.status_code == 400
       and (r.json().get("error") or {}).get("code") == -32700)
    expect_error(client, envelope("peer/nonsense"), a2a_svc.ERR_METHOD, "unknown method is refused")

    print("\norigin constraints (the card fetch is an SSRF sink)")
    for bad, label in [
        ("http://peer.example", "plain http origin refused"),
        ("https://169.254.169.254", "IP literal refused"),
        ("https://localhost", "localhost refused"),
        ("https://peer.example:8443", "non-default port refused"),
        ("https://peer.example/path", "origin with a path refused"),
        ("https://internal.internal", ".internal suffix refused"),
        ("https://peer.example/", "trailing slash refused with an actionable message"),
        ("https://Peer.Example", "mixed-case origin refused with an actionable message"),
    ]:
        env = envelope("peer/hello", origin=bad)
        expect_error(client, env, a2a_svc.ERR_PARAMS, label)

    print("\npolicy")
    a2a_svc.policy()["inbound"]["denied_origins"] = [PEER]
    expect_error(client, envelope("peer/hello"), a2a_svc.ERR_POLICY, "denied origin is refused")
    a2a_svc.policy()["inbound"]["denied_origins"] = []

    print("\ncard shapes + rotation")
    prime_card(shape="keys")
    ok("peer publishing a `keys` array verifies", "result" in call(client, envelope("peer/hello")))
    prime_card(shape="flat")
    ok("peer publishing a flat key pair verifies", "result" in call(client, envelope("peer/hello")))
    prime_card(shape="keys", status="retired")
    expect_error(client, envelope("peer/hello"), a2a_svc.ERR_PEER_CARD,
                 "peer key marked retired is refused")
    prime_card()

    our_keys = a2a_svc.our_keys()
    ok("our card uses the keys array", len(our_keys) >= 1 and "key_id" in our_keys[0])
    ok("our active key is KMS-held", our_keys[0].get("custody") == "aws-kms",
       json.dumps(our_keys[0]))
    ok("active_key_id reads from the card",
       a2a_svc.active_key_id() == our_keys[0]["key_id"])

    print("\nsigning")
    os.environ.pop("A2A_KMS_KEY_ID", None)
    a2a_svc._signing_key, a2a_svc._signing_loaded = None, False
    unsigned = a2a_svc.sign({"x": 1})
    ok("no key reachable → signed:false, payload still returned",
       unsigned.get("signed") is False and unsigned.get("digest", "").startswith("sha256:"))

    dev = Ed25519PrivateKey.generate()
    os.environ["A2A_SIGNING_KEY_PEM"] = dev.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    a2a_svc._signing_key, a2a_svc._signing_loaded = None, False
    signed = a2a_svc.sign({"x": 1})
    ok("local dev PEM signs", signed.get("signed") is True)
    dev.public_key().verify(base64.b64decode(signed["signature"]), signed["digest"].encode("ascii"))
    ok("dev signature verifies with a stock Ed25519 verifier", True)

    # ---------------------------------------------------------------------
    # Regressions for the 2026-07-28 self-audit. Each of these was a working
    # exploit against the first cut; the comment names what it was.
    # ---------------------------------------------------------------------
    print("\nregressions: audit findings")

    # F1 — a global nonce namespace let any card-publishing agent burn another
    # peer's nonce values, locking out anyone using predictable nonces.
    other = Ed25519PrivateKey.generate()
    OTHER = "https://other.example"
    a2a_svc._cards[OTHER] = (time.time() + 3600, {"extensions": {"o:fed": {
        "key_id": PEER_KEY_ID, "public_key_der_base64": der_b64(other)}}})
    shared = "collision-nonce-01"
    ok("F1 attacker's own call with a chosen nonce succeeds",
       "result" in call(client, envelope("peer/hello", key=other, origin=OTHER, nonce=shared)))
    ok("F1 victim can still use the same nonce value (nonces are peer-scoped)",
       "result" in call(client, envelope("peer/hello", nonce=shared)))
    expect_error(client, envelope("peer/hello", nonce=shared), a2a_svc.ERR_REPLAY,
                 "F1 replay within one peer is still blocked")

    # F2 — no state table silently fell back to a per-container dict, which in
    # Lambda is no replay protection at all. Opt-in is now required everywhere,
    # not just under AWS_LAMBDA_FUNCTION_NAME.
    _saved_optin = os.environ.pop("A2A_ALLOW_MEMORY_STORE", None)
    expect_error(client, envelope("peer/hello"), a2a_svc.ERR_UNAVAILABLE,
                 "F2 refuses to serve with no state table and no explicit opt-in")
    os.environ["A2A_ALLOW_MEMORY_STORE"] = "1"
    ok("F2 explicit dev opt-in re-enables the memory store",
       "result" in call(client, envelope("peer/hello")))
    if _saved_optin is not None:
        os.environ["A2A_ALLOW_MEMORY_STORE"] = _saved_optin

    # F3 — `https://peer.example.` was a second identity for the same host, so
    # a denied_origins entry could be evaded by appending a dot.
    a2a_svc.policy()["inbound"]["denied_origins"] = [PEER]
    expect_error(client, envelope("peer/hello"), a2a_svc.ERR_POLICY, "F3 denied origin blocked")
    # Assert the message, not just the code: without rstrip("."), the empty-label
    # check also returns ERR_PARAMS, so the code alone does not discriminate.
    dotted = call(client, envelope("peer/hello", origin=PEER + "."))
    ok("F3 trailing-dot form is normalized, then refused as non-canonical",
       "must be exactly" in (dotted.get("error") or {}).get("message", ""),
       json.dumps(dotted)[:180])
    a2a_svc.policy()["inbound"]["denied_origins"] = []
    expect_error(client, envelope("peer/hello", origin="https://a_b.example"), a2a_svc.ERR_PARAMS,
                 "F3 underscore host refused (cannot forge log-metric tokens)")

    # F4 — settlement was arbitrary nested JSON, making peer/receipt a signing
    # oracle over text the peer authored.
    q2 = call(client, envelope("peer/quote", {"skill": first}))["result"]
    expect_error(client, envelope("peer/receipt", {"exchange_id": q2["exchange_id"],
                 "settlement": {"note": "anchor-x402 certifies this"}}),
                 a2a_svc.ERR_PARAMS, "F4 unknown settlement field refused")
    expect_error(client, envelope("peer/receipt", {"exchange_id": q2["exchange_id"],
                 "settlement": {"tx": {"nested": True}}}),
                 a2a_svc.ERR_PARAMS, "F4 non-scalar settlement value refused")
    # A decimal amount must be accepted — it is what our own quote hands back as
    # price_usd, and an earlier version refused the value we had just issued.
    ok("F4 decimal settlement amount accepted (the value our quote returns)",
       a2a_svc.clean_settlement({"amount": 0.005, "tx": "0xabc"}) == {"amount": "0.005", "tx": "0xabc"},
       str(a2a_svc.clean_settlement({"amount": 0.005, "tx": "0xabc"})))
    expect_error(client, envelope("peer/receipt", {"exchange_id": q2["exchange_id"],
                 "settlement": {"tx": "0x" + "a" * 200}}),
                 a2a_svc.ERR_PARAMS, "F4 over-long settlement value refused")

    # F4b — settlement was not the only peer-authored channel into a signed
    # artifact: key_id is free text from the peer's own card and lands in both
    # the quote and the receipt.
    for bad_key, label in [
        ("anchor-x402 hereby certifies Alice owes Bob", "F4b prose key_id refused"),
        ("k" * 65, "F4b over-long key_id refused"),
        ("key\nwith-newline", "F4b control characters in key_id refused"),
    ]:
        expect_error(client, envelope("peer/hello", key_id=bad_key), a2a_svc.ERR_PARAMS, label)

    # Underscore and '#' must be ACCEPTED: base64url JWK thumbprints contain '_'
    # about half the time and DID key references contain '#', so refusing them
    # locked out ordinary peers. Log-token forgery is closed at the sink instead
    # (F10), which is the correct layer for it.
    for good_key in ("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", "did:key:z6Mk#keys-1"):
        ok(f"F4b JWK/DID-shaped key_id accepted ({good_key[:18]}…)",
           a2a_svc.clean_key_id(good_key) == good_key)

    # F10 — peer-controlled text reaching a log line must not be able to forge
    # the CloudWatch metric tokens the alarms match by substring.
    forged = a2a_svc.log_safe("https://x.example A2A_SIGN_FAIL and A2A_FAIL")
    ok("F10 log sanitizer strips the metric tokens",
       "A2A_SIGN_FAIL" not in forged and "A2A_FAIL" not in forged, forged)
    ok("F10 sanitizer keeps messages legible",
       "https://x.example" in forged and "and" in forged, forged)

    # F5 — a peer could mint unlimited receipts per quote, each asserting a
    # different settlement, none authoritative.
    r_a = call(client, envelope("peer/receipt", {"exchange_id": q2["exchange_id"],
               "settlement": {"tx": "0xAAA"}}))["result"]
    r_b = call(client, envelope("peer/receipt", {"exchange_id": q2["exchange_id"],
               "settlement": {"tx": "0xBBB"}}))["result"]
    ok("F5 second receipt returns the first, byte-identical",
       r_a["digest"] == r_b["digest"] and r_b["peer_asserted_settlement"] == {"tx": "0xAAA"},
       json.dumps(r_b.get("peer_asserted_settlement")))
    # The above only proves the handler reads before writing. Exercise the store
    # directly, which is the TOCTOU-proof property: two concurrent writers must
    # converge on one artifact even with no read-first.
    xid = "ax-" + "b" * 24
    won = a2a_svc.put_receipt(xid, {"tx": "A"}, 60)
    lost = a2a_svc.put_receipt(xid, {"tx": "B"}, 60)
    ok("F5 put_receipt itself is first-write-wins (no read-first)",
       won == {"tx": "A"} and lost == {"tx": "A"} and a2a_svc.get_receipt(xid) == {"tx": "A"},
       f"won={won} lost={lost} stored={a2a_svc.get_receipt(xid)}")

    # F8 — unvalidated exchange_id reached a DynamoDB key (2 KB limit).
    expect_error(client, envelope("peer/receipt", {"exchange_id": "x" * 3000}),
                 a2a_svc.ERR_PARAMS, "F8 malformed exchange_id refused before the store")
    expect_error(client, envelope("peer/receipt", {"exchange_id": "ax-NOTHEX"}),
                 a2a_svc.ERR_PARAMS, "F8 non-hex exchange_id refused")

    # F9 — quotes were priced from the card, which is not what charges. Drift
    # guard first (useful on its own), then the provenance assertion: poison the
    # card's price and require the quote to ignore it.
    for s in a2a_svc.our_card()["skills"]:
        card_price, route_price = s["price_usd"], _a2a_route_price(s)
        if card_price != route_price:
            failures.append(f"F9 price drift on {s['id']}: card {card_price} vs route {route_price}")
    ok("F9 every card price matches the metered route price",
       not [f for f in failures if f.startswith("F9")])

    poisoned = a2a_svc.our_card()["skills"][0]
    real_price, original = _a2a_route_price(poisoned), poisoned["price_usd"]
    poisoned["price_usd"] = real_price + 999
    try:
        q3 = call(client, envelope("peer/quote", {"skill": poisoned["id"]}))["result"]
        ok("F9 quote ignores the card price and uses the route price",
           q3["price_usd"] == real_price, f"quoted {q3['price_usd']}, route charges {real_price}")
    finally:
        poisoned["price_usd"] = original

    # F7 — the nonce record must outlive any quote derived from it. Read the
    # stored expiry back; comparing two policy constants tested no code at all.
    now = int(time.time())
    a2a_svc.claim_nonce("https://ttl.example", "ttl-probe-nonce-1", now + 60)
    held_until = a2a_svc._local_store["nonce#https://ttl.example#ttl-probe-nonce-1"][0]
    ok("F7 nonce is held at least as long as the quote it can derive",
       held_until >= now + a2a_svc.inbound_policy()["quote_ttl_s"],
       f"held {held_until - now}s, quote lives {a2a_svc.inbound_policy()['quote_ttl_s']}s")

    # F6 — rejecting IP literals in the origin string was never enough: a peer
    # can publish `evil.example A 169.254.169.254` and the hostname check passes.
    # Every resolved address must be public, and the address actually connected
    # to must be one of them (closing the rebinding race). getaddrinfo is stubbed
    # so this stays offline.
    # F11 — API Gateway cannot throttle this route (single greedy proxy route),
    # so the limit is enforced in-process against the shared table. Verify it
    # actually refuses, and that the refusal is a policy error, not a crash.
    print("\nrate limiting")
    pol = a2a_svc.inbound_policy()
    saved = (pol["rate_limit_per_peer"], pol["rate_limit_global"])
    pol["rate_limit_per_peer"], pol["rate_limit_global"] = 3, 10_000
    # Earlier tests already spent this window's budget for PEER, so start clean.
    for k in [k for k in a2a_svc._local_store if k.startswith("rate#")]:
        a2a_svc._local_store.pop(k)
    try:
        allowed_count = 0
        for _ in range(6):
            r = call(client, envelope("peer/hello"))
            if "result" in r:
                allowed_count += 1
            else:
                break
        ok("F11 per-peer rate limit refuses past the configured ceiling",
           allowed_count == 3, f"allowed {allowed_count}, limit 3")
        blocked = call(client, envelope("peer/hello"))
        ok("F11 refusal is a policy error naming the limit",
           (blocked.get("error") or {}).get("code") == a2a_svc.ERR_POLICY
           and "rate limit" in (blocked.get("error") or {}).get("message", ""),
           json.dumps(blocked)[:160])
        # F11b — counting costs a store write and runs before signature
        # verification, so a refused caller must stop costing writes. Note the
        # guarantee is tied to the GLOBAL counter: while only the per-peer limit is
        # tripped, the global one must still be bumped, or an attacker could trip
        # per-peer deliberately and then send unlimited uncounted traffic.
        pol["rate_limit_global"] = 2
        a2a_svc._rate_tripped.clear()
        for k in [k for k in a2a_svc._local_store if k.startswith("rate#")]:
            a2a_svc._local_store.pop(k)
        real_bump, writes = a2a_svc._bump, []
        a2a_svc._bump = lambda key, exp: (writes.append(key), real_bump(key, exp))[1]
        try:
            for _ in range(3):                    # third call trips the global limit
                call(client, envelope("peer/hello"))
            tripped_at = len(writes)
            for _ in range(6):
                call(client, envelope("peer/hello"))
            ok("F11b once the global limit trips, refusals cost no further writes",
               len(writes) == tripped_at,
               f"{len(writes) - tripped_at} extra writes across 6 refused calls")
            ok("F11b writes are bounded by the limit, not by request volume",
               tripped_at <= 6, f"{tripped_at} writes to reach a limit of 2")
        finally:
            a2a_svc._bump = real_bump
    finally:
        pol["rate_limit_per_peer"], pol["rate_limit_global"] = saved
        # Clear the counters so later tests are not throttled.
        for k in [k for k in a2a_svc._local_store if k.startswith("rate#")]:
            a2a_svc._local_store.pop(k)
        a2a_svc._rate_tripped.clear()

    print("\nregressions: SSRF via peer-controlled origin")
    real_getaddrinfo = socket.getaddrinfo

    def fake_dns(answers):
        return lambda host, port, *a, **k: [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in answers
        ]

    for answers, label in [
        (["169.254.169.254"], "F6 link-local (IMDS) answer refused"),
        (["127.0.0.1"], "F6 loopback answer refused"),
        (["10.0.0.5"], "F6 RFC 1918 answer refused"),
        (["100.64.0.1"], "F6 RFC 6598 carrier-NAT answer refused"),
        (["::ffff:169.254.169.254"], "F6 IPv4-mapped IPv6 answer refused"),
        (["fd00::1"], "F6 IPv6 unique-local answer refused"),
        (["93.184.216.34", "10.0.0.5"], "F6 mixed public+private answer set refused"),
    ]:
        socket.getaddrinfo = fake_dns(answers)
        a2a_svc._cards.clear(); a2a_svc._card_failures.clear(); a2a_svc._fetch_times.clear()
        try:
            a2a_svc.peer_card("https://ssrf-probe.example")
            ok(label, False, "no error raised — SSRF guard did not fire")
        except a2a_svc.A2AError as e:
            ok(label, "non-public" in e.message, e.message[:90])
        finally:
            socket.getaddrinfo = real_getaddrinfo

    # F1b — the fetch path had NO successful-case coverage, which is how a fix
    # that refused every peer whose server closes the connection passed 78 green
    # assertions. Serve a real card over real TLS, both keep-alive and
    # Connection: close, and require both to be accepted.
    for close_conn in (False, True):
        label = "Connection: close" if close_conn else "keep-alive"
        try:
            served = _serve_card_once(der_b64(_key), close_conn)
            ok(f"F1b card fetched over TLS with {label}",
               served is not None and served.get("extensions") is not None, str(served)[:120])
        except a2a_svc.A2AError as e:
            ok(f"F1b card fetched over TLS with {label}", False, f"REFUSED: {e.message[:110]}")
        except Exception as e:
            ok(f"F1b card fetched over TLS with {label}", None, f"harness: {type(e).__name__}: {e}")

    # Unit-level check of the classifier, independent of the fetch path.
    import ipaddress as _ip
    ok("F6 classifier accepts ordinary public addresses",
       all(a2a_svc._is_public_address(_ip.ip_address(x)) for x in ("8.8.8.8", "93.184.216.34", "2606:4700::1")))
    ok("F6 classifier rejects every private form",
       not any(a2a_svc._is_public_address(_ip.ip_address(x)) for x in
               ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                "100.64.0.1", "0.0.0.0", "::1", "fd00::1", "fe80::1", "::ffff:10.0.0.1")))
    a2a_svc._cards.clear(); a2a_svc._card_failures.clear(); a2a_svc._fetch_times.clear()
    prime_card()

    # F12 — receipt roots: a receipt is only as good as our signature until its
    # root is on-chain. The chain writer is stubbed; a real call would spend
    # mainnet funds from the treasury.
    # F14 — cross-recipient replay. Without `aud`, an envelope signed for us
    # verifies at any other server running this scheme, whose replay store is not
    # ours, so any recipient could relay a peer's authenticated requests onward.
    print("\nregressions: audience + key binding")
    env = envelope("peer/hello")
    del env["params"]["aud"]
    expect_error(client, env, a2a_svc.ERR_PARAMS, "F14 missing aud is refused")

    expect_error(client, envelope("peer/hello", aud="https://other.example"),
                 a2a_svc.ERR_PARAMS, "F14 envelope addressed to another server is refused")

    # aud is inside the signed bytes: rewriting it in transit must break the
    # signature, not merely fail the equality check.
    env = envelope("peer/hello", aud="https://other.example")
    env["params"]["aud"] = a2a_svc.AUDIENCE
    expect_error(client, env, a2a_svc.ERR_SIGNATURE,
                 "F14 rewriting aud to ours breaks the signature")
    ok("F14 aud is covered by the digest",
       a2a_svc.request_digest(method="peer/hello", origin=PEER, nonce="n" * 10, exp=1,
                              body=None, aud="https://a", key_id="k")
       != a2a_svc.request_digest(method="peer/hello", origin=PEER, nonce="n" * 10, exp=1,
                                 body=None, aud="https://b", key_id="k"))

    # F15 — key_id binding. A peer publishing one key under two ids (one active,
    # one retired) could otherwise have its revocation defeated by swapping ids.
    env = envelope("peer/hello", key_id="other-id")
    env["params"]["key_id"] = PEER_KEY_ID
    expect_error(client, env, a2a_svc.ERR_SIGNATURE,
                 "F15 swapping key_id after signing breaks the signature")
    ok("F15 key_id is covered by the digest",
       a2a_svc.request_digest(method="peer/hello", origin=PEER, nonce="n" * 10, exp=1,
                              body=None, aud="https://a", key_id="k1")
       != a2a_svc.request_digest(method="peer/hello", origin=PEER, nonce="n" * 10, exp=1,
                                 body=None, aud="https://a", key_id="k2"))

    # F16 — the card is the discovery contract; it drifted to advertising
    # eighteen services while listing nine. It is now generated from the routes
    # that charge, and this fails if anyone edits it by hand.
    print("\nregressions: agent card conformance (A2A 0.3.0)")
    card = a2a_svc.our_card()
    ok("F16 protocolVersion is the spec field", card.get("protocolVersion") == "0.3.0",
       str(card.get("protocolVersion")))
    ok("F16 non-spec fields are gone",
       "schemaVersion" not in card and "authentication" not in card)
    ok("F16 securitySchemes + security are declared",
       isinstance(card.get("securitySchemes"), dict) and isinstance(card.get("security"), list))
    ok("F16 provider uses organization", "organization" in (card.get("provider") or {}))
    paid_paths = {
        k.split(" ", 1)[1]
        for k, c in __import__("app").x402_routes.items()
        if any(isinstance(getattr(o, "price", None), str) for o in (c.accepts or []))
    }
    card_paths = {s["url"].split(".com", 1)[1] for s in card["skills"]}
    ok("F16 card covers every paid route, and nothing that is not one",
       card_paths == paid_paths,
       f"card-only={sorted(card_paths - paid_paths)} route-only={sorted(paid_paths - card_paths)}")
    ok("F16 description advertises the number it actually lists",
       card["description"].startswith(f"{len(card['skills'])} "), card["description"][:40])
    ok("F16 every skill names a real paid route (no prose-only services)",
       all(_a2a_route_price(s) == s["price_usd"] for s in card["skills"]))
    ok("F16 card declares the audience peers must sign",
       card["extensions"]["anchor-x402:a2a"]["audience"] == a2a_svc.AUDIENCE)
    ok("F16 card states the 402 header is authoritative on price",
       "authoritative" in card["extensions"]["anchor-x402:x402"]["price_authority"])

    # F17 — the card carries a detached JWS per A2A 0.3.0 `signatures`. Its value
    # is tamper-evidence for copies that travel outside TLS (registries, mirrors,
    # a peer's cache), so what matters is that mutation is detected — a signature
    # that verifies but doesn't discriminate would be decoration.
    print("\nregressions: signed agent card")
    import copy as _copy
    import importlib.util as _ilu
    from cryptography.hazmat.primitives import hashes as _h
    from cryptography.hazmat.primitives.asymmetric import ec as _ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature as _dss
    from cryptography.hazmat.primitives.serialization import load_der_public_key as _ldpk

    _spec = _ilu.spec_from_file_location(
        "signer", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sign_agent_card.py"))
    signer = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(signer)

    signed_card = signer.load_card()
    sigs = signed_card.get("signatures") or []
    ok("F17 card carries a signature", len(sigs) == 1, f"{len(sigs)} signatures")
    if sigs:
        prot = json.loads(signer.b64u_decode(sigs[0]["protected"]).decode())
        entry = next(k for k in signed_card["extensions"]["anchor-x402:a2a"]["card_signing_keys"]
                     if k["key_id"] == prot["kid"])
        pubkey = _ldpk(base64.b64decode(entry["public_key_der_base64"]))
        raw_sig = signer.b64u_decode(sigs[0]["signature"])
        der_sig = _dss(int.from_bytes(raw_sig[:32], "big"), int.from_bytes(raw_sig[32:], "big"))

        def card_verifies(c):
            try:
                pubkey.verify(der_sig, signer.signing_input(c, prot), _ec.ECDSA(_h.SHA256()))
                return True
            except Exception:
                return False

        ok("F17 signature is ES256 over a JCS-canonicalized payload", prot.get("alg") == "ES256")
        ok("F17 pristine card verifies against its published key", card_verifies(signed_card))
        ok("F17 raw signature is JWS-shaped (64 bytes, not DER)", len(raw_sig) == 64, str(len(raw_sig)))

        for label, mutate in {
            "price raised": lambda c: c["skills"][0].__setitem__("price_usd", 9.99),
            "endpoint swapped": lambda c: c["skills"][0].__setitem__("url", "https://evil.example/x"),
            "request key swapped": lambda c: c["extensions"]["anchor-x402:a2a"]["keys"][0]
                .__setitem__("public_key_der_base64", "MCowBQYDK2VwAyEA" + "A" * 28 + "="),
            "audience redirected": lambda c: c["extensions"]["anchor-x402:a2a"]
                .__setitem__("audience", "https://evil.example"),
            "retired key reactivated": lambda c: c["extensions"]["agoragentic:federation"]
                .__setitem__("status", "active"),
        }.items():
            mutated = _copy.deepcopy(signed_card)
            mutate(mutated)
            ok(f"F17 tamper detected: {label}", not card_verifies(mutated))

    # F18 — the Agoragentic pilot key stayed published as active long after the
    # pilot closed. It is software-held, so unlike the request key it is
    # extractable; an active claim we could not back was live attack surface.
    fed = signed_card["extensions"]["agoragentic:federation"]
    ok("F18 pilot key is published as retired", fed["status"] == "retired", fed["status"])
    ok("F18 pilot key consent flags are withdrawn",
       fed["capability_exchange"] is False and fed["federation_consent"] is False)
    a2a_svc._cards[PEER] = (time.time() + 3600, {"extensions": {"f": {
        "key_id": "anchor-pilot-2026-01", "public_key_der_base64": der_b64(_key),
        "status": "retired"}}})
    expect_error(client, envelope("peer/hello", key_id="anchor-pilot-2026-01"),
                 a2a_svc.ERR_PEER_CARD, "F18 a retired key is refused by our own reader")
    prime_card()

    print("\nreceipt root anchoring")
    from services import a2a_cron

    q4 = call(client, envelope("peer/quote", {"skill": first}))["result"]
    r4 = call(client, envelope("peer/receipt", {"exchange_id": q4["exchange_id"],
              "settlement": {"tx": "0xROOTTEST", "rail": "base"}}))["result"]

    real_chain, chain_calls = a2a_cron.anchor_svc.anchor_dual_chain, []
    a2a_cron.anchor_svc.anchor_dual_chain = lambda root: (
        chain_calls.append(root), {"base_tx": "0xbase", "solana_tx": "solsig"})[1]
    try:
        res = a2a_cron.anchor_receipt_root_handler({"a2a_root": True})
        ok("F12 root job anchors the live receipt set",
           res.get("anchored", 0) >= 1 and len(chain_calls) == 1, json.dumps(res)[:140])
        ok("F12 root written on-chain is bare 64-char hex",
           len(chain_calls[0]) == 64 and all(c in "0123456789abcdef" for c in chain_calls[0]),
           chain_calls[0])
        # Captured now: the retry test below deletes this record deliberately.
        root_rec = a2a_svc.get_root(chain_calls[0])

        # Content-addressed root ⇒ an unchanged set must not re-anchor.
        res2 = a2a_cron.anchor_receipt_root_handler({"a2a_root": True})
        ok("F12 unchanged receipt set is not re-anchored (idempotent)",
           res2.get("anchored") == 0 and res2.get("reason") == "unchanged"
           and len(chain_calls) == 1, json.dumps(res2)[:140])

        # A failing chain write must leave no root record, so the next run retries.
        a2a_cron.anchor_svc.anchor_dual_chain = lambda root: (_ for _ in ()).throw(RuntimeError("rpc down"))
        a2a_svc._local_store.pop(f"root#{chain_calls[0]}", None)
        res3 = a2a_cron.anchor_receipt_root_handler({"a2a_root": True})
        ok("F12 failed anchor records no root, so it retries next run",
           res3.get("reason") == "anchor_failed" and a2a_svc.get_root(chain_calls[0]) is None,
           json.dumps(res3)[:140])
    finally:
        a2a_cron.anchor_svc.anchor_dual_chain = real_chain

    # The proof must ride alongside the signed bytes, not inside them: the root is
    # anchored after the receipt was signed, so embedding it would void the signature.
    anchored = call(client, envelope("peer/receipt", {"exchange_id": q4["exchange_id"]}))["result"]
    ok("F12 receipt now carries on-chain proof", anchored.get("anchor", {}).get("root") is not None,
       json.dumps(anchored.get("anchor"))[:140])
    ok("F12 anchor did not change the receipt digest", anchored["digest"] == r4["digest"])
    unsigned = {k: v for k, v in anchored.items() if k not in
                ("digest", "signature", "signature_algorithm", "key_id", "signed", "anchor")}
    ok("F12 signed payload still recomputes to the same digest (anchor excluded)",
       a2a_svc.digest_of(unsigned) == anchored["digest"],
       f"{a2a_svc.digest_of(unsigned)} vs {anchored['digest']}")

    # F13 — domain separation: one key signs three artifact kinds, each self-identifying.
    print("\ndomain separation")
    ok("F13 quote declares its type", q4.get("type") == a2a_svc.TYPE_QUOTE)
    ok("F13 receipt declares its type", r4.get("type") == a2a_svc.TYPE_RECEIPT)
    ok("F13 root declares its type", (root_rec or {}).get("type") == a2a_svc.TYPE_RECEIPT_ROOT,
       str((root_rec or {}).get("type")))
    ok("F13 the three types are distinct",
       len({a2a_svc.TYPE_QUOTE, a2a_svc.TYPE_RECEIPT, a2a_svc.TYPE_RECEIPT_ROOT}) == 3)
    # The type is inside the signed bytes, so swapping it invalidates the signature.
    swapped = dict(unsigned, type=a2a_svc.TYPE_RECEIPT_ROOT)
    ok("F13 changing the type breaks the digest (it is signed, not decorative)",
       a2a_svc.digest_of(swapped) != anchored["digest"])

    print("\ncard on the API origin")
    r = client.get("/.well-known/agent-card.json")
    ok("GET /.well-known/agent-card.json serves the card", r.status_code == 200
       and r.json().get("extensions", {}).get("anchor-x402:a2a") is not None, str(r.status_code))
    ok("HEAD works for link previews", client.head("/.well-known/agent-card.json").status_code == 200)

    print("\nno money path")
    ok("/v1/a2a is not a metered route", "POST /v1/a2a" not in __import__("app").x402_routes)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print(f"all {_n} envelopes exercised — a2a door OK")


if __name__ == "__main__":
    main()
