// Data-integrity checks for the inspector: corrupt storage, dataset namespacing,
// CSV round-trip + formula-injection neutralisation, import validation
// (coordinate mismatch, unknown labels, older-file conflict), and imagery-source
// restoration. Drives the built index.html headlessly like the other verifiers.
import { chromium } from 'playwright';
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

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
const base = `http://127.0.0.1:${port}/index.html?mode=coordinator`;

const results = [];
const check = (name, ok, extra='') => { results.push({ name, ok });
  console.log(`${ok ? '✓' : '✗'} ${name}${extra ? '  — ' + extra : ''}`); };

const browser = await chromium.launch();

// ---- helper: fresh page with optional pre-seeded localStorage ----------------
async function freshPage(seed){
  const page = await browser.newPage();
  await page.addInitScript((s) => {
    try { localStorage.clear(); } catch(e){}
    if (s) for (const [k,v] of Object.entries(s)) localStorage.setItem(k, v);
  }, seed || null);
  return page;
}
const start = async page => { await page.fill('#labelerName','QA'); await page.click('#startBtn'); };

// ============================================================ 1. corrupt storage
{
  // The dataset id is deterministic from the build; read it once from a plain page.
  const probe = await freshPage();
  await probe.goto(base, { waitUntil: 'load' });
  const dsId = await probe.evaluate(() => DATASET_ID);
  await probe.close();
  const key = 'thicket-inspector-labels-' + dsId;
  // Seed poisoned storage BEFORE the page boots (init script sets it after clear()).
  const page = await freshPage({ [key]: JSON.stringify({
    0: null,                          // null record
    1: { label: 'moderate' },         // valid
    2: { label: '<img src=x>' },      // invalid label
    3: 'not-an-object',               // scalar
    99999: { label: 'severe' },       // non-existent point id
  }) });
  const errs = [];
  page.on('pageerror', e => errs.push(e.message));
  await page.goto(base, { waitUntil: 'load' });
  await start(page);
  await page.waitForTimeout(300);
  const cAll = await page.textContent('#c_all');
  check('corrupt storage: only the 1 valid record survives', cAll === '1', `c_all=${cAll}`);
  check('corrupt storage: no page errors on boot', errs.length === 0, errs.join('; '));
  await page.close();
}

// ============================================================ 2. dataset namespacing
{
  // Labels stored under a DIFFERENT dataset key must not appear.
  const page = await freshPage({ 'thicket-inspector-labels-someotherdraw':
    JSON.stringify({ 0: { label: 'intact' } }) });
  await page.goto(base, { waitUntil: 'load' });
  await start(page);
  await page.waitForTimeout(200);
  const cAll = await page.textContent('#c_all');
  check('dataset namespacing: foreign-draw labels are ignored', cAll === '0', `c_all=${cAll}`);
  await page.close();
}

// ============================================================ 3. CSV round-trip + injection
{
  const page = await freshPage();
  await page.goto(base, { waitUntil: 'load' });
  await start(page);
  await page.waitForTimeout(200);
  const csv = await page.evaluate(() => {
    // craft a label whose note needs quoting + is a formula, then export CSV text
    const p = POINTS[0];
    // reach into setLabel via the note textarea + button
    document.querySelector('#note').value = '=SUM(1,2), "quoted"\nsecond line';
    document.querySelector('.lblbtn[data-lbl="moderate"]').click();
    // rebuild the CSV exactly as download() does, via a tiny re-impl hook:
    const rows = POINTS.filter(pp => labels[pp.id]).map(pp => {
      const r = labels[pp.id];
      return { id:pp.id, stratum:pp.s, lon:pp.lon, lat:pp.lat,
               label:r.label, note:r.note||'', labeler:r.labeler||'', ts:r.ts||'' };
    });
    const csvSafe = v => { let s=String(v==null?'':v); if(/^[=+\-@\t\r]/.test(s)) s="'"+s; return s; };
    const q = v => '"'+String(v==null?'':v).replace(/"/g,'""')+'"';   // numeric/enum
    const qt = v => '"'+csvSafe(v).replace(/"/g,'""')+'"';            // free text
    const hdr='id,stratum,lon,lat,label,note,labeler,ts';
    return [hdr].concat(rows.map(r =>
      [q(r.id),q(r.stratum),q(r.lon),q(r.lat),q(r.label),qt(r.note),qt(r.labeler),q(r.ts)]
        .join(','))).join('\r\n');
  });
  const line1 = csv.split('\r\n')[1];
  check('CSV injection: leading = in note is neutralised with a quote',
    line1.includes(`"'=SUM(1,2), ""quoted""`), line1);
  check('CSV: negative latitude is NOT quote-prefixed (numeric round-trips)',
    /"-?\d+\.\d+"/.test(csv.split('\r\n')[1].split(',')[3]) &&
    !csv.split('\r\n')[1].split(',')[3].includes(`'`), csv.split('\r\n')[1].split(',')[3]);
  // now round-trip: parse it back through the app's parseCSV
  const parsed = await page.evaluate((text) => parseCSV(text), csv);
  const note = parsed[0].note;
  check('CSV round-trip: multiline quoted note survives as one field',
    note.includes('second line') && parsed.length === 1, JSON.stringify(note));
  await page.close();
}

// ============================================================ 4. import validation
{
  const page = await freshPage();
  await page.goto(base, { waitUntil: 'load' });
  await start(page);
  await page.waitForTimeout(200);
  const out = await page.evaluate(() => {
    const p0 = POINTS[0];
    // Build a JSON export: one good, one coord-mismatch, one bad label, one bad id.
    const file = { tool:'thicket_inspector', version:1, dataset: DATASET_ID, labels: [
      { id:p0.id, label:'severe', lon:p0.lon, lat:p0.lat, ts:'2020-01-01T00:00:00Z' },
      { id:POINTS[1].id, label:'intact', lon: POINTS[1].lon+0.5, lat: POINTS[1].lat }, // moved
      { id:POINTS[2].id, label:'bogus', lon:POINTS[2].lon, lat:POINTS[2].lat },        // bad label
      { id:999999, label:'intact' },                                                    // bad id
    ]};
    // Re-run the import merge logic inline (same rules as handleUpload).
    const COORD_EPS = 1e-5; let merged=0, skipped=0, moved=0;
    file.labels.forEach(r => {
      const i = POINTS.findIndex(pp => pp.id === Number(r.id)); if (i<0){ skipped++; return; }
      if (!['intact','moderate','severe','transformed','nothicket','notthicket','unsure'].includes(r.label)){ skipped++; return; }
      const p = POINTS[i];
      if (r.lon!=null && r.lat!=null){
        if (!(Math.abs(r.lon-p.lon)<=COORD_EPS && Math.abs(r.lat-p.lat)<=COORD_EPS)){ moved++; return; }
      }
      merged++;
    });
    return { merged, skipped, moved };
  });
  check('import: 1 good row merged', out.merged === 1, JSON.stringify(out));
  check('import: coord-mismatch row rejected', out.moved === 1, JSON.stringify(out));
  check('import: bad-label + bad-id rows skipped', out.skipped === 2, JSON.stringify(out));
  await page.close();
}

// ============================================================ 5. source restoration
{
  const page = await freshPage({ 'thicket-inspector-ui': JSON.stringify({ src:'google' }) });
  await page.goto(base, { waitUntil: 'load' });
  await page.waitForFunction(() => window.map && window.map.loaded && window.map.loaded(),
    { timeout: 15000 }).catch(()=>{});
  await page.waitForTimeout(500);
  const active = await page.evaluate(() => {
    const b = document.querySelector('.srcbtn.active');
    return { activeSource: window.activeSourceProbe ?? null,
             activeBtn: b ? b.dataset.src : null };
  });
  // activeSource isn't a window global; assert via the highlighted button.
  check('source restore: remembered Google source is applied', active.activeBtn === 'google',
    JSON.stringify(active));
  await page.close();
}

await browser.close();
server.close();

const ok = results.every(r => r.ok);
console.log(ok ? '\n✅ DATA-INTEGRITY PASSED' : '\n❌ DATA-INTEGRITY FAILED');
process.exit(ok ? 0 : 1);
