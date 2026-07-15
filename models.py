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


# --- /v1/investigate (async risk-investigator shim) ---


class InvestigateRequest(BaseModel):
    address: str = Field(
        description="EVM 0x... or Solana base58 address to investigate.",
    )


class InvestigateAcceptedResponse(BaseModel):
    job_id: str = Field(description="Unique job id; poll /v1/investigate/status/{job_id}.")
    status: Literal["accepted"] = "accepted"
    status_url: str = Field(description="Absolute URL to poll for completion.")
    eta_seconds: int = Field(default=600, description="Approximate wait until DELIVERED.")


class InvestigateDeliverable(BaseModel):
    reportUrl: str | None = None
    reportJsonUrl: str | None = None
    verdict: str | None = None
    score: float | None = None
    signedBy: str | None = None
    signature: str | None = None
    merkleRoot: str | None = None
    baseAnchorTx: str | None = None
    solanaAnchorTx: str | None = None
    disclaimer: str | None = None


class InvestigateStatusResponse(BaseModel):
    job_id: str
    status: Literal["DISPATCHING", "IN_PROGRESS", "DELIVERED", "FAILED", "UNKNOWN"]
    deliverable: InvestigateDeliverable | None = None
    eta_seconds: int | None = None
    error: str | None = None
    refund_tx: str | None = Field(
        default=None,
        description="On-chain USDC refund tx hash (Base) when a FAILED job has been refunded. Auto-refunded for Base USDC payers; Solana/JPYC payers see `refund_pending=manual` and need a follow-up.",
    )
    refund_pending: Literal["manual"] | None = None


# --- /v1/roast ---


class RoastRequest(BaseModel):
    target: str = Field(min_length=1, max_length=8000, description="Anything to roast: wallet, tweet, idea, code, etc.")


class RoastResponse(BaseModel):
    roast: str
    target_summary: str


# --- /v1/oracle ---


class OracleRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000, description="A yes/no question for the oracle.")


class OracleResponse(BaseModel):
    answer: Literal["YES", "NO", "MAYBE"]
    explanation: str
    merkle_root: str = Field(description="sha256(question|answer|asked_at), 64 hex chars no 0x.")
    base_tx: str
    solana_tx: str | None = None
    asked_at: int


# --- /v1/tldr ---


class TldrRequest(BaseModel):
    text: str | None = Field(default=None, max_length=200_000)
    url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _check_exclusive(self):
        if (self.text is None) == (self.url is None):
            raise ValueError("supply exactly one of `text` or `url`")
        return self


class TldrResponse(BaseModel):
    summary_bullets: list[str]
    source_chars: int


# --- /v1/aura ---


class AuraRequest(BaseModel):
    target: str = Field(min_length=1, max_length=4000, description="Anything to read the aura of.")


class AuraResponse(BaseModel):
    color: str
    tier: Literal["S", "A", "B", "C", "D", "F"]
    score: int = Field(ge=0, le=9999)
    description: str


# --- /v1/grade ---


class GradeRequest(BaseModel):
    target: str = Field(min_length=1, max_length=6000, description="Anything to grade.")


class GradeResponse(BaseModel):
    letter_grade: Literal["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "F"]
    marginalia: list[str]
    summary: str


# --- /v1/roll ---


class RollRequest(BaseModel):
    low: int = Field(description="Inclusive low bound of the integer range.")
    high: int = Field(description="Inclusive high bound. Must be >= low.")
    count: int = Field(default=1, ge=1, le=100, description="How many integers to sample. 1-100.")
    commitment: str | None = Field(
        default=None,
        description="Optional 32-byte hex pre-commitment from the caller. Included in the signed payload, so the server cannot have chosen the result after seeing the caller's downstream intent.",
    )
    label: str | None = Field(
        default=None, max_length=200,
        description="Optional caller-defined tag (e.g. 'treasure_drop_42'). Included verbatim in the signed payload.",
    )


class RollResponse(BaseModel):
    range: list[int]
    count: int
    commitment: str | None
    label: str | None
    result: list[int]
    input_hash: str
    result_hash: str
    signature: str
    signer: str
    scheme: Literal["eip191"]
    domain: Literal["anchor-x402/roll/v1"]


# --- /v1/chat (free) ---


class ChatRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list, description="Anthropic-shape conversation history.")


class ChatToolUse(BaseModel):
    id: str
    name: str
    input: dict
    price_usd: float


class ChatResponse(BaseModel):
    assistant_text: str | None = None
    tool_uses: list[ChatToolUse] | None = None
    stop_reason: str | None = None


# --- /v1/ledger/* (x402 spend accounting) ---


class LedgerSummaryRequest(BaseModel):
    model_config = {"populate_by_name": True}

    wallet: str = Field(description="EVM address (Base) whose x402 spend to reconstruct.")
    from_: str | None = Field(
        default=None, alias="from",
        description="ISO date or datetime, start of range. Default: 30 days before `to`.",
    )
    to: str | None = Field(default=None, description="ISO date or datetime, end of range. Default: now.")
    direction: Literal["outbound", "inbound", "both"] = Field(
        default="outbound", description="outbound = spend, inbound = revenue.",
    )
    min_amount: str | float | None = Field(default=None, description="Minimum USDC per transfer, e.g. 0.001.")
    group_by: Literal["service", "recipient", "day"] = "service"
    include_unfiltered: bool = Field(
        default=False,
        description="Include non-x402 USDC transfers as category=other_transfer.",
    )


class LedgerReportRequest(LedgerSummaryRequest):
    format: Literal["markdown", "csv", "both"] = "both"
    title: str | None = Field(default=None, description="Report title; appears in the markdown header.")
    prepared_for: str | None = Field(default=None, description="Optional client name for the report header.")


class LedgerReportAccepted(BaseModel):
    job_id: str = Field(description="Unique job id; poll /v1/ledger/report/{job_id}.")
    status: Literal["accepted"] = "accepted"
    status_url: str = Field(description="Absolute URL to poll for completion.")
    eta_seconds: int = Field(default=120, description="Approximate wait until DELIVERED.")


class LedgerReportStatus(BaseModel):
    job_id: str
    status: str = Field(description="DISPATCHING | IN_PROGRESS | DELIVERED | FAILED | UNKNOWN")
    deliverable: dict[str, Any] | None = Field(
        default=None,
        description="On DELIVERED: report URLs, sha256, eip191 signature, dual-chain anchor txs.",
    )
    eta_seconds: int | None = None
    error: str | None = None
    refund_tx: str | None = None
    refund_pending: str | None = None
