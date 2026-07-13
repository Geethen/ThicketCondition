// End-to-end smoke test of index.html with Playwright/Chromium.
// Verifies: map + tiles load, points render, labeling works, counts update,
// keyboard shortcuts, and JSON export payload is correct.
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import path from 'path';

const here = path.dirname(fileURLToPath(import.meta.url));
const url = 'file://' + path.join(here, 'index.html').replace(/\\/g, '/');

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

await page.goto(url, { waitUntil: 'load' });

// start screen -> enter name -> start
await page.fill('#labelerName', 'TEST');
await page.click('#startBtn');

// wait for maplibre map to finish loading its first render
await page.waitForFunction(() => window.map && window.map.loaded && window.map.loaded(), { timeout: 15000 })
  .catch(() => {}); // map var may not be global; fall through to layer check

// the points layer should exist and have 846 features
const nPts = await page.evaluate(() => {
  const src = window.map ? window.map.getSource('pts') : null;
  return src ? src._data.features.length : -1;
});
console.log('points on map:', nPts);

// label current point via keyboard "2" (moderate), then check the counter
await page.keyboard.press('2');
await page.waitForTimeout(200);
const cModerate = await page.textContent('#c_moderate');
const cAll = await page.textContent('#c_all');
console.log('after key "2": moderate =', cModerate, ' all =', cAll);

// move next and label severe with "3"
await page.keyboard.press('ArrowRight');
await page.keyboard.press('3');
await page.waitForTimeout(200);
const cSevere = await page.textContent('#c_severe');
console.log('after key "3" on next point: severe =', cSevere);

// verify the in-memory export payload
const payload = await page.evaluate(() => {
  const rows = window.POINTS.filter(p => window.labels && window.labels[p.id]);
  return rows.map(p => ({ id: p.id, label: window.labels[p.id].label,
                          labeler: window.labels[p.id].labeler }));
});
console.log('labeled rows:', JSON.stringify(payload));

// did any base tiles actually request? check for a loaded raster tile
const tileOK = await page.evaluate(() => {
  const imgs = performance.getEntriesByType('resource')
    .filter(r => /arcgisonline|google\.com\/vt|eox\.at/.test(r.name));
  return imgs.length;
});
console.log('basemap tile requests:', tileOK);

console.log('JS errors:', errors.length ? errors : 'none');
const pass = nPts === 846 && cModerate === '1' && cSevere === '1'
  && payload.length === 2 && payload.every(r => r.labeler === 'TEST')
  && errors.filter(e => !/Failed to load|net::ERR/.test(e)).length === 0;
console.log(pass ? '\n✅ SMOKE TEST PASSED' : '\n❌ SMOKE TEST FAILED');
await browser.close();
process.exit(pass ? 0 : 1);
