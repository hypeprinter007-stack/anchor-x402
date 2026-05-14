# JPYC Inc. — outreach email

**To:** `support@jpyc.jp` (or LinkedIn DM to Noritaka Okabe, CEO)
**Subject:** `First x402 service accepting JPYC — agent-payments distribution`

Bilingual: JP prefix establishes context for a JP team, EN body carries the
specifics. If LinkedIn-DM-ing Okabe directly, swap "Hi JPYC team" →
"Okabe-san," and trim the honest-scoping paragraph to one line.

---

突然のご連絡を失礼いたします。anchor-x402 を一人で開発しております Christopher Ferjo と申します。

このたび、おそらく世界で初めてとなる「JPYC で決済される x402 サービス」を立ち上げました。x402（Coinbase と Cloudflare が推進する HTTP 402 ベースの支払い規格）は、AI エージェントが API を呼び出すたびに自動でステーブルコイン決済を行う新しい標準です。これまで世界中の x402 サービスはすべて USDC 決済のみで、日本円ステーブルコインで支払う手段は存在しませんでした。anchor-x402 では Polygon 上の JPYC を USDC と並ぶ第 3 のレールとして組み込み、`/v1/anchor` は **1 回 1 円** で呼び出せます。日本円ネイティブなエージェント経済への入口として、メディアにも訴求しやすい仕様と考えております。

以下、英文にて、ご相談したい 3 点も含めて詳細をまとめております。ご一読いただけますと幸いです。

---

Hi JPYC team,

Christopher Ferjo here, solo builder behind anchor-x402.

I just shipped what's — as best I can tell — the first x402 service that settles in JPYC. x402 (Coinbase + Cloudflare-backed) is the emerging HTTP 402 standard for AI agents paying APIs per call. To date, every x402 service on the internet has settled exclusively in USDC. anchor-x402 now accepts JPYC on Polygon alongside USDC, with /v1/anchor priced at ¥1 per call — a press-friendly entry point into the agentic API economy.

Honest scoping: anchor-x402 is the 67th caller of JPYC v2's transferWithAuthorization. The prior 66 are 16 distinct addresses making test-pattern transfers over the past 7 months — none was wired to a paying service. anchor-x402 is the first production service rail, not the first transfer.

Live: https://api.anchor-x402.com
Example settle: https://polygonscan.com/tx/0x8c465c282e336bb389a992b47fe9370ba6b5d68d51e73705706f09b096b24a14
Repo: https://github.com/hypeprinter007-stack/anchor-x402

I want to make it easy for any JPYC holder to spend on AI agents — and easy for JPYC to point at a live integration when JP fintech press calls. Three things from you would help:

1. An intro to your BD / marketing / partnerships team — the people who care about JPYC adoption stories.
2. Your preferred framing — how you'd like this kind of integration described in your channels (or in mine).
3. A short conversation about a JPYC-priced endpoint catalog you'd want to feature. I can adjust pricing tiers or add JP-relevant endpoints quickly.

Self-funded, not raising. Happy to do a 5-minute live demo: Claude Desktop paying anchor-x402 ¥1 over MCP — brings the agent-payments + stablecoin story together on one screen.

Thanks for considering,

Chris Ferjo
cferjo@gmail.com
anchor-x402.com
Twitter: @hypeprinter
