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
const KEY_NAME   = 'thicket-inspector-name';
const KEY_UI     = 'thicket-inspector-ui';
// Coordinates must match the embedded draw within ~1 m to count as the same point.
const COORD_EPS = 1e-5;

// ------------------------------------------------------------------ state
let labels = {};            // id -> {label, note, labeler, ts}
let curIdx = -1;            // index into POINTS
let labeler = '';
let activeSource = 'esri';
let map;
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
function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t=setTimeout(()=>t.classList.remove('show'),1800); }

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
                  stratum:p.s, lon:p.lon, lat:p.lat };
  }
  return clean;
}
function loadStore(){
  let raw={};
  try{ raw = JSON.parse(localStorage.getItem(KEY_LABELS)||'{}'); }catch(e){ raw={}; }
  labels = sanitizeLabels(raw);
  labeler = localStorage.getItem(KEY_NAME) || '';
}
function saveStore(){ localStorage.setItem(KEY_LABELS, JSON.stringify(labels)); }

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
    map.addSource('pts', {type:'geojson', data: pointsGeoJSON()});
    map.addLayer({ id:'pts-layer', type:'circle', source:'pts', paint:{
      'circle-radius':['interpolate',['linear'],['zoom'],5,3,10,5,14,7],
      'circle-color':['match',['get','stratum'],
        'intact',STRAT_COLOR.intact,'moderate',STRAT_COLOR.moderate,
        'severe',STRAT_COLOR.severe,'#888'],
      'circle-stroke-width':['case',['get','labeled'],2.5,1],
      'circle-stroke-color':['case',['get','labeled'],'#ffffff','#00000088'],
      'circle-opacity':0.9
    }});
    // selection halo
    map.addLayer({ id:'sel-layer', type:'circle', source:'pts',
      filter:['==',['get','id'],-1], paint:{
        'circle-radius':['interpolate',['linear'],['zoom'],5,7,14,13],
        'circle-color':'#00000000','circle-stroke-width':3,'circle-stroke-color':'#4f8cff'
      }});

    map.on('click','pts-layer', e=>{
      const id = e.features[0].properties.id; gotoId(id);
    });
    map.on('mouseenter','pts-layer', ()=> map.getCanvas().style.cursor='pointer');
    map.on('mouseleave','pts-layer', ()=> map.getCanvas().style.cursor='');

    fetchWayback();
    refreshPoints();
    // Apply the remembered imagery source (base layer above is Esri by default).
    // GEE sources aren't in SOURCES yet — loadGeeLayers() re-applies once baked.
    if(activeSource && activeSource!=='esri' && SOURCES[activeSource]) setSource(activeSource);
  });
}

function pointsGeoJSON(){
  return { type:'FeatureCollection', features: POINTS.map(p=>({
    type:'Feature', geometry:{type:'Point',coordinates:[p.lon,p.lat]},
    properties:{ id:p.id, stratum:p.s, labeled: !!labels[p.id] }
  }))};
}
function refreshPoints(){
  const src = map && map.getSource('pts'); if(src) src.setData(pointsGeoJSON());
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
  }catch(e){ wb.releases=[]; }
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
  const c = map.getCenter();
  try{
    const set = await wbLocalReleases(c);
    if(myId !== wb.localId) return;                // superseded
    const idxs = wb.releases.map((r,i)=> set.has(r.num) ? i : -1).filter(i=>i>=0);
    wb.view = idxs.length ? idxs : wb.releases.map((_,i)=>i);
    if(!wb.view.includes(wb.idx)) { wb.idx = wb.view[0]; applyWayback(); }
    fillWbSelect($('#wbSelect'), wb.idx); fillWbSelect($('#wbSelectB'), wb.idxB);
    updateWbStepBtns();
  }catch(e){ if(myId===wb.localId){ if(sel) sel.disabled=false; } }
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
  activeSource=k;
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
function gotoIdx(idx){
  if(idx<0||idx>=POINTS.length) return;
  curIdx=idx; const p=POINTS[idx];
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
function nextUnlabeled(){
  for(let k=1;k<=POINTS.length;k++){
    const i=(curIdx+k)%POINTS.length;
    if(!labels[POINTS[i].id]){ gotoIdx(i); return; }
  }
  toast('All points labeled 🎉');
}

// ------------------------------------------------------------------ render
function renderPoint(){
  const p=POINTS[curIdx]; if(!p) return;
  $('#curId').textContent=p.id; $('#totId').textContent=POINTS.length;
  $('#curStratum').innerHTML = ` · <span class="pill ${p.s}">${p.s}</span>`;
  $('#mCoord').textContent=`${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;
  $('#mStratum').innerHTML=`<span class="pill ${p.s}">${CLASS_LABEL[p.s]||p.s}</span>`;
  const rec=labels[p.id];
  const mLabel=$('#mLabel');
  if(rec && isValidClass(rec.label)){
    const b=document.createElement('b');
    b.style.color = STRAT_COLOR[rec.label]||'#fff';
    b.textContent = CLASS_LABEL[rec.label];
    mLabel.textContent=''; mLabel.appendChild(b);
  } else { mLabel.textContent='–'; }
  $('#note').value = rec ? (rec.note||'') : '';
  document.querySelectorAll('.lblbtn').forEach(b=>{
    b.className='lblbtn';
    if(rec && isValidClass(rec.label) && rec.label===b.dataset.lbl) b.classList.add('sel-'+rec.label);
  });
  // imagery deep links
  $('#gmapsLink').href = `https://www.google.com/maps/@${p.lat},${p.lon},400m/data=!3m1!1e3`;
  $('#gearthLink').href = `https://earth.google.com/web/@${p.lat},${p.lon},0a,800d,35y,0h,0t,0r`;
  $('#prevBtn').disabled = curIdx<=0;
  $('#nextBtn').disabled = curIdx>=POINTS.length-1;
}

function setLabel(cls){
  if(curIdx<0){ toast('Pick a point first'); return; }
  const p=POINTS[curIdx];
  if(labels[p.id] && labels[p.id].label===cls){    // toggle off
    delete labels[p.id];
  }else{
    labels[p.id]={ label:cls, note:$('#note').value.trim(),
                   labeler:labeler, ts:new Date().toISOString(),
                   stratum:p.s, lon:p.lon, lat:p.lat };
  }
  saveStore(); renderPoint(); refreshPoints(); updateCounts();
}
function saveNote(){
  const p=POINTS[curIdx]; if(!p||!labels[p.id]) return;
  labels[p.id].note=$('#note').value.trim(); saveStore();
}

function updateCounts(){
  const c={intact:0,moderate:0,severe:0,all:0};
  Object.values(labels).forEach(r=>{ if(!r||!isValidClass(r.label)) return;
    c.all++; if(c[r.label]!=null) c[r.label]++; });
  $('#c_all').textContent=c.all; $('#c_intact').textContent=c.intact;
  $('#c_moderate').textContent=c.moderate; $('#c_severe').textContent=c.severe;
}

// ------------------------------------------------------------------ import / export
function download(){
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
  const hdr='id,stratum,lon,lat,label,note,labeler,ts';
  const csv=[hdr].concat(rows.map(r=>
    [q(r.id),q(r.stratum),q(r.lon),q(r.lat),q(r.label),qt(r.note||''),qt(r.labeler),q(r.ts)].join(',')
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
      if(file.name.toLowerCase().endsWith('.csv')){ rows=parseCSV(fr.result); }
      else { const j=JSON.parse(fr.result); rows=j.labels||[]; fileDataset=j.dataset||null; }
      if(!Array.isArray(rows)){ toast('Could not read that file'); return; }

      // Dataset-mismatch guard: a JSON export from a different sample draw must
      // not silently paint its labels onto these coordinates.
      if(fileDataset && fileDataset !== DS_ID){
        if(!confirm('This file was made for a different sample draw ('+fileDataset+
          ') than the current one ('+DS_ID+'). Coordinates may not match. Import anyway?'))
          { toast('Import cancelled'); return; }
      }

      let merged=0, skipped=0, moved=0, kept=0;
      rows.forEach(r=>{
        const id=Number(r.id); const i=byId(id); if(i<0){ skipped++; return; }
        if(!isValidClass(r.label)){ skipped++; return; }
        const p=POINTS[i];
        // If the file carries coordinates, they must match the embedded draw.
        if(r.lon!=null && r.lat!=null){
          const dlon=Math.abs(Number(r.lon)-p.lon), dlat=Math.abs(Number(r.lat)-p.lat);
          if(!(dlon<=COORD_EPS && dlat<=COORD_EPS)){ moved++; return; }
        }
        // Conflict: don't let an older file clobber a newer local label.
        const cur=labels[id], incTs=typeof r.ts==='string'?r.ts:'';
        if(cur && cur.ts && incTs && incTs < cur.ts){ kept++; return; }
        labels[id]={label:r.label, note:String(r.note||''), labeler:String(r.labeler||labeler),
                    ts:incTs||new Date().toISOString(), stratum:p.s, lon:p.lon, lat:p.lat};
        merged++;
      });
      saveStore(); refreshPoints(); updateCounts();
      if(curIdx>=0) renderPoint();
      const extra=[skipped&&`${skipped} skipped`, moved&&`${moved} moved`,
                   kept&&`${kept} kept newer`].filter(Boolean).join(', ');
      toast(`Loaded ${merged} labels${extra?` (${extra})`:''} — resuming`);
    }catch(e){ toast('Could not read that file'); }
  };
  fr.readAsText(file);
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
    id:cells[ix('id')], stratum:cells[ix('stratum')], label:cells[ix('label')],
    note:cells[ix('note')]||'', labeler:cells[ix('labeler')]||'', ts:cells[ix('ts')]||''
  }));
}

// ------------------------------------------------------------------ UI persistence
function saveUI(){ localStorage.setItem(KEY_UI, JSON.stringify({src:activeSource})); }
function loadUI(){ try{const u=JSON.parse(localStorage.getItem(KEY_UI)||'{}');
  if(u.src&&SOURCES[u.src]) activeSource=u.src;}catch(e){} }

// ------------------------------------------------------------------ wiring
function wire(){
  $('#prevBtn').onclick=()=>gotoIdx(curIdx-1);
  $('#nextBtn').onclick=()=>gotoIdx(curIdx+1);
  $('#nextUnlabeled').onclick=nextUnlabeled;
  document.querySelectorAll('.lblbtn').forEach(b=> b.onclick=()=>setLabel(b.dataset.lbl));
  $('#note').addEventListener('change', saveNote);
  $('#downloadBtn').onclick=download;
  $('#uploadBtn').onclick=()=>$('#uploadInput').click();
  $('#uploadInput').onchange=e=>{ if(e.target.files[0]) handleUpload(e.target.files[0]); e.target.value=''; };
  $('#helpBtn').onclick=()=>$('#intro').classList.remove('hidden');
  $('#hideLabeled').onchange=e=>{
    map.setFilter('pts-layer', e.target.checked ? ['!',['get','labeled']] : null);
  };
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

  document.addEventListener('keydown', e=>{
    if(e.target.tagName==='TEXTAREA'||e.target.tagName==='INPUT') return;
    const map5={'1':'intact','2':'moderate','3':'severe','4':'notthicket','5':'unsure'};
    if(map5[e.key]){ setLabel(map5[e.key]); e.preventDefault(); }
    else if(e.key==='ArrowRight'||e.key===' '){ gotoIdx(curIdx+1); e.preventDefault(); }
    else if(e.key==='ArrowLeft'){ gotoIdx(curIdx-1); e.preventDefault(); }
    else if(e.key.toLowerCase()==='n'){ nextUnlabeled(); e.preventDefault(); }
  });

  $('#startBtn').onclick=()=>{
    labeler=($('#labelerName').value||'').trim();
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
  buildSourceButtons(); wire(); initMap(); updateCounts();
  loadGeeLayers();   // async; re-renders source buttons if a manifest is present
  // restore hash target after map ready
  const m=location.hash.match(/p=(\d+)/);
  if(m){ const tid=+m[1]; map && map.on('load',()=>gotoId(tid)); }
}
boot();
