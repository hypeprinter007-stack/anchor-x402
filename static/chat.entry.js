// Entry for esbuild bundling. Re-exports the deps the chat UI needs so the
// browser fetches a single same-origin file instead of hundreds of cross-origin
// modules (which mobile Safari was choking on).
export { createWalletClient, custom } from "viem";
export { base } from "viem/chains";
export { default as EthereumProvider } from "@walletconnect/ethereum-provider";
export { wrapFetchWithPayment } from "x402-fetch";
