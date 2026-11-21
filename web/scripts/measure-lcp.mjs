// Largest Contentful Paint for /river under Lighthouse's "slow 4G" profile
// (150 ms RTT, 1.6 Mbit/s down, 750 kbit/s up) with the CPU slowed 4x, as a mid-range phone.
// Usage: node scripts/measure-lcp.mjs [baseUrl] [runs]. Needs Playwright with Chromium.
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)("playwright");
const base = process.argv[2] ?? "http://localhost:3000";
const runs = Number(process.argv[3] ?? 5);
const browser = await chromium.launch();
const values = [];
for (let i = 0; i < runs; i++) {
  // bypassCSP lets the measuring observer run; the page itself still has no scripts.
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, deviceScaleFactor: 3, bypassCSP: true });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false, latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8,
  });
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await page.addInitScript(() => {
    globalThis.__lcp = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) globalThis.__lcp = entry.startTime;
    }).observe({ type: "largest-contentful-paint", buffered: true });
  });
  await page.goto(`${base}/river`, { waitUntil: "load" });
  await page.waitForTimeout(500);
  values.push(await page.evaluate(() => globalThis.__lcp));
  await context.close();
}
await browser.close();
values.sort((a, b) => a - b);
console.log(`LCP over ${runs} cold loads: median ${Math.round(values[Math.floor(runs / 2)])} ms, worst ${Math.round(values.at(-1))} ms`);
