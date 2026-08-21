/**
 * Google Apps Script receiver for inspector/sync_config.json.
 *
 * Setup:
 * 1. Create the destination Google Sheet, then Extensions -> Apps Script.
 * 2. Paste this file into Code.gs and set EXPECTED_DATASET below after running
 *    `py -3 inspector/build.py` (the dataset id is printed by the build).
 *    This must equal the id in inspector/dataset_lineage.json, which is pinned
 *    for the life of the campaign: a mismatch is not a warning, it is a silent
 *    outage -- every POST is rejected with "Wrong dataset" while the app goes on
 *    reporting that labels save locally, which they do. Round 3 shipped with
 *    this constant still holding the Round 2 id.
 * 3. Deploy -> New deployment -> Web app; execute as yourself and grant the
 *    campaign labellers access. Copy the /exec URL into sync_config.json.
 *
 * Updating an existing deployment: Deploy -> Manage deployments -> the pencil
 * icon -> Version: New version -> Deploy. That keeps the /exec URL, which is
 * baked into the site through the GOOGLE_SHEETS_SYNC_ENDPOINT repo variable.
 * "New deployment" mints a DIFFERENT URL and every labeller silently stops
 * syncing until the variable is updated and the site rebuilt.
 *
 * The Labels tab is the current upserted state. Events is an append-only audit
 * trail. The static inspector cannot keep a secret, so dataset validation is a
 * guardrail, not authentication; choose the narrowest deployment access that
 * works for the campaign.
 */
const EXPECTED_DATASET = '6bda2aff9e69194f';
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

// The client sends {tool, version:2, events:[...]} and waits for this reply
// before it drops anything from its outbox, so an error here is a retry, never
// a lost label. A single v1 event is still accepted: an older tab must keep
// working while the campaign updates.
//
// Batching is what makes a catch-up finish. One event per request meant one
// document lock, one full-column search and one sheet write per label; six
// labellers replaying 600 labels each took hours and lost whatever timed out
// waiting for the lock. A batch takes the lock once, reads the key column once,
// and writes appends in a single call.
function doPost(e) {
  const lock = LockService.getDocumentLock();
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    const events = batchEvents_(body);
    events.forEach(validate_);
    lock.waitLock(30000);
    applyEvents_(events, new Date().toISOString());
    return json_({ok:true, accepted:events.length,
                  event_id:events.length === 1 ? events[0].event_id : undefined});
  } catch (error) {
    return json_({ok:false, error:String(error && error.message || error)});
  } finally {
    try { lock.releaseLock(); } catch (_) {}
  }
}

function batchEvents_(body) {
  if (body && body.version === 2 && Array.isArray(body.events)) {
    if (!body.events.length) throw new Error('Empty batch');
    if (body.events.length > 200) throw new Error('Batch too large');
    return body.events;
  }
  if (body && body.version === 1) return [body];
  throw new Error('Unsupported payload');
}

function applyEvents_(events, now) {
  const labels = sheet_(LABELS_SHEET, LABEL_HEADERS);
  // One read of the key column, not one search per event. TextFinder over A:A
  // rescans the whole sheet every call and gets slower as the campaign grows.
  const lastRow = labels.getLastRow();
  const rowOf = {};
  if (lastRow > 1) {
    const keys = labels.getRange(2, 1, lastRow - 1, 1).getValues();
    for (var i = 0; i < keys.length; i++) rowOf[String(keys[i][0])] = i + 2;
  }
  const appends = [];
  events.forEach(function (b) {
    const row = labelRow_(b, now);
    const at = rowOf[row[0]];
    if (at) labels.getRange(at, 1, 1, row.length).setValues([row]);
    else {
      // A batch can carry two edits of the same point; the later one wins, and
      // the pair must not become two rows.
      rowOf[row[0]] = lastRow + appends.length + 1;
      appends.push(row);
    }
  });
  if (appends.length) {
    labels.getRange(labels.getLastRow() + 1, 1, appends.length, LABEL_HEADERS.length)
          .setValues(appends);
  }
  const events_ = sheet_(EVENTS_SHEET, EVENT_HEADERS);
  const trail = events.map(function (b) { return eventRow_(b, now); });
  events_.getRange(events_.getLastRow() + 1, 1, trail.length, EVENT_HEADERS.length)
         .setValues(trail);
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

function eventRow_(b, now) {
  const a = b.assignment || {}, r = b.record || {}, p = b.point || {};
  return [
    safe_(b.event_id), now, safe_(b.action), safe_(b.dataset), safe_(b.campaign),
    safe_(a.code), safe_(a.id), safe_(b.labeler), Number(p.id), safe_(r.label), safe_(r.ts)
  ];
}

// Column A is the upsert key: one row per (dataset, assignment, labeller,
// point). Replaying an event overwrites its own row, which is what makes a
// catch-up idempotent and a retry harmless.
function labelRow_(b, now) {
  const a = b.assignment || {}, r = b.record || {}, p = b.point || {};
  const key = [b.dataset, a.id || 'coordinator', b.labeler || 'anon', p.id].join('|');
  return [
    key,safe_(b.dataset),safe_(b.campaign),safe_(a.code),safe_(a.id),safe_(b.labeler),Number(p.id),
    safe_(p.source),!!p.required,!!p.disagreement,safe_(p.stratum),safe_(p.mapcode),safe_(p.vegtype),safe_(p.efg),
    numberOrBlank_(p.cls2022),numberOrBlank_(p.cls2025),numberOrBlank_(p.lon),numberOrBlank_(p.lat),
    b.action === 'clear' ? '' : safe_(r.label),b.action === 'clear' ? '' : safe_(r.note),
    b.action === 'clear' ? false : !!r.flagged,b.action === 'clear' ? '' : safe_(r.confidence),
    b.action === 'clear' ? '' : safe_((r.reasons || []).join('|')),safe_(r.ts),safe_(b.event_id),now
  ];
}

function safe_(value) {
  const text = value == null ? '' : String(value);
  return /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
}
function numberOrBlank_(value) { return Number.isFinite(Number(value)) ? Number(value) : ''; }
function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}
