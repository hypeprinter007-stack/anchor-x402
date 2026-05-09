"""Pydantic schemas for the anchor service.

Two ways to use the endpoint:
  1. send a pre-computed 32-byte hex hash via `hash`
  2. send arbitrary JSON via `data` and let the server compute SHA-256
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class AnchorRequest(BaseModel):
    hash: str | None = Field(
        default=None,
        description="Pre-computed 32-byte hex hash (64 chars, no 0x prefix). Mutually exclusive with `data`.",
    )
    data: Any | None = Field(
        default=None,
        description="Arbitrary JSON to be canonicalized + SHA-256'd by the server. Mutually exclusive with `hash`.",
    )
    note: str | None = Field(
        default=None,
        max_length=200,
        description="Optional 200-char note included in the response (not on-chain).",
    )

    @model_validator(mode="after")
    def _check_exclusive(self):
        if (self.hash is None) == (self.data is None):
            raise ValueError("supply exactly one of `hash` or `data`")
        if self.hash is not None and not _HEX_RE.match(self.hash):
            raise ValueError("`hash` must be 64 hex chars (32 bytes), no 0x prefix")
        return self

    def merkle_root(self) -> str:
        """Return the 64-char hex root that gets anchored."""
        if self.hash:
            return self.hash.lower()
        canonical = json.dumps(self.data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class ChainAnchor(BaseModel):
    tx: str
    explorer_url: str


class AnchorResponse(BaseModel):
    merkle_root: str = Field(description="The 64-char hex hash that was anchored.")
    base: ChainAnchor
    solana: ChainAnchor | None = Field(
        default=None,
        description="None if the Solana side failed; Base anchor still proves the hash existed.",
    )
    anchored_at: int = Field(description="Unix epoch seconds when the anchor txs were broadcast.")
    note: str | None = None


# --- /v1/screen ---


class ScreenResponse(BaseModel):
    wallet: str
    chain_inferred: str = Field(description='"ethereum" | "solana" | "unknown"')
    sanctions_match: bool
    sanctioned_lists: list[str] = Field(default_factory=list)
    risk_level: str = Field(description='"low" | "medium" | "high"')
    notes: str
    checked_at: int


# --- /v1/attest ---


class AttestRequest(BaseModel):
    input_hash: str = Field(description="64-char hex SHA-256 of the agent's input.")
    output_hash: str = Field(description="64-char hex SHA-256 of the agent's output / decision payload.")
    decision: str = Field(max_length=64, description='Free-form short label, e.g. "APPROVED", "REJECTED", "CONFIDENCE=0.93".')
    scheme: Literal["eip191", "ed25519"] = Field(description='Signature scheme: "eip191" (EVM personal_sign) or "ed25519" (Solana).')
    signature: str = Field(description="0x-prefixed hex (eip191) or base58 (ed25519).")
    signer_pubkey: str | None = Field(
        default=None,
        description="Required for ed25519 (Solana base58 pubkey). Ignored for eip191 — address is recovered.",
    )

    @model_validator(mode="after")
    def _check_hashes(self):
        if not _HEX_RE.match(self.input_hash):
            raise ValueError("`input_hash` must be 64 hex chars")
        if not _HEX_RE.match(self.output_hash):
            raise ValueError("`output_hash` must be 64 hex chars")
        if self.scheme == "ed25519" and not self.signer_pubkey:
            raise ValueError("`signer_pubkey` is required when scheme=ed25519")
        return self


class AttestResponse(BaseModel):
    merkle_root: str = Field(description="SHA-256 over the domain-separated (input_hash, output_hash, decision) — this is what gets anchored.")
    signer_verified: bool
    signer: str = Field(description="Recovered EVM address (eip191) or supplied Solana pubkey (ed25519).")
    base: ChainAnchor | None = None
    solana: ChainAnchor | None = None
    decision: str
    signed_at: int


# --- /v1/decode/tx ---


class TxDecodeRequest(BaseModel):
    chain: Literal["base", "ethereum", "solana"]
    tx_hash: str = Field(min_length=1, max_length=128)


class TxDecodeResponse(BaseModel):
    chain: str
    tx_hash: str
    block_number: int | None = None
    timestamp: int | None = None
    from_address: str | None = None
    to_address: str | None = None
    value_wei: str | None = None
    value_eth: str | None = None
    gas_used: int | None = None
    status: int | str | None = None
    input_calldata_hex: str | None = None
    native_currency: str | None = None
    slot: int | None = None
    block_time: int | None = None
    fee_lamports: int | None = None
    signers: list[str] | None = None
    program_calls: list[dict] | None = None


# --- /v1/resolve/name ---


class NameAddress(BaseModel):
    chain: str = Field(description='"ethereum" | "solana"')
    address: str
    ttl_hint_seconds: int


class NameResolveResponse(BaseModel):
    name: str
    addresses: list[NameAddress] = Field(default_factory=list)
    resolved_at: int
    registry_used: str | None = Field(default=None, description='"ENS" | "SNS" | null')
    supported_tlds: list[str]
    notes: str | None = None


# --- /v1/price/token ---


class TokenPriceResponse(BaseModel):
    symbol: str | None = None
    name: str | None = None
    contract: str | None = None
    chain: str | None = None
    usd: float
    usd_24h_change_pct: float | None = None
    market_cap_usd: float | None = None
    source: str = "coingecko"
    fetched_at: int
    age_seconds: int


# --- /v1/decode/calldata ---


class CalldataDecodeRequest(BaseModel):
    chain: Literal["ethereum", "solana"] = Field(description='"ethereum" supported; "solana" returns 400.')
    calldata_hex: str = Field(min_length=8, description="Raw EVM calldata (>=4-byte selector), with or without 0x prefix.")
    contract_address: str | None = Field(default=None, description="Optional. Reserved for future on-chain ABI lookups; currently unused.")


class DecodedParam(BaseModel):
    name: str | None = None
    type: str
    value: Any


class CalldataDecodeResponse(BaseModel):
    function_selector: str
    function_name: str | None = None
    function_signature: str | None = None
    params: list[DecodedParam] = Field(default_factory=list)
    decoded: bool
    candidates: list[str] = Field(default_factory=list, description="Other matching sigs when the 4byte selector is ambiguous.")
    source: str = "openchain.xyz"


# --- /v1/parse/datetime ---


class DatetimeParseRequest(BaseModel):
    input: str = Field(min_length=1, max_length=500, description="Freeform datetime string in any format.")
    base_time: str | None = Field(default=None, description="ISO 8601 reference; defaults to now UTC.")
    timezone: str = Field(default="UTC", description="IANA tz name (e.g. 'America/New_York').")


class DatetimeComponents(BaseModel):
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    weekday: int = Field(ge=0, le=6)
    day_name: str


class DatetimeParseResponse(BaseModel):
    iso: str
    unix: int
    timezone: str
    components: DatetimeComponents
    relative_seconds: int
    relative_human: str
    confidence: Literal["high", "medium", "low"]
    parsed_input: str


# --- /v1/intel/wallet ---


class IntelWalletBalances(BaseModel):
    base_eth: str | None = None
    eth_eth: str | None = None
    base_usdc: str | None = None
    sol: str | None = None
    solana_usdc: str | None = None


class IntelWalletActivity(BaseModel):
    base_tx_count: int | None = None
    has_history: bool | None = None


class IntelWalletIdentity(BaseModel):
    ens_name: str | None = None
    sns_name: str | None = None


class IntelWalletError(BaseModel):
    source: str
    message: str


class IntelWalletResponse(BaseModel):
    wallet: str
    chain_inferred: str
    balances: IntelWalletBalances
    activity: IntelWalletActivity
    identity: IntelWalletIdentity
    sanctions: ScreenResponse | None = None
    errors: list[IntelWalletError] = Field(default_factory=list)
    fetched_at: int
    cache_age_seconds: int
