// record.mjs — Playwright recorder for one demo entry.
//
// Drives chat.anchor-x402.com headlessly, injects a deterministic chat
// transcript (user message → agent quote → approval card), records a webm,
// then captures a still of the post-payment result card.
//
// Outputs (under scripts/demo/build/<key>/):
//   chat.webm    — ~8s recording, viewport 1080×1920
//   result.png   — 1080×1920 still of the result card
//
// Usage:
//   node scripts/demo/record.mjs <key>
//
// Where <key> is a top-level key in scripts/demo/demos.json
// (aura, screen, anchor, roll, investigate).

import { chromium, devices } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const DEMOS = JSON.parse(fs.readFileSync(path.join(__dirname, "demos.json"), "utf8"));

const key = process.argv[2];
if (!key) {
  console.error("usage: node record.mjs <key>");
  process.exit(2);
}
const cfg = DEMOS.find((d) => d.key === key);
if (!cfg) {
  console.error(`no demo with key=${key} in demos.json`);
  process.exit(2);
}

const OUT = path.join(__dirname, "build", key);
fs.mkdirSync(OUT, { recursive: true });

// Clear stale webm/result artifacts so re-runs don't pick up old files
// during the rename-by-arrival-order step at the bottom.
for (const f of fs.readdirSync(OUT)) {
  if (f.endsWith(".webm") || f === "result.png") {
    fs.unlinkSync(path.join(OUT, f));
  }
}

// Render at native 1080×1920 (no DPR scaling — the video encoder shows
// fewer artifacts that way). We inject CSS to override the chat UI's
// 720px max-width so content fills the full 1080-wide frame.
const FRAME = { width: 1080, height: 1920 };
const URL = process.env.CHAT_URL || "https://chat.anchor-x402.com/";

const WIDEN_CSS = `
  .chat, header > div, footer > div, .toasts {
    max-width: none !important;
    padding-left: 48px !important;
    padding-right: 48px !important;
  }
  body { font-size: 22px !important; }
  .msg .who { font-size: 14px !important; }
  .approval h3 { font-size: 24px !important; }
  .approval pre, .result pre { font-size: 20px !important; line-height: 1.5 !important; max-height: none !important; }
  .approval .price, .result .head .ok { font-size: 18px !important; }
  .result .head span:first-child { font-size: 18px !important; }
  .btn { font-size: 18px !important; padding: 12px 20px !important; }
`;

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: FRAME,
  deviceScaleFactor: 1,
  isMobile: true,
  hasTouch: true,
  userAgent: devices["iPhone 14 Pro Max"].userAgent,
  recordVideo: { dir: OUT, size: FRAME },
});
const page = await ctx.newPage();

await page.goto(URL, { waitUntil: "networkidle" });
await page.addStyleTag({ content: WIDEN_CSS });
await page.waitForTimeout(2500);

// ----- Inject the chat → approval-card sequence -----
await page.evaluate((c) => {
  const chat = document.getElementById("chat");
  if (!chat) return;
  chat.innerHTML = "";
  const mkMsg = (role, html) => {
    const div = document.createElement("div");
    div.className = "msg " + role;
    const who = role === "agent" ? "agent" : role === "user" ? "you" : role === "tool" ? "tool" : "system";
    div.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
    div.querySelector(".body").textContent = html;
    chat.appendChild(div);
  };
  mkMsg("user", c.user_text);
  mkMsg("agent", c.agent_quote);

  const card = document.createElement("div");
  card.className = "approval";
  card.innerHTML = `
    <h3>${c.endpoint} <span class="price">$${c.price}</span></h3>
    <pre></pre>
    <div class="actions"><div class="row">
      <button class="btn run">connect</button>
    </div></div>`;
  card.querySelector("pre").textContent = c.approval_body;
  chat.appendChild(card);
  card.scrollIntoView({ behavior: "auto", block: "end" });
}, cfg);

// Hold on the approval card so the viewer can read it.
await page.waitForTimeout(6500);

// Stop recording — finalize chat.webm.
await ctx.close();

// Playwright writes its videos with random filenames. Rename the only
// webm in OUT right now to chat.webm so a later poll recording can be
// distinguished by exclusion.
{
  const wm = fs.readdirSync(OUT).filter((f) => f.endsWith(".webm"));
  if (wm.length === 1) fs.renameSync(path.join(OUT, wm[0]), path.join(OUT, "chat.webm"));
}

// ----- Optional polling segment (only for endpoints with poll_steps) -----
if (cfg.poll_steps && cfg.poll_steps.length) {
  const pollCtx = await browser.newContext({
    viewport: FRAME,
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
    userAgent: devices["iPhone 14 Pro Max"].userAgent,
    recordVideo: { dir: OUT, size: FRAME },
  });
  const pollPage = await pollCtx.newPage();
  await pollPage.goto(URL, { waitUntil: "networkidle" });
  await pollPage.addStyleTag({ content: WIDEN_CSS });
  await pollPage.waitForTimeout(2000);

  // Seed: user + signed-payment confirmation + job-accepted sys message.
  await pollPage.evaluate((c) => {
    const chat = document.getElementById("chat");
    if (!chat) return;
    chat.innerHTML = "";
    const mkMsg = (role, html) => {
      const div = document.createElement("div");
      div.className = "msg " + role;
      const who = role === "agent" ? "agent" : role === "user" ? "you" : role === "tool" ? "tool" : "system";
      div.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
      div.querySelector(".body").textContent = html;
      chat.appendChild(div);
    };
    mkMsg("user", c.user_text);
    mkMsg("sys", `signed transferWithAuthorization · $${c.price} USDC on Base`);
    mkMsg("sys", "investigate started — job_id 1be3bd50-51df-4e47-8624-ae5bd1df5953. Polling every 30s, up to ~15 min…");
    window.__progressContainer = chat;
  }, cfg);

  // Drip-feed each step every ~5s so the viewer can read them.
  for (const step of cfg.poll_steps) {
    await pollPage.waitForTimeout(5000);
    await pollPage.evaluate((text) => {
      const chat = window.__progressContainer;
      if (!chat) return;
      const div = document.createElement("div");
      div.className = "msg sys";
      div.innerHTML = `<div class="who">system</div><div class="body"></div>`;
      div.querySelector(".body").textContent = text;
      chat.appendChild(div);
      div.scrollIntoView({ behavior: "auto", block: "end" });
    }, step);
  }

  // Hold a beat on the completed step list.
  await pollPage.waitForTimeout(3000);
  await pollCtx.close();

  // After pollCtx closes, the new webm is the one that isn't chat.webm.
  const wm = fs.readdirSync(OUT).filter((f) => f.endsWith(".webm") && f !== "chat.webm");
  if (wm.length === 1) fs.renameSync(path.join(OUT, wm[0]), path.join(OUT, "poll.webm"));
}

// ----- Second pass: capture result-card still -----
const ctx2 = await browser.newContext({
  viewport: FRAME,
  deviceScaleFactor: 1,
  isMobile: true,
  hasTouch: true,
  userAgent: devices["iPhone 14 Pro Max"].userAgent,
});
const page2 = await ctx2.newPage();
await page2.goto(URL, { waitUntil: "networkidle" });
await page2.addStyleTag({ content: WIDEN_CSS });
await page2.waitForTimeout(2000);

await page2.evaluate((c) => {
  const chat = document.getElementById("chat");
  if (!chat) return;
  chat.innerHTML = "";

  const mkMsg = (role, html) => {
    const div = document.createElement("div");
    div.className = "msg " + role;
    const who = role === "agent" ? "agent" : role === "user" ? "you" : role === "tool" ? "tool" : "system";
    div.innerHTML = `<div class="who">${who}</div><div class="body"></div>`;
    div.querySelector(".body").textContent = html;
    chat.appendChild(div);
  };
  mkMsg("user", c.user_text);
  mkMsg("sys", `signed transferWithAuthorization · $${c.price} USDC on Base`);

  // Grid-style result card — clean key/value rows instead of raw JSON.
  // Matches the look in docs/screenshots/03-result.png.
  const card = document.createElement("div");
  card.className = "result pretty";
  const okColor = "var(--accent, #cc6e47)";
  const muted = "var(--muted, #9ea3b0)";
  const ok    = "#3aa66b";
  const err   = "#d65a5a";

  const rowsHtml = (c.pretty_rows || []).map(([label, value, kind]) => {
    const valColor =
      kind === "ok"   ? `color:${ok};font-weight:600;` :
      kind === "err"  ? `color:${err};font-weight:600;` :
      kind === "mono" ? "font-family:'JetBrains Mono',ui-monospace,monospace;" :
                        "";
    return `
      <div style="color:${muted};font-size:18px;">${label}</div>
      <div style="${valColor};font-size:22px;line-height:1.4;">${value}</div>
    `;
  }).join("");

  card.innerHTML = `
    <div style="padding:6px 0 18px;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:18px;">
        <span style="color:${okColor};font-weight:600;font-size:22px;letter-spacing:0.02em;"></span>
        <span class="ok" style="font-size:18px;"></span>
      </div>
      <div style="display:grid;grid-template-columns:200px 1fr;gap:14px 24px;"></div>
      <div style="margin-top:18px;padding-top:14px;border-top:1px solid var(--line, #2a2c33);color:${muted};font-size:16px;font-family:'JetBrains Mono',ui-monospace,monospace;"></div>
    </div>
  `;
  card.querySelector("span:first-child").textContent = c.result_title;
  card.querySelector("span.ok").textContent = "verified";
  card.querySelector("div[style*='grid-template-columns']").innerHTML = rowsHtml;
  card.querySelector("div[style*='border-top']").textContent =
    `paid $${c.price} USDC · tx 0xa3f1c5e3b9d8a2e0…74961c4e on Base`;
  chat.appendChild(card);
  card.scrollIntoView({ behavior: "auto", block: "end" });
}, cfg);

await page2.waitForTimeout(800);
await page2.screenshot({ path: path.join(OUT, "result.png"), fullPage: false });
await ctx2.close();

await browser.close();

const sz = (p) => fs.existsSync(p) ? `${(fs.statSync(p).size/1024).toFixed(1)} KB` : "—";
console.log(`${key}: chat.webm ${sz(path.join(OUT,"chat.webm"))} | poll.webm ${sz(path.join(OUT,"poll.webm"))} | result.png ${sz(path.join(OUT,"result.png"))}`);
