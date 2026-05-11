import { chromium, devices } from "playwright";
import fs from "fs";

const URL = "https://chat.anchor-x402.com/";
const OUT = "/Users/cferjoair/anchor-x402/docs/screenshots";
fs.mkdirSync(OUT, { recursive: true });

// Base App spec: 1284 x 2778 portrait, PNG, ≤5 MB.
const VIEWPORT = { width: 1284, height: 2778 };

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: 1,
  isMobile: true,
  hasTouch: true,
  userAgent: devices["iPhone 14 Pro Max"].userAgent,
});
const page = await ctx.newPage();

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForTimeout(2500); // let the bundle hydrate

// ---------- Frame 1: fresh load (empty chat with placeholder + connect) ----------
await page.screenshot({ path: `${OUT}/01-home.png`, fullPage: false });
console.log("01-home.png");

// ---------- Frame 2: /services overview (no wallet needed) ----------
await page.fill('input[type="text"], textarea', "/services");
await page.keyboard.press("Enter");
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}/02-services.png`, fullPage: false });
console.log("02-services.png");

// ---------- Frame 3: simulated tool run (inject a realistic transcript) ----------
await page.evaluate(() => {
  const chat = document.getElementById("chat");
  if (!chat) return;
  chat.innerHTML = "";
  const mkMsg = (role, html) => {
    const who = role === "agent" ? "agent" : role === "user" ? "you" : role === "tool" ? "tool" : "system";
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = `<div class="who">${who}</div><div class="body">${html}</div>`;
    chat.appendChild(div);
  };
  mkMsg("user", "screen vitalik.eth");
  mkMsg("agent", "Resolving the name (resolve_name, $0.001) then screening the address (screen_wallet, $0.001). Total: $0.002 USDC. Approve?");
  mkMsg("user", "yes");
  mkMsg("agent", "vitalik.eth → 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045");
  mkMsg("tool", `
    <div style="border:1px solid #2a2c33;border-radius:10px;padding:14px 16px;background:#16181d;font-family:ui-monospace,monospace;font-size:13px;line-height:1.7;color:#d6d8de">
      <div style="color:#cc6e47;font-weight:600;margin-bottom:10px">screen_wallet · result</div>
      <div style="display:grid;grid-template-columns:120px 1fr;gap:4px 12px">
        <div style="color:#9ea3b0">wallet</div><div>0xd8dA…6045</div>
        <div style="color:#9ea3b0">sanctioned</div><div style="color:#3aa66b">false</div>
        <div style="color:#9ea3b0">risk tier</div><div style="color:#3aa66b">clean</div>
        <div style="color:#9ea3b0">sources</div><div>OFAC SDN, Chainalysis, TRM Labs</div>
        <div style="color:#9ea3b0">checked</div><div>2026-05-11 13:00 UTC</div>
      </div>
      <div style="margin-top:10px;color:#9ea3b0;font-size:11px;border-top:1px solid #2a2c33;padding-top:8px">paid $0.001 USDC · tx 0xa3f1…1c4e on Base</div>
    </div>`);
});
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}/03-result.png`, fullPage: false });
console.log("03-result.png");

await browser.close();

// Report sizes
for (const f of ["01-home.png", "02-services.png", "03-result.png"]) {
  const st = fs.statSync(`${OUT}/${f}`);
  console.log(`${f}: ${st.size} bytes (${(st.size / 1024 / 1024).toFixed(2)} MB)`);
}
