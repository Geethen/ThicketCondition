/* Thicket Condition Inspector — static, no backend.
   Labels persist in localStorage and export/import as JSON or CSV. */
'use strict';

const CLASSES = ['intact','moderate','severe','notthicket','unsure'];
const CLASS_SET = new Set(CLASSES);
const CLASS_LABEL = {intact:'Intact',moderate:'Moderate',severe:'Severe',
                     notthicket:'Not thicket',unsure:'Unsure'};
// Only ever trust a label that is one of our known classes. Imported files are
// untrusted input, and labels flow into innerHTML / classList below.
const isValidClass = c => CLASS_SET.has(c);
const STRAT_COLOR = {intact:'#0a7d34',moderate:'#e0a400',severe:'#c0392b'};
// DATASET_ID is injected by build.py; if the page is opened unbuilt it stays the
// literal placeholder, which still works as a (single) stable key.
const DS_ID = (typeof DATASET_ID === 'string' && !DATASET_ID.startsWith('__'))
  ? DATASET_ID : 'dev';
// Labels are namespaced by dataset so a new sample draw never shows stale labels.
const KEY_LABELS = 'thicket-inspector-labels-' + DS_ID;
const KEY_DRAFTS = 'thicket-inspector-note-drafts-' + DS_ID;
const KEY_NAME   = 'thicket-inspector-name';
const KEY_UI     = 'thicket-inspector-ui';
const KEY_BACKUP = 'thicket-inspector-last-backup-' + DS_ID;
const KEY_WBCACHE = 'thicket-inspector-wayback-cache-' + DS_ID;
// Coordinates must match the embedded draw within ~1 m to count as the same point.
const COORD_EPS = 1e-5;

// ------------------------------------------------------------------ state
let labels = {};            // id -> {label, note, labeler, ts}
let noteDrafts = {};        // id -> note text before a point has a label
let curIdx = -1;            // index into POINTS
let labeler = '';
let activeSource = 'esri';
let blindMode = true;
let autoAdvance = true;
let pointFilter = 'all';
let pendingAdvance = 0;
let pendingImport = null;
let undoStack = [];
let navHistory = [], navHistoryPos = -1;
let lastBackup = localStorage.getItem(KEY_BACKUP) || '';
let wbPointCache = {};
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
  labeler = localStorage.getItem(KEY_NAME) || '';
  try{ wbPointCache=JSON.parse(localStorage.getItem(KEY_WBCACHE)||'{}')||{}; }catch(e){ wbPointCache={}; }
}
function saveStore(){
  try{
    localStorage.setItem(KEY_LABELS, JSON.stringify(labels));
    localStorage.setItem(KEY_DRAFTS, JSON.stringify(noteDrafts));
    const el=$('#saveStatus'); if(el) el.textContent='Saved locally · '+new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})+' · backup '+(lastBackup?new Date(lastBackup).toLocaleString():'never');
    return true;
  }catch(e){
    const el=$('#saveStatus'); if(el) el.textContent='⚠ Could not save in this browser';
    toast('Could not save locally — download a backup'); return false;
  }
}

function snapshotUndo(description){
  undoStack.push({labels:JSON.stringify(labels),drafts:JSON.stringify(noteDrafts),description});
  if(undoStack.length>30) undoStack.shift();
}
function undoLast(){
  const u=undoStack.pop(); if(!u){ toast('Nothing to undo'); return; }
  labels=sanitizeLabels(JSON.parse(u.labels));
  try{ noteDrafts=JSON.parse(u.drafts)||{}; }catch(e){ noteDrafts={}; }
  saveStore(); refreshPoints(); applyPointFilter(); updateCounts(); if(curIdx>=0) renderPoint();
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
    properties:{ id:p.id, stratum:blindMode?'blind':p.s, labeled: !!labels[p.id],
                 label:labels[p.id] ? labels[p.id].label : '',
                 flagged:!!(labels[p.id]&&labels[p.id].flagged),
                 confidence:labels[p.id] ? labels[p.id].confidence||'' : '' }
  }))};
}
function refreshPoints(){
  const src = map && map.getSource('pts'); if(src) src.setData(pointsGeoJSON());
}
function applyPointFilter(value=pointFilter){
  pointFilter=value;
  const filters={
    all:null,
    unlabeled:['==',['get','labeled'],false],
    labeled:['==',['get','labeled'],true],
    intact:['==',['get','label'],'intact'],
    moderate:['==',['get','label'],'moderate'],
    severe:['==',['get','label'],'severe'],
    notthicket:['==',['get','label'],'notthicket'],
    unsure:['==',['get','label'],'unsure'],
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
    const names={all:'unlabeled',unlabeled:'unlabeled',labeled:'labeled',notthicket:'not thicket'};
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
async function lookupCaptureDate(rel){
  const el = $('#wbCapDate'); if(!el) return;
  if(!rel.metaUrl){ el.textContent = rel.date + ' (release)'; return; }
  const c = map.getCenter();
  const key = rel.num + '@' + c.lng.toFixed(4) + ',' + c.lat.toFixed(4);
  if(_wbCapCache.has(key)){ el.textContent = _wbCapCache.get(key); return; }
  const myId = ++wb.capId;
  el.innerHTML = '<span class="wbspin"></span>';
  try{
    let a = await _wbIdentify(rel, c, 'top');
    if(!a || a.SRC_DATE==null) a = await _wbIdentify(rel, c, 'all');
    if(myId !== wb.capId) return;
    const d = a && (a.SRC_DATE || a.SRC_DATE2 || a.SAMP_RES);
    const txt = d ? String(d) : (rel.date + ' (release)');
    _wbCapCache.set(key, txt); el.textContent = txt;
  }catch(e){ if(myId===wb.capId) el.textContent = rel.date + ' (release)'; }
}
async function _wbIdentify(rel, pt, layersMode){
  const z = map.getZoom();
  const half = 360/Math.pow(2,z)/2;
  const ext = [pt.lng-half, pt.lat-half, pt.lng+half, pt.lat+half];
  const qs = new URLSearchParams({
    f:'json',
    geometry: JSON.stringify({x:pt.lng, y:pt.lat, spatialReference:{wkid:4326}}),
    geometryType:'esriGeometryPoint', sr:'4326', tolerance:'2',
    returnGeometry:'false', mapExtent: ext.join(','), imageDisplay:'512,512,96',
    layers: layersMode
  });
  const j = await fetch(rel.metaUrl + '/identify?' + qs).then(r=>r.json());
  return j && j.results && j.results[0] && j.results[0].attributes;
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
  for(let k=1;k<=POINTS.length;k++){ const i=(curIdx+k)%POINTS.length,r=labels[POINTS[i].id]; if(r&&test(r)){gotoIdx(i);return;} }
  toast(empty);
}
function recenter(){ const p=POINTS[curIdx]; if(p) map.easeTo({center:[p.lon,p.lat],duration:350}); }
async function copyPoint(){ const p=POINTS[curIdx]; if(!p)return; const text=`ID ${p.id}: ${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}`;
  try{ await navigator.clipboard.writeText(text); toast('Point ID and coordinates copied'); }catch(e){ toast(text); }
}
function nextUnlabeled(){
  for(let k=1;k<=POINTS.length;k++){
    const i=(curIdx+k)%POINTS.length;
    if(!labels[POINTS[i].id]){ gotoIdx(i); return; }
  }
  toast('All points labeled 🎉');
}
function nextForFilter(){
  if(pointFilter==='all'||pointFilter==='unlabeled'){ nextUnlabeled(); return; }
  for(let k=1;k<=POINTS.length;k++){
    const i=(curIdx+k)%POINTS.length, rec=labels[POINTS[i].id];
    if((pointFilter==='labeled'&&rec)||(rec&&rec.label===pointFilter)){ gotoIdx(i); return; }
  }
  toast(`No ${pointFilter==='labeled'?'labeled':pointFilter} points to review`);
}

// ------------------------------------------------------------------ render
function renderPoint(){
  const p=POINTS[curIdx]; if(!p) return;
  $('#curId').textContent=p.id; $('#curOrdinal').textContent=curIdx+1; $('#totId').textContent=POINTS.length;
  const rec=labels[p.id];
  const showPrediction=!blindMode || !!rec;
  if(showPrediction) $('#curStratum').innerHTML = ` · <span class="pill ${p.s}">${p.s}</span>`;
  else $('#curStratum').textContent=' · model hidden';
  $('#mCoord').textContent=`${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;
  if(showPrediction) $('#mStratum').innerHTML=`<span class="pill ${p.s}">${CLASS_LABEL[p.s]||p.s}</span>`;
  else $('#mStratum').textContent='Hidden until label saved';
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
  $('#flagBtn').setAttribute('aria-pressed',String(!!(rec&&rec.flagged)));
  document.querySelectorAll('[data-confidence]').forEach(b=>b.classList.toggle('active',!!rec&&rec.confidence===b.dataset.confidence));
  // imagery deep links
  $('#gmapsLink').href = `https://www.google.com/maps/@${p.lat},${p.lon},400m/data=!3m1!1e3`;
  $('#gearthLink').href = `https://earth.google.com/web/@${p.lat},${p.lon},0a,800d,35y,0h,0t,0r`;
  $('#prevBtn').disabled = curIdx<=0;
  $('#nextBtn').disabled = curIdx>=POINTS.length-1;
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
  snapshotUndo('Label change');
  const previousDraft=noteDrafts[p.id];
  labels[p.id]={ label:cls, note:$('#note').value.trim(),
                 labeler:labeler, ts:new Date().toISOString(),
                 flagged:previous?!!previous.flagged:false,
                 confidence:previous?previous.confidence||'':'', reasons:previous?previous.reasons||[]:[],
                 stratum:p.s, lon:p.lon, lat:p.lat };
  delete noteDrafts[p.id];
  const saved=saveStore(); renderPoint(); refreshPoints(); updateCounts();
  const undo=()=>{
    clearTimeout(pendingAdvance);
    if(previous) labels[p.id]=previous; else delete labels[p.id];
    if(previousDraft!=null) noteDrafts[p.id]=previousDraft; else delete noteDrafts[p.id];
    saveStore(); refreshPoints(); updateCounts(); gotoId(p.id); toast('Change undone');
  };
  toast(`Point ${p.id} marked ${CLASS_LABEL[cls]}`, 'Undo', undo, 4200);
  if(autoAdvance && saved) pendingAdvance=setTimeout(nextUnlabeled, 450);
}
function clearLabel(){
  const p=POINTS[curIdx]; if(!p||!labels[p.id]) return;
  clearTimeout(pendingAdvance);
  const previous={...labels[p.id]};
  snapshotUndo('Clear label');
  const note=$('#note').value.trim(); if(note) noteDrafts[p.id]=note;
  delete labels[p.id]; saveStore(); renderPoint(); refreshPoints(); updateCounts();
  toast(`Label cleared for point ${p.id}`, 'Undo', ()=>{
    labels[p.id]=previous; delete noteDrafts[p.id]; saveStore(); refreshPoints(); updateCounts();
    gotoId(p.id); toast('Label restored');
  }, 4200);
}
function updateReviewField(field,value){
  const p=POINTS[curIdx]; if(!p){return;} if(!labels[p.id]){ toast('Label the point before adding review metadata'); return; }
  snapshotUndo('Review metadata'); labels[p.id][field]=value; labels[p.id].ts=new Date().toISOString();
  saveStore(); refreshPoints(); updateCounts(); renderPoint();
}
function toggleFlag(){ const p=POINTS[curIdx],r=p&&labels[p.id]; if(!r){toast('Label the point before flagging it');return;} updateReviewField('flagged',!r.flagged); }
function addReason(tag){
  const p=POINTS[curIdx]; if(!p)return;
  const note=$('#note'), token=`[${tag}]`; if(!note.value.includes(token)) note.value=(note.value.trim()+' '+token).trim();
  autoGrowNote(); saveNote();
  if(labels[p.id]){ const rs=new Set(labels[p.id].reasons||[]); rs.add(tag); labels[p.id].reasons=Array.from(rs); saveStore(); }
}
function autoGrowNote(){ const n=$('#note'); if(!n)return; n.style.height='auto'; n.style.height=Math.min(180,Math.max(44,n.scrollHeight))+'px'; }
function saveNote(){
  const p=POINTS[curIdx]; if(!p) return;
  const note=$('#note').value.trim();
  if(labels[p.id]){ labels[p.id].note=note; delete noteDrafts[p.id]; }
  else if(note) noteDrafts[p.id]=note;
  else delete noteDrafts[p.id];
  saveStore();
  $('#noteStatus').textContent=note ? (labels[p.id]?'Note saved':'Draft saved locally') : '';
}

function updateCounts(){
  const c={intact:0,moderate:0,severe:0,notthicket:0,unsure:0,all:0,flagged:0,low:0};
  Object.values(labels).forEach(r=>{ if(!r||!isValidClass(r.label)) return;
    c.all++; if(c[r.label]!=null) c[r.label]++; if(r.flagged)c.flagged++; if(r.confidence==='low')c.low++; });
  $('#c_all').textContent=c.all; $('#c_intact').textContent=c.intact;
  $('#c_moderate').textContent=c.moderate; $('#c_severe').textContent=c.severe;
  $('#c_notthicket').textContent=c.notthicket; $('#c_unsure').textContent=c.unsure;
  $('#c_flagged').textContent=c.flagged; $('#c_low').textContent=c.low;
  const remaining=Math.max(0,POINTS.length-c.all), pct=POINTS.length?c.all/POINTS.length*100:0;
  $('#c_remaining').textContent=remaining; $('#progressText').textContent=`${c.all} of ${POINTS.length} labeled`;
  $('#progressPct').textContent=(pct<10?pct.toFixed(1):Math.round(pct))+'%'; $('#progressFill').style.width=pct+'%';
}

// ------------------------------------------------------------------ import / export
function exportRows(){
  return POINTS.filter(p=>labels[p.id]).map(p=>{ const r=labels[p.id]; return {id:p.id,stratum:p.s,lon:p.lon,lat:p.lat,
    label:r.label,note:r.note||'',labeler:r.labeler||labeler,ts:r.ts||'',flagged:!!r.flagged,
    confidence:r.confidence||'',reasons:(r.reasons||[]).join('|')}; });
}
function checksumText(text){ let h=2166136261; for(let i=0;i<text.length;i++){h^=text.charCodeAt(i);h=Math.imul(h,16777619);} return ('00000000'+(h>>>0).toString(16)).slice(-8); }
function download(){ openCompletion(); }
function openCompletion(){
  saveNote(); const rows=exportRows(), remaining=POINTS.length-rows.length;
  const counts={}; CLASSES.forEach(c=>counts[c]=rows.filter(r=>r.label===c).length);
  const flagged=rows.filter(r=>r.flagged), low=rows.filter(r=>r.confidence==='low'), unsure=rows.filter(r=>r.label==='unsure');
  $('#completionState').textContent=remaining?`${remaining} point${remaining===1?' is':'s are'} incomplete. You can export a backup now, but final QA is not complete.`:'All points are labeled. Review the items below before final export.';
  $('#completionState').className=remaining?'statusline error':'statusline ok';
  const stats=[['Labeled',rows.length],['Remaining',remaining],['Intact',counts.intact],['Moderate',counts.moderate],['Severe',counts.severe],['Not thicket',counts.notthicket],['Unsure',counts.unsure],['Flagged',flagged.length],['Low confidence',low.length]];
  const grid=$('#finalSummary'); grid.textContent=''; stats.forEach(([n,v])=>{const d=document.createElement('div'),b=document.createElement('b');b.textContent=v;d.append(n,b);grid.appendChild(d);});
  $('#lastBackup').textContent=lastBackup?new Date(lastBackup).toLocaleString():'Never';
  $('#finalDownload').textContent=remaining?'Download backup':'Download final';
  const review=$('#reviewList'); review.textContent='';
  const ids=new Set([...unsure,...flagged,...low].map(r=>r.id));
  if(!ids.size){const d=document.createElement('div');d.className='reviewitem';d.textContent='No unsure, flagged, or low-confidence points.';review.appendChild(d);}
  ids.forEach(id=>{const r=labels[id],d=document.createElement('div');d.className='reviewitem';d.append(`ID ${id} · ${CLASS_LABEL[r.label]}${r.flagged?' · flagged':''}${r.confidence?` · ${r.confidence} confidence`:''}`);const b=document.createElement('button');b.className='btn';b.textContent='Review';b.onclick=()=>{closeDialog('#completionModal');gotoId(id);};d.appendChild(b);review.appendChild(d);});
  openDialog('#completionModal');
}
async function exportFinal(){
  const rows = exportRows();
  const exported=new Date().toISOString(), completion={complete:rows.length===POINTS.length,total:POINTS.length,labeled:rows.length,
    flagged:rows.filter(r=>r.flagged).length,unsure:rows.filter(r=>r.label==='unsure').length,lowConfidence:rows.filter(r=>r.confidence==='low').length};
  const canonical=JSON.stringify(rows), checksum=checksumText(canonical);
  const payload={tool:'thicket_inspector',version:2,dataset:DS_ID,labeler,exported,n:rows.length,completion,checksum:{algorithm:'fnv1a-32',value:checksum},labels:rows};
  const stamp=exported.slice(0,19).replace(/[:T]/g,'-'), safe=(labeler||'anon').replace(/[^A-Za-z0-9_-]/g,'');
  const format=$('#exportFormat').value;
  if(format==='json') blobDownload(JSON.stringify(payload,null,2),`thicket_labels_${safe}_${stamp}.json`,'application/json');
  else {
    const csvSafe=v=>{let s=String(v==null?'':v);if(/^[=+\-@\t\r]/.test(s))s="'"+s;return s;},q=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"',qt=v=>'"'+csvSafe(v).replace(/"/g,'""')+'"';
    const hdr='dataset,id,stratum,lon,lat,label,note,labeler,ts,flagged,confidence,reasons,checksum';
    const csv=[hdr].concat(rows.map(r=>[q(DS_ID),q(r.id),q(r.stratum),q(r.lon),q(r.lat),q(r.label),qt(r.note),qt(r.labeler),q(r.ts),q(r.flagged),q(r.confidence),qt(r.reasons),q(checksum)].join(','))).join('\r\n');
    blobDownload(csv,`thicket_labels_${safe}_${stamp}.csv`,'text/csv');
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
      let rows, fileDataset=null;
      if(file.name.toLowerCase().endsWith('.csv')){
        rows=parseCSV(fr.result); fileDataset=rows.find(r=>r.dataset)?.dataset||null;
      }
      else { const j=JSON.parse(fr.result); rows=j.labels||[]; fileDataset=j.dataset||null; }
      if(!Array.isArray(rows)){ toast('Could not read that file'); return; }

      // Dataset-mismatch guard: a JSON export from a different sample draw must
      // not silently paint its labels onto these coordinates.
      if(fileDataset && fileDataset !== DS_ID){
        toast('Import blocked: file belongs to a different dataset'); return;
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
  const before=JSON.stringify(labels), strategy=$('#importStrategy').value;
  snapshotUndo('Import');
  let applied=0,kept=0;
  pendingImport.records.forEach(({id,rec,unchanged})=>{
    const cur=labels[id];
    if(unchanged){ kept++; return; }
    let use=!cur;
    if(cur && strategy==='replace') use=true;
    else if(cur && strategy==='newer') use=!!rec.ts && (!cur.ts || rec.ts>cur.ts);
    else if(cur && strategy==='manual'){ const s=document.querySelector(`[data-conflict="${id}"]`); use=!!s&&s.value==='import'; }
    if(use){ labels[id]={...rec,ts:rec.ts||new Date().toISOString()}; applied++; }
    else kept++;
  });
  closeImport(); saveStore(); refreshPoints(); applyPointFilter(); updateCounts();
  if(curIdx>=0) renderPoint();
  toast(`Applied ${applied} label${applied===1?'':'s'} · kept ${kept}`, 'Undo', ()=>{
    labels=sanitizeLabels(JSON.parse(before)); saveStore(); refreshPoints(); applyPointFilter(); updateCounts();
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
    flagged:cells[ix('flagged')]||'',confidence:cells[ix('confidence')]||'',reasons:cells[ix('reasons')]||''
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
    if(!$('#helpModal').classList.contains('hidden')){if(e.key==='Escape')closeDialog('#helpModal');else trapFocus(e,'#helpModal');return;}
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){undoLast();e.preventDefault();return;}
    if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT'||e.target.tagName==='SELECT') return;
    const map5={'1':'intact','2':'moderate','3':'severe','4':'notthicket','5':'unsure'};
    if(map5[e.key]){ setLabel(map5[e.key]); e.preventDefault(); }
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
    const firstUnlab=POINTS.findIndex(p=>!labels[p.id]);
    gotoIdx(firstUnlab>=0?firstUnlab:0);
  };
}

// ------------------------------------------------------------------ boot
function boot(){
  loadStore(); loadUI();
  // expose for tooling / debugging (harmless in production)
  window.POINTS=POINTS;
  Object.defineProperty(window,'labels',{get:()=>labels});
  $('#labelerName').value = labeler;
  $('#blindMode').checked=blindMode; $('#autoAdvance').checked=autoAdvance;
  $('#pointFilter').value=pointFilter;
  document.querySelectorAll('.model-key').forEach(x=>x.classList.toggle('hidden',blindMode));
  const savedCount=Object.keys(labels).length;
  if(labeler && savedCount) $('#startBtn').textContent=`Continue as ${labeler} · ${POINTS.length-savedCount} remaining`;
  buildSourceButtons(); wire(); initMap(); updateCounts();
  loadGeeLayers();   // async; re-renders source buttons if a manifest is present
  // restore hash target after map ready
  const m=location.hash.match(/p=(\d+)/);
  if(m){ const tid=+m[1]; map && map.on('load',()=>gotoId(tid)); }
  if('serviceWorker' in navigator && location.protocol.startsWith('http')) navigator.serviceWorker.register('./sw.js').catch(()=>{});
  window.addEventListener('online',()=>setSourceHealth('Network restored',false));
  window.addEventListener('offline',()=>setSourceHealth('Offline: saved labels remain available; uncached imagery may not load.',true));
  setInterval(()=>{ if(Object.keys(labels).length && (!lastBackup || Date.now()-Date.parse(lastBackup)>30*60*1000)) toast('Backup reminder: download your latest work',null,null,5000); },15*60*1000);
}
boot();
