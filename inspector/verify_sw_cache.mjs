// Verify the service worker never strands a user on a stale build.
//
// Regression guard for the bug where sw.js was cache-first with a hardcoded
// cache name: once a URL was cached it was served forever, and because
// caches.match defaults to ignoreSearch:false, every ?assignment=/?mode= link
// was its own frozen entry. Coordinators (who revisit most) silently ran an
// older app than labellers, with no error to notice.
//
// Serves the real index.html over HTTP so the worker is actually active
// (app.js only registers it over http/https, never file://).
//
//   node inspector/verify_sw_cache.mjs
import { chromium } from 'playwright';
import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const MARKER = 'NEW_BUILD_MARKER';

const indexHtml = fs.readFileSync(path.join(here, 'index.html'), 'utf8');
const oldBuild = indexHtml;
const newBuild = indexHtml.replace('</body>', `<div id="newFeature">${MARKER}</div></body>`);
const swNew = fs.readFileSync(path.join(here, 'sw.js'), 'utf8');

// The pre-fix worker, kept verbatim so the test proves it still catches the bug.
const swOld = `const CACHE='thicket-inspector-shell-v2';
const SHELL=['./','./index.html','./manifest.webmanifest'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(c=>Promise.allSettled(SHELL.map(u=>c.add(u)))).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(caches.match(e.request).then(hit=>hit||fetch(e.request).then(r=>{
    if(r&&r.ok&&new URL(e.request.url).origin===location.origin){const copy=r.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));}return r;
  }).catch(()=>caches.match('./index.html'))));
});`;

let serving = 'old';
let swSource = swOld;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/sw.js') {
    res.writeHead(200, { 'Content-Type': 'text/javascript', 'Cache-Control': 'no-cache' });
    return res.end(swSource);
  }
  if (url.pathname === '/manifest.webmanifest') {
    res.writeHead(200, { 'Content-Type': 'application/manifest+json' });
    return res.end(fs.readFileSync(path.join(here, 'manifest.webmanifest')));
  }
  res.writeHead(200, { 'Content-Type': 'text/html', 'Cache-Control': 'no-cache' });
  res.end(serving === 'old' ? oldBuild : newBuild);
});
await new Promise(r => server.listen(0, r));
const base = `http://localhost:${server.address().port}/`;

// Nudge the update check, then let install -> activate settle. Without this the
// lifecycle can still be mid-install when we sample, which looks like a failure.
// The app reloads itself once it spots a new build, so any evaluate here can
// race a navigation it just triggered. Losing the execution context that way is
// the feature working, not a failure -- retry once the page has settled.
async function settle(page, fn, arg) {
  for (let i = 0; i < 3; i++) {
    try { return await page.evaluate(fn, arg); }
    catch (e) {
      if (!/Execution context was destroyed|navigation/i.test(String(e))) throw e;
      await page.waitForTimeout(700);
    }
  }
  return undefined;
}

async function reloadAndSettle(page) {
  // The app's own reload can abort ours; either way the page ends up loaded.
  try { await page.reload({ waitUntil: 'load' }); }
  catch (e) {
    if (!/ERR_ABORTED|detached|navigation/i.test(String(e))) throw e;
    await page.waitForLoadState('load').catch(() => {});
  }
  await page.waitForTimeout(1200);
  await settle(page, async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    if (reg) { try { await reg.update(); } catch { /* offline; fine */ } }
  });
  await page.waitForTimeout(600);
}

const checks = [];
const check = (name, ok, detail = '') => {
  checks.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};

// ---------------------------------------------------------------------------
// 1. A redeploy must reach a user who already has their URL cached.
// ---------------------------------------------------------------------------
async function redeployReaches(label, swAfterRedeploy, expectReached) {
  const browser = await chromium.launch();
  const page = await (await browser.newContext()).newPage();

  serving = 'old'; swSource = swOld;
  await page.goto(base + '?mode=coordinator', { waitUntil: 'load' });
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null, { timeout: 15000 })
    .catch(() => {});
  await page.waitForTimeout(800);
  // Second visit is the one the worker intercepts, pinning ?mode=coordinator.
  await page.reload({ waitUntil: 'load' });
  await page.waitForTimeout(1000);

  serving = 'new'; swSource = swAfterRedeploy;

  let reloads = -1;
  for (let i = 1; i <= 6; i++) {
    await reloadAndSettle(page);
    if (await settle(page, m => document.body.innerHTML.includes(m), MARKER)) { reloads = i; break; }
  }
  await browser.close();

  const reached = reloads > 0;
  check(`${label}: redeploy ${expectReached ? 'reaches' : 'never reaches'} a returning user`,
        reached === expectReached,
        reached ? `after ${reloads} reload(s)` : 'still stale after 6 reloads');
}

await redeployReaches('pre-fix worker', swOld, false);   // proves the guard still bites
await redeployReaches('current worker', swNew, true);

// ---------------------------------------------------------------------------
// 2. The upgrade must not disturb a labeller who is mid-campaign.
// ---------------------------------------------------------------------------
{
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const LINK = base + '?assignment=AP';

  serving = 'old'; swSource = swOld;
  await page.goto(LINK, { waitUntil: 'load' });
  await page.fill('#labelerName', 'FIELDTEST');
  await page.click('#startBtn');
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null, { timeout: 15000 })
    .catch(() => {});
  await page.waitForTimeout(1200);
  await page.keyboard.press('2');
  await page.waitForTimeout(400);

  const readStore = () => settle(page, () => {
    const key = Object.keys(localStorage).find(k => k.startsWith('thicket-inspector-labels-'));
    return { key, labels: localStorage.getItem(key), name: localStorage.getItem('thicket-inspector-name') };
  });
  const before = await readStore();
  await page.reload({ waitUntil: 'load' });      // pins ?assignment=AP in the old cache
  await page.waitForTimeout(1000);

  swSource = swNew;                              // the fix ships underneath them
  for (let i = 0; i < 3; i++) await reloadAndSettle(page);

  const after = await readStore();
  const cacheKeys = await settle(page, () => caches.keys());

  check('labeller keeps labels, name and storage key across the upgrade',
        before.labels === after.labels && before.key === after.key && before.name === after.name
          && !!before.labels && before.labels !== '{}',
        `${before.key}`);
  check('stale pre-fix cache is purged',
        !cacheKeys.includes('thicket-inspector-shell-v2'), cacheKeys.join(', '));
  check('replacement cache is present',
        cacheKeys.some(k => k.startsWith('thicket-inspector-shell-') && k !== 'thicket-inspector-shell-v2'));

  // Field connectivity: the app must still open with no network.
  await ctx.setOffline(true);
  let offlineOK = false, ms = -1;
  try {
    const t0 = Date.now();
    await page.goto(LINK, { waitUntil: 'load', timeout: 30000 });
    ms = Date.now() - t0;
    offlineOK = await page.evaluate(() => !!document.querySelector('#startBtn'));
  } catch { /* reported below */ }
  const offlineLabels = await page.evaluate(() => {
    const key = Object.keys(localStorage).find(k => k.startsWith('thicket-inspector-labels-'));
    return localStorage.getItem(key);
  }).catch(() => null);
  await ctx.setOffline(false);
  await browser.close();

  check('app still opens offline after the upgrade', offlineOK && ms < 15000, `${ms} ms`);
  check('labels still readable offline', offlineLabels === before.labels);
}

// ---------------------------------------------------------------------------
// 3. An OPEN tab must pick a redeploy up on its own. Labellers leave the app
//    open for days; before this they kept labelling an older point set until
//    somebody told them to hard-reload.
// ---------------------------------------------------------------------------
{
  const swNewer = swNew.replace(/thicket-inspector-shell-[^']*/, 'thicket-inspector-shell-next');

  // 3a. idle tab: reload itself, no prompt, no manual reload from the test.
  const browser = await chromium.launch();
  const page = await (await browser.newContext()).newPage();
  serving = 'old'; swSource = swNew;
  await page.goto(base + '?assignment=AP', { waitUntil: 'load' });
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null, { timeout: 15000 })
    .catch(() => {});
  await page.reload({ waitUntil: 'load' });   // now controlled by the old worker
  await page.waitForTimeout(800);

  serving = 'new'; swSource = swNewer;        // the deploy happens
  await page.evaluate(() => { lastInteraction = Date.now() - 60000; }); // an idle tab
  await page.evaluate(() => window.checkForUpdate());
  let picked = false;
  for (let i = 0; i < 20 && !picked; i++) {
    await page.waitForTimeout(500);
    picked = await settle(page, m => document.body.innerHTML.includes(m), MARKER)
      .catch(() => false) || false;
  }
  check('an open, idle tab picks up a redeploy with no manual reload', picked);
  await browser.close();

  // 3b. tab in active use: offer the reload rather than yanking the page away.
  const browser2 = await chromium.launch();
  const page2 = await (await browser2.newContext()).newPage();
  serving = 'old'; swSource = swNew;
  await page2.goto(base + '?assignment=AP', { waitUntil: 'load' });
  await page2.waitForFunction(() => navigator.serviceWorker.controller !== null, { timeout: 15000 })
    .catch(() => {});
  await page2.reload({ waitUntil: 'load' });
  await page2.waitForTimeout(800);
  // Get past the intro: an unstarted session is always safe to reload outright,
  // so the "offer instead of interrupt" path only exists once work has begun.
  await page2.fill('#labelerName', 'FIELDTEST');
  await page2.click('#startBtn');
  await page2.waitForTimeout(400);

  serving = 'new'; swSource = swNewer;
  await page2.evaluate(() => { lastInteraction = Date.now(); });  // mid-judgement
  await page2.evaluate(() => window.checkForUpdate());
  await page2.waitForTimeout(3000);
  const state = await settle(page2, m => ({
    reloaded: document.body.innerHTML.includes(m),
    offered: document.querySelector('#toast')?.textContent.includes('newer version') || false,
  }), MARKER);
  check('a tab in active use is offered the update instead of interrupted',
        !state.reloaded && state.offered, JSON.stringify(state));

  // and taking the offer applies it
  await page2.click('#toast button');
  let applied = false;
  for (let i = 0; i < 20 && !applied; i++) {
    await page2.waitForTimeout(500);
    applied = await settle(page2, m => document.body.innerHTML.includes(m), MARKER)
      .catch(() => false) || false;
  }
  check('taking the offer applies the update', applied);
  await browser2.close();
}

server.close();

const failed = checks.filter(c => !c.ok);
console.log(failed.length ? `\n❌ ${failed.length} check(s) failed` : '\n✅ SERVICE WORKER CACHE VERIFIED');
process.exit(failed.length ? 1 : 0);
