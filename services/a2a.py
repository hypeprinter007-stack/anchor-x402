"""Agent-to-agent door — signed JSON-RPC, no human in the loop.

A peer authenticates with an Ed25519 key published in its own agent card at
`<origin>/.well-known/agent-card.json`. We fetch that card, take the key, and
verify a detached signature over the canonical digest of the request. Trust
bootstraps from DNS + TLS: no API keys, no registration, no out-of-band
handoff, so nothing in the path requires a human to be awake.

Authorization is a standing policy (`data/a2a-policy.json`) rather than a
per-deal mandate. Free methods are open to any verified peer; paid work is
authorized by x402 payment on the existing metered routes, which is already
human-free. This endpoint therefore never moves money — it does identity,
capability discovery, quoting, and receipts, and money keeps flowing through
the one already-audited payment path.

Key separation is deliberate and load-bearing: the A2A key proves *identity*
only. It is not the treasury key, not the CDP key, and not the Agoragentic
federation pilot key. A stolen A2A key buys an attacker signed
`capabilities/list` calls and nothing else.

Signature construction is byte-identical to scripts/federation-sign.py
(canonical JSON -> ascii "sha256:<hex>" -> detached Ed25519), so one signer
covers both this door and the Agoragentic pilot.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    load_der_public_key,
    load_pem_private_key,
)

from services import secrets as secrets_mod

log = logging.getLogger("anchor.a2a")

# Our own extension namespace. Deliberately not `agoragentic:federation` —
# that block stays scoped to their pilot. Advertising a general-purpose key
# under a partner's namespace would imply their spec and their endorsement to
# every third-party agent that reads the card.
NAMESPACE = "anchor-x402:a2a"
KEY_ID = "anchor-a2a-2026-01"

METHODS = ("peer/hello", "capabilities/list", "peer/quote", "peer/receipt")

_ROOT = os.path.dirname(os.path.dirname(__file__))
_POLICY_PATH = os.path.join(_ROOT, "data", "a2a-policy.json")
_CARD_PATH = os.path.join(_ROOT, "docs", ".well-known", "agent-card.json")

_CARD_TIMEOUT = 5
_CARD_MAX_BYTES = 256 * 1024
_CARD_CACHE_TTL = 3600

# JSON-RPC application error codes (server-defined range).
ERR_SIGNATURE = -32001
ERR_REPLAY = -32002
ERR_POLICY = -32003
ERR_PEER_CARD = -32004
ERR_NOT_FOUND = -32005
ERR_UNAVAILABLE = -32006
ERR_PARAMS = -32602
ERR_METHOD = -32601

# Peers may only assert a settlement reference in these fields, each a short
# scalar. Without the allowlist, peer/receipt is a signing oracle over
# arbitrary nested JSON: our key would attest to text the peer authored.
SETTLEMENT_FIELDS = ("tx", "rail", "asset", "amount", "payer")
SETTLEMENT_MAX_LEN = 120
EXCHANGE_ID_RE = re.compile(r"^ax-[0-9a-f]{24}$")

# Domain separation for everything our key signs. One key now signs three kinds
# of artifact, and without a type inside the signed bytes they are distinguished
# only by their field names — fine against SHA-256, but it leaves a verifier free
# to accept the wrong kind. Carried inside the payload rather than as a digest
# prefix so the published verification recipe (canonicalize the payload minus the
# signature fields) keeps working unchanged.
TYPE_QUOTE = "a2a.quote.v1"
TYPE_RECEIPT = "a2a.receipt.v1"
TYPE_RECEIPT_ROOT = "a2a.receipt-root.v1"

# RFC 6598 carrier-grade NAT — ipaddress does not report this as private.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class A2AError(Exception):
    """Maps onto a JSON-RPC error object."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# canonical form + digest
# --------------------------------------------------------------------------

def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(obj).encode()).hexdigest()


def request_digest(*, method: str, origin: str, nonce: str, exp: int, body: Any) -> str:
    """The exact bytes a peer signs. Every field that changes the meaning of
    the call is covered: method (so a signature for `peer/hello` cannot be
    replayed onto `peer/quote`), origin (so it cannot be reattributed to
    another peer), nonce + exp (replay window), body (arguments).

    Note the body is re-canonicalized from the parsed JSON, so float values
    would be a cross-language interop hazard (JS emits `1`, Python `1.0`).
    Documented in llms.txt: bodies stay strings/ints/bools. No current method
    takes a number.
    """
    return digest_of(
        {"body": body, "exp": exp, "method": method, "nonce": nonce, "origin": origin}
    )


# --------------------------------------------------------------------------
# policy + our own card
# --------------------------------------------------------------------------

_policy: dict[str, Any] | None = None
_card: dict[str, Any] | None = None


def policy() -> dict[str, Any]:
    global _policy
    if _policy is None:
        with open(_POLICY_PATH) as f:
            _policy = json.load(f)
        _warn_on_dead_denials(_policy)
    return _policy


def _warn_on_dead_denials(pol: dict[str, Any]) -> None:
    """denied_origins is compared against the canonical origin form, so an entry
    typed with a trailing slash, uppercase, or a port silently blocks nothing.
    A denial that does not deny is worse than no denial, so say so at load."""
    for entry in (pol.get("inbound", {}).get("denied_origins") or []):
        try:
            if _safe_origin(str(entry)) != entry:
                raise ValueError("non-canonical")
        except Exception:
            log.error(
                "denied_origins entry %r is not a canonical https origin and will "
                "block nothing — use the exact form https://host",
                str(entry)[:80],
            )


def our_card() -> dict[str, Any]:
    global _card
    if _card is None:
        with open(_CARD_PATH) as f:
            _card = json.load(f)
    return _card


def inbound_policy() -> dict[str, Any]:
    return policy()["inbound"]


# --------------------------------------------------------------------------
# peer identity — the card is the keyring
# --------------------------------------------------------------------------

def _is_public_address(addr: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """Reject anything that is not a routable public unicast address.

    `is_private` covers RFC 1918, loopback, and IPv4-mapped IPv6 (verified:
    ::ffff:169.254.169.254 reports private). It does NOT cover RFC 6598 carrier
    NAT (100.64.0.0/10), which is why that range is listed explicitly. Teredo
    and 6to4 are unwrapped because both can embed a private IPv4 address inside
    an otherwise public-looking IPv6 address.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        for embedded in (addr.ipv4_mapped, addr.sixtofour,
                         (addr.teredo[1] if addr.teredo else None)):
            if embedded is not None and not _is_public_address(embedded):
                return False
    if addr.version == 4 and addr in _CGNAT:
        return False
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


def _resolve_public(host: str) -> set[str]:
    """Resolve the peer host and require EVERY answer to be public.

    Rejecting IP literals in the origin string was never sufficient: a peer can
    simply publish `evil.example. A 169.254.169.254` and the hostname check
    passes untouched. All answers must be public, not merely the first — a
    round-robin set mixing one public and one private address would otherwise be
    a coin flip.
    """
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise A2AError(ERR_PEER_CARD, f"peer origin {host} does not resolve") from e
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise A2AError(ERR_PEER_CARD, f"peer origin {host} does not resolve")
    for text in sorted(addrs):
        if not _is_public_address(ipaddress.ip_address(text)):
            raise A2AError(
                ERR_PEER_CARD,
                f"peer origin {host} resolves to a non-public address — "
                "agent cards must be reachable on the public internet",
            )
    return addrs


def _assert_connected_peer(response, allowed: set[str], origin: str) -> None:
    """Best-effort check that the socket landed on one of the vetted addresses.

    Deliberately advisory, not mandatory. urllib3 releases the socket at
    header-parse time whenever the server declines keep-alive — HTTP/1.0
    responses, `Connection: close`, many CDNs — so `conn.sock` is None for a
    perfectly good response with a fully readable body. An earlier version
    refused in that case, which deterministically locked out honest peers whose
    card host happens not to keep connections alive.

    The load-bearing defenses are elsewhere: _resolve_public blocks the actual
    easy attack (a static private A record), and TLS verification prevents any
    content disclosure, since an internal host cannot present a valid
    certificate for the peer's name. This check only adds detection for the
    narrow rebinding race, and only when the socket is still attached.
    """
    conn = getattr(response.raw, "connection", None) or getattr(response.raw, "_connection", None)
    sock = getattr(conn, "sock", None)
    peer = None
    if sock is not None:
        try:
            peer = sock.getpeername()[0].split("%")[0]  # strip IPv6 scope id
        except Exception:
            peer = None
    if peer is None:
        log.info("connected address unreadable for %s; relying on pre-resolution", origin)
        return
    if peer not in allowed or not _is_public_address(ipaddress.ip_address(peer)):
        raise A2AError(
            ERR_PEER_CARD,
            f"peer origin {origin} connected to an address outside its resolved public set",
        )


def _safe_origin(origin: str) -> str:
    """A peer names the origin we then fetch, so this is an SSRF sink.
    Constrain it to a bare public https origin before any request goes out.
    Address-level validation happens in peer_card via _resolve_public.
    """
    u = urlparse(origin or "")
    if u.scheme != "https" or not u.hostname or u.path not in ("", "/") or u.query or u.username:
        raise A2AError(ERR_PARAMS, "origin must be a bare https origin, e.g. https://peer.example")
    if u.port not in (None, 443):
        raise A2AError(ERR_PARAMS, "origin must use the default https port")
    # The DNS root label makes `peer.example.` and `peer.example` the same host
    # but two different strings. Left unnormalized, one agent gets two
    # identities and a denied_origins entry is evadable by adding a dot.
    host = u.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise A2AError(ERR_PARAMS, "origin must be a DNS name, not an IP literal")
    # RFC 1123 hostname charset. Excluding underscores also keeps a peer from
    # registering a name that collides with a log-metric filter token.
    if not re.fullmatch(r"[a-z0-9.-]+", host) or len(host) > 253:
        raise A2AError(ERR_PARAMS, "origin host must be a plain DNS hostname (a-z, 0-9, dot, hyphen)")
    labels = host.split(".")
    if len(labels) < 2 or any(not lbl or len(lbl) > 63 for lbl in labels):
        raise A2AError(ERR_PARAMS, "origin must be a public DNS name with non-empty labels")
    if host.endswith((".local", ".internal", ".localhost", ".home.arpa")):
        raise A2AError(ERR_PARAMS, "origin must be a public DNS name")
    canonical_origin = f"https://{host}"
    if origin != canonical_origin:
        # The peer signs the origin string it sent, and we verify against the
        # normalized one, so anything other than an exact match would surface
        # as a baffling signature failure. Say what is actually wrong instead.
        raise A2AError(
            ERR_PARAMS,
            f"origin must be exactly {canonical_origin} — no trailing slash, "
            "no uppercase, no port, no path",
        )
    return canonical_origin


_cards: dict[str, tuple[float, dict[str, Any]]] = {}
_card_failures: dict[str, tuple[float, str]] = {}
_fetch_times: list[float] = []

# A card fetch necessarily precedes signature verification (we need the key to
# verify), so an unauthenticated caller chooses the URL we retrieve. Two limits
# keep that from being a usable scanner or amplifier: failures are cached as
# hard as successes, and each container will not exceed this many fetches per
# minute regardless of how many distinct origins are offered.
_FETCH_BUDGET_PER_MIN = 20
_FAILURE_CACHE_TTL = 60


def peer_card(origin: str) -> dict[str, Any]:
    """Fetch and cache a peer's agent card. Cache is per Lambda container, so
    a peer's key revocation takes effect within an hour of them editing their
    card — revocation stays entirely in the peer's hands."""
    origin = _safe_origin(origin)
    hit = _cards.get(origin)
    if hit and hit[0] > time.time():
        return hit[1]
    failed = _card_failures.get(origin)
    if failed and failed[0] > time.time():
        raise A2AError(ERR_PEER_CARD, failed[1])

    now = time.time()
    _fetch_times[:] = [t for t in _fetch_times if t > now - 60]
    if len(_fetch_times) >= _FETCH_BUDGET_PER_MIN:
        raise A2AError(ERR_PEER_CARD, "card fetch budget exhausted; retry shortly")
    _fetch_times.append(now)

    url = f"{origin}/.well-known/agent-card.json"

    def fail(msg: str) -> A2AError:
        _card_failures[origin] = (time.time() + _FAILURE_CACHE_TTL, msg)
        return A2AError(ERR_PEER_CARD, msg)

    # Validate every address the name resolves to before connecting. Routed
    # through fail() so a resolution refusal is cached like any other failure:
    # otherwise one repeated bad origin drains the shared per-minute budget and
    # locks out every peer not already cached.
    try:
        allowed = _resolve_public(origin.removeprefix("https://"))
    except A2AError as e:
        raise fail(e.message) from e

    try:
        # Context-managed: an unclosed streamed response leaks a connection per
        # fetch for the life of the container.
        with requests.get(
            url,
            timeout=_CARD_TIMEOUT,
            stream=True,
            allow_redirects=False,
            headers={"accept": "application/json", "user-agent": "anchor-x402-a2a/1"},
        ) as r:
            # requests resolves the name again, so _resolve_public alone leaves a
            # rebinding window: a 0-TTL record can answer public for our check and
            # private for the real connection. Checked before any body is read.
            _assert_connected_peer(r, allowed, origin)
            r.raise_for_status()
            raw = r.raw.read(_CARD_MAX_BYTES + 1, decode_content=True)
    except A2AError as e:
        raise fail(e.message) from e
    except Exception as e:
        # The exception text can embed the peer server's HTTP reason phrase, i.e.
        # a string that server chose — log_safe rather than safe_echo, because
        # this is the one message a peer operator actually needs to read and
        # ?-mangling an SSL error makes it useless.
        raise fail(f"peer card unreachable at {url}: {log_safe(e, 140)}") from e
    if len(raw) > _CARD_MAX_BYTES:
        raise fail("peer card exceeds 256 KB")
    try:
        card = json.loads(raw)
    except ValueError as e:
        raise fail(f"peer card at {url} is not valid JSON") from e
    if not isinstance(card, dict):
        raise fail(f"peer card at {url} is not a JSON object")

    _cards[origin] = (time.time() + _CARD_CACHE_TTL, card)
    _card_failures.pop(origin, None)
    return card


def keys_in_block(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Both card shapes are accepted: a `keys` array (ours — a single key can
    never be rotated without a gap, so the array is the shape that makes
    publishing old and new simultaneously possible) and a flat
    key_id/public_key_der_base64 pair, which is what the Agoragentic pilot
    block and most peers publish today."""
    if isinstance(block.get("keys"), list):
        return [k for k in block["keys"] if isinstance(k, dict)]
    if block.get("key_id"):
        return [block]
    return []


def peer_key(origin: str, key_id: str) -> Ed25519PublicKey:
    """Locate key_id anywhere in the peer's `extensions` block.

    Namespace-agnostic on purpose: peers publish under their own vendor prefix
    (ours is NAMESPACE, Agoragentic's is `agoragentic:federation`) and a reader
    that insisted on our naming would force every peer to adopt it. Strict
    writer, tolerant reader.
    """
    ext = peer_card(origin).get("extensions")
    for block in (ext.values() if isinstance(ext, dict) else []):
        if not isinstance(block, dict):
            continue
        for entry in keys_in_block(block):
            if entry.get("key_id") != key_id:
                continue
            # A peer marking a key retired is revoking it; honour that rather
            # than accepting any key it has ever published.
            status = entry.get("status", "active")
            if status != "active":
                # status comes from the peer's own card, so it is arbitrary JSON.
                raise A2AError(
                    ERR_PEER_CARD,
                    f"key {key_id} is published as {safe_echo(status, 24)}, not active",
                )
            der = entry.get("public_key_der_base64") or ""
            if not der:
                continue
            try:
                key = load_der_public_key(base64.b64decode(der))
            except Exception as e:
                raise A2AError(
                    ERR_PEER_CARD, f"key {key_id} is not a readable DER public key"
                ) from e
            if not isinstance(key, Ed25519PublicKey):
                raise A2AError(ERR_PEER_CARD, f"key {key_id} is not Ed25519")
            return key
    raise A2AError(ERR_PEER_CARD, f"peer card at {origin} publishes no active key_id {key_id}")


# --------------------------------------------------------------------------
# replay store + quote store (one DynamoDB table, TTL'd)
# --------------------------------------------------------------------------

_local_store: dict[str, tuple[int, dict[str, Any] | None]] = {}

# Counter keys this container has already seen exceed their limit, mapped to the
# end of the window they tripped in. Lets rate_check() refuse without paying for
# another write. Per-container by design: the shared counter in the table is the
# authority, this only avoids re-asking it once the answer is known.
_rate_tripped: dict[str, int] = {}


def _table_name() -> str:
    """Fail closed instead of silently degrading.

    Without the table the only replay defense is a per-container dict, and
    Lambda container fan-out makes that no defense at all: an attacker replaying
    an envelope simply lands on a cold container and succeeds. Previously a
    single missing env var downgraded replay protection to nothing with no
    warning, so refuse to serve rather than appear to work.
    """
    table = os.getenv("A2A_STATE_TABLE", "")
    if table:
        return table
    # Opt-in is required everywhere, not just in Lambda. Keying the gate on
    # AWS_LAMBDA_FUNCTION_NAME meant any other container runtime — Fargate, or
    # the gateway-shim pattern — silently fell back to a per-container dict again.
    if os.getenv("A2A_ALLOW_MEMORY_STORE") == "1":
        return ""
    log.error(
        "A2A_STATE_TABLE unset — refusing /v1/a2a traffic without durable replay "
        "protection. Set A2A_STATE_TABLE to the DynamoDB table, or "
        "A2A_ALLOW_MEMORY_STORE=1 for local development only."
    )
    raise A2AError(
        ERR_UNAVAILABLE,
        "a2a state store unavailable — refusing to serve without replay protection",
    )


def _ddb():
    import boto3

    return boto3.client("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _local_gc() -> None:
    now = int(time.time())
    for k, (exp, _) in list(_local_store.items()):
        if exp <= now:
            _local_store.pop(k, None)


def _put_once(key: str, exp: int, payload: dict[str, Any] | None = None) -> bool:
    """Conditional write. False if the key already exists."""
    table = _table_name()
    if not table:
        _local_gc()
        if key in _local_store:
            return False
        _local_store[key] = (exp, payload)
        return True

    from botocore.exceptions import ClientError

    item = {"id": {"S": key}, "ttl": {"N": str(exp)}}
    if payload is not None:
        item["payload"] = {"S": canonical(payload)}
    try:
        _ddb().put_item(
            TableName=table,
            Item=item,
            # DynamoDB's TTL sweep lags by up to 48h, so a logically expired item
            # can still be physically present. _get() honours the stored expiry, so
            # a TTL-blind condition here would let an expired-but-present record
            # block a write forever while reading as absent.
            ConditionExpression="attribute_not_exists(id) OR #t <= :now",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":now": {"N": str(int(time.time()))}},
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise
    return True


def _get(key: str) -> dict[str, Any] | None:
    table = _table_name()
    if not table:
        _local_gc()
        hit = _local_store.get(key)
        return hit[1] if hit else None
    item = _ddb().get_item(TableName=table, Key={"id": {"S": key}}).get("Item") or {}
    raw = item.get("payload", {}).get("S")
    if not raw:
        return None
    # DynamoDB TTL deletion lags by up to 48h, so honour the stored expiry here.
    if int(item.get("ttl", {}).get("N", "0")) <= int(time.time()):
        return None
    return json.loads(raw)


def _bump(key: str, exp: int) -> int:
    """Atomic counter, returning the post-increment value."""
    table = _table_name()
    if not table:
        _local_gc()
        current = (_local_store.get(key) or (exp, {"n": 0}))[1]["n"] + 1
        _local_store[key] = (exp, {"n": current})
        return current
    resp = _ddb().update_item(
        TableName=table,
        Key={"id": {"S": key}},
        UpdateExpression="ADD #n :one SET #t = if_not_exists(#t, :exp)",
        ExpressionAttributeNames={"#n": "n", "#t": "ttl"},
        ExpressionAttributeValues={":one": {"N": "1"}, ":exp": {"N": str(exp)}},
        ReturnValues="UPDATED_NEW",
    )
    return int(resp["Attributes"]["n"]["N"])


def rate_check(origin: str) -> None:
    """Durable rate limit for /v1/a2a, global and per peer.

    This exists because API Gateway cannot throttle this route: the stack exposes
    one greedy `ANY /{proxy+}` route, so a per-route throttle key matches nothing
    (confirmed against a real changeset's processed template), and throttling the
    catch-all would cap the paid endpoints too. The in-process card-fetch budget
    is per-container and so bounds nothing fleet-wide. Counting in the shared
    table is what actually caps the endpoint.

    The per-peer limit is by *claimed* origin, checked before signature
    verification — an attacker can rotate that, which is what the global limit is
    for.

    Counting costs a write, and it happens before signature verification, so an
    unauthenticated caller could otherwise drive one write per request with no
    valid key — an unbounded cost vector, given that API Gateway cannot throttle
    this route either. Once a counter trips, the container remembers it for the
    rest of that window and refuses with no further writes. The global counter is
    bumped first, so once it trips no per-peer write happens at all: total writes
    settle at roughly the limit itself plus one per container per window.
    """
    pol = inbound_policy()
    window = pol["rate_window_s"]
    now = int(time.time())
    bucket = now // window
    window_end = (bucket + 1) * window
    expiry = (bucket + 2) * window

    for stale, until in list(_rate_tripped.items()):
        if until <= now:
            _rate_tripped.pop(stale, None)

    for key, limit, scope in (
        (f"rate#all#{bucket}", pol["rate_limit_global"], "global"),
        (f"rate#{origin}#{bucket}", pol["rate_limit_per_peer"], "per-peer"),
    ):
        refusal = f"rate limit exceeded ({scope}: {limit} per {window}s) — retry shortly"
        # Identical refusal either way, so a caller cannot tell a remembered trip
        # from a freshly counted one.
        if _rate_tripped.get(key, 0) > now:
            raise A2AError(ERR_POLICY, refusal)
        if _bump(key, expiry) > limit:
            _rate_tripped[key] = window_end
            raise A2AError(ERR_POLICY, refusal)


def claim_nonce(origin: str, nonce: str, exp: int) -> None:
    """First write wins, scoped to the peer.

    The origin is part of the key deliberately. A global nonce namespace let any
    agent that could publish a card burn another peer's nonce values — and since
    the protocol only requires 8-128 characters, a peer using a counter was
    trivially locked out by a stranger. Scoping makes one peer's nonce choices
    unable to affect another's.

    The record outlives both the envelope (exp) and any quote derived from this
    nonce: exchange_id is a hash of the nonce, so if the nonce were reusable
    while its quote was still live, a second quote would silently collide with
    the stored first one.
    """
    hold_until = max(exp + 60, int(time.time()) + inbound_policy()["quote_ttl_s"] + 60)
    if not _put_once(f"nonce#{origin}#{nonce}", hold_until):
        raise A2AError(ERR_REPLAY, "nonce already used")


def put_quote(exchange_id: str, quote: dict[str, Any], ttl_s: int) -> None:
    if not _put_once(f"quote#{exchange_id}", int(time.time()) + ttl_s, quote):
        # Unreachable while nonces outlive quotes (see claim_nonce). Loud rather
        # than silent, because the failure mode is a signed quote whose stored
        # record says something else.
        log.error("quote %s already exists — signed quote may disagree with stored record", exchange_id)


def get_quote(exchange_id: str) -> dict[str, Any] | None:
    return _get(f"quote#{exchange_id}")


def safe_echo(value: Any, limit: int = 48) -> str:
    """Sanitize peer-supplied text that ends up in an error message, and so in a
    log line. The CloudWatch metric filters in template.yaml key on the literal
    tokens A2A_FAIL and A2A_SIGN_FAIL; a peer able to echo either — by naming a
    skill or a settlement field after one — could forge alarm signal. Dropping
    underscores makes both tokens unformable."""
    return re.sub(r"[^A-Za-z0-9.:/=-]", "?", str(value))[:limit]


def log_safe(text: Any, limit: int = 160) -> str:
    """Text bound for a log line: readable, but unable to forge the A2A_* tokens
    that template.yaml's metric filters match by substring.

    Only underscores and control characters are removed — every token contains an
    underscore, so that alone makes them unformable, and operator-facing messages
    stay legible. safe_echo() is the stricter variant for identifier-shaped
    values interpolated into messages.
    """
    return re.sub(r"[\x00-\x1f\x7f]", "", str(text).replace("_", "-"))[:limit]


def clean_key_id(raw: Any) -> str:
    """Bound the peer's key_id before it reaches anything we sign.

    key_id is peer-authored free text that lands inside signed quotes and
    receipts, so it is the same signing-oracle channel as `settlement`: a peer
    could publish a key_id reading "anchor-x402 hereby certifies ..." and have
    our KMS key attest to it.

    Underscore and `#` are permitted despite both appearing in the CloudWatch
    metric tokens: base64url JWK thumbprints contain `_` about half the time and
    DID key references contain `#`, so excluding them would lock out ordinary
    peers. Token forgery is already prevented at the log sink by log_safe(),
    which is the right place for it — this function's job is bounding what we
    sign, not sanitizing logs.
    """
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9._:#-]{1,64}", raw):
        raise A2AError(
            ERR_PARAMS,
            "key_id must be 1-64 characters of letters, digits, dot, underscore, "
            "colon, hash or hyphen",
        )
    return raw


def clean_exchange_id(raw: Any) -> str:
    """Validate before it becomes a store key. Unbounded peer input reaching a
    DynamoDB key exceeds the 2 KB key limit and surfaces as an internal error
    rather than a clear refusal."""
    if not isinstance(raw, str) or not EXCHANGE_ID_RE.fullmatch(raw):
        raise A2AError(ERR_PARAMS, "exchange_id must match ^ax-[0-9a-f]{24}$")
    return raw


def clean_settlement(raw: Any) -> dict[str, str] | None:
    """Constrain what a peer can put inside something we sign.

    Previously this was arbitrary nested JSON up to the body cap, which made
    peer/receipt a signing oracle: our key would attest to a blob whose text the
    peer chose. Now it is a fixed set of short scalars, so the signature covers
    only a settlement reference.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise A2AError(ERR_PARAMS, "settlement must be an object")
    unknown = sorted(set(raw) - set(SETTLEMENT_FIELDS))
    if unknown:
        raise A2AError(
            ERR_PARAMS,
            f"settlement fields not permitted: {', '.join(safe_echo(u, 24) for u in unknown[:5])} "
            f"(allowed: {', '.join(SETTLEMENT_FIELDS)})",
        )
    out: dict[str, str] = {}
    for field, value in raw.items():
        # Floats are accepted because our own quote hands back price_usd: 0.005,
        # and the natural receipt call echoes it into settlement.amount — an
        # earlier version refused exactly the value we had just issued. Stored as
        # text so the signed form is unambiguous across languages.
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise A2AError(ERR_PARAMS, f"settlement.{field} must be a string or number")
        text = repr(value) if isinstance(value, float) else str(value)
        if len(text) > SETTLEMENT_MAX_LEN:
            raise A2AError(ERR_PARAMS, f"settlement.{field} exceeds {SETTLEMENT_MAX_LEN} characters")
        out[field] = text
    return out


def put_receipt(exchange_id: str, receipt: dict[str, Any], ttl_s: int) -> dict[str, Any]:
    """First-write-wins, returning whichever receipt won.

    A receipt is dispute evidence, so exactly one may exist per exchange.
    Without this a peer could mint any number of validly signed receipts for one
    quote, each asserting a different settlement, and none authoritative.
    """
    key = f"receipt#{exchange_id}"
    if _put_once(key, int(time.time()) + ttl_s, receipt):
        return receipt
    stored = _get(key)
    if stored is None:
        # Lost the race but cannot read the winner. Returning our own copy would
        # put two differently-signed receipts for one exchange into the world,
        # which is the exact defect this function exists to prevent.
        raise A2AError(
            ERR_UNAVAILABLE,
            "a receipt for this exchange is being written — retry",
        )
    return stored


def get_receipt(exchange_id: str) -> dict[str, Any] | None:
    return _get(f"receipt#{exchange_id}")


def list_receipts() -> list[dict[str, Any]]:
    """Every receipt currently live in the store, for the daily root job."""
    table = _table_name()
    if not table:
        _local_gc()
        return [v[1] for k, v in _local_store.items()
                if k.startswith("receipt#") and isinstance(v[1], dict)]
    out: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "TableName": table,
        "FilterExpression": "begins_with(id, :p) AND #t > :now",
        "ExpressionAttributeNames": {"#t": "ttl"},
        "ExpressionAttributeValues": {
            ":p": {"S": "receipt#"}, ":now": {"N": str(int(time.time()))},
        },
    }
    while True:
        page = _ddb().scan(**kwargs)
        for item in page.get("Items", []):
            raw = item.get("payload", {}).get("S")
            if raw:
                out.append(json.loads(raw))
        if not page.get("LastEvaluatedKey"):
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def compute_receipt_root(digests: list[str]) -> str:
    """Bare 64-char hex digest over the sorted member list, which is what
    anchor_dual_chain writes on-chain.

    Deliberately not a Merkle tree: at these volumes the root record carries
    every member, so inclusion is proven by recomputation rather than by a
    compact proof, and a tree would be machinery without a use.
    """
    return hashlib.sha256(canonical(sorted(digests)).encode()).hexdigest()


def put_root(root_hex: str, record: dict[str, Any], ttl_s: int) -> bool:
    """Content-addressed by the root itself, which makes the daily job naturally
    idempotent: an unchanged receipt set produces an identical root and is
    skipped, while a grown set produces a new root covering the superset."""
    return _put_once(f"root#{root_hex}", int(time.time()) + ttl_s, record)


def get_root(root_hex: str) -> dict[str, Any] | None:
    return _get(f"root#{root_hex}")


def put_anchored(receipt_digest: str, proof: dict[str, Any], ttl_s: int) -> None:
    """Reverse index so peer/receipt can attach on-chain proof with one read
    instead of scanning roots. First write wins, so the earliest root covering a
    receipt is the one cited."""
    _put_once(f"anchored#{receipt_digest}", int(time.time()) + ttl_s, proof)


def get_anchored(receipt_digest: str) -> dict[str, Any] | None:
    return _get(f"anchored#{receipt_digest}")


# --------------------------------------------------------------------------
# inbound verification
# --------------------------------------------------------------------------

def verify(method: str, params: dict[str, Any]) -> str:
    """Verify a signed inbound envelope. Returns the peer's origin.

    Raises A2AError for every failure mode; callers map that onto JSON-RPC.
    """
    pol = inbound_policy()

    if not isinstance(params, dict):
        raise A2AError(ERR_PARAMS, "params must be an object")
    for field in ("origin", "key_id", "nonce", "exp", "signature"):
        if field not in params:
            raise A2AError(ERR_PARAMS, f"params.{field} is required")

    body = params.get("body")
    if len(canonical(body).encode()) > pol["max_body_bytes"]:
        raise A2AError(ERR_PARAMS, f"body exceeds max_body_bytes ({pol['max_body_bytes']})")

    try:
        exp = int(params["exp"])
    except (TypeError, ValueError) as e:
        raise A2AError(ERR_PARAMS, "exp must be a unix timestamp") from e
    now = int(time.time())
    if exp <= now:
        raise A2AError(ERR_PARAMS, "envelope expired")
    if exp > now + pol["nonce_ttl_s"]:
        raise A2AError(
            ERR_PARAMS,
            f"exp must be within {pol['nonce_ttl_s']}s — longer windows would outlive the replay record",
        )

    nonce = str(params["nonce"])
    if not 8 <= len(nonce) <= 128:
        raise A2AError(ERR_PARAMS, "nonce must be 8-128 characters")

    origin = _safe_origin(str(params["origin"]))
    if origin in (pol.get("denied_origins") or []):
        # The one lever a human may need in a hurry: block a misbehaving peer
        # by editing the policy file, no code change or redeploy of logic.
        raise A2AError(ERR_POLICY, f"origin {origin} is denied by policy")
    # Before peer_key(), which may fetch the peer's card — that outbound request
    # is the expensive, abusable part, so the limit has to precede it.
    rate_check(origin)
    key = peer_key(origin, clean_key_id(params["key_id"]))
    expected = request_digest(method=method, origin=origin, nonce=nonce, exp=exp, body=body)
    try:
        key.verify(base64.b64decode(params["signature"]), expected.encode("ascii"))
    except (InvalidSignature, ValueError, TypeError) as e:
        raise A2AError(ERR_SIGNATURE, "signature does not verify over the canonical digest") from e

    # Only burn the nonce once the signature holds. Claiming it earlier would
    # let an unauthenticated flood consume a peer's nonces and lock it out.
    claim_nonce(origin, nonce, exp)
    return origin


# --------------------------------------------------------------------------
# our signature (optional — inbound verification never needs it)
# --------------------------------------------------------------------------

_signing_key: Any = None
_signing_loaded = False


def our_block() -> dict[str, Any]:
    return (our_card().get("extensions") or {}).get(NAMESPACE, {})


def our_keys() -> list[dict[str, Any]]:
    return keys_in_block(our_block())


def active_key_id() -> str:
    """Read from the card rather than hardcoded, so rotating is one card edit
    (publish the new key active, mark the old retired) with no code change."""
    for entry in our_keys():
        if entry.get("status", "active") == "active" and entry.get("key_id"):
            return str(entry["key_id"])
    return KEY_ID


def signing_key():
    """Local Ed25519 private key for development only — KMS is the production
    path (see sign()). Read from the composite runtime secret under
    `a2a_signing_key_pem`, or env A2A_SIGNING_KEY_PEM."""
    global _signing_key, _signing_loaded
    if _signing_loaded:
        return _signing_key
    _signing_loaded = True
    pem = secrets_mod.get("a2a_signing_key_pem", env_fallback="A2A_SIGNING_KEY_PEM")
    if pem:
        try:
            _signing_key = load_pem_private_key(pem.encode(), password=None)
        except Exception as e:
            log.warning("a2a signing key unreadable: %s", e)
    return _signing_key


def _kms_sign(digest: str) -> bytes | None:
    """Sign with the KMS-held identity key. Verified interoperable: KMS returns
    a raw 64-byte RFC 8032 signature and GetPublicKey returns a standard
    Ed25519 SPKI, so peers verify with any stock library — nothing about our
    key custody reaches the wire.

    MessageType=RAW with ED25519_SHA_512 is plain Ed25519 (which hashes
    internally). ED25519_PH_SHA_512 is the prehashed variant and would NOT
    verify against a standard verifier here.
    """
    key_id = os.getenv("A2A_KMS_KEY_ID", "")
    if not key_id:
        # A dropped env var is a likelier misconfiguration than a KMS exception,
        # and it took the same silent path to `signed: false`. Only alarm when
        # deployed — locally, no KMS key is the normal state.
        if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
            log.error("A2A_KMS_KEY_ID unset in Lambda — quotes and receipts will be unsigned")
            print("A2A_SIGN_FAIL " + json.dumps(
                {"ts": int(time.time()), "err": "A2A-KMS-KEY-ID unset"}, separators=(",", ":")))
        return None
    try:
        import boto3

        return boto3.client("kms", region_name=os.getenv("AWS_REGION", "us-east-1")).sign(
            KeyId=key_id,
            Message=digest.encode("ascii"),
            MessageType="RAW",
            SigningAlgorithm="ED25519_SHA_512",
        )["Signature"]
    except Exception as e:
        # Degrade to an unsigned payload rather than failing the peer's call: a
        # KMS outage should cost non-repudiation, not availability. But that
        # degradation hides a misconfiguration — a wrong IAM grant would ship
        # `signed: false` forever — so emit a dedicated line the metric filter
        # in template.yaml alarms on. Silent graceful failure is the bug.
        log.error("kms sign failed, returning unsigned payload: %s", e)
        print("A2A_SIGN_FAIL " + json.dumps(
            {"ts": int(time.time()), "key": key_id, "err": str(e)[:160]}, separators=(",", ":")))
        return None


def sign_digest(digest: str) -> bytes | None:
    """Raw 64-byte Ed25519 signature over the ASCII digest — KMS in production,
    local PEM for dev, None if neither is reachable. Shared by inbound replies
    (sign()) and the outbound client (scripts/a2a-call.py) so both paths always
    use the same key."""
    signature = _kms_sign(digest)
    if signature is None:
        local = signing_key()
        signature = local.sign(digest.encode("ascii")) if local is not None else None
    return signature


def sign(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach our detached signature over the canonical digest of payload.
    `signed: false` means no key was reachable — the payload is still returned,
    just without non-repudiation."""
    out = dict(payload)
    out["digest"] = digest_of(payload)

    signature = sign_digest(out["digest"])
    if signature is None:
        out["signed"] = False
        return out

    out["signed"] = True
    out["signature_algorithm"] = "ed25519"
    out["key_id"] = active_key_id()
    out["signature"] = base64.b64encode(signature).decode()
    return out


def reset_cache_for_testing() -> None:
    global _policy, _card, _signing_key, _signing_loaded
    _policy = _card = _signing_key = None
    _signing_loaded = False
    _cards.clear()
    _card_failures.clear()
    _fetch_times.clear()
    _local_store.clear()
    _rate_tripped.clear()
