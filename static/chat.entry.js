// Entry for esbuild bundling. Single same-origin file.
// Wallet stack: Coinbase Smart Wallet (passkey) primary, window.ethereum
// (injected) secondary. No WalletConnect anywhere.
// Payment stack: @x402/fetch v2 with @x402/evm's ExactEvmScheme as the
// EIP-3009 signer scheme. Server is x402==2.9.0 (v2 protocol) — must use
// the v2 client packages.

export { createWalletClient, custom } from "viem";
export { base } from "viem/chains";
export { CoinbaseWalletSDK } from "@coinbase/wallet-sdk";
export { wrapFetchWithPayment, wrapFetchWithPaymentFromConfig } from "@x402/fetch";
export { ExactEvmScheme } from "@x402/evm";
export * as Attribution from "ox/erc8021/Attribution";
