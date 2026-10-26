// Checks that no page of the built site widens sideways on a phone (the cause of "a white strip on the right").
//
//   cd site && python3 -m http.server 8765 &      # serve the built site
//   node tools/check-overflow.mjs [http://127.0.0.1:8765]
//
// Needs playwright-core and a Chromium (in the cloud sessions: /opt/pw-browsers). For each width it reports how far
// the page is wider than the screen and what sticks out. It looks at the lines of text, not only at element boxes:
// a line that may not wrap (white-space:nowrap) runs past its box without making the box any wider.
import { chromium } from 'playwright-core';

const base = process.argv[2] || 'http://127.0.0.1:8765';
const pages = ['/index.html', '/search.html', '/search.html?cat=enzyme', '/later.html', '/issues.html', '/new.html', '/update.html', '/study.html', '/newsletter.html', '/about.html', '/privacy.html', '/tokushoho.html', '/issues/' + (process.argv[3] || 'latest')];
const executablePath = process.env.CHROME || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const browser = await chromium.launch({ executablePath, args: ['--no-sandbox'] });
let bad = 0;
for (const w of [340, 360, 375, 390, 414]) {
  const p = await (await browser.newContext({ viewport: { width: w, height: 800 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })).newPage();
  for (let url of pages) {
    if (url.endsWith('/latest')) {              // the newest issue page linked from the front page
      await p.goto(base + '/index.html', { waitUntil: 'load' });
      url = '/' + await p.evaluate(() => document.querySelector('.ix-all').getAttribute('href'));
    }
    await p.goto(base + url, { waitUntil: 'load' });
    await p.waitForTimeout(300);
    const r = await p.evaluate(() => {
      const W = document.documentElement.clientWidth, out = [];
      // a row that scrolls sideways on its own (a phone's strip of an issue's structures) and itself fits: what runs
      // past the edge inside it is reached by scrolling the row, not the page
      const fitsOn = (e, re) => {
        for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
          const cs = getComputedStyle(a);
          if (re.test(cs.overflowX) && a.getBoundingClientRect().right <= W + 0.5) return true;
        }
        return false;
      };
      const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = tw.nextNode())) {
        if (!n.textContent.trim() || fitsOn(n, /auto|scroll/)) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const b of range.getClientRects()) {
          if (b.right > W + 0.5) { out.push('text "' + n.textContent.trim().slice(0, 24) + '" right=' + Math.round(b.right)); break; }
        }
      }
      // a box may run past the edge inside a parent that clips it and itself fits (a phone's first-screen structure,
      // large on purpose): that cannot widen the page or be scrolled to. Clipped text is checked above all the same.
      const clipped = e => fitsOn(e, /clip|hidden|auto|scroll/);
      document.querySelectorAll('body *').forEach(e => {
        const b = e.getBoundingClientRect();
        if (b.width && b.right > W + 0.5 && !clipped(e)) out.push(e.tagName.toLowerCase() + '.' + (typeof e.className === 'string' ? e.className : '') + ' right=' + Math.round(b.right));
      });
      return { over: document.documentElement.scrollWidth - W, out: out.slice(0, 5) };
    });
    if (r.over > 0 || r.out.length) bad++;
    console.log(`${w}px ${url}: ${r.over > 0 || r.out.length ? 'NG' : 'OK'} ${r.over > 0 ? '(page ' + r.over + ' px too wide) ' : ''}${r.out.join(' | ')}`);
  }
}
await browser.close();
process.exit(bad ? 1 : 0);
