# JPYC Inc. — outreach

**Domain note:** JPYC's corporate domain is `jpyc.co.jp`, not `jpyc.jp`. No
public catch-all email is exposed. Three real intake channels, each with
its own body variant below.

---

## Path A — Corporate inquiry form (recommended, primary)

URL: https://corporate.jpyc.co.jp/contact

The form is explicitly for **協業・業務提携、広報、メディア出演・講演依頼**
(collaborations / partnerships / PR / media). Header text reminds readers
that general JPYC use does NOT require permission, so the body must read
as a partnership/PR proposal — not a "can I use JPYC?" question.

Recipient is JP corporate BD; JP-dominant body lands best.

### Form fields

| Field | Value |
|---|---|
| お名前 | `Christopher Ferjo`（クリストファー・ファージョ） |
| 会社名 | `anchor-x402` |
| メール | `cferjo@gmail.com` |
| Inquiry category (if dropdown) | 協業・業務提携 |

### Message body (JP, paste into お問い合わせ内容)

```
はじめまして。anchor-x402 を一人で開発しております Christopher Ferjo（クリストファー・ファージョ）と申します。本件は「協業・業務提携・広報」のご相談としてご連絡差し上げております。

2026 年 5 月 14 日、おそらく世界で初めてとなる「JPYC で決済される x402 サービス」を立ち上げました。x402（Coinbase と Cloudflare が推進する HTTP 402 ベースの支払い規格）は、AI エージェントが API を呼び出すたびに自動でステーブルコイン決済を行う新しい標準です。これまで世界中の x402 サービスはすべて USDC 決済のみで、日本円ステーブルコインで支払う手段は存在しませんでした。

anchor-x402 では Polygon 上の JPYC を USDC と並ぶ第 3 の決済レールとして組み込み、/v1/anchor は 1 回 1 円 で呼び出せます。現在は 16 種の有料エンドポイントを公開し、5 月 22 日には AI エージェント向けトレジャリーインテリジェンス（Divigent 社との統合）も実装、5 月 25 日にはサービスカタログを日本語化しました（https://anchor-x402.com/llms.ja.txt）。日本円ネイティブなエージェント経済への入口として、メディア・パートナーにも訴求しやすい仕様と考えております。

【誠実な前提】anchor-x402 は JPYC v2 の transferWithAuthorization を呼び出した 67 番目のサービスです。過去 66 件は 16 アドレスからの単発的なテスト送金で、API 課金サービスとして 402 経由で運用されているのは anchor-x402 が初めてです。「最初の送金」ではなく「最初のプロダクションサービスレール」が正確な表現です。

▼ ライブ環境
- API: https://api.anchor-x402.com
- 決済例: https://polygonscan.com/tx/0x8c465c282e336bb389a992b47fe9370ba6b5d68d51e73705706f09b096b24a14
- リポジトリ: https://github.com/hypeprinter007-stack/anchor-x402

ご相談したい点は次の 3 つです。

1. JPYC 様の事業開発・広報・パートナーシップ担当の方をご紹介いただきたく存じます。JPYC を「エージェント経済での採用事例」として活用いただける方々と直接お話したい意図です。
2. 御社の発信における望ましいフレーミング(表現方法・タイトル付け)をご教示いただけますと幸いです。当方の発信でも整合性を取りたいと考えております。
3. 日本市場で訴求しやすい価格帯・エンドポイント構成についてご意見をいただきたいです。価格や対応エンドポイントは柔軟に調整可能です。

なお、本件は資金調達の話ではなく、自己資金で運用しているサービスのパートナーシップ提案です。MCP 経由で Claude Desktop が anchor-x402 に 1 円を支払う 5 分間のデモも、ご希望あればすぐお見せできます。

何卒よろしくお願い申し上げます。

Christopher Ferjo
cferjo@gmail.com
https://anchor-x402.com
Twitter: @thexferj
```

---

## Path B — LinkedIn DM to Noritaka Okabe (CEO)

Use if Path A doesn't get a response in ~5 business days. ZoomInfo confirms
his email pattern is `n***@jpyc.co.jp` but full handle isn't public — LinkedIn
beats guessing.

LinkedIn caps practical DM length at ~2 screens. Open in JP, switch to EN
for the asks since DM is informal:

```
岡部様

突然のご連絡を失礼いたします。anchor-x402 を一人で開発している Christopher Ferjo と申します。

このたび、おそらく世界で初めてとなる「JPYC で決済される x402 サービス」を立ち上げました。x402 は AI エージェントが API 呼び出しごとに支払う HTTP 402 ベースの新しい標準（Coinbase・Cloudflare 後押し）です。/v1/anchor は 1 回 1 円。決済例: https://polygonscan.com/tx/0x8c465c282e336bb389a992b47fe9370ba6b5d68d51e73705706f09b096b24a14

Honest scoping: 67th caller of JPYC v2's transferWithAuthorization, but first production service rail. The prior 66 were test transfers.

Self-funded, not raising. Three asks: (1) intro to your BD/PR team; (2) preferred framing for this kind of integration; (3) JPYC-priced endpoint catalog you'd want featured. 5-min live demo available.

何卒よろしくお願いいたします。
Chris
cferjo@gmail.com · anchor-x402.com
```

---

## Path C — X DM to @jpyc_official

Short, in JP. They run public-facing comms; will route to internal team.

```
はじめまして。「JPYC で決済される x402 サービス」を立ち上げました（anchor-x402）。AI エージェントが /v1/anchor を 1 円で呼び出せます。
ライブ: https://api.anchor-x402.com
決済例: https://polygonscan.com/tx/0x8c465c282e336bb389a992b47fe9370ba6b5d68d51e73705706f09b096b24a14
広報・BD 担当の方をご紹介いただくことは可能でしょうか？
```

---

## Native-speaker review notes (apply before sending)

- 「世界で初めてとなる」 — strong claim. Native eye may soften to 「私の知る限り初の」 (first to my knowledge).
- 「メディアにも訴求しやすい仕様」 — signals you understand JPYC cares about press. May want more humble phrasing.
- Personal name in カタカナ: included Christopher Ferjo（クリストファー・ファージョ） for readability. Confirm phonetic preference.
