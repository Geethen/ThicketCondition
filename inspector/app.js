/* Thicket Condition Inspector — static, no backend.
   Labels persist in localStorage and export/import as JSON or CSV. */
'use strict';

const CLASSES = ['intact','moderate','severe','notthicket','unsure'];
const CLASS_LABEL = {intact:'Intact',moderate:'Moderate',severe:'Severe',
                     notthicket:'Not thicket',unsure:'Unsure'};
const STRAT_COLOR = {intact:'#0a7d34',moderate:'#e0a400',severe:'#c0392b'};
const KEY_LABELS = 'thicket-inspector-labels-v1';
const KEY_NAME   = 'thicket-inspector-name';
const KEY_UI     = 'thicket-inspector-ui';

// ------------------------------------------------------------------ state
let labels = {};            // id -> {label, note, labeler, ts}
let curIdx = -1;            // index into POINTS
let labeler = '';
let activeSource = 'esri';
let map, waybackReleases = [], waybackIdx = 0, waybackLayerAdded = false;

// ------------------------------------------------------------------ helpers
const $ = s => document.querySelector(s);
const byId = id => POINTS.findIndex(p => p.id === id);
function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t=setTimeout(()=>t.classList.remove('show'),1800); }

function loadStore(){
  try{ labels = JSON.parse(localStorage.getItem(KEY_LABELS)||'{}'); }catch(e){ labels={}; }
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
  wayback: { name:'Esri Wayback', dynamic:true, attribution:'Esri Wayback', max:19 }
};

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
// Public Wayback config lists every imagery release with an itemURL template.
async function fetchWayback(){
  try{
    const r = await fetch('https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json');
    const cfg = await r.json();
    waybackReleases = Object.keys(cfg).map(k=>({
      num:k, date:cfg[k].itemTitle, url:cfg[k].itemURL
    })).sort((a,b)=> (a.date<b.date?1:-1));   // newest first
    const s = $('#wbSlider'); s.max = Math.max(0,waybackReleases.length-1);
  }catch(e){ /* wayback optional */ waybackReleases=[]; }
}
function applyWayback(){
  if(!waybackReleases.length){ toast('Wayback unavailable'); return; }
  const rel = waybackReleases[waybackIdx];
  const tiles = [ rel.url.replace('{level}','{z}').replace('{row}','{y}').replace('{col}','{x}') ];
  swapBase(rasterSourceDef(SOURCES.wayback, tiles));
  $('#wbYear').textContent = rel.date;
}
function swapBase(def){
  if(map.getLayer('base-layer')) map.removeLayer('base-layer');
  if(map.getSource('base')) map.removeSource('base');
  map.addSource('base', def);
  map.addLayer({id:'base-layer', type:'raster', source:'base'}, 'pts-layer');
}

// ------------------------------------------------------------------ source UI
function buildSourceButtons(){
  const g = $('#srcgrid'); g.innerHTML='';
  const order = [['esri','Esri World Imagery','highest-res, single date'],
                 ['wayback','Esri Wayback','multi-date time slider'],
                 ['google','Google Satellite','alternate high-res'],
                 ['s2','Sentinel-2 2023','10 m, whole-canopy view']];
  order.forEach(([k,name,desc])=>{
    const b=document.createElement('button'); b.className='srcbtn'+(k===activeSource?' active':'');
    b.dataset.src=k; b.innerHTML=`${name}<small>${desc}</small>`;
    b.onclick=()=>setSource(k); g.appendChild(b);
  });
}
function setSource(k){
  activeSource=k;
  document.querySelectorAll('.srcbtn').forEach(b=>b.classList.toggle('active',b.dataset.src===k));
  $('#waybackrow').classList.toggle('hidden', k!=='wayback');
  if(k==='wayback'){ applyWayback(); }
  else { swapBase(rasterSourceDef(SOURCES[k])); }
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
  $('#mLabel').innerHTML = rec ? `<b style="color:${STRAT_COLOR[rec.label]||'#fff'}">${CLASS_LABEL[rec.label]||rec.label}</b>` : '–';
  $('#note').value = rec ? (rec.note||'') : '';
  document.querySelectorAll('.lblbtn').forEach(b=>{
    b.className='lblbtn'; if(rec && rec.label===b.dataset.lbl) b.classList.add('sel-'+rec.label);
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
  Object.values(labels).forEach(r=>{ c.all++; if(c[r.label]!=null) c[r.label]++; });
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
  const payload={ tool:'thicket_inspector', version:1, labeler,
                  exported:new Date().toISOString(), n:rows.length, labels:rows };
  const stamp=new Date().toISOString().slice(0,19).replace(/[:T]/g,'-');
  const safe=(labeler||'anon').replace(/[^A-Za-z0-9_-]/g,'');
  blobDownload(JSON.stringify(payload,null,2),
    `thicket_labels_${safe}_${stamp}.json`,'application/json');
  // also CSV
  const hdr='id,stratum,lon,lat,label,note,labeler,ts';
  const csv=[hdr].concat(rows.map(r=>[r.id,r.stratum,r.lon,r.lat,r.label,
    '"'+(r.note||'').replace(/"/g,'""')+'"',r.labeler,r.ts].join(','))).join('\n');
  blobDownload(csv, `thicket_labels_${safe}_${stamp}.csv`,'text/csv');
  toast(`Downloaded ${rows.length} labels (JSON + CSV)`);
}
function blobDownload(text,name,type){
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([text],{type}));
  a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),2000);
}
function handleUpload(file){
  const fr=new FileReader();
  fr.onload=()=>{
    try{
      let rows;
      if(file.name.toLowerCase().endsWith('.csv')) rows=parseCSV(fr.result);
      else rows=(JSON.parse(fr.result).labels)||[];
      let merged=0;
      rows.forEach(r=>{
        const id=Number(r.id); if(byId(id)<0) return;
        const p=POINTS[byId(id)];
        labels[id]={label:r.label, note:r.note||'', labeler:r.labeler||labeler,
                    ts:r.ts||new Date().toISOString(), stratum:p.s, lon:p.lon, lat:p.lat};
        merged++;
      });
      saveStore(); refreshPoints(); updateCounts();
      if(curIdx>=0) renderPoint();
      toast(`Loaded ${merged} labels — resuming`);
    }catch(e){ toast('Could not read that file'); }
  };
  fr.readAsText(file);
}
function parseCSV(txt){
  const lines=txt.split(/\r?\n/).filter(l=>l.trim());
  const hdr=lines.shift().split(',');
  const ix=n=>hdr.indexOf(n);
  return lines.map(line=>{
    // minimal CSV: split respecting quotes, with "" -> " un-escaping
    const cells=[]; let cur='',q=false;
    for(let i=0;i<line.length;i++){
      const ch=line[i];
      if(ch==='"'){
        if(q && line[i+1]==='"'){ cur+='"'; i++; }   // escaped quote
        else { q=!q; }
      } else if(ch===',' && !q){ cells.push(cur); cur=''; }
      else cur+=ch;
    } cells.push(cur);
    return {id:cells[ix('id')], stratum:cells[ix('stratum')], label:cells[ix('label')],
            note:cells[ix('note')]||'', labeler:cells[ix('labeler')]||'', ts:cells[ix('ts')]||''};
  });
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
  $('#wbSlider').oninput=e=>{ waybackIdx=+e.target.value; applyWayback(); };

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
  // restore hash target after map ready
  const m=location.hash.match(/p=(\d+)/);
  if(m){ const tid=+m[1]; map && map.on('load',()=>gotoId(tid)); }
}
boot();
