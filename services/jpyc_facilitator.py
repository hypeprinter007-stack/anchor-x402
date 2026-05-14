"""JPYC v2 facilitator — in-process EIP-3009 verify + settle on Polygon.

Wraps the x402 SDK's own EVM facilitator with a web3.py signer pointed at
Polygon and the relayer EOA. The result satisfies the FacilitatorClient
protocol and plugs into x402ResourceServer's facilitator list alongside
the CDP HTTP client.

Disabled unless both POLYGON_RPC_URL and a relayer private key
(env POLYGON_RELAYER_KEY or Secrets Manager `polygon_relayer_key`) are
set. When disabled, the rail is silently absent — the main app keeps
running on Base + Solana USDC.
"""
from __future__ import annotations

import logging
import os

from services import secrets

log = logging.getLogger("anchor.jpyc")

# JPYC v2 on Polygon mainnet — first FSA-licensed JPY stablecoin.
# https://polygonscan.com/address/0x431D5dfF03120AFA4bDf332c61A6e1766eF37BDB
JPYC_POLYGON_ADDRESS = "0x431D5dfF03120AFA4bDf332c61A6e1766eF37BDB"
JPYC_DECIMALS = 18
# EIP-712 domain — verified against the audited FiatTokenV2 test fixtures.
# https://github.com/code-423n4/2022-02-jpyc/blob/main/test/v1/EIP3009.behavior.js
JPYC_EIP712_NAME = "JPY Coin"
JPYC_EIP712_VERSION = "1"
POLYGON_CAIP2 = "eip155:137"


def build_jpyc_facilitator():
    """Build the in-process x402Facilitator for Polygon JPYC, or None if disabled.

    Single-wallet fallback: if no dedicated Polygon relayer key is configured,
    reuse the Base treasury key. Ethereum addresses are universal across EVM
    chains, so the same EOA can hold POL for gas and JPYC for receipts.
    """
    private_key = secrets.get("polygon_relayer_key", env_fallback="POLYGON_RELAYER_KEY")
    if not private_key:
        private_key = secrets.get("treasury_evm_key", env_fallback="TREASURY_PRIVATE_KEY")
        if not private_key:
            log.info("no Polygon relayer or Base treasury key; JPYC rail disabled")
            return None
        log.info("JPYC rail using Base treasury key (single-wallet mode)")
    rpc_url = os.getenv("POLYGON_RPC_URL", "")
    if not rpc_url:
        log.info("POLYGON_RPC_URL unset; JPYC rail disabled")
        return None

    from x402 import x402Facilitator
    from x402.mechanisms.evm import FacilitatorWeb3Signer
    from x402.mechanisms.evm.exact import register_exact_evm_facilitator

    signer = FacilitatorWeb3Signer(private_key=private_key, rpc_url=rpc_url)
    facilitator = x402Facilitator()
    register_exact_evm_facilitator(facilitator, signer, networks=POLYGON_CAIP2)
    log.info("JPYC facilitator ready: relayer=%s", signer.address)
    return facilitator
