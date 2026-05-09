"""Pydantic schemas for the anchor service.

Two ways to use the endpoint:
  1. send a pre-computed 32-byte hex hash via `hash`
  2. send arbitrary JSON via `data` and let the server compute SHA-256
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

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
