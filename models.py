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
