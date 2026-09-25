"""Circle Facilitator Service client, used for the Arc rail only.

Circle settles EIP-3009 `exact` USDC on Arc. It authenticates either with a
Circle API key (Bearer) or, in the keyless trial, with a Facilitator-Seller-Proof
header: an EIP-712 signature by the payTo key over the purpose, method, the
keccak256 of the raw request body, network, payTo, a nonce and a validity window.

The SDK client's auth hook never sees the request body, so this subclass
serializes the body itself and signs exactly the bytes it sends. With a
`circle_api_key` secret set it sends the key instead, which binds the payTo
account to the Circle account and lifts the trial allowance.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak
from x402.http import FacilitatorConfig, HTTPFacilitatorClient
from x402.schemas import SettleResponse, VerifyResponse

from services import secrets

URL = "https://api.circle.com/v1/facilitator/x402"
ARC_MAINNET = "eip155:5042"
ARC_USDC = "0x3600000000000000000000000000000000000000"
ARC_USDC_EXTRA = {"name": "USDC", "version": "2"}  # from Circle's /supported
PROOF_TTL_S = 300  # Circle's maximum

_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "SellerRequest": [
        {"name": "purpose", "type": "string"},
        {"name": "method", "type": "string"},
        {"name": "bodyHash", "type": "bytes32"},
        {"name": "network", "type": "string"},
        {"name": "payTo", "type": "address"},
        {"name": "nonce", "type": "bytes32"},
        {"name": "issuedAt", "type": "uint64"},
        {"name": "expiresAt", "type": "uint64"},
    ],
}


def seller_proof(private_key: str, *, purpose: str, body: bytes, network: str, pay_to: str,
                 now: int | None = None) -> str:
    issued = int(time.time()) if now is None else now
    message = {
        "purpose": purpose,
        "method": "POST",
        "bodyHash": keccak(body),
        "network": network,
        "payTo": pay_to,
        "nonce": os.urandom(32),
        "issuedAt": issued,
        "expiresAt": issued + PROOF_TTL_S,
    }
    typed = {
        "types": _TYPES,
        "primaryType": "SellerRequest",
        "domain": {"name": "Circle Facilitator Seller Request", "version": "1", "chainId": int(network.split(":", 1)[1])},
        "message": message,
    }
    signature = Account.sign_message(encode_typed_data(full_message=typed), private_key=private_key).signature.hex()
    envelope = {
        "version": 1,
        "signature": signature if signature.startswith("0x") else "0x" + signature,
        "network": network,
        "payTo": pay_to,
        "nonce": "0x" + message["nonce"].hex(),
        "issuedAt": issued,
        "expiresAt": message["expiresAt"],
    }
    return base64.urlsafe_b64encode(json.dumps(envelope, separators=(",", ":")).encode()).decode().rstrip("=")


class CircleFacilitatorClient(HTTPFacilitatorClient):
    def __init__(self):
        super().__init__(FacilitatorConfig(url=URL))

    def _headers(self, purpose: str, body: bytes, requirements: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = secrets.get("circle_api_key", env_fallback="CIRCLE_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            return headers
        key = secrets.get("treasury_evm_key", env_fallback="TREASURY_PRIVATE_KEY") or ""
        key = key if key.startswith("0x") else "0x" + key
        headers["Facilitator-Seller-Proof"] = seller_proof(
            key, purpose=purpose, body=body,
            network=requirements["network"], pay_to=requirements["payTo"],
        )
        return headers

    async def _post(self, purpose: str, version: int, payload: dict, requirements: dict, model):
        body = json.dumps(self._build_request_body(version, payload, requirements), separators=(",", ":")).encode()
        response = await self._get_async_client().post(
            f"{self._url}/{purpose}", headers=self._headers(purpose, body, requirements), content=body,
        )
        if response.status_code != 200:
            raise ValueError(f"Facilitator {purpose} failed ({response.status_code}): {response.text}")
        return model.model_validate(response.json())

    async def _verify_http(self, version, payload_dict, requirements_dict) -> VerifyResponse:
        return await self._post("verify", version, payload_dict, requirements_dict, VerifyResponse)

    async def _settle_http(self, version, payload_dict, requirements_dict) -> SettleResponse:
        return await self._post("settle", version, payload_dict, requirements_dict, SettleResponse)
