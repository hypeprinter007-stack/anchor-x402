"""Shared Bedrock client + model IDs.

Bedrock auth uses the Lambda execution role (no API key). Region + specific
inference-profile IDs are env-configurable so we can roll models without
redeploying code.
"""
from __future__ import annotations

import os

from anthropic import AnthropicBedrock

_client: AnthropicBedrock | None = None


def get_client() -> AnthropicBedrock:
    global _client
    if _client is None:
        _client = AnthropicBedrock(
            aws_region=os.environ.get("BEDROCK_REGION", "us-east-1"),
        )
    return _client


# Cross-region inference profiles. Date-stamped IDs match risk-investigator.
MODEL_FAST = os.environ.get(
    "BEDROCK_FAST_MODEL",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)
MODEL_REASON = os.environ.get(
    "BEDROCK_REASON_MODEL",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)
