/**
 * Google Apps Script receiver for inspector/sync_config.json.
 *
 * Setup:
 * 1. Create the destination Google Sheet, then Extensions -> Apps Script.
 * 2. Paste this file into Code.gs and set EXPECTED_DATASET below after running
 *    `py -3 inspector/build.py` (the dataset id is printed by the build).
 * 3. Deploy -> New deployment -> Web app; execute as yourself and grant the
 *    campaign labellers access. Copy the /exec URL into sync_config.json.
 *
 * The Labels tab is the current upserted state. Events is an append-only audit
 * trail. The static inspector cannot keep a secret, so dataset validation is a
 * guardrail, not authentication; choose the narrowest deployment access that
 * works for the campaign.
 */
const EXPECTED_DATASET = 'd085313296f1b27e';
const LABELS_SHEET = 'Labels';
const EVENTS_SHEET = 'Events';

const LABEL_HEADERS = [
  'key','dataset','campaign','assignment','assignment_id','labeler','point_id',
  'source','required','disagreement','stratum','mapcode','vegtype','efg',
  'cls2022','cls2025','lon','lat','label','note','flagged','confidence','reasons',
  'label_ts','last_event','updated_utc'
];
const EVENT_HEADERS = [
  'event_id','received_utc','action','dataset','campaign','assignment',
  'assignment_id','labeler','point_id','label','label_ts'
];

function doGet() {
  return json_({ok:true, service:'thicket_inspector_sync', utc:new Date().toISOString()});
}

function doPost(e) {
  const lock = LockService.getDocumentLock();
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    validate_(body);
    lock.waitLock(20000);
    const now = new Date().toISOString();
    appendEvent_(body, now);
    upsertLabel_(body, now);
    return json_({ok:true, event_id:body.event_id});
  } catch (error) {
    return json_({ok:false, error:String(error && error.message || error)});
  } finally {
    try { lock.releaseLock(); } catch (_) {}
  }
}

function validate_(body) {
  if (body.tool !== 'thicket_inspector_sync' || body.version !== 1) throw new Error('Unsupported payload');
  if (!body.event_id || !body.dataset || !body.point || !Number.isFinite(Number(body.point.id))) throw new Error('Missing identifiers');
  if (EXPECTED_DATASET && body.dataset !== EXPECTED_DATASET) throw new Error('Wrong dataset');
  if (!['upsert','clear'].includes(body.action)) throw new Error('Bad action');
  if (body.action === 'upsert' && (!body.record || !body.record.label)) throw new Error('Missing label');
}

function sheet_(name, headers) {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = book.getSheetByName(name) || book.insertSheet(name);
  if (sheet.getLastRow() === 0) sheet.appendRow(headers);
  return sheet;
}

function appendEvent_(b, now) {
  const a = b.assignment || {}, r = b.record || {}, p = b.point || {};
  sheet_(EVENTS_SHEET, EVENT_HEADERS).appendRow([
    safe_(b.event_id), now, safe_(b.action), safe_(b.dataset), safe_(b.campaign),
    safe_(a.code), safe_(a.id), safe_(b.labeler), Number(p.id), safe_(r.label), safe_(r.ts)
  ]);
}

function upsertLabel_(b, now) {
  const a = b.assignment || {}, r = b.record || {}, p = b.point || {};
  const key = [b.dataset, a.id || 'coordinator', b.labeler || 'anon', p.id].join('|');
  const sheet = sheet_(LABELS_SHEET, LABEL_HEADERS);
  const finder = sheet.getRange('A:A').createTextFinder(key).matchEntireCell(true).findNext();
  const row = [
    key,safe_(b.dataset),safe_(b.campaign),safe_(a.code),safe_(a.id),safe_(b.labeler),Number(p.id),
    safe_(p.source),!!p.required,!!p.disagreement,safe_(p.stratum),safe_(p.mapcode),safe_(p.vegtype),safe_(p.efg),
    numberOrBlank_(p.cls2022),numberOrBlank_(p.cls2025),numberOrBlank_(p.lon),numberOrBlank_(p.lat),
    b.action === 'clear' ? '' : safe_(r.label),b.action === 'clear' ? '' : safe_(r.note),
    b.action === 'clear' ? false : !!r.flagged,b.action === 'clear' ? '' : safe_(r.confidence),
    b.action === 'clear' ? '' : safe_((r.reasons || []).join('|')),safe_(r.ts),safe_(b.event_id),now
  ];
  if (finder) sheet.getRange(finder.getRow(), 1, 1, row.length).setValues([row]);
  else sheet.appendRow(row);
}

function safe_(value) {
  const text = value == null ? '' : String(value);
  return /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
}
function numberOrBlank_(value) { return Number.isFinite(Number(value)) ? Number(value) : ''; }
function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
