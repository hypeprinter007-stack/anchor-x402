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

2026 年 5 月 14 日、おそらく世界で初めてとなる「JPYC で決済される x402 サービス」を立ち上げ、その後も継続的に機能拡張を続けております。x402（Coinbase と Cloudflare が推進する HTTP 402 ベースの新しい支払い規格）は、AI エージェントが API を呼び出すたびに自動でステーブルコイン決済を行うための標準です。これまで世界中の x402 サービスはすべて USDC 決済のみで、日本円ステーブルコインで支払う手段は存在しませんでした。

anchor-x402 では Polygon 上の JPYC を USDC と並ぶ第 3 の決済レールとして組み込み、/v1/anchor（ハッシュアンカーサービス）は 1 回 1 円 で AI エージェントが直接支払い可能です。現在 16 種の有料エンドポイントを公開しており、すべて本番環境で稼働、決済はオンチェーンで検証可能です。日本語版のサービスカタログも公開済みです（https://anchor-x402.com/llms.ja.txt）。

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
Twitter: @thexferj
```

When sending to The Bridge, swap the first line to `BRIDGE 編集部 御中`.

---

## Follow-up variant — NADA NEWS (use if 5/14 send is silent)

The 5/14 send to `info@navenue.jp` has been silent for 11 days as of
2026-05-25. No bounce recorded, just no response. A follow-up with
fresh news beats a duplicate send.

**Subject:** `【続報】JPYC × x402 サービス — 16 エンドポイント＋日本語カタログ公開`

**Body (paste verbatim):**

```
NADA NEWS 編集部 御中

5 月 14 日に「JPYC で決済される世界初の x402 サービス」のリリース情報を
お送りしました Christopher Ferjo（クリストファー・ファージョ）です。
続報として、その後の進捗をお伝えします。

▼ 5 月 14 日以降の主なアップデート

- 5/22: Divigent（@divigent_xyz）と提携し、AI エージェント向けの「ト
  レジャリーインテリジェンス」を統合。エージェントウォレットが API
  支払い前に流動性を事前評価し、必要に応じて運用先から USDC を取り戻
  すフローを実装しました。
- 5/25: サービスカタログを日本語化。https://anchor-x402.com/llms.ja.txt
  および /.well-known/x402.json の `description_ja` フィールドで、
  日本語環境のクローラー・LLM・読み手が母語で情報を取得できます。
- 直近 7 日間で 8 エンドポイントにわたり 27 件の有料 POST が成立。
  全件オンチェーンで検証可能です。
- 16 エンドポイント体制（前回 15 → /v1/roll を追加: 暗号学的乱数 ¥0.1）

▼ 検証用情報（更新版）

- ライブ API: https://api.anchor-x402.com
- 日本語サービス概要: https://anchor-x402.com/llms.ja.txt
- 公式カタログ: https://anchor-x402.com/.well-known/x402.json
- 最新の Base USDC 決済例: https://basescan.org/tx/0xfcb6559b4a0c797486363dbd3e533e75c79101b7438f15bef83eddfb57a07f1f

5 月 14 日時点と比べ、技術的・商業的に進展しております。引き続きご検討
いただけますと幸いです。MCP 経由で Claude Desktop が 1 円を支払う 5 分
デモも、ご希望あればすぐお見せできます。

何卒よろしくお願い申し上げます。

Christopher Ferjo
cferjo@gmail.com
https://anchor-x402.com
Twitter: @thexferj
```

---

## Recommendation

If NADA NEWS hasn't responded by now: send the follow-up variant above.
Send The Bridge with the original body (they haven't seen it yet) —
different angle (foreign-built JP-relevant startup) plays to their
editorial preference. Don't blast both same day.
