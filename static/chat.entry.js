// Entry for esbuild bundling. Single same-origin file — mobile Safari was
// choking on chained cross-origin CDN imports.
//
// Two wallet paths, no WalletConnect:
//   - Coinbase Smart Wallet (passkey) — primary, works everywhere.
//   - Injected EIP-1193 provider (window.ethereum) — desktop browser
//     extensions (MetaMask, Rabby, Frame, etc.).
//
// AppKit / WalletConnect was removed: per WalletConnect Sign v2 spec
// (error 7001 noSessionForTopic) and Coinbase's own x402 reference
// architecture, WC mobile is fundamentally unreliable for x402 payments
// because mobile wallet apps lose their relay subscription when
// backgrounded and the spec provides no soft recovery.

export { createWalletClient, custom } from "viem";
export { base } from "viem/chains";
export { CoinbaseWalletSDK } from "@coinbase/wallet-sdk";
export { wrapFetchWithPayment } from "x402-fetch";
