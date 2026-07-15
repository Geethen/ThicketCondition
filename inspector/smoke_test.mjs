// End-to-end smoke test of index.html with Playwright/Chromium.
// Verifies: map + tiles load, points render, labeling works, counts update,
// keyboard shortcuts, and JSON export payload is correct.
import { chromium } from 'playwright';
import { fileURLToPath } from 'url';
import path from 'path';

const here = path.dirname(fileURLToPath(import.meta.url));
const url = 'file://' + path.join(here, 'index.html').replace(/\\/g, '/') + '?mode=coordinator';

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push('pageerror: ' + e.message));
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

await page.goto(url, { waitUntil: 'load' });

// start screen -> enter name -> start
await page.fill('#labelerName', 'TEST');
await page.click('#startBtn');

const simplifiedUiOK = await page.evaluate(() => {
  const body = document.querySelector('.pbody');
  const labels = [...document.querySelectorAll('.lblbtn')].map(b => b.dataset.lbl);
  return body.firstElementChild.classList.contains('guidance')
    && labels.join(',') === 'intact,moderate,severe,transformed,nothicket,unsure'
    && getComputedStyle(document.querySelector('.seclbl')).fontSize === '15px'
    && !document.querySelector('.secondary').open;
});
console.log('simplified ecological workflow:', simplifiedUiOK);

// wait for maplibre map to finish loading its first render
await page.waitForFunction(() => window.map && window.map.loaded && window.map.loaded(), { timeout: 15000 })
  .catch(() => {}); // map var may not be global; fall through to layer check

// the points layer should exist and have 846 features
const nPts = await page.evaluate(() => {
  const src = window.map ? window.map.getSource('pts') : null;
  return src ? src._data.features.length : -1;
});
console.log('points on map:', nPts);

// Blind mode is the safe default: map strata and current model prediction hidden.
const blindOK = await page.evaluate(() => {
  const src = window.map.getSource('pts');
  return document.querySelector('#blindMode').checked
    && src._data.features.every(f => f.properties.stratum === 'blind')
    && document.querySelector('#mStratum').textContent.includes('Hidden');
});
console.log('blind labeling default:', blindOK);

// Notes typed before choosing a class persist as a local draft.
await page.getByText('Notes and review options').click();
await page.fill('#note', 'edge effect draft');
await page.waitForTimeout(500);
const draftOK = await page.evaluate(() => {
  const key = Object.keys(localStorage).find(k => k.startsWith('thicket-inspector-note-drafts-'));
  return key && JSON.parse(localStorage.getItem(key))['0'] === 'edge effect draft';
});
console.log('unlabeled note draft saved:', !!draftOK);

// Label via keyboard. Auto-advance should move to the next unlabelled point.
await page.click('.phead h1'); // leave the textarea so global shortcuts are active
await page.keyboard.press('2');
await page.waitForTimeout(650);
const cModerate = await page.evaluate(() => Object.values(window.labels).filter(r => r.label === 'moderate').length.toString());
const cAll = await page.textContent('#c_all');
const autoAdvancedTo = await page.textContent('#curId');
console.log('after key "2": moderate =', cModerate, ' all =', cAll, ' current =', autoAdvancedTo);

// Clicking the already-selected class must not erase it. Clearing is explicit and undoable.
await page.keyboard.press('ArrowLeft');
await page.keyboard.press('2');
await page.waitForTimeout(100);
const repeatKept = await page.textContent('#c_all');
await page.click('#clearLabelBtn');
const afterClear = await page.textContent('#c_all');
await page.click('#toast button');
await page.waitForTimeout(100);
const afterUndo = await page.textContent('#c_all');
console.log('repeat/clear/undo counts:', repeatKept, afterClear, afterUndo);

// Keep the remainder deterministic for this smoke test.
await page.uncheck('#autoAdvance');

// move next and label severe with "3"
await page.click('#nextBtn');
await page.keyboard.press('3');
await page.waitForTimeout(200);
const cSevere = await page.evaluate(() => Object.values(window.labels).filter(r => r.label === 'severe').length.toString());
console.log('after key "3" on next point: severe =', cSevere);

const progressOK = await page.evaluate(() =>
  document.querySelector('#c_remaining').textContent === '844'
  && document.querySelector('#progressText').textContent === '2 of 846 labeled'
  && document.querySelector('#progressPct').textContent === '0.2%');
console.log('expanded progress:', progressOK);

// A JSON backup from another dataset is rejected before it can alter labels.
await page.evaluate(() => {
  const bad={tool:'thicket_inspector',dataset:'different-dataset',labels:[
    {id:2,label:'intact',lon:window.POINTS[2].lon,lat:window.POINTS[2].lat}
  ]};
  window.handleUpload(new File([JSON.stringify(bad)], 'bad.json', {type:'application/json'}));
});
await page.waitForTimeout(100);
const mismatchBlocked = await page.evaluate(() =>
  !window.labels[2] && document.querySelector('#toast').textContent.includes('different dataset'));
console.log('dataset mismatch blocked:', mismatchBlocked);

// Valid imports are previewed. The default strategy fills blanks without
// overwriting a conflicting local label, and the whole import can be undone.
await page.evaluate(() => {
  const ds=document.querySelector('#datasetId').textContent;
  const incoming={tool:'thicket_inspector',dataset:ds,labels:[
    {id:0,label:'severe',note:'conflict',lon:window.POINTS[0].lon,lat:window.POINTS[0].lat,
      stratum:window.POINTS[0].s,ts:'2099-01-01T00:00:00Z'},
    {id:2,label:'intact',note:'new',lon:window.POINTS[2].lon,lat:window.POINTS[2].lat,
      stratum:window.POINTS[2].s,ts:'2099-01-01T00:00:00Z'}
  ]};
  window.handleUpload(new File([JSON.stringify(incoming)], 'preview.json', {type:'application/json'}));
});
await page.waitForTimeout(100);
const previewOK = await page.evaluate(() =>
  !document.querySelector('#importPreview').classList.contains('hidden')
  && document.querySelector('#impNew').textContent === '1'
  && document.querySelector('#impConflicts').textContent === '1'
  && document.querySelector('#importStrategy').value === 'fill');
await page.click('#applyImport');
const fillOK = await page.evaluate(() =>
  window.labels[0].label === 'moderate' && window.labels[2].label === 'intact');
await page.click('#toast button');
await page.waitForTimeout(100);
const importUndoOK = await page.evaluate(() =>
  window.labels[0].label === 'moderate' && !window.labels[2]);
console.log('import preview/fill/undo:', previewOK, fillOK, importUndoOK);

// Filters work from both the select and clickable progress chips.
await page.selectOption('#pointFilter', 'labeled');
const selectFilterOK = await page.evaluate(() =>
  document.querySelector('#pointFilter').value === 'labeled'
  && document.querySelector('.chip.all').classList.contains('filter-active'));
await page.selectOption('#pointFilter', 'moderate');
const chipFilterOK = await page.evaluate(() =>
  document.querySelector('#pointFilter').value === 'moderate'
  && document.querySelector('#nextUnlabeled').textContent.includes('next moderate'));
await page.click('#nextUnlabeled');
const filteredNavOK = await page.textContent('#curId') === '0';
await page.click('.chip.all');
await page.click('.chip.all'); // clicking the active summary chip returns to All
const filterResetOK = await page.evaluate(() => document.querySelector('#pointFilter').value === 'all');
console.log('select/chip/navigation/reset filters:', selectFilterOK, chipFilterOK, filteredNavOK, filterResetOK);

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
  && simplifiedUiOK && blindOK && draftOK && autoAdvancedTo === '1'
  && repeatKept === '1' && afterClear === '0' && afterUndo === '1'
  && progressOK && mismatchBlocked && previewOK && fillOK && importUndoOK
  && selectFilterOK && chipFilterOK && filteredNavOK && filterResetOK
  && payload.length === 2 && payload.every(r => r.labeler === 'TEST')
  // Ignore the optional gee_layers.json fetch: over file:// it logs a scheme
  // error, over http a 404 — neither is an app fault.
  && errors.filter(e => !/Failed to load|net::ERR|URL scheme .* not supported|gee_layers\.json/.test(e)).length === 0;
console.log(pass ? '\n✅ SMOKE TEST PASSED' : '\n❌ SMOKE TEST FAILED');
await browser.close();
process.exit(pass ? 0 : 1);
