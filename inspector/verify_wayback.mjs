// Headless end-to-end check of the Wayback upgrade + GEE plumbing.
// Serves the built index.html, boots it in Chromium, exercises the Wayback flow,
// and asserts no console errors and that the new controls actually work.
import { chromium } from 'playwright';
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Serve the inspector dir this script lives in, not the process CWD.
const DIR = path.dirname(fileURLToPath(import.meta.url));
const server = http.createServer(async (req, res) => {
  try {
    const p = path.join(DIR, decodeURIComponent(req.url.split('?')[0]));
    if (path.relative(DIR, p).startsWith('..')) { res.statusCode = 403; res.end('no'); return; }
    const body = await readFile(p);
    const ext = path.extname(p);
    res.setHeader('content-type', ext === '.json' ? 'application/json'
      : ext === '.js' ? 'text/javascript' : 'text/html');
    res.end(body);
  } catch { res.statusCode = 404; res.end('nf'); }
});
await new Promise(r => server.listen(0, r));
const port = server.address().port;
const base = `http://127.0.0.1:${port}/index.html`;

const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage();
// Ignore the optional-manifest 404 (gee_layers.json is baked in CI, absent locally).
// The console message for a failed fetch omits the URL, so drop generic
// "Failed to load resource" 404 lines and rely on pageerror for real JS faults.
page.on('console', m => {
  if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text());
});
page.on('pageerror', e => errors.push('PAGEERROR ' + e.message));

await page.goto(base, { waitUntil: 'networkidle' });
// dismiss intro
await page.fill('#labelerName', 'VERIFY');
await page.click('#startBtn');
await page.waitForTimeout(1500);
// navigate to a real point first — Wayback is point-relative
await page.evaluate(() => window.gotoIdx ? null : null);
await page.click('#nextUnlabeled');
await page.waitForTimeout(1200);

const n = await page.evaluate(() => window.POINTS.length);
console.log('points:', n);

// switch to Wayback and wait for the config-driven dropdown to fill
await page.click('.srcbtn[data-src="wayback"]');
await page.waitForFunction(() => {
  const s = document.querySelector('#wbSelect');
  return s && s.options.length > 2 && s.options[0].textContent.match(/^\d{4}-\d{2}-\d{2}$/);
}, { timeout: 15000 });
const nOpts = await page.evaluate(() => document.querySelector('#wbSelect').options.length);
const firstDate = await page.evaluate(() => document.querySelector('#wbSelect').options[0].textContent);
console.log('wayback releases in dropdown:', nOpts, '| newest:', firstDate);

// step older via ‹
await page.click('#wbPrev');
await page.waitForTimeout(400);

// capture-date lookup populates (real SRC_DATE or release fallback)
await page.waitForFunction(() => {
  const el = document.querySelector('#wbCapDate');
  const t = (el.textContent || '').trim();
  return t.length > 0 && t !== '–' && !el.querySelector('.wbspin');
}, { timeout: 20000 }).catch(() => {});
const cap = await page.evaluate(() => (document.querySelector('#wbCapDate').textContent || '').trim());
console.log('capture date shown:', JSON.stringify(cap));

// "only new imagery here" filter — dropdown should shrink to just the releases
// whose imagery changed at this point (tilemap trace).
const before = await page.evaluate(() => document.querySelector('#wbSelect').options.length);
await page.check('#wbLocal');
// wait for the tilemap trace to complete and shrink the list below the full set
await page.waitForFunction((b) => {
  const s = document.querySelector('#wbSelect');
  return s && s.options.length >= 1 && s.options.length < b;
}, before, { timeout: 15000 }).catch(() => {});
const after = await page.evaluate(() => document.querySelector('#wbSelect').options.length);
console.log('local filter: releases', before, '->', after);

// swipe compare — second map + divider appear
await page.check('#wbCompare');
await page.waitForTimeout(1500);
const cmp = await page.evaluate(() => ({
  map: !!document.querySelector('#wb-compare-map'),
  swipe: !!document.querySelector('#wb-swipe'),
  rowB: !document.querySelector('#wb-row-b').classList.contains('hidden'),
}));
console.log('compare:', JSON.stringify(cmp));

await browser.close();
server.close();

const ok = n === 846 && nOpts > 2 && /^\d{4}-\d{2}-\d{2}$/.test(firstDate)
  && cap && cap !== '–' && after >= 1 && after < before && cmp.map && cmp.swipe && cmp.rowB
  && errors.length === 0;
if (errors.length) console.log('CONSOLE ERRORS:\n' + errors.join('\n'));
console.log(ok ? '\n✅ VERIFY PASSED' : '\n❌ VERIFY FAILED');
process.exit(ok ? 0 : 1);
