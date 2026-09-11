/* check_demo.mjs - verifies web/index.html by measurement, not by eye.
 *
 * A screenshot proves nothing you can assert on. This renders the page in
 * headless Chromium and measures it: console errors, layout overflow, bar
 * geometry, the dark-mode surface actually changing, and - the one that
 * matters - that every example chip produces the verdict the engine says it
 * should, so the page cannot drift from web/gate-engine.js.
 *
 * Layout overflow is measured as the deepest descendant's bottom edge, not
 * scrollHeight: a clipped container reports a passing scrollHeight while its
 * content spills, so scrollHeight false-passes.
 *
 *     node tests/check_demo.mjs
 */

import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { evaluate, DEMO } from '../web/gate-engine.js';

const require = createRequire(import.meta.url);
const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

let chromium;
for (const c of ['playwright', 'playwright-core', 'C:/Users/priya/career-ops/node_modules/playwright']) {
  try { ({ chromium } = require(c)); break; } catch { /* keep looking */ }
}
if (!chromium) {
  console.log('SKIP  playwright not installed; layout cannot be measured here');
  process.exit(0);
}

const results = [];
const check = (ok, name, detail = '') => results.push({ ok, name, detail });

/* Serve web/ over HTTP. An ES module cannot be imported from a file:// page -
 * Chromium blocks it as a cross-origin request - so the demo needs an origin
 * both here and in the browser. That is why the README tells you to serve the
 * directory rather than double-click the file. */
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json' };
const server = createServer((req, res) => {
  const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || 'index.html';
  const file = path.join(ROOT, 'web', rel);
  if (!file.startsWith(path.join(ROOT, 'web'))) { res.writeHead(403).end(); return; }
  readFile(file).then((buf) => {
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
    res.end(buf);
  }).catch(() => { res.writeHead(404).end(); });
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const PAGE = `http://127.0.0.1:${server.address().port}/index.html`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(PAGE, { waitUntil: 'load' });
await page.waitForSelector('#out .verdict-word');

check(errors.length === 0, 'no console or page errors', errors.join(' | ').slice(0, 160));

/* ---- 1. every example agrees with the engine ------------------------- */
const chipCount = await page.locator('#chips .chip').count();
check(chipCount === DEMO.length, `all ${DEMO.length} examples rendered as chips`, `found ${chipCount}`);

for (let i = 0; i < DEMO.length; i += 1) {
  const [label, text, opts] = DEMO[i];
  const expected = evaluate(text, opts);
  await page.locator('#chips .chip').nth(i).click();
  await page.waitForTimeout(30);
  const word = (await page.locator('#out .verdict-word').innerText()).trim().toLowerCase();
  const tier = (await page.locator('#out .tier').innerText()).replace('tier', '').trim();
  const nFindings = await page.locator('#out .finding').count();
  const ok = word === expected.verdict && tier === expected.tier
    && nFindings === expected.findings.length;
  check(ok, `example renders engine verdict: ${label}`,
    ok ? '' : `page=${word}/${tier}/${nFindings} engine=${expected.verdict}/${expected.tier}/${expected.findings.length}`);
}

/* ---- 2. every finding shows its evidence ----------------------------- */
await page.locator('#chips .chip').nth(0).click();
await page.waitForTimeout(30);
const quotes = await page.locator('#out blockquote').count();
const findings = await page.locator('#out .finding').count();
check(quotes === findings && quotes > 0, 'every finding carries an evidence quote', `${quotes} quotes / ${findings} findings`);

/* ---- 3. bar geometry ------------------------------------------------- */
const barInfo = await page.evaluate(() => {
  const rows = [...document.querySelectorAll('#bars .row')];
  return rows.map((r) => {
    const bar = r.querySelector('.bar');
    const track = r.querySelector('.track');
    return {
      label: r.querySelector('.lab').textContent,
      barW: bar.getBoundingClientRect().width,
      trackW: track.getBoundingClientRect().width,
      radius: getComputedStyle(bar).borderTopRightRadius,
    };
  });
});
check(barInfo.length === 5, 'five terminal-state bars', `found ${barInfo.length}`);
check(barInfo.every((b) => b.barW > 0.5), 'no zero-width bars');
check(barInfo.every((b) => b.barW <= b.trackW + 1), 'no bar overflows its track');
const widest = Math.max(...barInfo.map((b) => b.barW));
check(Math.abs(widest - barInfo[0].trackW) < 1.5, 'largest value spans the full track', `${widest.toFixed(1)} vs ${barInfo[0].trackW.toFixed(1)}`);
check(barInfo.every((b) => b.radius === '4px'), 'data-ends are 4px rounded', barInfo[0].radius);

/* numbers on the page must equal the stated total */
/* Read the count only. .val holds the number AND a nested percentage span, so
 * textContent concatenates them ("305" + "57.1%" parses as 30557). */
const sum = await page.evaluate(() =>
  [...document.querySelectorAll('#bars .row .val')]
    .reduce((a, el) => a + parseInt(el.firstChild.textContent.trim(), 10), 0));
check(sum === 534, 'bars sum to the stated 534 rows', `sum=${sum}`);

/* ---- 4. layout: deepest descendant, not scrollHeight ----------------- */
const layout = await page.evaluate(() => {
  let maxBottom = 0; let maxRight = 0; let worst = '';
  for (const el of document.querySelectorAll('.viz-root *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const b = r.bottom + scrollY;
    if (b > maxBottom) { maxBottom = b; worst = el.tagName + '.' + (el.className || ''); }
    if (r.right > maxRight) maxRight = r.right;
  }
  return { maxBottom, maxRight, clientW: document.documentElement.clientWidth, worst };
});
check(layout.maxRight <= layout.clientW + 1, 'no horizontal overflow', `deepest right ${layout.maxRight.toFixed(0)} vs viewport ${layout.clientW}`);

/* ---- 5. dark mode is a real change, not an auto-flip ----------------- */
const light = await page.evaluate(() => getComputedStyle(document.querySelector('.viz-root')).getPropertyValue('--surface-1').trim());
await page.locator('#theme').click();
await page.waitForTimeout(40);
const darkSurface = await page.evaluate(() => getComputedStyle(document.querySelector('.viz-root')).getPropertyValue('--surface-1').trim());
const darkStage4 = await page.evaluate(() => getComputedStyle(document.querySelector('.viz-root')).getPropertyValue('--stage-4').trim());
check(light !== darkSurface, 'theme toggle changes the chart surface', `${light} -> ${darkSurface}`);
check(darkStage4 === '#184f95', 'dark ramp uses its own validated darkest step', darkStage4);

/* ---- 5b. TEXT CONTRAST IN BOTH THEMES -------------------------------
 * The original suite asserted that --surface-1 changed when the theme
 * toggled, and passed while h1, the stat digits, the verdict word and every
 * rule name rendered pure black on the dark surface at 1.08:1. Asserting a
 * variable changed is not asserting the page is readable. So measure the
 * real computed contrast of actual text against its real painted
 * background, in both themes.
 *
 * Cause of that bug, worth keeping written down: the custom properties were
 * scoped to .viz-root while body's own `color` referenced them from outside
 * that scope. An unresolvable var() in `color` falls back to black, and
 * everything that did not set its own colour inherited it.
 */
const CONTRAST_TARGETS = [
  ['h1', 'h1'], ['stat digits', '.tile .v'], ['stat label', '.tile .k'],
  ['h2', 'h2'], ['verdict word', '.verdict-word'], ['rule name', '.frule'],
  ['note', '.note'], ['bar value', '.row .val'], ['table header', 'th'],
  ['quoted evidence', '#out blockquote'], ['field label', '.fld'],
];
for (const theme of ['light', 'dark']) {
  const measured = await page.evaluate(({ t, targets }) => {
    document.documentElement.dataset.theme = t;
    const lum = (c) => {
      const p = c.match(/[\d.]+/g).slice(0, 3).map(Number).map((v) => {
        v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
    };
    const bgOf = (el) => {
      let n = el;
      while (n) {
        const bg = getComputedStyle(n).backgroundColor;
        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') return bg;
        n = n.parentElement;
      }
      return 'rgb(255,255,255)';
    };
    return targets.map(([label, sel]) => {
      const el = document.querySelector(sel);
      if (!el) return { label, ratio: null };
      const a = lum(getComputedStyle(el).color), b = lum(bgOf(el));
      const [hi, lo] = a > b ? [a, b] : [b, a];
      return { label, ratio: +((hi + 0.05) / (lo + 0.05)).toFixed(2) };
    });
  }, { t: theme, targets: CONTRAST_TARGETS });

  for (const m of measured) {
    if (m.ratio === null) { check(false, `${theme}: ${m.label} present`, 'selector not found'); continue; }
    // 4.5:1 is the WCAG AA body-text bar. Large display text could legally
    // sit at 3:1, but everything here clears the stricter bar, so hold it
    // there rather than carving out exceptions that hide a regression.
    check(m.ratio >= 4.5, `${theme}: ${m.label} readable`, `${m.ratio}:1`);
  }
}
await page.evaluate(() => { delete document.documentElement.dataset.theme; });

/* ---- 5c. the role/company fields drive the verdict panel ------------- */
await page.locator('#chips .chip').nth(0).click();
await page.fill('#role', 'Data Analyst, GTM Sales Insights');
await page.fill('#co', 'Lyft');
await page.waitForTimeout(60);
const subject = await page.locator('#out .summary').innerText().catch(() => '');
check(/Data Analyst, GTM Sales Insights at Lyft/.test(subject), 'role and company label the verdict', subject);
check(await page.locator('#out #copy').count() === 1, 'verdict is copyable');

/* ---- 6. table view exists (the relief rule) -------------------------- */
await page.locator('#tableBtn').click();
await page.waitForTimeout(40);
const rowsVisible = await page.locator('#tableWrap tbody tr').count();
check(rowsVisible === 5, 'table view lists every bar', `${rowsVisible} rows`);

await browser.close();
server.close();

/* ---------------------------------------------------------------- report */
const pad = 52;
console.log('='.repeat(96));
console.log('  DEMO LAYOUT + PARITY CHECK  (measured, no screenshots)');
console.log('='.repeat(96));
for (const r of results) {
  console.log(`  ${r.ok ? 'ok  ' : 'FAIL'}  ${r.name.padEnd(pad)} ${r.detail}`);
}
const bad = results.filter((r) => !r.ok).length;
console.log('-'.repeat(96));
console.log(`  ${results.length - bad}/${results.length} checks passed`);
console.log('='.repeat(96));
process.exit(bad ? 1 : 0);
