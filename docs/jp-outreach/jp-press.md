# JP crypto press — outreach emails

Two verified email targets. Same body works for both — Japanese news pitch
format: news angle first, on-chain proof second, source/demo offer last. No
30-min-meeting ask (press doesn't do meetings).

| Outlet | Email | Notes |
|---|---|---|
| NADA NEWS (旧 CoinDesk Japan) | `info@navenue.jp` | Larger reach. CoinDesk Japan was rebranded to NADA NEWS in 2025/2026. |
| The Bridge | `info@thebridge.jp` | EN-friendly outlet covering foreign-built JP-relevant startups. |

**Subject:** `【リリース情報】JPYC で決済される世界初の x402 サービスを公開`

Strong claim ("世界初") OK here — defensible on the "first x402 service" framing.
A native eye may want 「私の知る限り初の」 ("first to my knowledge"). Adjust per
your appetite.

---

## Body (paste verbatim)

```
NADA NEWS 編集部 御中

anchor-x402 を一人で開発しております Christopher Ferjo（クリストファー・ファージョ）と申します。

2026 年 5 月 14 日、おそらく世界で初めてとなる「JPYC で決済される x402 サービス」を立ち上げました。x402（Coinbase と Cloudflare が推進する HTTP 402 ベースの新しい支払い規格）は、AI エージェントが API を呼び出すたびに自動でステーブルコイン決済を行うための標準です。これまで世界中の x402 サービスはすべて USDC 決済のみで、日本円ステーブルコインで支払う手段は存在しませんでした。

anchor-x402 では Polygon 上の JPYC を USDC と並ぶ第 3 の決済レールとして組み込み、/v1/anchor（ハッシュアンカーサービス）は 1 回 1 円 で AI エージェントが直接支払い可能です。すでに本番環境で稼働しており、決済はすべてオンチェーンで検証可能です。

▼ 検証用情報

- ライブ API: https://api.anchor-x402.com
- 決済例（Polygonscan）: https://polygonscan.com/tx/0x8c465c282e336bb389a992b47fe9370ba6b5d68d51e73705706f09b096b24a14
- リポジトリ: https://github.com/hypeprinter007-stack/anchor-x402

【正確な前提】anchor-x402 は JPYC v2 の transferWithAuthorization を呼び出した 67 番目のサービスです。過去 66 件は 16 アドレスからの単発的なテスト送金で、API 課金サービスとして 402 経由で運用されているのは anchor-x402 が初めてです。「最初の送金」ではなく「最初のプロダクションサービスレール」が正確な表現です。

ご質問・追加情報・取材等はいつでもお気軽にご連絡ください。MCP 経由で Claude Desktop が anchor-x402 に 1 円を支払う 5 分間のデモも、ご希望あればすぐお見せできます。

何卒よろしくお願い申し上げます。

Christopher Ferjo
cferjo@gmail.com
https://anchor-x402.com
Twitter: @hypeprinter
```

When sending to The Bridge, swap the first line to `BRIDGE 編集部 御中`.

---

## Recommendation

Send NADA NEWS first (larger reach). Wait 2 business days. If no response,
send The Bridge — different angle (foreign-built JP-relevant startup) plays
to their editorial preference. Don't blast both same day.
