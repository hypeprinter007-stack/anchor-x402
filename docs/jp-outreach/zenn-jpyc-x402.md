---
title: "AIエージェントが1円でAPIを呼ぶ — JPYC × x402 を実装した"
emoji: "🪙"
type: "tech"
topics: ["ai", "stablecoin", "jpyc", "x402", "polygon"]
published: false
---

## TL;DR

x402プロトコルで API を有料化している `anchor-x402` に **JPYC レール** を追加した。AI エージェントが日本円で API 呼び出しを支払えるようになった。`/v1/anchor` は **1円/回**、Polygon 上の EIP-3009 で 3 秒以内に決済される。実物のトランザクションハッシュも下に貼る。

なお、JPYC v2 の `transferWithAuthorization` 自体は anchor-x402 が最初ではない（過去 66 件、16 アドレスから呼ばれている）。**ただし、それらはすべて単発の試験的なトランスファーで、API 課金サービスとして 402 経由で動かしたのは anchor-x402 が最初。** ここが本記事の主張。

## x402 とは

Coinbase が提唱し、Cloudflare が後押しする HTTP 402 ベースの支払いプロトコル。AI エージェントが API を「呼ぶたびに」ステーブルコインで自動支払いする。事実上の標準は USDC。

問題は——**日本円のステーブルコインで支払う手段が今まで無かった。**

## JPYC × x402 が成立する理由

JPYC v2 は EIP-3009 の `transferWithAuthorization` を実装している（CENTRE / USDC と同じパターン）。x402 の `exact` スキームはまさにこの関数を使う。だから理論上は対応可能だった。

実装で必要なのは 3 つだけ：

1. **Polygon facilitator** — `verify` と `settle` を司る in-process コンポーネント。x402 Python SDK の `x402Facilitator` + `FacilitatorWeb3Signer` をそのまま使う。
2. **EIP-712 domain** — JPYC v2 の `name="JPY Coin"`、`version="1"`（監査済み 2022 年版コードから確認）。
3. **PaymentOption の追加** — Base USDC、Solana USDC に並ぶ第 3 のレールとして登録。

コアの追加は 50 行ほど：

```python
def build_jpyc_facilitator():
    private_key = secrets.get("polygon_relayer_key", ...)
    rpc_url = os.getenv("POLYGON_RPC_URL", "")
    if not private_key or not rpc_url:
        return None  # rail disabled
    signer = FacilitatorWeb3Signer(private_key, rpc_url)
    facilitator = x402Facilitator()
    register_exact_evm_facilitator(facilitator, signer, networks="eip155:137")
    return facilitator
```

リレイヤーが POL でガス代を払い、エージェントは JPYC だけを保有していればいい。ガスのことを意識しなくていい点が、ウォレット UX 的に重要。

## 価格

| エンドポイント | 価格 |
|---|---|
| `/v1/screen`（サンクション照合） | ¥0.1 |
| `/v1/anchor`（Base + Solana にハッシュをアンカー） | ¥1 |
| `/v1/oracle`（YES/NO オラクル） | ¥10 |

すべて Base USDC、Solana USDC、JPYC のいずれでも支払える。クライアント側のウォレットが何を持っているかで自動選択される。

## オンチェーン検証

エンドツーエンドのペイドテストを実行した（2026-05-14）：

- `/v1/screen` → https://polygonscan.com/tx/0x652631c3ddd4f40f5434f22f8afe9dc1a84d5ce28a019082514f7a77d19e2c37
- `/v1/anchor` → https://polygonscan.com/tx/0x8c465c282e336bb389a992b47fe9370ba6b5d68d51e73705706f09b096b24a14
- `/v1/oracle` → https://polygonscan.com/tx/0xbe2c58366a0a09ec46cae9c3bc61e91f57b6cdfa6fffedb179c004f52fa37b50

合計 **¥11.1** が確実にオンチェーンで動いた。残高差分も期待値と完全一致。

## 試してみる

JPYC を保有している Polygon ウォレットで、x402 対応のクライアント（[Claude Desktop + anchor-x402-mcp](https://www.npmjs.com/package/anchor-x402-mcp) など）から `api.anchor-x402.com` を叩く。402 レスポンスに JPYC オプションが含まれる。

## なぜこれが大事か

日本のステーブルコイン市場は始まったばかり。JPYC が初の正規ライセンスを取得し、Progmat（MUFG 主導）が銀行発行ステーブルコインを準備中。「日本円で AI エージェントに支払う」という体験を、誰かが最初に作る必要があった。

技術的には**完全に標準パターン**で、特別な拡張は一切要らなかった。EIP-3009 を実装している ERC-20 トークンであれば、どれでも x402 のレールに乗る。それを最初に運用したのが anchor-x402 というだけ。

## ソース

- Repo: https://github.com/hypeprinter007-stack/anchor-x402
- Live API: https://api.anchor-x402.com
- 質問・フィードバック: Twitter [@hypeprinter](https://twitter.com/hypeprinter) または GitHub Issues
