// Proof that deploying the Round 4 build does not disturb the four labellers
// who are mid-way through Round 3.
//
// The risk this covers is specific. Labels live only in localStorage, under a
// key built from the dataset fingerprint and the assignment id. Both are
// embedded in index.html at build time, so a careless rebuild silently renames
// the key: the labeller opens the same link, sees an empty app, and their work
// is stranded under a key nothing reads -- with the import guard refusing their
// own backup file for good measure.
//
// So this does the real thing rather than a proxy for it. It serves the
// CURRENTLY DEPLOYED build over http, labels points as AP, then swaps the new
// build in at the same origin and path -- exactly what the Pages deploy does --
// and reloads. The labels must still be there.
//
// Run:  node inspector/verify_round4_migration.mjs
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';
import { createServer } from 'http';
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from 'fs';
import { tmpdir } from 'os';
import { fileURLToPath } from 'url';
import path from 'path';

const here = path.dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(path.join(tmpdir(), 'thicket-migration-'));
const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${name}${detail ? ' — ' + detail : ''}`);
};

// The deployed build is whatever is committed: the Pages workflow rebuilds from
// the tree, and the last run's output was verified byte-identical to the live
// page. Read it from git so the test does not depend on the network.
const deployed = execFileSync('git', ['show', 'HEAD:inspector/index.html'],
  { cwd: path.dirname(here), maxBuffer: 64 << 20, encoding: 'utf8' });
const built = readFileSync(path.join(here, 'index.html'), 'utf8');
const manifest = JSON.parse(readFileSync(path.join(here, 'assignment_manifest.json'), 'utf8'));

const idOf = html => (html.match(/const DATASET_ID = '([0-9a-f]+)'/) || [])[1];
const assignmentIdsOf = html =>
  [...html.matchAll(/"assignment_id":"([0-9a-f]+)"/g)].map(m => m[1]);

// ---------------------------------------------------------------- static facts
check('campaign dataset fingerprint is unchanged',
  idOf(deployed) === idOf(built), `${idOf(deployed)} -> ${idOf(built)}`);

const oldIds = assignmentIdsOf(deployed);
const newIds = assignmentIdsOf(built);
check('every deployed assignment id survives verbatim',
  oldIds.every(id => newIds.includes(id)), `${oldIds.length} existing, ${newIds.length} total`);

const oldManifest = JSON.parse(
  execFileSync('git', ['show', 'HEAD:inspector/assignment_manifest.json'],
    { cwd: path.dirname(here), maxBuffer: 8 << 20, encoding: 'utf8' }));
const carriedOK = Object.entries(oldManifest.labelers).every(([code, rec]) => {
  const now = manifest.labelers[code];
  return now && now.assignment_id === rec.assignment_id
    && now.point_ids.length === rec.point_ids.length
    && now.point_ids.every((id, i) => id === rec.point_ids[i]);
});
check('existing point lists are identical, element for element', carriedOK);

const newPointIds = new Set(manifest.extensions.at(-1).new_points);
const noLeak = Object.keys(oldManifest.labelers)
  .every(code => manifest.labelers[code].point_ids.every(id => !newPointIds.has(id)));
check('no Round 4 point lands in an existing assignment', noLeak,
  `${newPointIds.size} new points`);

// ------------------------------------------------------------ live swap test
const server = createServer((req, res) => {
  const name = (req.url.split('?')[0] === '/' ? '/index.html' : req.url.split('?')[0]);
  const file = path.join(dir, path.basename(name));
  if (!existsSync(file)) { res.writeHead(404); res.end('no'); return; }
  res.writeHead(200, { 'Content-Type': name.endsWith('.json') ? 'application/json' : 'text/html',
                       'Cache-Control': 'no-store' });
  res.end(readFileSync(file));
});
await new Promise(r => server.listen(8731, '127.0.0.1', r));
const origin = 'http://127.0.0.1:8731';

writeFileSync(path.join(dir, 'index.html'), deployed);

const browser = await chromium.launch();
// Block service workers: this test is about localStorage, and a cached shell
// would mask the very swap we are trying to observe.
const context = await browser.newContext({ serviceWorkers: 'block' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e.message)));

// --- 1. work as AP on the deployed build
await page.goto(`${origin}/?assignment=AP`, { waitUntil: 'load' });
await page.fill('#labelerName', 'Alastair');
await page.click('#startBtn');
await page.click('.phead h1');
// Auto-advance moves to the next point 450 ms after a label lands; press slower
// than that or every keystroke re-labels the same point.
for (const key of ['1', '2', '3', '1', '2']) {
  await page.keyboard.press(key);
  await page.waitForTimeout(700);
}
const beforeState = await page.evaluate(() => ({
  required: window.REQUIRED_POINTS.length,
  assignment: window.ASSIGNMENT.id,
  labels: JSON.parse(JSON.stringify(labels)),
  keys: Object.keys(localStorage).filter(k => k.startsWith('thicket-inspector')).sort(),
}));
check('deployed build accepted labels as AP', Object.keys(beforeState.labels).length >= 4,
  `${Object.keys(beforeState.labels).length} labels, ${beforeState.required} required points`);

// --- 2. deploy: replace index.html at the same URL
writeFileSync(path.join(dir, 'index.html'), built);

// --- 3. AP comes back to the same link
await page.goto(`${origin}/?assignment=AP`, { waitUntil: 'load' });
const afterState = await page.evaluate(() => ({
  required: window.REQUIRED_POINTS.length,
  assignment: window.ASSIGNMENT.id,
  points: window.POINTS.length,
  all: ALL_POINTS.length,
  labels: JSON.parse(JSON.stringify(labels)),
  keys: Object.keys(localStorage).filter(k => k.startsWith('thicket-inspector')).sort(),
  name: localStorage.getItem('thicket-inspector-name'),
}));

check('AP keeps every label across the deploy',
  JSON.stringify(afterState.labels) === JSON.stringify(beforeState.labels),
  `${Object.keys(afterState.labels).length} labels`);
check('AP keeps the same storage keys',
  JSON.stringify(afterState.keys) === JSON.stringify(beforeState.keys),
  `${afterState.keys.length} keys`);
check('AP keeps the same assignment and workload',
  afterState.assignment === beforeState.assignment
  && afterState.required === beforeState.required,
  `${afterState.required} required points`);
check('AP sees the grown dataset but not the new points',
  afterState.all === 4197 && afterState.required === 601,
  `${afterState.all} points in the build, ${afterState.points} visible to AP`);
check('AP is not asked to re-enter their name', afterState.name === 'Alastair');

// --- 4. an old export still imports into the new build
await page.click('#startBtn');
await page.click('#downloadBtn');
const dl = page.waitForEvent('download');
await page.click('#finalDownload');
const file = await (await dl).path();
await page.setInputFiles('#uploadInput', file);
await page.waitForTimeout(500);
const importOK = await page.evaluate(() =>
  !document.querySelector('#importPreview').classList.contains('hidden')
  && Number(document.querySelector('#impValid').textContent) > 0);
check('a backup exported before the deploy still imports after it', importOK);

// --- 5. a new labeller can work
await page.goto(`${origin}/?assignment=AM`, { waitUntil: 'load' });
const nl = await page.evaluate(() => ({
  required: window.REQUIRED_POINTS.length,
  labels: Object.keys(labels).length,
  blocked: document.querySelector('#startBtn').disabled,
}));
check('new labeller AM gets a working link with independent storage',
  !nl.blocked && nl.required === manifest.labelers.AM.point_ids.length && nl.labels === 0,
  `${nl.required} points, ${nl.labels} labels`);

await page.fill('#labelerName', 'New One');
await page.click('#startBtn');
await page.click('.phead h1');
await page.keyboard.press('1');
await page.waitForTimeout(120);
const nlLabelled = await page.evaluate(() => Object.keys(labels).length);
check('new labeller can label', nlLabelled === 1);

// --- 6. and doing so did not touch AP
await page.goto(`${origin}/?assignment=AP`, { waitUntil: 'load' });
const apStill = await page.evaluate(() => JSON.parse(JSON.stringify(labels)));
check("a new labeller's work does not touch AP's",
  JSON.stringify(apStill) === JSON.stringify(beforeState.labels));

check('no page errors', errors.length === 0, errors.slice(0, 2).join(' | '));

await browser.close();
server.close();

const pass = results.every(r => r.ok);
console.log(pass ? '\n✅ ROUND 4 MIGRATION PASSED' : '\n❌ ROUND 4 MIGRATION FAILED');
process.exit(pass ? 0 : 1);
