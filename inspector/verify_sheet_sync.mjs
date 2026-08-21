// Verify the Google Sheets outbox: a backlog must reach the Sheet without
// wedging the browser, and nothing may be dropped when the Sheet says no.
//
// The bug this exists for: the outbox posted one event at a time with
// mode:'no-cors'. An opaque response cannot be read, so a REJECTED event --
// stale EXPECTED_DATASET, lock timeout, exhausted quota -- was indistinguishable
// from an accepted one, and the client shifted it off the queue anyway. Round 3
// labels were posted for a month into an endpoint answering "Wrong dataset" and
// thrown away at both ends, with the app reporting "sync sent" throughout.
//
// The endpoint here mirrors the real Apps Script: it batches, it keys upserts on
// (dataset, assignment, labeller, point), and it can be told to reject.
//
//   node inspector/verify_sheet_sync.mjs
import { chromium } from 'playwright';
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DIR = path.dirname(fileURLToPath(import.meta.url));

let mode = 'accept';              // 'accept' | 'reject' | 'legacy'
let requests = [];                // one entry per POST: how many events it carried
const rows = new Map();           // upsert key -> row, exactly as the Sheet holds it

const server = http.createServer(async (req, res) => {
  if (req.url.startsWith('/sync')) {
    let body = ''; for await (const chunk of req) body += chunk;
    const doc = JSON.parse(body || '{}');
    const batched = doc && doc.version === 2 && Array.isArray(doc.events);
    if (mode === 'legacy' && batched) {
      res.setHeader('content-type', 'application/json');
      return res.end('{"ok":false,"error":"Unsupported payload"}');
    }
    if (mode === 'reject') {
      res.setHeader('content-type', 'application/json');
      return res.end('{"ok":false,"error":"Wrong dataset"}');
    }
    const events = batched ? doc.events : [doc];
    requests.push(events.length);
    for (const b of events) {
      const a = b.assignment || {}, p = b.point || {};
      rows.set([b.dataset, a.id || 'coordinator', b.labeler || 'anon', p.id].join('|'),
               { label: b.record && b.record.label, action: b.action });
    }
    res.setHeader('content-type', 'application/json');
    return res.end(JSON.stringify({ ok: true, accepted: events.length }));
  }
  try {
    const pathname = decodeURIComponent(req.url.split('?')[0]);
    const target = path.join(DIR, pathname === '/' ? 'index.html' : pathname);
    if (path.relative(DIR, target).startsWith('..')) throw new Error('outside');
    res.setHeader('content-type', path.extname(target) === '.json' ? 'application/json' : 'text/html');
    res.end(await readFile(target));
  } catch { res.statusCode = 404; res.end('nf'); }
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;
const sync = `http://127.0.0.1:${port}/sync`;
const link = a => `http://127.0.0.1:${port}/index.html?assignment=${a}`
  + `&syncEndpoint=${encodeURIComponent(sync)}`;

const checks = [];
const check = (name, ok, detail = '') => {
  checks.push({ name, ok });
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext();
const page = await ctx.newPage();

// Seed a realistic backlog: 600 labels already in localStorage, none yet synced.
await page.goto(link('AP'), { waitUntil: 'load' });
const seeded = await page.evaluate(() => {
  const ts = new Date().toISOString();
  const store = {};
  REQUIRED_POINTS.slice(0, 600).forEach(p => {
    store[p.id] = { label: 'moderate', note: '', labeler: 'AP-TEST', ts,
                    flagged: false, confidence: 'high', reasons: [], stratum: p.s,
                    lon: p.lon, lat: p.lat };
  });
  const key = Object.keys(localStorage).find(k => k.startsWith('thicket-inspector-labels-'))
    || 'thicket-inspector-labels-' + DATASET_ID + '-assignment-' + ASSIGNMENT_ID;
  localStorage.setItem(key, JSON.stringify(store));
  localStorage.setItem('thicket-inspector-name', 'AP-TEST');
  return Object.keys(store).length;
});

// ---------------------------------------------------------------------------
// 1. The Sheet says no. Nothing may be lost.
// ---------------------------------------------------------------------------
mode = 'reject';
await page.goto(link('AP'), { waitUntil: 'load' });
await page.fill('#labelerName', 'AP-TEST');
await page.click('#startBtn');                 // triggers the one-time backfill
await page.waitForTimeout(2500);

const rejected = await page.evaluate(() => ({
  queued: syncQueue.length,
  status: document.querySelector('#syncStatus').textContent,
  stored: JSON.parse(localStorage.getItem(Object.keys(localStorage)
    .find(k => k.startsWith('thicket-inspector-sheet-sync-queue-'))) || '[]').length,
}));
check('a rejected batch keeps every event queued',
  rejected.queued === seeded && rejected.stored === seeded,
  `${rejected.queued} in memory, ${rejected.stored} persisted, of ${seeded}`);
check('the rejection is reported, not swallowed',
  /paused/i.test(rejected.status) && /wrong dataset/i.test(rejected.status),
  rejected.status);

// ---------------------------------------------------------------------------
// 2. The Sheet recovers. The backlog drains, batched, without blocking the UI.
// ---------------------------------------------------------------------------
mode = 'accept'; requests = []; rows.clear();
const t0 = Date.now();
const jank = await page.evaluate(async () => {
  // Sample the main thread while the drain runs: a frame budget blown by
  // synchronous localStorage writes shows up as a long gap between ticks.
  let worst = 0, last = performance.now();
  const timer = setInterval(() => {
    const now = performance.now();
    worst = Math.max(worst, now - last - 16);
    last = now;
  }, 16);
  await flushSheetSync();
  clearInterval(timer);
  return worst;
});
const drainMs = Date.now() - t0;

const drained = await page.evaluate(() => ({
  queued: syncQueue.length,
  status: document.querySelector('#syncStatus').textContent,
}));
check('the backlog drains once the endpoint recovers', drained.queued === 0,
  `${requests.length} request(s), ${drainMs} ms`);
check('every label reached the Sheet exactly once',
  rows.size === seeded && requests.reduce((a, b) => a + b, 0) === seeded,
  `${rows.size} rows from ${requests.reduce((a, b) => a + b, 0)} events`);
check('the backlog is sent in batches, not one request per label',
  requests.length <= Math.ceil(seeded / 50) && Math.max(...requests) <= 50,
  `${requests.length} requests, largest ${Math.max(...requests)}`);
check('the drain does not block the main thread', jank < 250, `worst stall ${jank.toFixed(0)} ms`);
check('sync reports success once the queue is empty', /sent/i.test(drained.status), drained.status);

// ---------------------------------------------------------------------------
// 3. The catch-up runs once, not on every visit.
// ---------------------------------------------------------------------------
requests = [];
await page.goto(link('AP'), { waitUntil: 'load' });
await page.fill('#labelerName', 'AP-TEST');
await page.click('#startBtn');
await page.waitForTimeout(1500);
check('the catch-up does not re-run on the next visit',
  requests.length === 0, `${requests.length} request(s) on reopen`);

// A later edit still syncs normally.
const firstId = await page.evaluate(() => REQUIRED_POINTS[0].id);
await page.evaluate(id => { gotoId(id); setLabel('severe'); }, firstId);
await page.waitForTimeout(1500);
const edited = rows.get([...rows.keys()].find(k => k.endsWith('|' + firstId)));
check('an edit after the catch-up still reaches the Sheet',
  !!edited && edited.label === 'severe', JSON.stringify(edited));

// ---------------------------------------------------------------------------
// 4. An endpoint still running the pre-batch script must not wedge the client:
//    the app should notice and fall back to single events, so it does not
//    matter whether the site or the Apps Script is updated first.
// ---------------------------------------------------------------------------
mode = 'legacy'; requests = []; rows.clear();
await page.evaluate(async () => {
  syncBatchUnsupported = false;
  const ts = new Date().toISOString();
  syncQueue = REQUIRED_POINTS.slice(0, 3).map(p =>
    syncEvent(p, { label: 'intact', note: '', ts, flagged: false, confidence: '', reasons: [] }, 'upsert'));
  persistSyncState();
  await flushSheetSync();
});
const legacy = await page.evaluate(() => ({ queued: syncQueue.length, fallback: syncBatchUnsupported }));
check('a pre-batch endpoint is detected and the client falls back to single events',
  legacy.fallback === true && legacy.queued === 0 && rows.size === 3
  && requests.every(n => n === 1),
  `fallback=${legacy.fallback}, ${rows.size} rows, ${requests.length} single sends`);

await browser.close();
server.close();

const failed = checks.filter(c => !c.ok);
console.log(failed.length ? `\n❌ ${failed.length} check(s) failed` : '\n✅ SHEET SYNC VERIFIED');
process.exit(failed.length ? 1 : 0);
