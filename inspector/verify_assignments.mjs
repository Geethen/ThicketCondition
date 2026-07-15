// End-to-end verification of deterministic pre-assigned point lists.
import { chromium } from 'playwright';
import { execFileSync } from 'child_process';
import { mkdtempSync, readFileSync } from 'fs';
import { tmpdir } from 'os';
import { fileURLToPath } from 'url';
import path from 'path';

const here = path.dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(path.join(tmpdir(), 'thicket-assignments-'));
const manifestPath = path.join(dir, 'assignments.json');
const htmlPath = path.join(dir, 'index.html');
const py = process.platform === 'win32' ? 'py' : 'python3';
const pyPrefix = process.platform === 'win32' ? ['-3'] : [];

execFileSync(py, [...pyPrefix, path.join(here, 'create_assignments.py'),
  '--campaign', 'test-campaign', '--labelers', 'ALPHA', 'BETA', 'GAMMA',
  '--overlap', '0.12', '--output', manifestPath], {stdio:'inherit'});
execFileSync(py, [...pyPrefix, path.join(here, 'build.py'),
  '--assignments', manifestPath, '--out', htmlPath], {stdio:'inherit'});

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
const occurrence = new Map();
for (const record of Object.values(manifest.labelers)) {
  for (const id of record.point_ids) occurrence.set(id, (occurrence.get(id)||0)+1);
}
const overlap = new Set(manifest.qa_overlap_point_ids);
const allocationOK = occurrence.size === 846
  && [...occurrence].every(([id,n]) => n === (overlap.has(id)?2:1))
  && Math.max(...Object.values(manifest.labelers).map(r=>r.point_ids.length))
     - Math.min(...Object.values(manifest.labelers).map(r=>r.point_ids.length)) <= 2;
console.log('balanced coverage and deliberate overlap:', allocationOK);

const url = 'file://' + htmlPath.replace(/\\/g, '/');
const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(url+'?assignment=alpha', {waitUntil:'load'});
const alphaExpected = manifest.labelers.ALPHA.point_ids.length;
const alphaOK = await page.evaluate(expected =>
  window.POINTS.length === expected
  && window.ASSIGNMENT.code === 'ALPHA'
  && document.querySelector('#assignmentStatus').textContent.includes(`${expected} points`), alphaExpected);
await page.fill('#labelerName','Alice');
await page.click('#startBtn');
await page.click('.phead h1');
await page.keyboard.press('1');
await page.waitForTimeout(100);
const isolatedKeyOK = await page.evaluate(id =>
  Object.keys(localStorage).some(k=>k.includes(id) && k.includes('labels')),
  manifest.labelers.ALPHA.assignment_id);
await page.click('#downloadBtn');
const downloadPromise = page.waitForEvent('download');
await page.click('#finalDownload');
const download = await downloadPromise;
const exported = JSON.parse(readFileSync(await download.path(), 'utf8'));
const exportOK = exported.assignment.code === 'ALPHA'
  && exported.assignment.campaign === 'test-campaign'
  && exported.assignment.id === manifest.labelers.ALPHA.assignment_id
  && download.suggestedFilename().includes('ALPHA');
console.log('assignment link, storage, and export metadata:', alphaOK, isolatedKeyOK, exportOK);

await page.goto(url+'?assignment=BETA', {waitUntil:'load'});
const betaOK = await page.evaluate(expected =>
  window.POINTS.length === expected && Object.keys(window.labels).length === 0,
  manifest.labelers.BETA.point_ids.length);
console.log('second assignment has independent progress:', betaOK);

await page.goto(url+'?assignment=DOES_NOT_EXIST', {waitUntil:'load'});
const invalidBlocked = await page.evaluate(() =>
  window.POINTS.length === 0
  && document.querySelector('#startBtn').disabled
  && document.querySelector('#introAssignment').classList.contains('error'));
console.log('invalid assignment link blocked:', invalidBlocked);

await page.goto(url, {waitUntil:'load'});
const bareLinkBlocked = await page.evaluate(() =>
  window.POINTS.length === 0 && document.querySelector('#startBtn').disabled);
await page.goto(url+'?mode=coordinator', {waitUntil:'load'});
const coordinatorOK = await page.evaluate(() =>
  window.POINTS.length === 846 && !document.querySelector('#startBtn').disabled);
console.log('bare campaign link blocked; coordinator override works:', bareLinkBlocked, coordinatorOK);

await browser.close();
const pass = allocationOK && alphaOK && isolatedKeyOK && exportOK && betaOK
  && invalidBlocked && bareLinkBlocked && coordinatorOK;
console.log(pass ? '\n✅ ASSIGNMENTS PASSED' : '\n❌ ASSIGNMENTS FAILED');
process.exit(pass ? 0 : 1);
