/* Thicket Condition Inspector — static, no backend.
   Labels persist in localStorage and export/import as JSON or CSV. */
'use strict';

const URL_PARAMS=new URLSearchParams(location.search);
const ASSIGNMENT_REQUEST=(URL_PARAMS.get('assignment')||'').trim();
const SYNC_ENDPOINT_REQUEST=(URL_PARAMS.get('syncEndpoint')||'').trim();
const COORDINATOR_MODE=URL_PARAMS.get('mode')==='coordinator';
const ASSIGNMENT_CODES=Object.keys((ASSIGNMENT_MANIFEST&&ASSIGNMENT_MANIFEST.labelers)||{});
const ASSIGNMENT_CODE=ASSIGNMENT_REQUEST
  ? ASSIGNMENT_CODES.find(c=>c.toLowerCase()===ASSIGNMENT_REQUEST.toLowerCase())||'' : '';
const ASSIGNMENT_RECORD=ASSIGNMENT_CODE?ASSIGNMENT_MANIFEST.labelers[ASSIGNMENT_CODE]:null;
const ASSIGNMENT_ERROR=ASSIGNMENT_REQUEST&&!ASSIGNMENT_RECORD
  ? `Assignment “${ASSIGNMENT_REQUEST}” was not found. Ask the coordinator for the correct link.`
  : (!ASSIGNMENT_REQUEST&&ASSIGNMENT_CODES.length&&!COORDINATOR_MODE
    ? 'A personal assignment link is required for this campaign. Ask the coordinator for your link.' : '');
const ASSIGNED_IDS=new Set(ASSIGNMENT_RECORD?ASSIGNMENT_RECORD.point_ids:[]);
const DISAGREEMENT_IDS=new Set(Object.keys(DISAGREEMENT_MANIFEST||{}).map(Number));
// Round 2 completion is based only on the new draw. Historical disagreement
// points are optional exploration/adjudication work and never inflate remaining.
const REQUIRED_IDS=ASSIGNMENT_RECORD?ASSIGNED_IDS:
  new Set(ALL_POINTS.filter(p=>p.src==='new').map(p=>p.id));
const REQUIRED_POINTS=ALL_POINTS.filter(p=>REQUIRED_IDS.has(p.id));
const POINTS=ASSIGNMENT_RECORD
  ? ALL_POINTS.filter(p=>ASSIGNED_IDS.has(p.id)||DISAGREEMENT_IDS.has(p.id))
  : (ASSIGNMENT_ERROR?[]:ALL_POINTS);
const isRequiredPoint=p=>!!p&&REQUIRED_IDS.has(p.id);
const ASSIGNMENT_ID=ASSIGNMENT_RECORD?ASSIGNMENT_RECORD.assignment_id||'':'';
const CAMPAIGN=String((ASSIGNMENT_MANIFEST&&ASSIGNMENT_MANIFEST.campaign)||'');

const CLASSES = ['intact','moderate','severe','transformed','nothicket','unsure'];
const LEGACY_CLASS = 'notthicket';
const CLASS_SET = new Set([...CLASSES, LEGACY_CLASS]);
const CLASS_LABEL = {intact:'Intact',moderate:'Moderate',severe:'Severe',
                     transformed:'Transformed',nothicket:'No thicket',unsure:'Unsure',
                     notthicket:'Legacy: no thicket / transformed'};
// Only ever trust a label that is one of our known classes. Imported files are
// untrusted input, and labels flow into innerHTML / classList below.
const isValidClass = c => CLASS_SET.has(c);
const STRAT_COLOR = {intact:'#0a7d34',moderate:'#e0a400',severe:'#c0392b',
                     transformed:'#8c5a2b',nothicket:'#526979',notthicket:'#ffb84d'};
// DATASET_ID is injected by build.py; if the page is opened unbuilt it stays the
// literal placeholder, which still works as a (single) stable key.
const DS_ID = (typeof DATASET_ID === 'string' && !DATASET_ID.startsWith('__'))
  ? DATASET_ID : 'dev';
// Assignment IDs isolate campaigns and regenerated point lists in browser storage.
const STORAGE_SCOPE = DS_ID + (ASSIGNMENT_ID?'-assignment-'+ASSIGNMENT_ID:'');
const KEY_LABELS = 'thicket-inspector-labels-' + STORAGE_SCOPE;
const KEY_DRAFTS = 'thicket-inspector-note-drafts-' + STORAGE_SCOPE;
const KEY_REVIEWS = 'thicket-inspector-review-drafts-' + STORAGE_SCOPE;
const KEY_NAME   = 'thicket-inspector-name';
const KEY_UI     = 'thicket-inspector-ui' + (ASSIGNMENT_ID?'-'+ASSIGNMENT_ID:'');
const KEY_BACKUP = 'thicket-inspector-last-backup-' + STORAGE_SCOPE;
const KEY_WBCACHE = 'thicket-inspector-wayback-cache-' + DS_ID;
const KEY_SYNC_QUEUE = 'thicket-inspector-sheet-sync-queue-' + STORAGE_SCOPE;
const KEY_SYNC_PREFS = 'thicket-inspector-sheet-sync-prefs-' + STORAGE_SCOPE;
// Coordinates must match the embedded draw within ~1 m to count as the same point.
const COORD_EPS = 1e-5;

// ------------------------------------------------------------------ state
let labels = {};            // id -> {label, note, labeler, ts}
let noteDrafts = {};        // id -> note text before a point has a label
let reviewDrafts = {};      // id -> review metadata before a point has a label
let curIdx = -1;            // index into POINTS
let labeler = '';
let activeSource = 'esri';
let blindMode = true;
let autoAdvance = true;
let pointFilter = 'round2';
let pendingAdvance = 0;
let pendingImport = null;
let undoStack = [];
let navHistory = [], navHistoryPos = -1;
let lastBackup = localStorage.getItem(KEY_BACKUP) || '';
let wbPointCache = {};
let syncEnabled=!!(GOOGLE_SHEETS_SYNC_CONFIG&&GOOGLE_SHEETS_SYNC_CONFIG.enabled_by_default);
let syncEndpoint=SYNC_ENDPOINT_REQUEST||String((GOOGLE_SHEETS_SYNC_CONFIG&&GOOGLE_SHEETS_SYNC_CONFIG.endpoint)||'').trim();
let syncSheetUrl=String((GOOGLE_SHEETS_SYNC_CONFIG&&GOOGLE_SHEETS_SYNC_CONFIG.sheet_url)||'').trim();
let syncQueue=[];
let syncBusy=false;
let map;
let sourceErrors=0, fallbackInProgress=false;
// Esri Wayback state
const wb = {
  releases: [],      // [{num,title,date,metaUrl,tileUrl}], newest-first
  view: [],          // indices into releases currently offered in the dropdown
  idx: 0,            // active index into releases (the "A" date)
  idxB: 1,           // "B" date for swipe compare
  local: false,      // "only new imagery here" filter on
  compare: false,    // swipe compare on
  capId: 0,          // token to cancel stale capture-date lookups
  localId: 0,        // token to cancel stale "new imagery here" refreshes
};
const WB_SOURCE = 'base', WB_LAYER = 'base-layer';   // wayback reuses the base raster
let wbCmp = { map:null, f:0.5 };                       // swipe compare map + fraction

// ------------------------------------------------------------------ helpers
const $ = s => document.querySelector(s);
const byId = id => POINTS.findIndex(p => p.id === id);
function toast(msg, actionLabel, action, duration=2200){
  const t=$('#toast'); t.textContent='';
  const span=document.createElement('span'); span.textContent=msg; t.appendChild(span);
  if(actionLabel && action){
    const b=document.createElement('button'); b.textContent=actionLabel;
    b.onclick=()=>{ clearTimeout(toast._t); t.classList.remove('show'); action(); };
    t.appendChild(b);
  }
  t.classList.add('show'); clearTimeout(toast._t);
  toast._t=setTimeout(()=>t.classList.remove('show'),duration);
}

// Accept only a plain object of {id -> valid record for a current point}.
function sanitizeLabels(raw){
  const clean={};
  if(!raw || typeof raw!=='object' || Array.isArray(raw)) return clean;
  for(const [k,r] of Object.entries(raw)){
    if(!r || typeof r!=='object' || Array.isArray(r)) continue;
    if(!isValidClass(r.label)) continue;
    const i=byId(Number(k)); if(i<0) continue;               // not a current point
    const p=POINTS[i];
    clean[p.id]={ label:r.label, note:String(r.note||''),
                  labeler:String(r.labeler||''),
                  ts:typeof r.ts==='string'?r.ts:'',
                  flagged:!!r.flagged,
                  confidence:['high','medium','low'].includes(r.confidence)?r.confidence:'',
                  reasons:Array.isArray(r.reasons)?r.reasons.map(String).slice(0,12):[],
                  stratum:p.s, lon:p.lon, lat:p.lat };
  }
  return clean;
}
function sanitizeReviewDrafts(raw){
  const clean={};
  if(!raw || typeof raw!=='object' || Array.isArray(raw)) return clean;
  for(const [k,r] of Object.entries(raw)){
    if(!r || typeof r!=='object' || Array.isArray(r)) continue;
    const i=byId(Number(k)); if(i<0) continue;
    const p=POINTS[i]; if(labels[p.id]) continue;
    const draft={flagged:!!r.flagged,
      confidence:['high','medium','low'].includes(r.confidence)?r.confidence:'',
      reasons:Array.isArray(r.reasons)?r.reasons.map(String).slice(0,12):[],
      ts:typeof r.ts==='string'?r.ts:''};
    if(draft.flagged||draft.confidence||draft.reasons.length) clean[p.id]=draft;
  }
  return clean;
}
function reviewState(id){ return labels[id]||reviewDrafts[id]||{flagged:false,confidence:'',reasons:[]}; }
function needsReview(p){
  const meta=reviewState(p.id), rec=labels[p.id];
  return !!(meta.flagged||meta.confidence==='low'||(rec&&(rec.label==='unsure'||rec.label===LEGACY_CLASS)));
}
function loadStore(){
  let raw={};
  try{ raw = JSON.parse(localStorage.getItem(KEY_LABELS)||'{}'); }catch(e){ raw={}; }
  labels = sanitizeLabels(raw);
  try{
    const d=JSON.parse(localStorage.getItem(KEY_DRAFTS)||'{}');
    if(d && typeof d==='object' && !Array.isArray(d)){
      Object.entries(d).forEach(([id,note])=>{
        if(byId(Number(id))>=0 && typeof note==='string') noteDrafts[id]=note.slice(0,5000);
      });
    }
  }catch(e){ noteDrafts={}; }
  try{ reviewDrafts=sanitizeReviewDrafts(JSON.parse(localStorage.getItem(KEY_REVIEWS)||'{}')); }
  catch(e){ reviewDrafts={}; }
  labeler = localStorage.getItem(KEY_NAME) || '';
  try{ wbPointCache=JSON.parse(localStorage.getItem(KEY_WBCACHE)||'{}')||{}; }catch(e){ wbPointCache={}; }
  try{
    const prefs=JSON.parse(localStorage.getItem(KEY_SYNC_PREFS)||'{}');
    if(typeof prefs.enabled==='boolean') syncEnabled=prefs.enabled;
    if(!SYNC_ENDPOINT_REQUEST&&typeof prefs.endpoint==='string') syncEndpoint=prefs.endpoint.trim();
    if(typeof prefs.sheetUrl==='string') syncSheetUrl=prefs.sheetUrl.trim();
  }catch(e){}
  try{
    const queued=JSON.parse(localStorage.getItem(KEY_SYNC_QUEUE)||'[]');
    syncQueue=Array.isArray(queued)?queued.filter(x=>x&&typeof x==='object').slice(-5000):[];
  }catch(e){syncQueue=[];}
}
function saveStore(){
  try{
    localStorage.setItem(KEY_LABELS, JSON.stringify(labels));
    localStorage.setItem(KEY_DRAFTS, JSON.stringify(noteDrafts));
    localStorage.setItem(KEY_REVIEWS, JSON.stringify(reviewDrafts));
    const el=$('#saveStatus'); if(el) el.textContent='Saved locally · '+new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})+' · backup '+(lastBackup?new Date(lastBackup).toLocaleString():'never');
    return true;
  }catch(e){
    const el=$('#saveStatus'); if(el) el.textContent='⚠ Could not save in this browser';
    toast('Could not save locally — download a backup'); return false;
  }
}

// --------------------------------------------------------- Google Sheets sync
// A deployed Google Apps Script Web App accepts these events and upserts its
// Labels sheet. Local storage remains authoritative; failed sends stay queued.
function validSyncEndpoint(value){
  try{
    const u=new URL(value);
    return u.protocol==='https:' || (u.protocol==='http:'&&['127.0.0.1','localhost'].includes(u.hostname));
  }catch(e){return false;}
}
function persistSyncState(){
  localStorage.setItem(KEY_SYNC_QUEUE,JSON.stringify(syncQueue.slice(-5000)));
  localStorage.setItem(KEY_SYNC_PREFS,JSON.stringify({enabled:syncEnabled,endpoint:syncEndpoint,sheetUrl:syncSheetUrl}));
}
function updateSyncStatus(message='',error=false){
  const box=$('#sheetSync'); if(box) box.checked=syncEnabled;
  const el=$('#syncStatus'); if(!el)return;
  let text=message;
  if(!text){
    if(!syncEnabled) text='Google Sheets sync is off · labels still save locally';
    else if(!validSyncEndpoint(syncEndpoint)) text='Google Sheets sync needs a Web App URL · labels save locally';
    else if(syncQueue.length) text=`Google Sheets sync · ${syncQueue.length} change${syncQueue.length===1?'':'s'} queued`;
    else text='Google Sheets sync ready · local backup remains enabled';
  }
  el.textContent=text;
  el.className='save-status'+(error?' sync-error':'');
  const link=$('#openSheetLink');
  if(link){link.href=syncSheetUrl||'#';link.classList.toggle('hidden',!validSyncEndpoint(syncSheetUrl));}
}
function syncEvent(point,record,action){
  const ts=(record&&record.ts)||new Date().toISOString();
  return {
    tool:'thicket_inspector_sync',version:1,
    event_id:[DS_ID,ASSIGNMENT_ID||'coordinator',labeler||'anon',point.id,ts,action].join('|'),
    dataset:DS_ID,campaign:CAMPAIGN,
    assignment:ASSIGNMENT_RECORD?{code:ASSIGNMENT_CODE,id:ASSIGNMENT_ID}:null,
    labeler:labeler||'',action,occurred:ts,
    point:{id:point.id,source:point.src,stratum:point.s,mapcode:point.mc||'',
      vegtype:point.veg||'',efg:point.efg||'',cls2022:point.c22,cls2025:point.c25,
      lon:point.lon,lat:point.lat,required:isRequiredPoint(point),
      disagreement:DISAGREEMENT_IDS.has(point.id)},
    record:record?{label:record.label,note:record.note||'',ts:record.ts||ts,
      flagged:!!record.flagged,confidence:record.confidence||'',reasons:record.reasons||[]}:null
  };
}
function enqueueSheetSync(point,record,action=record?'upsert':'clear'){
  if(!point)return;
  syncQueue.push(syncEvent(point,record,action));
  if(syncQueue.length>5000)syncQueue=syncQueue.slice(-5000);
  persistSyncState();updateSyncStatus();flushSheetSync();
}
function queueAllLabelsForSync(){
  POINTS.forEach(p=>{if(labels[p.id])syncQueue.push(syncEvent(p,labels[p.id],'upsert'));});
  if(syncQueue.length>5000)syncQueue=syncQueue.slice(-5000);
  persistSyncState();updateSyncStatus();flushSheetSync();
}
function enqueueSyncChanges(before,after){
  const ids=new Set([...Object.keys(before||{}),...Object.keys(after||{})]);
  ids.forEach(raw=>{
    const id=Number(raw),idx=byId(id),p=idx>=0?POINTS[idx]:null;
    if(!p||JSON.stringify(before&&before[id])===JSON.stringify(after&&after[id]))return;
    const rec=after&&after[id];enqueueSheetSync(p,rec||null,rec?'upsert':'clear');
  });
}
async function flushSheetSync(){
  if(syncBusy||!syncEnabled||!validSyncEndpoint(syncEndpoint)||!navigator.onLine||!syncQueue.length){updateSyncStatus();return;}
  syncBusy=true;
  try{
    while(syncEnabled&&syncQueue.length&&navigator.onLine){
      const event=syncQueue[0];
      await fetch(syncEndpoint,{method:'POST',mode:'no-cors',cache:'no-store',keepalive:true,
        headers:{'Content-Type':'text/plain;charset=utf-8'},body:JSON.stringify(event)});
      syncQueue.shift();persistSyncState();updateSyncStatus();
    }
    updateSyncStatus('Google Sheets sync sent · local backup remains enabled');
  }catch(e){updateSyncStatus(`Google Sheets sync paused · ${syncQueue.length} queued`,true);}
  finally{syncBusy=false;}
}

function snapshotUndo(description){
  undoStack.push({labels:JSON.stringify(labels),drafts:JSON.stringify(noteDrafts),reviews:JSON.stringify(reviewDrafts),description});
  if(undoStack.length>30) undoStack.shift();
}
function undoLast(){
  const u=undoStack.pop(); if(!u){ toast('Nothing to undo'); return; }
  const before=labels;
  labels=sanitizeLabels(JSON.parse(u.labels));
  try{ noteDrafts=JSON.parse(u.drafts)||{}; }catch(e){ noteDrafts={}; }
  try{ reviewDrafts=sanitizeReviewDrafts(JSON.parse(u.reviews||'{}')); }catch(e){ reviewDrafts={}; }
  saveStore(); refreshPoints(); applyPointFilter(); updateCounts(); if(curIdx>=0) renderPoint();
  enqueueSyncChanges(before,labels);
  toast(`${u.description||'Change'} undone`);
}

// ------------------------------------------------------------------ imagery sources
// All keyless raster XYZ sources so the page stays a shareable static file.
const SOURCES = {
  esri: {
    name:'Esri World Imagery', tiles:[
      'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
    attribution:'Esri, Maxar, Earthstar Geographics', max:19 },
  google: {
    name:'Google Satellite', tiles:[
      'https://mt0.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
      'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
      'https://mt2.google.com/vt/lyrs=s&x={x}&y={y}&z={z}'],
    attribution:'© Google', max:20 },
  s2: {
    // ESA Sentinel-2 cloudless (EOX) — recent-ish annual composite, keyless WMTS.
    name:'Sentinel-2 cloudless', tiles:[
      'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2023_3857/default/g/{z}/{y}/{x}.jpg'],
    attribution:'Sentinel-2 cloudless 2023 by EOX (ESA)', max:16 },
  wayback: { name:'Esri Wayback', dynamic:true, attribution:'Esri World Imagery Wayback', max:23 }
};
// GEE composites are injected at boot from gee_layers.json (baked offline; keyless).
let geeLayers = [];   // [{id,name,year,tiles,attribution,max}]

function rasterSourceDef(def, tiles){
  return { type:'raster', tiles: tiles||def.tiles, tileSize:256,
           attribution:def.attribution, maxzoom:def.max||19 };
}

// ------------------------------------------------------------------ map init
function initMap(){
  map = new maplibregl.Map({
    container:'map', attributionControl:{compact:true},
    style:{ version:8, sources:{}, layers:[
      {id:'bg', type:'background', paint:{'background-color':'#0a0c10'}} ] },
    center:[25.5,-33.2], zoom:6, maxZoom:19
  });
  window.map=map;
  map.addControl(new maplibregl.NavigationControl({showCompass:false}), 'bottom-right');
  map.addControl(new maplibregl.ScaleControl({unit:'metric'}), 'bottom-left');

  map.on('load', ()=>{
    // base imagery
    map.addSource('base', rasterSourceDef(SOURCES.esri));
    map.addLayer({id:'base-layer', type:'raster', source:'base'}, );

    // sample points as GeoJSON
    map.addSource('pts', {type:'geojson', data: pointsGeoJSON(), cluster:true, clusterMaxZoom:8, clusterRadius:45});
    map.addLayer({id:'clusters',type:'circle',source:'pts',maxzoom:9,filter:['has','point_count'],paint:{
      'circle-radius':['step',['get','point_count'],14,20,18,100,24],
      'circle-color':'#263957','circle-stroke-color':'#8fb5ff','circle-stroke-width':2
    }});
    map.addLayer({ id:'pts-layer', type:'circle', source:'pts', paint:{
      'circle-radius':['interpolate',['linear'],['zoom'],5,3,10,5,14,7],
      'circle-color':['match',['get','stratum'],
        'intact',STRAT_COLOR.intact,'moderate',STRAT_COLOR.moderate,
        'severe',STRAT_COLOR.severe,'#888'],
      'circle-stroke-width':['case',['get','labeled'],2.5,1],
      'circle-stroke-color':['case',['get','labeled'],'#ffffff','#00000088'],
      'circle-opacity':0.55
    }, filter:['!',['has','point_count']], minzoom:8});
    // selection halo
    map.addLayer({ id:'sel-layer', type:'circle', source:'pts',
      filter:['==',['get','id'],-1], paint:{
        'circle-radius':['interpolate',['linear'],['zoom'],5,9,14,16],
        'circle-color':'#ffffff22','circle-stroke-width':5,'circle-stroke-color':'#67a0ff'
      }});

    map.on('click','clusters',e=>{
      const f=e.features[0]; map.getSource('pts').getClusterExpansionZoom(f.properties.cluster_id)
        .then(z=>map.easeTo({center:f.geometry.coordinates,zoom:z}));
    });
    map.on('error',e=>{ if(!e||!e.error)return; sourceErrors++; setSourceHealth('Imagery is having trouble loading…',true);
      if(sourceErrors>=4&&!fallbackInProgress){fallbackInProgress=true;const next=activeSource==='esri'?'google':'esri';toast(`${SOURCES[activeSource].name} unavailable — switching to ${SOURCES[next].name}`);setSource(next);setTimeout(()=>fallbackInProgress=false,3000);}
    });

    map.on('click','pts-layer', e=>{
      const id = e.features[0].properties.id; gotoId(id);
    });
    map.on('mouseenter','pts-layer', ()=> map.getCanvas().style.cursor='pointer');
    map.on('mouseleave','pts-layer', ()=> map.getCanvas().style.cursor='');

    fetchWayback();
    refreshPoints();
    applyPointFilter();
    // Apply the remembered imagery source (base layer above is Esri by default).
    // GEE sources aren't in SOURCES yet — loadGeeLayers() re-applies once baked.
    if(activeSource && activeSource!=='esri' && SOURCES[activeSource]) setSource(activeSource);
  });
}

function pointsGeoJSON(){
  return { type:'FeatureCollection', features: POINTS.map(p=>({
    type:'Feature', geometry:{type:'Point',coordinates:[p.lon,p.lat]},
    properties:{ id:p.id, stratum:blindMode?'blind':p.m, source:p.src,
                 required:isRequiredPoint(p), disagreement:DISAGREEMENT_IDS.has(p.id),
                 labeled: !!labels[p.id],
                 label:labels[p.id] ? labels[p.id].label : '',
                  flagged:!!reviewState(p.id).flagged,
                  confidence:reviewState(p.id).confidence||'' }
  }))};
}
function refreshPoints(){
  const src = map && map.getSource('pts'); if(src) src.setData(pointsGeoJSON());
}
function applyPointFilter(value=pointFilter){
  pointFilter=value;
  const filters={
    all:null,
    round2:['==',['get','source'],'new'],
    previous:['==',['get','source'],'existing'],
    disagreement:['==',['get','disagreement'],true],
    unlabeled:['==',['get','labeled'],false],
    labeled:['==',['get','labeled'],true],
    intact:['==',['get','label'],'intact'],
    moderate:['==',['get','label'],'moderate'],
    severe:['==',['get','label'],'severe'],
    transformed:['==',['get','label'],'transformed'],
    nothicket:['==',['get','label'],'nothicket'],
    notthicket:['==',['get','label'],'notthicket'],
    unsure:['==',['get','label'],'unsure'],
    review:['any',['==',['get','flagged'],true],['==',['get','confidence'],'low'],['==',['get','label'],'unsure'],['==',['get','label'],LEGACY_CLASS]],
    flagged:['==',['get','flagged'],true],
    low:['==',['get','confidence'],'low']
  };
  if(!Object.hasOwn(filters,pointFilter)) pointFilter='all';
  const base=['!', ['has','point_count']];
  if(map && map.getLayer('pts-layer')) map.setFilter('pts-layer',filters[pointFilter]?['all',base,filters[pointFilter]]:base);
  const sel=$('#pointFilter'); if(sel) sel.value=pointFilter;
  document.querySelectorAll('.chip[data-filter]').forEach(c=>
    c.classList.toggle('filter-active',c.dataset.filter===pointFilter));
  const next=$('#nextUnlabeled');
  if(next){
    const names={all:'unlabeled Round 2 point',round2:'unlabeled Round 2 point',previous:'previous point',
      disagreement:'disagreement',unlabeled:'unlabeled',labeled:'labeled',review:'review item',
      nothicket:'no thicket',notthicket:'legacy combined'};
    next.textContent=`Jump to next ${names[pointFilter]||pointFilter} →`;
  }
  saveUI();
}

// ------------------------------------------------------------------ Esri Wayback
// Public Wayback config lists every imagery release with an itemURL template and
// a per-release metadata MapServer (for true acquisition dates). Ported to match
// the dist_alert_inspector behaviour: date dropdown, "new imagery here" filter,
// capture-date lookup, and swipe compare.
const WAYBACK_CONFIG_URL =
  'https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json';
// The Wayback config is remote and untrusted: only accept https URLs whose host
// is an Esri/ArcGIS domain before we ever fetch or render them.
const WB_HOST_RE = /(^|\.)(arcgis(online)?\.com|arcgis\.com)$/i;
function isEsriUrl(u){
  try{ const x=new URL(u); return x.protocol==='https:' && WB_HOST_RE.test(x.hostname); }
  catch(e){ return false; }
}

async function fetchWayback(){
  const status=$('#wbStatus');
  if(status){ status.textContent='Loading Wayback releases…'; status.className='statusline'; }
  try{
    const cfg = await fetch(WAYBACK_CONFIG_URL).then(r=>{
      if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); });
    wb.releases = Object.keys(cfg).map(num=>{
      const e = cfg[num];
      const m = /Wayback\s+([\d-]+)/.exec(e.itemTitle||'');
      const tileUrl = (e.itemURL||'')
        .replace('{level}','{z}').replace('{row}','{y}').replace('{col}','{x}');
      const metaUrl = e.metadataLayerUrl || null;
      return {
        num: Number(num),
        title: e.itemTitle || ('Release '+num),
        date: m ? m[1] : ('release '+num),
        metaUrl: (metaUrl && isEsriUrl(metaUrl)) ? metaUrl : null,
        tileUrl: isEsriUrl(tileUrl) ? tileUrl : ''
      };
    }).filter(r=> r.tileUrl)                          // drop releases with bad/hostile URLs
      .sort((a,b)=> b.date.localeCompare(a.date));    // newest first
    wb.view = wb.releases.map((_,i)=>i);
    wb.idx = 0; wb.idxB = Math.min(1, wb.releases.length-1);
    fillWbSelect($('#wbSelect'), wb.idx);
    fillWbSelect($('#wbSelectB'), wb.idxB);
    if(status){ status.textContent=`${wb.releases.length} releases available`; status.className='statusline ok'; }
    if(activeSource==='wayback') applyWayback();
  }catch(e){ wb.releases=[]; if(status){ status.textContent='Wayback could not be loaded. Check the network or use another source.'; status.className='statusline error'; } }
}

function fillWbSelect(el, cur){
  if(!el) return;
  el.disabled = !wb.view.length;
  el.textContent = '';   // clear without innerHTML
  if(!wb.view.length){
    const o=document.createElement('option'); o.textContent='—'; el.appendChild(o); return;
  }
  wb.view.forEach(i=>{
    const o=document.createElement('option');
    o.value=String(i); o.textContent=wb.releases[i].date;   // date is remote → textContent
    el.appendChild(o);
  });
  if(wb.view.includes(cur)) el.value = String(cur);
  else el.value = String(wb.view[0]);
}

// Retile the base raster in place — no remove/re-add flicker.
function applyWayback(){
  const r = wb.releases[wb.idx];
  if(!r) return;
  const src = map.getSource(WB_SOURCE);
  if(src && src.setTiles){ src.setTiles([r.tileUrl]); }
  else { swapBase(rasterSourceDef(SOURCES.wayback, [r.tileUrl])); }
  const sel = $('#wbSelect'); if(sel && wb.view.includes(wb.idx)) sel.value = String(wb.idx);
  const rd=$('#wbReleaseDate'); if(rd) rd.textContent=r.date;
  lookupCaptureDate(r);
  if(wb.compare) applyWaybackB();
}

// Step the A date through the (possibly filtered) view list.
function wbStep(delta){
  const pos = wb.view.indexOf(wb.idx);
  const base = pos<0 ? 0 : pos;
  const np = Math.max(0, Math.min(wb.view.length-1, base+delta));
  wb.idx = wb.view[np]; applyWayback(); updateWbStepBtns();
}
function updateWbStepBtns(){
  const pos = wb.view.indexOf(wb.idx);
  $('#wbPrev').disabled = pos>=wb.view.length-1;   // older = further down list
  $('#wbNext').disabled = pos<=0;
}

// "only dates with new imagery here": walk Esri's tilemap service at the current
// map point and keep only releases whose imagery actually changed there.
async function refreshWbLocal(){
  const myId = ++wb.localId;
  if(!wb.local || !wb.releases.length){
    wb.view = wb.releases.map((_,i)=>i);
    fillWbSelect($('#wbSelect'), wb.idx); fillWbSelect($('#wbSelectB'), wb.idxB);
    updateWbStepBtns(); return;
  }
  const sel = $('#wbSelect'); if(sel){ sel.disabled = true; }
  const status=$('#wbStatus'); if(status){ status.textContent='Finding imagery changes at this point…'; status.className='statusline'; }
  const c = map.getCenter();
  try{
    const pid=curIdx>=0?POINTS[curIdx].id:null;
    let set;
    if(pid && wbPointCache[pid]) set=new Set(wbPointCache[pid]);
    else { set=await wbLocalReleases(c); if(pid){ wbPointCache[pid]=Array.from(set); localStorage.setItem(KEY_WBCACHE,JSON.stringify(wbPointCache)); } }
    if(myId !== wb.localId) return;                // superseded
    const idxs = wb.releases.map((r,i)=> set.has(r.num) ? i : -1).filter(i=>i>=0);
    wb.view = idxs.length ? idxs : wb.releases.map((_,i)=>i);
    if(!wb.view.includes(wb.idx)) { wb.idx = wb.view[0]; applyWayback(); }
    fillWbSelect($('#wbSelect'), wb.idx); fillWbSelect($('#wbSelectB'), wb.idxB);
    updateWbStepBtns();
    if(status){ status.textContent=`${wb.view.length} dates with imagery changes here`; status.className='statusline ok'; }
  }catch(e){ if(myId===wb.localId){ if(sel) sel.disabled=false; if(status){ status.textContent='Local date filtering failed; showing all releases.'; status.className='statusline error'; } } }
}
async function wbLocalReleases(pt){
  const z = Math.max(3, Math.min(18, Math.round(map.getZoom())));
  const n = Math.pow(2, z);
  const col = Math.floor((pt.lng + 180)/360*n);
  const latR = pt.lat*Math.PI/180;
  const row = Math.floor((1 - Math.log(Math.tan(latR)+1/Math.cos(latR))/Math.PI)/2*n);
  const out = new Set();
  let i = 0, guard = 0;
  while(i < wb.releases.length && guard++ < 80){
    const rel = wb.releases[i];
    const url = rel.tileUrl.replace('/tile/','/tilemap/')
      .replace('{z}',z).replace('{y}',row).replace('{x}',col);
    let tm;
    try{ tm = await fetch(url).then(r=>r.json()); }catch(e){ break; }
    if(!tm || tm.valid===false || !(tm.data && tm.data[0])) break;
    const actual = (tm.select && tm.select.length) ? Number(tm.select[0]) : rel.num;
    out.add(actual);
    const ai = wb.releases.findIndex(x=>x.num===actual);
    i = (ai>=0 ? ai : i) + 1;
  }
  return out;
}

// True acquisition date (not release date) from the release metadata MapServer.
const _wbCapCache = new Map();
function formatWbCaptureDate(a){
  if(!a) return '';
  // /query returns date fields as epoch milliseconds; older services and the
  // former /identify path may supply M/D/YYYY or compact YYYYMMDD instead.
  const sourceDate2 = a.SRC_DATE2;
  const epoch = typeof sourceDate2==='number' ? sourceDate2
    : (/^\d{12,}$/.test(String(sourceDate2||'')) ? Number(sourceDate2) : NaN);
  if(Number.isFinite(epoch)){
    const d = new Date(epoch);
    if(!Number.isNaN(d.getTime())) return d.toISOString().slice(0,10);
  }
  const readable = String(sourceDate2 || '').trim();
  let m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(readable);
  if(m) return `${m[3]}-${m[1].padStart(2,'0')}-${m[2].padStart(2,'0')}`;
  const compact = String(a.SRC_DATE || '').trim();
  m = /^(\d{4})(\d{2})(\d{2})$/.exec(compact);
  if(m) return `${m[1]}-${m[2]}-${m[3]}`;
  return readable || compact;
}
async function lookupCaptureDate(rel){
  const el = $('#wbCapDate'); if(!el) return;
  // Invalidate every older request before taking a synchronous cache/fallback
  // path. Otherwise a slow response for the previously selected release can
  // overwrite the date we display for this one.
  const myId = ++wb.capId;
  if(!rel.metaUrl){ el.textContent = rel.date + ' (release)'; return; }
  const c = map.getCenter();
  const layerId = Math.max(0, Math.min(13, 23-Math.round(map.getZoom())));
  // Capture footprints can have sharp boundaries; do not share cached results
  // between points that are several metres apart or different resolution bands.
  const key = rel.num + '@' + c.lng.toFixed(6) + ',' + c.lat.toFixed(6) + '/'+layerId;
  if(_wbCapCache.has(key)){ el.textContent = _wbCapCache.get(key); return; }
  el.innerHTML = '<span class="wbspin"></span>';
  try{
    const a = await _wbQueryMetadata(rel, c, layerId);
    if(myId !== wb.capId) return;
    // SAMP_RES is spatial resolution, not a date, and must never be shown here.
    const d = formatWbCaptureDate(a);
    const txt = d ? String(d) : (rel.date + ' (release)');
    _wbCapCache.set(key, txt); el.textContent = txt;
  }catch(e){ if(myId===wb.capId) el.textContent = rel.date + ' (release)'; }
}
async function _wbQueryMetadata(rel, pt, firstLayer){
  // Match @esri/wayback-core: point-intersects query on metadata sublayer
  // 23 - zoom. Some old releases have gaps in fine bands, so walk toward the
  // coarser bands only when the matching band has no footprint at this point.
  for(let layerId=firstLayer; layerId<=13; layerId++){
    const qs = new URLSearchParams({
      f:'json', where:'1=1', outFields:'SRC_DATE,SRC_DATE2',
      geometry:JSON.stringify({x:pt.lng,y:pt.lat,spatialReference:{wkid:4326}}),
      geometryType:'esriGeometryPoint', inSR:'4326',
      spatialRel:'esriSpatialRelIntersects', returnGeometry:'false'
    });
    const j = await fetch(rel.metaUrl+'/'+layerId+'/query?'+qs).then(r=>{
      if(!r.ok) throw new Error('HTTP '+r.status);
      return r.json();
    });
    if(j && j.error) throw new Error(j.error.message || 'Wayback metadata query failed');
    const a = j && j.features && j.features[0] && j.features[0].attributes;
    if(a && (a.SRC_DATE!=null || a.SRC_DATE2!=null)) return a;
  }
  return null;
}

// ---- swipe compare: a second, non-interactive map clipped by a draggable divider
function applyWaybackB(){
  const r = wb.releases[wb.idxB]; if(!r || !wbCmp.map) return;
  const s = wbCmp.map.getSource(WB_SOURCE);
  if(s && s.setTiles){ s.setTiles([r.tileUrl]); }
  else {
    wbCmp.map.addSource(WB_SOURCE, rasterSourceDef(SOURCES.wayback, [r.tileUrl]));
    wbCmp.map.addLayer({id:WB_LAYER, type:'raster', source:WB_SOURCE});
  }
  const lb = document.querySelector('.wb-swipe-label.b'); if(lb) lb.textContent = r.date;
  const la = document.querySelector('.wb-swipe-label.a');
  if(la) la.textContent = wb.releases[wb.idx] ? wb.releases[wb.idx].date : '';
}
function enableCompare(){
  const mapEl = $('#map');
  if(!wbCmp.map){
    const div=document.createElement('div'); div.id='wb-compare-map';
    const sw=document.createElement('div'); sw.id='wb-swipe'; sw.innerHTML='<div class="knob">⇆</div>';
    const la=document.createElement('div'); la.className='wb-swipe-label a';
    const lb=document.createElement('div'); lb.className='wb-swipe-label b';
    mapEl.appendChild(div); mapEl.appendChild(sw); mapEl.appendChild(la); mapEl.appendChild(lb);
    wbCmp.map = new maplibregl.Map({
      container:div, style:{version:8, sources:{}, layers:[]},
      center:map.getCenter(), zoom:map.getZoom(), bearing:map.getBearing(),
      pitch:map.getPitch(), interactive:false, attributionControl:false
    });
    // keep the compare map locked to the main camera
    const sync=()=>{ if(!wbCmp.map) return;
      wbCmp.map.jumpTo({center:map.getCenter(), zoom:map.getZoom(),
        bearing:map.getBearing(), pitch:map.getPitch()}); };
    map.on('move', sync); wbCmp._sync = sync;
    let drag=false;
    const setX = px=>{
      const w=mapEl.clientWidth, x=Math.max(30, Math.min(w-30, px));
      wbCmp.f = x/w; sw.style.left=x+'px'; div.style.clipPath='inset(0 0 0 '+x+'px)';
    };
    sw.addEventListener('pointerdown', e=>{ drag=true; sw.setPointerCapture(e.pointerId); e.preventDefault(); });
    sw.addEventListener('pointermove', e=>{ if(drag) setX(e.clientX-mapEl.getBoundingClientRect().left); });
    sw.addEventListener('pointerup',  ()=> drag=false);
    wbCmp._setX = setX;
    wbCmp.map.on('load', ()=>{ applyWaybackB(); setX(mapEl.clientWidth*0.5); });
  }
  $('#wb-compare-map').style.display='';
  $('#wb-swipe').style.display='';
  document.querySelectorAll('.wb-swipe-label').forEach(e=>e.style.display='');
  if(wbCmp.map && wbCmp.map.loaded()){ applyWaybackB(); wbCmp._setX(mapEl.clientWidth*0.5); }
}
function disableCompare(){
  if(!wbCmp.map) return;
  $('#wb-compare-map').style.display='none';
  $('#wb-swipe').style.display='none';
  document.querySelectorAll('.wb-swipe-label').forEach(e=>e.style.display='none');
}

function resetDivider(){ if(wbCmp._setX) wbCmp._setX($('#map').clientWidth*0.5); }
function swapWayback(){ const x=wb.idx; wb.idx=wb.idxB; wb.idxB=x; fillWbSelect($('#wbSelect'),wb.idx); fillWbSelect($('#wbSelectB'),wb.idxB); applyWayback(); applyWaybackB(); }
let flickerTimer=0;
function setFlicker(on){ clearInterval(flickerTimer); flickerTimer=0; if(!on) return;
  let b=false; flickerTimer=setInterval(()=>{ b=!b; const a=wb.releases[b?wb.idxB:wb.idx]; const s=map.getSource(WB_SOURCE); if(a&&s&&s.setTiles)s.setTiles([a.tileUrl]); },650);
}

function swapBase(def){
  if(map.getLayer(WB_LAYER)) map.removeLayer(WB_LAYER);
  if(map.getSource(WB_SOURCE)) map.removeSource(WB_SOURCE);
  map.addSource(WB_SOURCE, def);
  map.addLayer({id:WB_LAYER, type:'raster', source:WB_SOURCE}, 'pts-layer');
}

// ------------------------------------------------------------------ source UI
function buildSourceButtons(){
  const g = $('#srcgrid'); g.innerHTML='';
  const order = [['esri','Esri World Imagery','highest-res, single date'],
                 ['wayback','Esri Wayback','multi-date time slider'],
                 ['google','Google Satellite','alternate high-res'],
                 ['s2','Sentinel-2 2023','10 m, whole-canopy view']];
  // Append any GEE-baked composites (keyless tile URLs from gee_layers.json).
  geeLayers.forEach(l=> order.push([l.id, l.name, 'GEE composite · 10 m']));
  order.forEach(([k,name,desc])=>{
    const b=document.createElement('button'); b.className='srcbtn'+(k===activeSource?' active':'');
    b.dataset.src=k;
    // text nodes only — name/desc for GEE layers come from an external manifest.
    b.appendChild(document.createTextNode(name));
    const s=document.createElement('small'); s.textContent=desc; b.appendChild(s);
    b.onclick=()=>setSource(k); g.appendChild(b);
  });
}

function setSourceHealth(msg,error=false){ const el=$('#sourceHealth'); if(el){ el.textContent=msg; el.className='statusline '+(error?'error':'ok'); } }

// A tile URL template must be https; {x}/{y}/{z} placeholders survive the URL parse.
function isHttpsTileUrl(u){
  try{ return new URL(u).protocol === 'https:'; }catch(e){ return false; }
}

// Load offline-baked Earth Engine layers. No key/login in the browser — the
// service account did the privileged work in CI; these are public tile URLs.
async function loadGeeLayers(){
  try{
    const r = await fetch('gee_layers.json', {cache:'no-store'});
    if(!r.ok) return;
    const m = await r.json();
    (m.layers||[]).forEach(l=>{
      if(!l.id || !l.tiles || !l.tiles.length) return;
      // manifest is untrusted: accept only https tile URLs.
      if(!l.tiles.every(isHttpsTileUrl)) return;
      SOURCES[l.id] = { name:String(l.name||l.id), tiles:l.tiles,
                        attribution:String(l.attribution||'Google Earth Engine'), max:l.max||18 };
      geeLayers.push({...l, name:String(l.name||l.id)});
    });
    if(geeLayers.length){
      buildSourceButtons();
      // A remembered GEE source couldn't be restored at boot (manifest hadn't
      // loaded yet); apply it now that its tiles are registered.
      if(activeSource!=='esri' && SOURCES[activeSource] && map && map.loaded())
        setSource(activeSource);
    }
  }catch(e){ /* manifest optional — page works without it */ }
}
function setSource(k){
  if(!SOURCES[k]) return;
  activeSource=k;
  sourceErrors=0; setSourceHealth(`${SOURCES[k].name} selected`,false);
  document.querySelectorAll('.srcbtn').forEach(b=>b.classList.toggle('active',b.dataset.src===k));
  $('#waybackrow').classList.toggle('hidden', k!=='wayback');
  if(k==='wayback'){
    applyWayback(); updateWbStepBtns();
    if(wb.local) refreshWbLocal();
    if(wb.compare) enableCompare();
  } else {
    disableCompare();
    swapBase(rasterSourceDef(SOURCES[k]));
  }
  saveUI();
}

// ------------------------------------------------------------------ navigation
function gotoIdx(idx, recordHistory=true){
  if(idx<0||idx>=POINTS.length) return;
  if(curIdx>=0) saveNote();
  clearTimeout(pendingAdvance);
  curIdx=idx; const p=POINTS[idx];
  if(recordHistory && navHistory[navHistoryPos]!==p.id){
    navHistory=navHistory.slice(0,navHistoryPos+1); navHistory.push(p.id);
    if(navHistory.length>100) navHistory.shift(); navHistoryPos=navHistory.length-1;
  }
  map.easeTo({center:[p.lon,p.lat], zoom:Math.max(map.getZoom(),15.5), duration:600});
  map.setFilter('sel-layer',['==',['get','id'],p.id]);
  renderPoint();
  location.hash = `p=${p.id}`;
  // Wayback is point-relative: re-evaluate "new imagery here" + capture date on move.
  if(activeSource==='wayback'){
    map.once('moveend', ()=>{
      if(wb.local) refreshWbLocal();
      const r=wb.releases[wb.idx]; if(r) lookupCaptureDate(r);
    });
  }
}
function gotoId(id){ const i=byId(id); if(i>=0) gotoIdx(i); }
function historyBack(){ if(navHistoryPos>0){ navHistoryPos--; const i=byId(navHistory[navHistoryPos]); if(i>=0) gotoIdx(i,false); } else toast('No earlier point in history'); }
function gotoQueue(test,empty){
  for(let k=1;k<=POINTS.length;k++){ const i=(curIdx+k)%POINTS.length,p=POINTS[i]; if(test(reviewState(p.id),p)){gotoIdx(i);return;} }
  toast(empty);
}
function recenter(){ const p=POINTS[curIdx]; if(p) map.easeTo({center:[p.lon,p.lat],duration:350}); }
async function copyPoint(){ const p=POINTS[curIdx]; if(!p)return; const text=`ID ${p.id}: ${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}`;
  try{ await navigator.clipboard.writeText(text); toast('Point ID and coordinates copied'); }catch(e){ toast(text); }
}
function nextUnlabeled(){
  for(let k=1;k<=POINTS.length;k++){
    const i=(curIdx+k)%POINTS.length;
    if(isRequiredPoint(POINTS[i])&&!labels[POINTS[i].id]){ gotoIdx(i); return; }
  }
  toast('All required Round 2 points labeled 🎉');
}
function nextForFilter(){
  if(pointFilter==='all'||pointFilter==='round2'||pointFilter==='unlabeled'){ nextUnlabeled(); return; }
  for(let k=1;k<=POINTS.length;k++){
    const i=(curIdx+k)%POINTS.length, p=POINTS[i], rec=labels[p.id], meta=reviewState(p.id);
    if(pointFilter==='disagreement'&&DISAGREEMENT_IDS.has(p.id)){gotoIdx(i);return;}
    if(pointFilter==='previous'&&p.src==='existing'){gotoIdx(i);return;}
    if(pointFilter==='review'&&needsReview(p)){ gotoIdx(i); return; }
    if(pointFilter==='flagged'&&meta.flagged){ gotoIdx(i); return; }
    if(pointFilter==='low'&&meta.confidence==='low'){ gotoIdx(i); return; }
    if((pointFilter==='labeled'&&rec)||(rec&&rec.label===pointFilter)){ gotoIdx(i); return; }
  }
  toast(`No ${pointFilter==='labeled'?'labeled':pointFilter} points to review`);
}

// ------------------------------------------------------------------ render
function renderPoint(){
  const p=POINTS[curIdx]; if(!p) return;
  $('#curId').textContent=p.id; $('#curOrdinal').textContent=curIdx+1; $('#totId').textContent=POINTS.length;
  const rec=labels[p.id], meta=reviewState(p.id);
  const showPrediction=!blindMode || !!rec;
  if(showPrediction) $('#curStratum').innerHTML = ` · <span class="pill ${p.m}">${p.m}</span>`;
  else $('#curStratum').textContent=' · model hidden';
  $('#mCoord').textContent=`${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;
  $('#mSampleSource').textContent=isRequiredPoint(p)?'Round 2 · required':
    (DISAGREEMENT_IDS.has(p.id)?'Round 1 · disagreement exploration':'Round 1 · historical');
  if(showPrediction) $('#mStratum').innerHTML=`<span class="pill ${p.m}">${CLASS_LABEL[p.m]||p.m}</span> · ${p.s}`;
  else $('#mStratum').textContent='Hidden until label saved';
  $('#mVegetation').textContent=showPrediction?[p.mc,p.veg,p.efg].filter(Boolean).join(' · '):'Hidden until label saved';
  renderPriorDisagreement(p);
  const mLabel=$('#mLabel');
  if(rec && isValidClass(rec.label)){
    const b=document.createElement('b');
    b.style.color = STRAT_COLOR[rec.label]||'#fff';
    b.textContent = CLASS_LABEL[rec.label];
    mLabel.textContent=''; mLabel.appendChild(b);
  } else { mLabel.textContent='–'; }
  $('#note').value = rec ? (rec.note||'') : (noteDrafts[p.id]||'');
  autoGrowNote();
  $('#noteStatus').textContent = noteDrafts[p.id] && !rec ? 'Draft saved locally' : '';
  $('#clearLabelBtn').classList.toggle('hidden', !rec);
  document.querySelectorAll('.lblbtn').forEach(b=>{
    b.className='lblbtn';
    if(rec && isValidClass(rec.label) && rec.label===b.dataset.lbl) b.classList.add('sel-'+rec.label);
  });
  $('#flagBtn').setAttribute('aria-pressed',String(!!meta.flagged));
  document.querySelectorAll('[data-confidence]').forEach(b=>b.classList.toggle('active',meta.confidence===b.dataset.confidence));
  // imagery deep links
  $('#gmapsLink').href = `https://www.google.com/maps/@${p.lat},${p.lon},400m/data=!3m1!1e3`;
  $('#gearthLink').href = `https://earth.google.com/web/@${p.lat},${p.lon},0a,800d,35y,0h,0t,0r`;
  $('#prevBtn').disabled = curIdx<=0;
  $('#nextBtn').disabled = curIdx>=POINTS.length-1;
}

function renderPriorDisagreement(p){
  const panel=$('#disagreementPanel'), list=$('#priorLabels');
  const item=DISAGREEMENT_MANIFEST[String(p.id)];
  panel.classList.toggle('hidden',!item);list.textContent='';
  if(!item)return;
  Object.entries(item.labels||{}).forEach(([code,value])=>{
    const row=document.createElement('div');row.className='prior-label';
    const who=document.createElement('b');who.textContent=code;
    const label=document.createElement('span');label.textContent=CLASS_LABEL[value]||value;
    label.style.color=STRAT_COLOR[value]||'#fff';row.append(who,label);list.appendChild(row);
  });
}

function setLabel(cls){
  if(curIdx<0){ toast('Pick a point first'); return; }
  if(!isValidClass(cls)) return;
  clearTimeout(pendingAdvance);
  const p=POINTS[curIdx];
  if(labels[p.id] && labels[p.id].label===cls){
    toast(`${CLASS_LABEL[cls]} already selected`); return;
  }
  const previous=labels[p.id] ? {...labels[p.id]} : null;
  const previousReview=reviewDrafts[p.id] ? {...reviewDrafts[p.id],reasons:[...(reviewDrafts[p.id].reasons||[])]} : null;
  snapshotUndo('Label change');
  const previousDraft=noteDrafts[p.id];
  const meta=previous||previousReview||{};
  labels[p.id]={ label:cls, note:$('#note').value.trim(),
                 labeler:labeler, ts:new Date().toISOString(),
                 flagged:!!meta.flagged,
                 confidence:meta.confidence||'', reasons:[...(meta.reasons||[])],
                 stratum:p.s, lon:p.lon, lat:p.lat };
  delete noteDrafts[p.id];
  delete reviewDrafts[p.id];
  const saved=saveStore(); renderPoint(); refreshPoints(); updateCounts();
  enqueueSheetSync(p,labels[p.id]);
  const undo=()=>{
    clearTimeout(pendingAdvance);
    if(previous) labels[p.id]=previous; else delete labels[p.id];
    if(previousDraft!=null) noteDrafts[p.id]=previousDraft; else delete noteDrafts[p.id];
    if(previousReview) reviewDrafts[p.id]=previousReview; else delete reviewDrafts[p.id];
    saveStore(); refreshPoints(); updateCounts(); gotoId(p.id); toast('Change undone');
    enqueueSheetSync(p,labels[p.id]||null,labels[p.id]?'upsert':'clear');
  };
  toast(`Point ${p.id} marked ${CLASS_LABEL[cls]}`, 'Undo', undo, 4200);
  if(autoAdvance && saved) pendingAdvance=setTimeout(nextUnlabeled, 450);
}
function clearLabel(){
  const p=POINTS[curIdx]; if(!p||!labels[p.id]) return;
  clearTimeout(pendingAdvance);
  const previous={...labels[p.id]};
  const previousReview=reviewDrafts[p.id] ? {...reviewDrafts[p.id],reasons:[...(reviewDrafts[p.id].reasons||[])]} : null;
  snapshotUndo('Clear label');
  const note=$('#note').value.trim(); if(note) noteDrafts[p.id]=note;
  const review={flagged:!!previous.flagged,confidence:previous.confidence||'',reasons:[...(previous.reasons||[])],ts:new Date().toISOString()};
  if(review.flagged||review.confidence||review.reasons.length) reviewDrafts[p.id]=review;
  else delete reviewDrafts[p.id];
  delete labels[p.id]; saveStore(); renderPoint(); refreshPoints(); updateCounts();
  enqueueSheetSync(p,null,'clear');
  toast(`Label cleared for point ${p.id}`, 'Undo', ()=>{
    labels[p.id]=previous; delete noteDrafts[p.id];
    if(previousReview) reviewDrafts[p.id]=previousReview; else delete reviewDrafts[p.id];
    saveStore(); refreshPoints(); updateCounts();
    enqueueSheetSync(p,labels[p.id]);
    gotoId(p.id); toast('Label restored');
  }, 4200);
}
function updateReviewField(field,value){
  const p=POINTS[curIdx]; if(!p)return;
  if(field==='confidence'&&!['','high','medium','low'].includes(value))return;
  snapshotUndo('Review metadata');
  const target=labels[p.id]||(reviewDrafts[p.id]={flagged:false,confidence:'',reasons:[]});
  target[field]=field==='flagged'?!!value:value; target.ts=new Date().toISOString();
  if(!labels[p.id]&&!target.flagged&&!target.confidence&&!(target.reasons||[]).length) delete reviewDrafts[p.id];
  saveStore(); refreshPoints(); updateCounts(); renderPoint();
  if(labels[p.id]) enqueueSheetSync(p,labels[p.id]);
}
function toggleFlag(){ const p=POINTS[curIdx]; if(p)updateReviewField('flagged',!reviewState(p.id).flagged); }
function addReason(tag){
  const p=POINTS[curIdx]; if(!p)return;
  const note=$('#note'), token=`[${tag}]`; if(!note.value.includes(token)) note.value=(note.value.trim()+' '+token).trim();
  autoGrowNote(); saveNote();
  const target=labels[p.id]||(reviewDrafts[p.id]={flagged:false,confidence:'',reasons:[]});
  const rs=new Set(target.reasons||[]); rs.add(tag); target.reasons=Array.from(rs).slice(0,12); target.ts=new Date().toISOString();
  saveStore(); if(labels[p.id])enqueueSheetSync(p,labels[p.id]);
}
function autoGrowNote(){ const n=$('#note'); if(!n)return; n.style.height='auto'; n.style.height=Math.min(180,Math.max(44,n.scrollHeight))+'px'; }
function saveNote(){
  const p=POINTS[curIdx]; if(!p) return;
  const note=$('#note').value.trim();let changed=false;
  if(labels[p.id]){
    changed=(labels[p.id].note||'')!==note;labels[p.id].note=note;
    if(changed)labels[p.id].ts=new Date().toISOString();delete noteDrafts[p.id];
  }
  else if(note) noteDrafts[p.id]=note;
  else delete noteDrafts[p.id];
  saveStore();
  if(changed)enqueueSheetSync(p,labels[p.id]);
  $('#noteStatus').textContent=note ? (labels[p.id]?'Note saved':'Draft saved locally') : '';
}

function updateCounts(){
  const c={intact:0,moderate:0,severe:0,transformed:0,nothicket:0,notthicket:0,unsure:0,all:0,flagged:0,low:0};
  REQUIRED_POINTS.forEach(p=>{const r=labels[p.id]; if(!r||!isValidClass(r.label)) return;
    c.all++; if(c[r.label]!=null) c[r.label]++; if(r.flagged)c.flagged++; if(r.confidence==='low')c.low++; });
  $('#c_all').textContent=c.all;
  $('#c_review').textContent=POINTS.filter(needsReview).length;
  $('#c_disagreements').textContent=POINTS.filter(p=>DISAGREEMENT_IDS.has(p.id)).length;
  const remaining=Math.max(0,REQUIRED_POINTS.length-c.all), pct=REQUIRED_POINTS.length?c.all/REQUIRED_POINTS.length*100:0;
  $('#c_remaining').textContent=remaining; $('#progressText').textContent=`${c.all} of ${REQUIRED_POINTS.length} Round 2 labeled`;
  $('#progressPct').textContent=(pct<10?pct.toFixed(1):Math.round(pct))+'%'; $('#progressFill').style.width=pct+'%';
}

// ----------------------------------------------------------- area estimates
const AREA_CLASS_LABELS={intact:'Intact',moderate:'Moderate',severe:'Severe',nothicket:'No thicket'};
const AREA_VEG_NAMES=new Map(POINTS.filter(p=>p.mc).map(p=>[p.mc,p.veg||p.mc]));
function formatArea(value){ return `${Math.round(Number(value)||0).toLocaleString('en-ZA')} ha`; }
function compactArea(value){
  const n=Number(value)||0;
  if(n>=1e6)return `${(n/1e6).toFixed(1).replace(/\.0$/,'')} Mha`;
  if(n>=1e3)return `${Math.round(n/1e3).toLocaleString('en-ZA')}k ha`;
  return formatArea(n);
}
function areaGroupTitle(level,key){
  if(level==='landscape')return 'All solid thicket';
  if(level==='vegtype')return AREA_VEG_NAMES.get(key)||key;
  return key.replace(/([a-z])([A-Z])/g,'$1 $2').replace(/Thicket$/,' thicket');
}
function areaGroups(scenario,level){
  if(level==='landscape')return [{key:'landscape',title:'All solid thicket',area_ha:scenario.area_covered_ha,
    n:scenario.n_used,estimable:scenario.strata_without_variance===0,composition:scenario.reference_area}];
  const source=level==='efg'?scenario.by_efg:scenario.by_vegtype;
  return Object.entries(source).map(([key,value])=>({key,title:areaGroupTitle(level,key),...value}))
    .sort((a,b)=>a.key.localeCompare(b.key,undefined,{numeric:true}));
}
function setAreaDetail(group,cls,metric){
  const fraction=group.area_ha?metric.area_ha/group.area_ha*100:0;
  $('#areaDetail').textContent=`${group.title} · ${AREA_CLASS_LABELS[cls]||cls}: ${formatArea(metric.area_ha)} ± ${formatArea(metric.moe95_ha)} (95% margin; ${fraction.toFixed(1)}% of this area; n=${group.n}).`;
}
function renderAreaEstimates(){
  const scenarioKey=$('#areaScenario').value,level=$('#areaLevel').value;
  const scenario=AREA_ESTIMATION.scenarios[scenarioKey]||Object.values(AREA_ESTIMATION.scenarios)[0];
  const groups=areaGroups(scenario,level),maxArea=Math.max(...groups.map(g=>g.area_ha),1);
  const coverage=scenario.area_total_ha?scenario.area_covered_ha/scenario.area_total_ha*100:0;
  $('#areaMeta').textContent=`${AREA_ESTIMATION.assessment_year} assessment · ${AREA_ESTIMATION.stratification_year} stratification · ${scenario.n_used.toLocaleString('en-ZA')} of ${AREA_ESTIMATION.n_reference_labels.toLocaleString('en-ZA')} reference labels usable (${AREA_ESTIMATION.n_reference_labels_on_new_points.toLocaleString('en-ZA')} Round 2) · ${coverage.toFixed(1)}% area coverage`;

  const legend=$('#areaLegend');legend.textContent='';
  scenario.ref_classes.forEach(cls=>{
    const item=document.createElement('span'),swatch=document.createElement('i');
    swatch.className=`area-swatch ${cls}`;swatch.setAttribute('aria-hidden','true');
    item.append(swatch,AREA_CLASS_LABELS[cls]||cls);legend.appendChild(item);
  });

  const chart=$('#areaChart');chart.textContent='';
  chart.setAttribute('aria-label',`Estimated condition area at ${level==='landscape'?'landscape':level==='efg'?'ecosystem functional group':'vegetation type'} level`);
  groups.forEach(group=>{
    const row=document.createElement('div');row.className='area-row';row.setAttribute('role','listitem');
    const label=document.createElement('div');label.className='area-row-label';
    const name=document.createElement('b');name.textContent=group.title;label.appendChild(name);
    const sub=document.createElement('span');sub.textContent=`${level==='vegtype'?group.key+' · ':''}n=${group.n}${group.estimable?'':' · ⚠ limited variance'}`;label.appendChild(sub);
    const track=document.createElement('div');track.className='area-track';
    const bar=document.createElement('div');bar.className='area-bar';bar.style.width=`${Math.max(0,Math.min(100,group.area_ha/maxArea*100))}%`;
    const summary=[];
    scenario.ref_classes.forEach(cls=>{
      const metric=group.composition[cls];if(!metric||metric.area_ha<=0)return;
      const fraction=group.area_ha?metric.area_ha/group.area_ha:0;
      const segment=document.createElement('button');segment.type='button';segment.className=`area-segment ${cls}`;
      segment.style.width=`${fraction*100}%`;
      const description=`${group.title}, ${AREA_CLASS_LABELS[cls]||cls}: ${formatArea(metric.area_ha)}, plus or minus ${formatArea(metric.moe95_ha)} at 95 percent`;
      segment.setAttribute('aria-label',description);segment.title=description;
      if(metric.area_ha/maxArea>=.09)segment.textContent=AREA_CLASS_LABELS[cls]||cls;
      const show=()=>setAreaDetail(group,cls,metric);segment.onmouseenter=show;segment.onfocus=show;segment.onclick=show;
      bar.appendChild(segment);summary.push(`${AREA_CLASS_LABELS[cls]||cls} ${formatArea(metric.area_ha)}`);
    });
    track.setAttribute('aria-label',`${group.title}: ${summary.join(', ')}`);track.appendChild(bar);
    const total=document.createElement('div');total.className='area-row-total';total.textContent=compactArea(group.area_ha);total.title=formatArea(group.area_ha);
    row.append(label,track,total);chart.appendChild(row);
  });
  $('#areaDetail').textContent='Select or hover a coloured segment to see its estimate and 95% margin of error.';
  const weak=level==='landscape'?scenario.strata_without_variance:groups.filter(g=>!g.estimable).length;
  const unit=level==='landscape'?`${weak} of ${scenario.strata_total} strata`:`${weak} of ${groups.length} ${level==='efg'?'EFGs':'vegetation types'}`;
  $('#areaCaveat').textContent=weak?`⚠ ${unit} include fewer than two usable labels for at least one variance estimate; uncertainty there is incomplete.`:'';
  const generated=new Date(AREA_ESTIMATION.generated_utc),date=Number.isNaN(generated.valueOf())?AREA_ESTIMATION.generated_utc:generated.toLocaleDateString('en-ZA',{day:'numeric',month:'short',year:'numeric'});
  $('#areaSnapshot').textContent=`Static analysis snapshot generated ${date}; ${formatArea(scenario.area_uncovered_ha)} is outside the covered strata. Estimates do not recalculate live from Google Sheets.`;
}
function openAreaEstimates(){ renderAreaEstimates();openDialog('#areaModal'); }

// ------------------------------------------------------------------ import / export
function exportRows(){
  return POINTS.filter(p=>labels[p.id]).map(p=>{ const r=labels[p.id]; return {id:p.id,source:p.src,stratum:p.s,
    mapcode:p.mc||'',vegtype:p.veg||'',efg:p.efg||'',cls2022:p.c22,cls2025:p.c25,
    required:isRequiredPoint(p),disagreement:DISAGREEMENT_IDS.has(p.id),lon:p.lon,lat:p.lat,
    label:r.label,note:r.note||'',labeler:r.labeler||labeler,ts:r.ts||'',flagged:!!r.flagged,
    confidence:r.confidence||'',reasons:(r.reasons||[]).join('|')}; });
}
function checksumText(text){ let h=2166136261; for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,16777619);} return ('00000000'+(h>>>0).toString(16)).slice(-8); }
function download(){ openCompletion(); }
function openCompletion(){
  saveNote(); const rows=exportRows(), requiredRows=rows.filter(r=>r.required), remaining=REQUIRED_POINTS.length-requiredRows.length;
  const counts={}; CLASSES.forEach(c=>counts[c]=requiredRows.filter(r=>r.label===c).length);
  const flagged=REQUIRED_POINTS.filter(p=>reviewState(p.id).flagged), low=REQUIRED_POINTS.filter(p=>reviewState(p.id).confidence==='low'), unsure=requiredRows.filter(r=>r.label==='unsure'), legacy=requiredRows.filter(r=>r.label===LEGACY_CLASS);
  const qaIncomplete=remaining||legacy.length;
  $('#completionState').textContent=remaining?`${remaining} point${remaining===1?' is':'s are'} incomplete. You can export a backup now, but final QA is not complete.`:legacy.length?`${legacy.length} label${legacy.length===1?' uses':'s use'} the old combined class. Review and replace ${legacy.length===1?'it':'them'} with Transformed or No thicket before final export.`:'All points are labeled. Review the items below before final export.';
  $('#completionState').className=qaIncomplete?'statusline error':'statusline ok';
  const optional=rows.length-requiredRows.length;
  const stats=[['Round 2 labeled',requiredRows.length],['Remaining',remaining],['Exploration labels',optional],['Intact',counts.intact],['Moderate',counts.moderate],['Severe',counts.severe],['Transformed',counts.transformed],['No thicket',counts.nothicket],['Unsure',counts.unsure],['Legacy review',legacy.length],['Flagged',flagged.length],['Low confidence',low.length]];
  const grid=$('#finalSummary'); grid.textContent=''; stats.forEach(([n,v])=>{const d=document.createElement('div'),b=document.createElement('b');b.textContent=v;d.append(n,b);grid.appendChild(d);});
  $('#lastBackup').textContent=lastBackup?new Date(lastBackup).toLocaleString():'Never';
  $('#finalDownload').textContent=qaIncomplete?'Download backup':'Download final';
  const review=$('#reviewList'); review.textContent='';
  const ids=new Set([...unsure,...legacy,...flagged,...low].map(r=>r.id));
  if(!ids.size){const d=document.createElement('div');d.className='reviewitem';d.textContent='No unsure, flagged, or low-confidence points.';review.appendChild(d);}
  ids.forEach(id=>{const r=labels[id],meta=reviewState(id),d=document.createElement('div');d.className='reviewitem';d.append(`ID ${id} · ${r?CLASS_LABEL[r.label]:'Unlabeled'}${meta.flagged?' · flagged':''}${meta.confidence?` · ${meta.confidence} confidence`:''}`);const b=document.createElement('button');b.className='btn';b.textContent='Review';b.onclick=()=>{closeDialog('#completionModal');gotoId(id);};d.appendChild(b);review.appendChild(d);});
  openDialog('#completionModal');
}
async function exportFinal(){
  const rows = exportRows();
  const requiredRows=rows.filter(r=>r.required),legacyCount=requiredRows.filter(r=>r.label===LEGACY_CLASS).length;
  const exported=new Date().toISOString(), completion={complete:requiredRows.length===REQUIRED_POINTS.length&&!legacyCount,
    total:REQUIRED_POINTS.length,labeled:requiredRows.length,explorationLabels:rows.length-requiredRows.length,
    flagged:REQUIRED_POINTS.filter(p=>reviewState(p.id).flagged).length,unsure:requiredRows.filter(r=>r.label==='unsure').length,legacyCombined:legacyCount,lowConfidence:REQUIRED_POINTS.filter(p=>reviewState(p.id).confidence==='low').length};
  const canonical=JSON.stringify(rows), checksum=checksumText(canonical);
  const assignment=ASSIGNMENT_RECORD?{campaign:CAMPAIGN,code:ASSIGNMENT_CODE,id:ASSIGNMENT_ID,assigned:REQUIRED_POINTS.length}:null;
  const payload={tool:'thicket_inspector',version:5,dataset:DS_ID,round:2,assignment,labeler,exported,n:rows.length,completion,checksum:{algorithm:'fnv1a-32',value:checksum},labels:rows};
  const stamp=exported.slice(0,19).replace(/[:T]/g,'-'), safe=(labeler||'anon').replace(/[^A-Za-z0-9_-]/g,''), assignmentSafe=ASSIGNMENT_CODE?`_${ASSIGNMENT_CODE}`:'';
  const format=$('#exportFormat').value;
  if(format==='json') blobDownload(JSON.stringify(payload,null,2),`thicket_labels_${safe}${assignmentSafe}_${stamp}.json`,'application/json');
  else {
    const csvSafe=v=>{let s=String(v==null?'':v);if(/^[=+\-@\t\r]/.test(s))s="'"+s;return s;},q=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"',qt=v=>'"'+csvSafe(v).replace(/"/g,'""')+'"';
    const hdr='dataset,round,id,source,stratum,mapcode,vegtype,efg,cls2022,cls2025,required,disagreement,lon,lat,label,note,labeler,ts,flagged,confidence,reasons,checksum,campaign,assignment,assignment_id';
    const csv=[hdr].concat(rows.map(r=>[q(DS_ID),q(2),q(r.id),q(r.source),q(r.stratum),q(r.mapcode),qt(r.vegtype),q(r.efg),q(r.cls2022),q(r.cls2025),q(r.required),q(r.disagreement),q(r.lon),q(r.lat),q(r.label),qt(r.note),qt(r.labeler),q(r.ts),q(r.flagged),q(r.confidence),qt(r.reasons),q(checksum),qt(CAMPAIGN),q(ASSIGNMENT_CODE),q(ASSIGNMENT_ID)].join(','))).join('\r\n');
    blobDownload(csv,`thicket_labels_${safe}${assignmentSafe}_${stamp}.csv`,'text/csv');
  }
  lastBackup=exported; localStorage.setItem(KEY_BACKUP,lastBackup); closeDialog('#completionModal'); saveStore(); toast(`Downloaded ${rows.length} labels as ${format.toUpperCase()}`);
}
/* Legacy two-file export retained below for reference during older file imports. */
function legacyDownload(){
  const rows = POINTS.filter(p=>labels[p.id]).map(p=>{
    const r=labels[p.id];
    return {id:p.id, stratum:p.s, lon:p.lon, lat:p.lat,
            label:r.label, note:r.note||'', labeler:r.labeler||labeler, ts:r.ts||''};
  });
  const payload={ tool:'thicket_inspector', version:1, dataset:DS_ID, labeler,
                  exported:new Date().toISOString(), n:rows.length, labels:rows };
  const stamp=new Date().toISOString().slice(0,19).replace(/[:T]/g,'-');
  const safe=(labeler||'anon').replace(/[^A-Za-z0-9_-]/g,'');
  blobDownload(JSON.stringify(payload,null,2),
    `thicket_labels_${safe}_${stamp}.json`,'application/json');
  // also CSV — quote/escape every field so notes/initials with commas,
  // quotes, or newlines round-trip through parseCSV() intact. For the free-text
  // fields (note, labeler) prefix a leading =/+/-/@/tab/CR with a single quote
  // so spreadsheets don't evaluate them as formulas (CSV injection). Numeric and
  // enum columns are left untouched so their values still round-trip exactly.
  const csvSafe=v=>{ let s=String(v==null?'':v);
    if(/^[=+\-@\t\r]/.test(s)) s="'"+s; return s; };
  const q=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"';       // numeric/enum
  const qt=v=>'"'+csvSafe(v).replace(/"/g,'""')+'"';                // free text
  const hdr='dataset,id,stratum,lon,lat,label,note,labeler,ts';
  const csv=[hdr].concat(rows.map(r=>
    [q(DS_ID),q(r.id),q(r.stratum),q(r.lon),q(r.lat),q(r.label),qt(r.note||''),qt(r.labeler),q(r.ts)].join(',')
  )).join('\r\n');
  blobDownload(csv, `thicket_labels_${safe}_${stamp}.csv`,'text/csv');
  toast(`Downloaded ${rows.length} labels (JSON + CSV)`);
}
function blobDownload(text,name,type){
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([text],{type}));
  a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),2000);
}
const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;   // 8 MB — far above any real label file
function handleUpload(file){
  if(file.size > MAX_UPLOAD_BYTES){
    toast('File too large (max 8 MB)'); return;
  }
  const fr=new FileReader();
  fr.onload=()=>{
    try{
      let rows, fileDataset=null, fileAssignment='', fileCampaign='';
      if(file.name.toLowerCase().endsWith('.csv')){
        rows=parseCSV(fr.result); fileDataset=rows.find(r=>r.dataset)?.dataset||null;
        fileAssignment=rows.find(r=>r.assignment)?.assignment||'';
        fileCampaign=rows.find(r=>r.campaign)?.campaign||'';
      }
      else { const j=JSON.parse(fr.result); rows=j.labels||[]; fileDataset=j.dataset||null;
        fileAssignment=j.assignment&&j.assignment.code||''; fileCampaign=j.assignment&&j.assignment.campaign||''; }
      if(!Array.isArray(rows)){ toast('Could not read that file'); return; }

      // Dataset-mismatch guard: a JSON export from a different sample draw must
      // not silently paint its labels onto these coordinates.
      if(fileDataset && fileDataset !== DS_ID){
        toast('Import blocked: file belongs to a different dataset'); return;
      }
      if(ASSIGNMENT_CODE&&fileAssignment&&fileAssignment.toLowerCase()!==ASSIGNMENT_CODE.toLowerCase()){
        toast('Import blocked: file belongs to a different assignment'); return;
      }
      if(ASSIGNMENT_CODE&&fileCampaign&&CAMPAIGN&&fileCampaign!==CAMPAIGN){
        toast('Import blocked: file belongs to a different campaign'); return;
      }

      previewImport(rows,file.name);
    }catch(e){ toast('Could not read that file'); }
  };
  fr.onerror=()=>toast('Could not read that file');
  fr.readAsText(file);
}
function previewImport(rows,fileName){
      const records=[]; let invalid=0, moved=0, fresh=0, conflicts=0, same=0,duplicates=0; const seen=new Set();
      rows.forEach(r=>{
        const id=Number(r.id); const i=byId(id); if(i<0||!isValidClass(r.label)){ invalid++; return; }
        if(seen.has(id)){duplicates++;return;} seen.add(id);
        const p=POINTS[i];
        if(r.stratum && r.stratum!==p.s){ moved++; return; }
        // If the file carries coordinates, they must match the embedded draw.
        if(r.lon!=null && r.lat!=null){
          const dlon=Math.abs(Number(r.lon)-p.lon), dlat=Math.abs(Number(r.lat)-p.lat);
          if(!(dlon<=COORD_EPS && dlat<=COORD_EPS)){ moved++; return; }
        }
        const rec={label:r.label,note:String(r.note||'').slice(0,5000),
          labeler:String(r.labeler||labeler).slice(0,200),
          ts:typeof r.ts==='string'?r.ts:'',flagged:r.flagged===true||String(r.flagged).toLowerCase()==='true',
          confidence:['high','medium','low'].includes(r.confidence)?r.confidence:'',
          reasons:Array.isArray(r.reasons)?r.reasons:String(r.reasons||'').split('|').filter(Boolean),stratum:p.s,lon:p.lon,lat:p.lat};
        const cur=labels[id];
        const unchanged=cur && cur.label===rec.label && (cur.note||'')===rec.note;
        if(!cur) fresh++; else if(unchanged) same++; else conflicts++;
        records.push({id,rec,unchanged:!!unchanged});
      });
      pendingImport={records,invalid,moved,fresh,conflicts,same,duplicates,fileName};
      $('#importFile').textContent=`${fileName} contains ${rows.length} row${rows.length===1?'':'s'}. Nothing changes until you apply it.`+
        (rows.some(r=>!r.dataset)?' This appears to be a legacy file without a dataset fingerprint; point IDs, strata, and coordinates are validated where present.':'');
      $('#impValid').textContent=records.length; $('#impNew').textContent=fresh;
      $('#impConflicts').textContent=conflicts; $('#impSame').textContent=same;
      $('#impInvalid').textContent=invalid; $('#impMoved').textContent=moved;
      $('#impDuplicates').textContent=duplicates;
      $('#importStrategy').value='fill'; $('#importStrategy').disabled=conflicts===0;
      $('#importHint').textContent=conflicts
        ? `${conflicts} existing label${conflicts===1?' differs':'s differ'} from this file. Choose how to resolve them.`
        : 'No conflicting local labels were found.';
      $('#applyImport').disabled=records.length===0;
      renderConflictChoices();
      openDialog('#importPreview'); $('#cancelImport').focus();
}
function renderConflictChoices(){
  const list=$('#conflictList'); list.textContent='';
  (pendingImport?pendingImport.records:[]).filter(x=>labels[x.id]&&!x.unchanged).forEach(x=>{
    const d=document.createElement('div');d.className='reviewitem';d.append(`ID ${x.id}: local ${CLASS_LABEL[labels[x.id].label]} / imported ${CLASS_LABEL[x.rec.label]}`);
    const s=document.createElement('select');s.dataset.conflict=String(x.id);s.innerHTML='<option value="local">Keep local</option><option value="import">Use imported</option>';d.appendChild(s);list.appendChild(d);
  });
  list.classList.toggle('hidden',$('#importStrategy').value!=='manual');
}
function closeImport(){
  closeDialog('#importPreview'); pendingImport=null;
}
function applyImport(){
  if(!pendingImport) return;
  const before=JSON.stringify(labels), beforeLabels=JSON.parse(before), beforeReviews=JSON.stringify(reviewDrafts), strategy=$('#importStrategy').value;
  snapshotUndo('Import');
  let applied=0,kept=0;
  pendingImport.records.forEach(({id,rec,unchanged})=>{
    const cur=labels[id];
    if(unchanged){ kept++; return; }
    let use=!cur;
    if(cur && strategy==='replace') use=true;
    else if(cur && strategy==='newer') use=!!rec.ts && (!cur.ts || rec.ts>cur.ts);
    else if(cur && strategy==='manual'){ const s=document.querySelector(`[data-conflict="${id}"]`); use=!!s&&s.value==='import'; }
    if(use){
      const draft=reviewDrafts[id]||{};
      labels[id]={...rec,flagged:!!(rec.flagged||draft.flagged),confidence:rec.confidence||draft.confidence||'',
        reasons:Array.from(new Set([...(rec.reasons||[]),...(draft.reasons||[])])),ts:rec.ts||new Date().toISOString()};
      delete reviewDrafts[id]; applied++;
    }
    else kept++;
  });
  closeImport(); saveStore(); refreshPoints(); applyPointFilter(); updateCounts();
  enqueueSyncChanges(beforeLabels,labels);
  if(curIdx>=0) renderPoint();
  toast(`Applied ${applied} label${applied===1?'':'s'} · kept ${kept}`, 'Undo', ()=>{
    const current=labels;labels=sanitizeLabels(JSON.parse(before));reviewDrafts=sanitizeReviewDrafts(JSON.parse(beforeReviews));saveStore();refreshPoints();applyPointFilter();updateCounts();
    enqueueSyncChanges(current,labels);
    if(curIdx>=0) renderPoint(); toast('Import undone');
  },5000);
}
// Full RFC-4180-ish tokenizer: quotes may contain commas and newlines, "" -> ".
// Records are split on unquoted CR/LF, so a multiline note stays one record.
function tokenizeCSV(txt){
  const rows=[]; let row=[], cur='', q=false;
  const pushCell=()=>{ row.push(cur); cur=''; };
  const pushRow=()=>{ pushCell(); rows.push(row); row=[]; };
  for(let i=0;i<txt.length;i++){
    const ch=txt[i];
    if(q){
      if(ch==='"'){ if(txt[i+1]==='"'){ cur+='"'; i++; } else q=false; }
      else cur+=ch;
    } else if(ch==='"'){ q=true; }
    else if(ch===','){ pushCell(); }
    else if(ch==='\r'){ if(txt[i+1]==='\n') i++; pushRow(); }
    else if(ch==='\n'){ pushRow(); }
    else cur+=ch;
  }
  if(cur!=='' || row.length){ pushRow(); }
  return rows;
}
function parseCSV(txt){
  const rows=tokenizeCSV(txt).filter(r=>r.some(c=>c.trim()!==''));
  if(!rows.length) return [];
  const hdr=rows.shift();
  const ix=n=>hdr.indexOf(n);
  return rows.map(cells=>({
    dataset:cells[ix('dataset')], id:cells[ix('id')], stratum:cells[ix('stratum')], label:cells[ix('label')],
    lon:cells[ix('lon')], lat:cells[ix('lat')], note:cells[ix('note')]||'',
    labeler:cells[ix('labeler')]||'', ts:cells[ix('ts')]||'',
    flagged:cells[ix('flagged')]||'',confidence:cells[ix('confidence')]||'',reasons:cells[ix('reasons')]||'',
    campaign:cells[ix('campaign')]||'',assignment:cells[ix('assignment')]||'',assignment_id:cells[ix('assignment_id')]||''
  }));
}

// ------------------------------------------------------------------ UI persistence
function saveUI(){ localStorage.setItem(KEY_UI, JSON.stringify({
  src:activeSource, blind:blindMode, autoAdvance, filter:pointFilter,
  panelCollapsed:document.body.classList.contains('panel-collapsed')
})); }
function loadUI(){ try{const u=JSON.parse(localStorage.getItem(KEY_UI)||'{}');
  if(typeof u.src==='string') activeSource=u.src;
  if(typeof u.blind==='boolean') blindMode=u.blind;
  if(typeof u.autoAdvance==='boolean') autoAdvance=u.autoAdvance;
  if(typeof u.filter==='string') pointFilter=u.filter;
  if(u.panelCollapsed) document.body.classList.add('panel-collapsed');
}catch(e){} }

let dialogReturnFocus=null;
function openDialog(sel){ const d=$(sel);dialogReturnFocus=document.activeElement;d.classList.remove('hidden');const box=d.querySelector('[tabindex="-1"]')||d.querySelector('button');if(box)box.focus(); }
function closeDialog(sel){ const d=$(sel);d.classList.add('hidden');if(dialogReturnFocus&&dialogReturnFocus.focus)dialogReturnFocus.focus();dialogReturnFocus=null; }
function trapFocus(e,sel){ if(e.key!=='Tab')return;const els=Array.from($(sel).querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex="0"]')).filter(x=>x.offsetParent!==null);if(!els.length)return;const a=els[0],z=els[els.length-1];if(e.shiftKey&&document.activeElement===a){z.focus();e.preventDefault();}else if(!e.shiftKey&&document.activeElement===z){a.focus();e.preventDefault();} }
function setWaybackPreset(kind){ if(!wb.view.length)return; let idx=wb.view[0]; if(kind==='oldest')idx=wb.view[wb.view.length-1]; else if(kind==='5y'){const y=new Date().getFullYear()-5;idx=wb.view.reduce((best,i)=>Math.abs(parseInt(wb.releases[i].date)-y)<Math.abs(parseInt(wb.releases[best].date)-y)?i:best,wb.view[0]);} wb.idx=idx;applyWayback();updateWbStepBtns(); }

function openSyncConfig(){
  $('#syncEndpointInput').value=syncEndpoint;$('#syncSheetInput').value=syncSheetUrl;
  openDialog('#syncModal');$('#syncEndpointInput').focus();
}
function saveSyncConfig(){
  const endpoint=$('#syncEndpointInput').value.trim(),sheet=$('#syncSheetInput').value.trim();
  if(endpoint&&!validSyncEndpoint(endpoint)){toast('Use an HTTPS Google Apps Script Web App URL');return;}
  if(sheet&&!validSyncEndpoint(sheet)){toast('Use a valid HTTPS Google Sheet URL');return;}
  syncEndpoint=endpoint;syncSheetUrl=sheet;syncEnabled=true;persistSyncState();
  closeDialog('#syncModal');updateSyncStatus();queueAllLabelsForSync();
}

// ------------------------------------------------------------------ wiring
function wire(){
  $('#prevBtn').onclick=()=>gotoIdx(curIdx-1);
  $('#nextBtn').onclick=()=>gotoIdx(curIdx+1);
  $('#nextUnlabeled').onclick=nextForFilter;
  document.querySelectorAll('.lblbtn').forEach(b=> b.onclick=()=>setLabel(b.dataset.lbl));
  let noteTimer=0;
  $('#note').addEventListener('input', ()=>{
    $('#noteStatus').textContent='Saving…'; autoGrowNote(); clearTimeout(noteTimer);
    noteTimer=setTimeout(saveNote, 350);
  });
  $('#note').addEventListener('change', ()=>{ clearTimeout(noteTimer); saveNote(); });
  $('#clearLabelBtn').onclick=clearLabel;
  $('#downloadBtn').onclick=download;
  $('#uploadBtn').onclick=()=>$('#uploadInput').click();
  $('#areaEstimatesBtn').onclick=openAreaEstimates;
  $('#closeAreaEstimates').onclick=()=>closeDialog('#areaModal');
  $('#areaLevel').onchange=renderAreaEstimates;$('#areaScenario').onchange=renderAreaEstimates;
  $('#uploadInput').onchange=e=>{ if(e.target.files[0]) handleUpload(e.target.files[0]); e.target.value=''; };
  $('#helpBtn').onclick=()=>openDialog('#helpModal');
  $('#closeHelp').onclick=()=>closeDialog('#helpModal');
  $('#closeCompletion').onclick=()=>closeDialog('#completionModal'); $('#finalDownload').onclick=exportFinal;
  $('#welcomeUpload').onclick=()=>$('#uploadInput').click();
  $('#switchLabeller').onclick=()=>{ $('#labelerName').value=labeler; $('#intro').classList.remove('hidden'); $('#labelerName').focus(); };
  $('#gotoBtn').onclick=()=>{const id=Number($('#gotoInput').value),i=byId(id);if(i>=0)gotoIdx(i);else toast('Point ID not found');};
  $('#gotoInput').onkeydown=e=>{if(e.key==='Enter')$('#gotoBtn').click();};
  $('#historyBack').onclick=historyBack; $('#copyPoint').onclick=copyPoint; $('#copyCoords').onclick=copyPoint; $('#recenterBtn').onclick=recenter;
  $('#nextFlagged').onclick=()=>gotoQueue(r=>r.flagged,'No flagged points');
  $('#nextLow').onclick=()=>gotoQueue(r=>r.confidence==='low','No low-confidence points');
  $('#nextDisagreement').onclick=()=>{applyPointFilter('disagreement');nextForFilter();};
  document.querySelectorAll('[data-zoom]').forEach(b=>b.onclick=()=>{recenter();map.easeTo({zoom:+b.dataset.zoom,duration:350});});
  $('#flagBtn').onclick=toggleFlag;
  document.querySelectorAll('[data-confidence]').forEach(b=>b.onclick=()=>updateReviewField('confidence',b.dataset.confidence));
  document.querySelectorAll('[data-tag]').forEach(b=>b.onclick=()=>addReason(b.dataset.tag));
  $('#panelToggle').onclick=()=>{document.body.classList.toggle('panel-collapsed');setTimeout(()=>map.resize(),20);saveUI();};
  $('#pointFilter').onchange=e=>applyPointFilter(e.target.value);
  document.querySelectorAll('.chip[data-filter]').forEach(c=>c.onclick=()=>
    applyPointFilter(pointFilter===c.dataset.filter?'all':c.dataset.filter));
  $('#cancelImport').onclick=closeImport; $('#applyImport').onclick=applyImport;
  $('#importStrategy').onchange=renderConflictChoices;
  $('#blindMode').onchange=e=>{
    blindMode=e.target.checked;
    document.querySelectorAll('.model-key').forEach(x=>x.classList.toggle('hidden',blindMode));
    refreshPoints(); if(curIdx>=0) renderPoint(); saveUI();
  };
  $('#autoAdvance').onchange=e=>{ autoAdvance=e.target.checked; saveUI(); };
  $('#sheetSync').onchange=e=>{syncEnabled=e.target.checked;persistSyncState();updateSyncStatus();if(syncEnabled)flushSheetSync();};
  $('#syncConfigure').onclick=openSyncConfig;$('#cancelSyncConfig').onclick=()=>closeDialog('#syncModal');
  $('#saveSyncConfig').onclick=saveSyncConfig;$('#syncNow').onclick=queueAllLabelsForSync;
  // Esri Wayback controls
  $('#wbSelect').onchange=e=>{ wb.idx=+e.target.value; applyWayback(); updateWbStepBtns(); };
  $('#wbPrev').onclick=()=>wbStep(+1);   // older
  $('#wbNext').onclick=()=>wbStep(-1);   // newer
  $('#wbLocal').onchange=e=>{ wb.local=e.target.checked; refreshWbLocal(); };
  $('#wbCompare').onchange=e=>{
    wb.compare=e.target.checked;
    $('#wb-row-b').classList.toggle('hidden', !wb.compare);
    if(wb.compare) enableCompare(); else disableCompare();
  };
  $('#wbSelectB').onchange=e=>{ wb.idxB=+e.target.value; applyWaybackB(); };
  $('#wbPrevB').onclick=()=>{ const p=wb.view.indexOf(wb.idxB); const np=Math.min(wb.view.length-1,(p<0?0:p)+1);
    wb.idxB=wb.view[np]; fillWbSelect($('#wbSelectB'),wb.idxB); applyWaybackB(); };
  $('#wbNextB').onclick=()=>{ const p=wb.view.indexOf(wb.idxB); const np=Math.max(0,(p<0?0:p)-1);
    wb.idxB=wb.view[np]; fillWbSelect($('#wbSelectB'),wb.idxB); applyWaybackB(); };
  $('#wbSwap').onclick=swapWayback; $('#wbReset').onclick=resetDivider; $('#wbFlicker').onchange=e=>setFlicker(e.target.checked);
  $('#wbRecent').onclick=()=>setWaybackPreset('recent'); $('#wb5y').onclick=()=>setWaybackPreset('5y'); $('#wbOldest').onclick=()=>setWaybackPreset('oldest');

  const drop=e=>{e.preventDefault();document.body.classList.remove('drop-active');const f=e.dataTransfer&&e.dataTransfer.files[0];if(f)handleUpload(f);};
  document.addEventListener('dragover',e=>{e.preventDefault();document.body.classList.add('drop-active');});
  document.addEventListener('dragleave',e=>{if(!e.relatedTarget)document.body.classList.remove('drop-active');});document.addEventListener('drop',drop);

  document.addEventListener('keydown', e=>{
    if(!$('#importPreview').classList.contains('hidden')){
      if(e.key==='Escape') closeImport(); else trapFocus(e,'#importPreview'); return;
    }
    if(!$('#completionModal').classList.contains('hidden')){if(e.key==='Escape')closeDialog('#completionModal');else trapFocus(e,'#completionModal');return;}
    if(!$('#areaModal').classList.contains('hidden')){if(e.key==='Escape')closeDialog('#areaModal');else trapFocus(e,'#areaModal');return;}
    if(!$('#helpModal').classList.contains('hidden')){if(e.key==='Escape')closeDialog('#helpModal');else trapFocus(e,'#helpModal');return;}
    if(!$('#syncModal').classList.contains('hidden')){if(e.key==='Escape')closeDialog('#syncModal');else trapFocus(e,'#syncModal');return;}
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){undoLast();e.preventDefault();return;}
    if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT'||e.target.tagName==='SELECT') return;
    const map6={'1':'intact','2':'moderate','3':'severe','4':'transformed','5':'nothicket','6':'unsure'};
    if(map6[e.key]){ setLabel(map6[e.key]); e.preventDefault(); }
    else if(e.key==='ArrowRight'||e.key===' '){ gotoIdx(curIdx+1); e.preventDefault(); }
    else if(e.key==='ArrowLeft'){ gotoIdx(curIdx-1); e.preventDefault(); }
    else if(e.key.toLowerCase()==='n'){ nextForFilter(); e.preventDefault(); }
    else if(e.key.toLowerCase()==='f'){toggleFlag();e.preventDefault();}
    else if(e.key==='?'){openDialog('#helpModal');e.preventDefault();}
    else if(e.key==='Escape'){map.getCanvas().focus();}
    else if(e.key==='['&&activeSource==='wayback'){wbStep(+1);e.preventDefault();}
    else if(e.key===']'&&activeSource==='wayback'){wbStep(-1);e.preventDefault();}
    else if(['i','g','s','w'].includes(e.key.toLowerCase())){const x={i:'esri',g:'google',s:'s2',w:'wayback'}[e.key.toLowerCase()];setSource(x);e.preventDefault();}
  });

  $('#startBtn').onclick=()=>{
    labeler=($('#labelerName').value||'').trim();
    if(!labeler&&!$('#allowAnon').checked){toast('Enter a name/initials, or explicitly allow anonymous labeling');$('#labelerName').focus();return;}
    localStorage.setItem(KEY_NAME, labeler);
    $('#intro').classList.add('hidden');
    // first unlabeled, or first point
    const firstUnlab=POINTS.findIndex(p=>isRequiredPoint(p)&&!labels[p.id]);
    gotoIdx(firstUnlab>=0?firstUnlab:0);
  };
}

// ------------------------------------------------------------------ boot
function boot(){
  loadStore(); loadUI();
  // expose for tooling / debugging (harmless in production)
  window.POINTS=POINTS;
  window.REQUIRED_POINTS=REQUIRED_POINTS;window.DISAGREEMENT_MANIFEST=DISAGREEMENT_MANIFEST;
  window.ASSIGNMENT=ASSIGNMENT_RECORD?{campaign:CAMPAIGN,code:ASSIGNMENT_CODE,id:ASSIGNMENT_ID,assigned:REQUIRED_POINTS.length}:null;
  Object.defineProperty(window,'labels',{get:()=>labels});
  $('#labelerName').value = labeler;
  const assignmentText=ASSIGNMENT_RECORD
    ? `${CAMPAIGN||'Campaign'} · assignment ${ASSIGNMENT_CODE} · ${REQUIRED_POINTS.length} Round 2 points · ${DISAGREEMENT_IDS.size} disagreements available`
    : ASSIGNMENT_ERROR||(ASSIGNMENT_CODES.length?`Coordinator mode · ${REQUIRED_POINTS.length} Round 2 points · ${DISAGREEMENT_IDS.size} disagreements`:`${REQUIRED_POINTS.length} Round 2 points · ${DISAGREEMENT_IDS.size} disagreements`);
  ['#assignmentStatus','#introAssignment'].forEach(sel=>{const el=$(sel);el.textContent=assignmentText;el.classList.toggle('error',!!ASSIGNMENT_ERROR);});
  if(ASSIGNMENT_ERROR){ $('#startBtn').disabled=true; $('#welcomeUpload').disabled=true; }
  $('#blindMode').checked=blindMode; $('#autoAdvance').checked=autoAdvance;
  $('#pointFilter').value=pointFilter;
  document.querySelectorAll('.model-key').forEach(x=>x.classList.toggle('hidden',blindMode));
  const savedCount=REQUIRED_POINTS.filter(p=>labels[p.id]).length;
  if(labeler && savedCount) $('#startBtn').textContent=`Continue as ${labeler} · ${REQUIRED_POINTS.length-savedCount} remaining`;
  buildSourceButtons(); wire(); initMap(); updateCounts();
  updateSyncStatus();flushSheetSync();
  loadGeeLayers();   // async; re-renders source buttons if a manifest is present
  // restore hash target after map ready
  const m=location.hash.match(/p=(\d+)/);
  if(m){ const tid=+m[1]; map && map.on('load',()=>gotoId(tid)); }
  if('serviceWorker' in navigator && location.protocol.startsWith('http')) navigator.serviceWorker.register('./sw.js').catch(()=>{});
  window.addEventListener('online',()=>{setSourceHealth('Network restored',false);flushSheetSync();});
  window.addEventListener('offline',()=>setSourceHealth('Offline: saved labels remain available; uncached imagery may not load.',true));
  setInterval(()=>{ if(Object.keys(labels).length && (!lastBackup || Date.now()-Date.parse(lastBackup)>30*60*1000)) toast('Backup reminder: download your latest work',null,null,5000); },15*60*1000);
}
boot();
