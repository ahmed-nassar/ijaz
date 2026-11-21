// Browser checks for the River: no scripts, no horizontal scroll at 375 px, no CSP errors,
// working pager and filters. Saves screenshots. Usage: node scripts/check-page.mjs [baseUrl] [outDir]
// Needs Playwright with Chromium (not a project dependency).
import { createRequire } from "node:module";

// require() so a globally installed Playwright is found through NODE_PATH.
const { chromium } = createRequire(import.meta.url)("playwright");

const base = process.argv[2] ?? "http://localhost:3000";
const out = process.argv[3] ?? ".";
const browser = await chromium.launch();
const results = [];
const check = (name, ok, detail = "") => results.push({ name, ok, detail });

for (const [label, viewport, scale] of [["desktop", { width: 1280, height: 900 }, 2], ["phone", { width: 375, height: 812 }, 3]]) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: scale, colorScheme: "light", isMobile: label === "phone" });
  const page = await context.newPage();
  const problems = [];
  const scripts = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(String(e)));
  page.on("request", (r) => r.resourceType() === "script" && scripts.push(r.url()));
  const response = await page.goto(`${base}/river`, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  check(`${label}: status 200`, response.status() === 200, String(response.status()));
  check(`${label}: no script requests`, scripts.length === 0, scripts.join(", "));
  check(`${label}: no <script> elements`, (await page.locator("script").count()) === 0);
  check(`${label}: no console or CSP errors`, problems.length === 0, problems.join(" | "));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(`${label}: no horizontal scroll`, overflow <= 0, `overflow ${overflow}px`);
  const fontOk = await page.evaluate(() => document.fonts.check('16px "IBM Plex Sans Arabic"', "خبر"));
  check(`${label}: Arabic font loaded`, fontOk);
  await page.screenshot({ path: `${out}/river-${label}.png`, fullPage: false });
  if (label === "phone") {
    await page.locator("details.copies summary").first().click();
    await page.locator("details.copies").first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${out}/river-phone-copies-open.png` });
  }
  await context.close();
}

const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "dark", deviceScaleFactor: 2 });
const page = await context.newPage();
await page.goto(`${base}/river`, { waitUntil: "networkidle" });
await page.screenshot({ path: `${out}/river-dark.png` });

const first = await page.locator("ol.items > li a.headline").first().getAttribute("href");
await page.getByRole("link", { name: "أقدم" }).click();
await page.waitForLoadState("networkidle");
const second = await page.locator("ol.items > li a.headline").first().getAttribute("href");
check("pager: «أقدم» opens an older page", page.url().includes("cursor=") && first !== second);
check("pager: «الأحدث» shown on later pages", (await page.getByRole("link", { name: "الأحدث" }).count()) === 1);

await page.goto(`${base}/river`);
check("filters: folded away by default", !(await page.locator('select[name="lang"]').isVisible()));
await page.locator("details.filter-toggle summary").click();
await page.selectOption('select[name="lang"]', "tr");
await page.getByRole("button", { name: "عرض" }).click();
await page.waitForLoadState("networkidle");
const langs = await page.locator("a.headline").evaluateAll((as) => [...new Set(as.map((a) => a.lang))]);
check("filter: language form works without JavaScript", page.url().includes("lang=tr") && langs.join() === "tr", langs.join());
check("filters: shown open while a filter is in use", await page.locator('select[name="lang"]').isVisible());
await page.screenshot({ path: `${out}/river-filtered-tr.png` });

const bad = await page.goto(`${base}/river?cursor=nonsense`);
check("bad cursor: 400 page with a way back", bad.status() === 400 && (await page.getByRole("link", { name: "العودة إلى أحدث الأخبار" }).count()) === 1);
await browser.close();

for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.detail ? `  (${r.detail})` : ""}`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
