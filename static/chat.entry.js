// Entry for esbuild bundling. Re-exports the deps the chat UI needs so the
// browser fetches a single same-origin file instead of hundreds of cross-origin
// modules (which mobile Safari was choking on).
//
// AppKit handles the mobile signature flow correctly (auto-deeplinks back into
// the wallet app on every signature request) — direct EthereumProvider was
// failing to round-trip signatures from a backgrounded wallet app.

export { createWalletClient, custom } from "viem";
export { base } from "viem/chains";
export { createAppKit } from "@reown/appkit";
export { WagmiAdapter } from "@reown/appkit-adapter-wagmi";
export { getWalletClient, disconnect as wagmiDisconnect } from "@wagmi/core";
export { CoinbaseWalletSDK } from "@coinbase/wallet-sdk";
export { wrapFetchWithPayment } from "x402-fetch";
