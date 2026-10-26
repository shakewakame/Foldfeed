// Screenshots each page of a manifest to a PNG (the cards for SNS and link previews, pipeline/cards.py).
//
//   node tools/shoot.mjs manifest.json        # [{"html": "/abs/card.html", "png": "/abs/card.png", "w": 1200, "h": 630}, ...]
//
// Needs playwright-core and a Chromium (in the cloud sessions: /opt/node-tools and /opt/pw-browsers).
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const pw = await import('playwright-core').catch(() => import('/opt/node-tools/node_modules/playwright-core/index.mjs'));
const executablePath = process.env.CHROME || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const jobs = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const browser = await pw.chromium.launch({ executablePath, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
for (const j of jobs) {
  await page.setViewportSize({ width: j.w || 1200, height: j.h || 630 });
  await page.goto(pathToFileURL(j.html).href, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: j.png, type: 'png' });
}
await browser.close();
console.error(`${jobs.length} images`);
