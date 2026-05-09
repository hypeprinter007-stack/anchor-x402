"""Runtime secrets via AWS Secrets Manager.

A single composite secret (`anchor-x402/runtime`) holds all sensitive
runtime values as a JSON object. Fetched once per Lambda container
on cold-start; cached in module-level memory thereafter (sub-
microsecond access on warm invocations).

When `ANCHOR_SECRET_ARN` is unset (local dev, pre-migration), every
lookup falls back to the corresponding env var so the same
`secrets.get(key, env_fallback="...")` call is portable across
environments.

Why this matters for institutional review:
- Sensitive values never live in Lambda env vars (visible to anyone
  with `lambda:GetFunctionConfiguration`)
- Secret rotation does not require a stack redeploy — just
  `aws secretsmanager update-secret`
- CloudTrail logs every access; KMS-encrypted at rest
- Lambda IAM is scoped to the specific secret ARN, not the service
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

log = logging.getLogger("anchor.secrets")

SECRET_ARN = os.getenv("ANCHOR_SECRET_ARN", "")

_cache: dict[str, Any] | None = None
_sm = None


def _client():
    global _sm
    if _sm is None:
        _sm = boto3.client(
            "secretsmanager",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
    return _sm


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if not SECRET_ARN:
        log.info("ANCHOR_SECRET_ARN unset; falling back to env vars")
        _cache = {}
        return _cache
    try:
        resp = _client().get_secret_value(SecretId=SECRET_ARN)
        _cache = json.loads(resp["SecretString"])
        log.info("loaded %d secrets from %s", len(_cache), SECRET_ARN)
    except Exception as e:
        log.warning("secrets fetch failed: %s; falling back to env vars", e)
        _cache = {}
    return _cache


def get(key: str, env_fallback: str | None = None) -> str:
    """Fetch a secret value by key. Tries Secrets Manager first, then
    the env_fallback env var (if provided)."""
    value = _load().get(key, "")
    if value:
        return value
    if env_fallback:
        return os.getenv(env_fallback, "")
    return ""


def reset_cache_for_testing() -> None:
    """Test hook — forces the next get() to re-fetch."""
    global _cache
    _cache = None
