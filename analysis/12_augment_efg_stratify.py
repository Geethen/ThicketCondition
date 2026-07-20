#!/usr/bin/env python
"""AUGMENT the existing condition-only sample into a 9-stratum EFG x severity
design, keeping every one of the existing 846 points and drawing only the extra
points needed to hit the new per-stratum targets.

Motivation
----------
The original design (10_sample_design.py) stratified ONLY by condition severity
(intact / moderate / severe) over the solid-thicket AOI: 232 / 275 / 339 = 846
points. Design-based area/accuracy estimates are more useful per Ecological
Functional Group (EFG), so we now ALSO stratify by EFG:

    stratum9 = efg_id * 10 + cls        efg_id in {1,2,3}  cls in {0,1,2}
    efg_id: 1=Arid  2=Valley  3=Mesic   cls: 0=intact 1=moderate 2=severe

giving 9 mutually-exclusive strata that partition the same mapped area. Both the
severity classes (argmax of the 3 RF probability bands) and the EFG raster are
reproduced EXACTLY as steph.js / 10_sample_design.py build them (same masks,
same per-EFG RF models, same EXPORT_SCALE), so the new strata nest cleanly inside
the old ones -- a severity stratum is just the union of its three EFG cells.

Because a random subset within a stratum of a stratified random sample is itself
a valid stratified random sample, and a union of independent stratified draws
across disjoint strata is valid too, we can KEEP the existing 846 points and only
TOP UP each 9-cell to its target. The existing points are assigned to their EFG
by sampling the efg raster at each point.

Allocation (Olofsson et al. 2014, over the 9 strata)
----------------------------------------------------
  - total n from Eq.13 for a target SE of overall accuracy,
  - Neyman optimal allocation n_i propto W_i * S_i across the 9 strata,
    S_i = sqrt(U_i (1-U_i)),  with a rare-class FLOOR + per-class CI half-width.
  - U_i are HONEST per-(EFG,argmax-class) user's accuracies (precision of the
    argmax-mapped class within that EFG) measured on the spatially cross-validated
    OOF points in analysis/data/oof_3band.json.
  - W_i are per-stratum area weights from the 9-value stratum map (Earth Engine).

Top-up = max(0, target_i - existing_i). The extra points are drawn with
stratifiedSample on the 9-value band (per-stratum classPoints = top-up counts),
then merged with the existing 846.

Run:
  python -u analysis/12_augment_efg_stratify.py \
      --target-se 0.015 --halfwidth 0.075 --floor 50 --seed 42

Outputs (kept separate from the originals so nothing is clobbered):
  analysis/results/sample_design_efg.json       9-stratum design + top-up plan
  analysis/results/sample_points_efg.geojson    augmented points (existing + new)
  analysis/results/sample_points_efg.csv        same, lon/lat/stratum table
"""
import argparse, json, math, os, time
from collections import defaultdict
import ee

HERE = os.path.dirname(os.path.abspath(__file__))
def data_path(n): return os.path.join(HERE, 'data', n)
def res_path(n):  return os.path.join(HERE, 'results', n)

# ----------------------------------------------------------------- constants
YEAR = 2022
SCALE = 10           # embedding / model scale (as in steph.js + script 07)
EXPORT_SCALE = 30    # the export scale of the probability raster
# explicit metric CRS for area reductions (Finding 6); see script 10.
AREA_CRS = 'EPSG:32735'   # WGS84 / UTM zone 35S (metres)
CLASS_NAME = {0: 'intact', 1: 'moderate', 2: 'severe'}
CLASS_ID = {v: k for k, v in CLASS_NAME.items()}
EFG_NAME = {1: 'AridThicket', 2: 'ValleyThicket', 3: 'MesicThicket'}


def stratum9(efg_id, cls):
    return efg_id * 10 + cls

def stratum9_label(s):
    efg, cls = s // 10, s % 10
    return f'{EFG_NAME[efg]}_{CLASS_NAME[cls]}'


# ---------------------------------- per-stratum U_i from the OOF confusion ----
def per_stratum_U():
    """Honest user's accuracy U_i for each (efg_id, argmax-class) stratum:
    precision of the argmax-mapped class WITHIN that EFG, on the spatially
    cross-validated OOF points. Returns {stratum9: U}."""
    oof = json.load(open(data_path('oof_3band.json')))
    tp = defaultdict(int); n = defaultdict(int)
    for r in oof['rows']:
        efg = int(r['efg_id'])
        p = [r['p_intact'], r['p_moderate'], r['p_severe']]
        pred = p.index(max(p))
        s = stratum9(efg, pred)
        n[s] += 1
        if pred == int(r['ClassId']):
            tp[s] += 1
    U = {}
    for efg in (1, 2, 3):
        for cls in (0, 1, 2):
            s = stratum9(efg, cls)
            U[s] = (tp[s] / n[s]) if n[s] else float('nan')
    return U, {s: n[s] for s in U}


# ----------------------------------------------------- EE map (as steph.js) ---
def build_stratum_map():
    """Reproduce steph.js and return (stratum_image, cls_image, efg_image, aoi).

    stratum_image: single band 'strat9' = efg_id*10 + cls over natural
    solid-thicket pixels only (values 10/11/12/20/21/22/30/31/32).
    """
    geometry = ee.Geometry.Polygon(
        [[[20.651320170862977, -31.977939185448044],
          [20.651320170862977, -34.55873881519996],
          [29.286574077112977, -34.55873881519996],
          [29.286574077112977, -31.977939185448044]]], None, False)
    aoi = geometry.bounds(1)

    EFG = ee.FeatureCollection('projects/thicket-ecological-condition/assets/ThicketEFGs')
    solidEFG = EFG.filter(ee.Filter.inList('RevisedFVG',
                          ['Arid Thicket', 'Valley Thicket', 'Mesic Thicket']))

    def _add_efg_id(f):
        name = ee.String(f.get('RevisedFVG'))
        idv = ee.Number(ee.Algorithms.If(name.equals('Arid Thicket'), 1,
                        ee.Algorithms.If(name.equals('Valley Thicket'), 2, 3)))
        return f.set('efg_id', idv)

    solidEFG_id = solidEFG.map(_add_efg_id)
    efgRaster = (solidEFG_id.reduceToImage(properties=['efg_id'], reducer=ee.Reducer.first())
                 .rename('efg_id').toByte())
    allThicketMask = efgRaster.gt(0)
    mask_efg = {1: efgRaster.eq(1), 2: efgRaster.eq(2), 3: efgRaster.eq(3)}

    nlcBand = ee.Image('projects/thicket-ecological-condition/assets/SA_NLC_2022_GEO').select(0).clip(aoi)
    water = nlcBand.remap([14, 15, 16, 17, 18, 19, 20, 21], [1, 1, 1, 1, 1, 1, 1, 1], 0)
    notWaterMask = water.Not()
    lcBand = ee.Image('projects/thicket-ecological-condition/assets/nlc2022_7class').select(0).clip(aoi)
    naturalMask = lcBand.eq(1).Or(lcBand.eq(2))
    validSolidMask = allThicketMask.updateMask(notWaterMask)

    emb = (ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
           .filterDate(f'{YEAR}-01-01', f'{YEAR+1}-01-01')
           .mosaic().toFloat().clip(aoi).updateMask(notWaterMask))
    predictors = emb.bandNames()

    validClasses = ['intact', 'moderate', 'severe']
    classToId = ee.Dictionary({'intact': 0, 'moderate': 1, 'severe': 2})
    trainFC = ee.FeatureCollection('projects/thicket-ecological-condition/assets/training_collated_withFLC').filterBounds(aoi)

    def _clean(f):
        return ee.Feature(f.geometry(), {'Class': ee.String(f.get('Class')).toLowerCase()})
    train_clean = trainFC.map(_clean).filter(ee.Filter.inList('Class', validClasses))

    def _setid(f):
        return f.set('ClassId', ee.Number(classToId.get(ee.String(f.get('Class')))))
    train_id = train_clean.map(_setid)
    train_solid = (efgRaster.sampleRegions(collection=train_id, properties=['Class', 'ClassId'],
                                            scale=SCALE, geometries=True, tileScale=2)
                   .filter(ee.Filter.notNull(['efg_id'])))

    def make_rf():
        return (ee.Classifier.smileRandomForest(numberOfTrees=300, seed=123,
                                                 minLeafPopulation=1, bagFraction=0.632)
                .setOutputMode('MULTIPROBABILITY'))

    samples = emb.sampleRegions(collection=train_solid, properties=['ClassId', 'efg_id'],
                                scale=SCALE, geometries=False, tileScale=4)

    # per-EFG models -> argmax class, mosaicked (exactly the export's masking)
    cls_surfaces = []
    for efg in (1, 2, 3):
        tr = samples.filter(ee.Filter.eq('efg_id', efg))
        model = make_rf().train(features=tr, classProperty='ClassId', inputProperties=predictors)
        naturalPredictionMask = mask_efg[efg].updateMask(naturalMask).updateMask(notWaterMask)
        probArray = emb.updateMask(naturalPredictionMask).classify(model).select(0)
        cls = probArray.arrayArgmax().arrayGet([0]).rename('cls').toByte()
        cls_surfaces.append(cls)
    cls_image = ee.ImageCollection(cls_surfaces).mosaic().updateMask(validSolidMask).rename('cls').toByte()

    efg_image = efgRaster.updateMask(validSolidMask).rename('efg_id').toByte()
    # combined 9-value stratum band; masked to where BOTH cls and efg are valid
    strat = efg_image.multiply(10).add(cls_image).rename('strat9').toInt16()
    strat = strat.updateMask(cls_image.mask()).updateMask(efg_image.mask())
    return strat, cls_image, efg_image, aoi


# ---------------------------------------------- per-stratum area from the map --
def stratum_areas(strat_image, aoi):
    """Area (m2) per 9-value stratum via a grouped area histogram.

    Finding 6 fix (see script 10 class_areas): reduction pinned to an EXPLICIT
    fixed projection (crs+scale), bestEffort OFF, so the grid is deterministic and
    `used_scale_m` is the scale actually used. Coarsening (if a level OOMs) uses
    reduceResolution(mode) onto an explicit coarser grid rather than bestEffort."""
    groups = None; used_scale = None
    for area_scale in (EXPORT_SCALE, 60, 100, 200):
        try:
            print(f'  reducing stratum areas at FIXED scale={area_scale} m ({AREA_CRS}) ...', flush=True)
            proj = ee.Projection(AREA_CRS).atScale(area_scale)
            fine = ee.Projection(AREA_CRS).atScale(EXPORT_SCALE)
            if area_scale > EXPORT_SCALE:
                strat_fixed = (strat_image.setDefaultProjection(fine)
                               .reduceResolution(reducer=ee.Reducer.mode(), maxPixels=1024)
                               .reproject(proj))
            else:
                strat_fixed = strat_image.reproject(proj)
            px_area = ee.Image.pixelArea().reproject(proj).rename('area')
            # group band (strat9) must come AFTER the summed pixelArea band.
            stack = px_area.addBands(strat_fixed.rename('strat9'))
            grouped = stack.reduceRegion(
                reducer=ee.Reducer.sum().group(groupField=1, groupName='strat9'),
                geometry=aoi, crs=AREA_CRS, scale=area_scale,
                maxPixels=int(1e13), bestEffort=False, tileScale=4)
            groups = grouped.getInfo().get('groups', [])
            used_scale = area_scale
            break
        except Exception as e:
            print(f'    scale={area_scale} failed ({str(e).splitlines()[0][:60]}); escalating', flush=True)
    if groups is None:
        raise RuntimeError('stratum-area reduction failed at all scales')
    area_m2 = {}
    for g in groups:
        s = int(g['strat9'])
        if s // 10 in EFG_NAME and s % 10 in CLASS_NAME:
            area_m2[s] = float(g['sum'])
    return area_m2, used_scale


# ------------------------------------------------------------- the design ------
def design(area_m2, U, target_se, halfwidth, floor):
    strata = [stratum9(e, c) for e in (1, 2, 3) for c in (0, 1, 2)]
    strata = [s for s in strata if area_m2.get(s, 0) > 0]
    A = {s: area_m2[s] for s in strata}
    Atot = sum(A.values())
    W = {s: A[s] / Atot for s in strata}
    S = {s: math.sqrt(U[s] * (1 - U[s])) for s in strata}

    sumWS = sum(W[s] * S[s] for s in strata)
    n_tot = math.ceil((sumWS / target_se) ** 2)

    ney = {s: n_tot * W[s] * S[s] / sumWS for s in strata}

    z = 1.96
    def ci_n(u):
        return math.ceil((z / halfwidth) ** 2 * u * (1 - u))
    alloc = {s: int(math.ceil(max(ney[s], floor, ci_n(U[s])))) for s in strata}

    return dict(strata=strata, area_m2=A, area_ha={s: A[s] / 1e4 for s in strata},
                area_total_ha=Atot / 1e4, W=W, U={s: U[s] for s in strata}, S=S,
                sumWS=sumWS, n_tot=n_tot,
                neyman={s: int(math.ceil(ney[s])) for s in strata},
                alloc=alloc, n_alloc_total=sum(alloc.values()),
                ci_n={s: ci_n(U[s]) for s in strata},
                target_se=target_se, halfwidth=halfwidth, floor=floor)


def fmt(r, existing, topup):
    L = [f"total mapped area = {r['area_total_ha']:,.0f} ha   Sum W*S = {r['sumWS']:.4f}",
         f"target SE(overall acc) = {r['target_se']}  ->  total n (Olofsson Eq.13) = {r['n_tot']}",
         f"per-class CI half-width = +/-{r['halfwidth']}   rare-class floor = {r['floor']}",
         "",
         f"  {'stratum':<22} {'area_ha':>11} {'W_i':>7} {'U_i':>6} {'S_i':>6} "
         f"{'Neyman':>7} {'CI_n':>6} {'TARGET':>7} {'have':>5} {'ADD':>5}"]
    for s in r['strata']:
        L.append(f"  {stratum9_label(s):<22} {r['area_ha'][s]:>11,.0f} {r['W'][s]:>7.4f} "
                 f"{r['U'][s]:>6.3f} {r['S'][s]:>6.3f} {r['neyman'][s]:>7} {r['ci_n'][s]:>6} "
                 f"{r['alloc'][s]:>7} {existing.get(s,0):>5} {topup.get(s,0):>5}")
    L.append(f"  {'TOTAL':<22} {r['area_total_ha']:>11,.0f} {'':>7} {'':>6} {'':>6} "
             f"{sum(r['neyman'].values()):>7} {'':>6} {r['n_alloc_total']:>7} "
             f"{sum(existing.values()):>5} {sum(topup.values()):>5}")
    return "\n".join(L)


# ----------------------------------- assign existing points to their EFG cell --
def load_existing_points():
    gj = json.load(open(res_path('sample_points.geojson')))
    pts = []
    for f in gj['features']:
        p = f['properties']
        pts.append({'id': int(p['id']), 'stratum': p['stratum'], 'cls': int(p['cls']),
                    'lon': float(p['lon']), 'lat': float(p['lat'])})
    return pts


def tag_existing_with_efg(pts, efg_image):
    """Sample the efg raster at each existing point to get its efg_id, so each of
    the 846 points lands in one of the 9 strata. Uses computeFeatures (paged) to
    stay clear of the interactive getInfo timeout."""
    feats = [ee.Feature(ee.Geometry.Point([p['lon'], p['lat']]), {'idx': i})
             for i, p in enumerate(pts)]
    fc = efg_image.rename('efg_id').sampleRegions(
        collection=ee.FeatureCollection(feats), properties=['idx'],
        scale=EXPORT_SCALE, geometries=False, tileScale=4)
    try:
        gdf = ee.data.computeFeatures({'expression': fc,
                                       'fileFormat': 'GEOPANDAS_GEODATAFRAME'})
        recs = [{'idx': int(row['idx']), 'efg_id': row['efg_id']} for _, row in gdf.iterrows()]
    except Exception:
        info = fc.getInfo()
        recs = [{'idx': int(f['properties']['idx']),
                 'efg_id': f['properties'].get('efg_id')} for f in info['features']]
    by_idx = {r['idx']: r['efg_id'] for r in recs}
    tagged = []
    for i, p in enumerate(pts):
        efg = by_idx.get(i)
        if efg is None:
            p = {**p, 'efg_id': None, 'strat9': None}   # point fell off the efg raster
        else:
            efg = int(efg)
            p = {**p, 'efg_id': efg, 'strat9': stratum9(efg, p['cls'])}
        tagged.append(p)
    return tagged


# ------------------------------------------------ draw the top-up sample -------
def _topup_fc(strat_image, aoi, topup, seed, tile_scale):
    strata = sorted(s for s, k in topup.items() if k > 0)
    classValues = list(strata)
    classPoints = [topup[s] for s in strata]
    fc = strat_image.rename('strat9').stratifiedSample(
        numPoints=0, classBand='strat9', region=aoi, scale=EXPORT_SCALE,
        classValues=classValues, classPoints=classPoints, seed=seed,
        geometries=True, tileScale=tile_scale)

    def _tag(f):
        s = ee.Number(f.get('strat9'))
        coords = f.geometry().coordinates()
        return f.set({'strat9': s, 'lon': coords.get(0), 'lat': coords.get(1)})
    return fc.map(_tag)


def draw_topup(strat_image, aoi, topup, seed):
    """Draw only the top-up points via computeFeatures (paged). Returns list of
    dicts. Falls back to a batch Drive export if the compute times out."""
    fc = _topup_fc(strat_image, aoi, topup, seed, tile_scale=16)
    try:
        gdf = ee.data.computeFeatures({'expression': fc,
                                       'fileFormat': 'GEOPANDAS_GEODATAFRAME'})
    except TypeError:
        gdf = ee.data.computeFeatures(expression=fc, fileFormat='GEOPANDAS_GEODATAFRAME')
    out = []
    for _, row in gdf.iterrows():
        s = int(row['strat9'])
        out.append({'strat9': s, 'efg_id': s // 10, 'cls': s % 10,
                    'stratum': CLASS_NAME[s % 10],
                    'lon': float(row['lon']), 'lat': float(row['lat'])})
    return out


def start_topup_export(strat_image, aoi, topup, seed, desc):
    """Fallback: batch Export.table.toDrive of the top-up stratified sample."""
    fc = _topup_fc(strat_image, aoi, topup, seed, tile_scale=16)
    task = ee.batch.Export.table.toDrive(
        collection=fc.map(lambda f: f.setGeometry(None)),  # props carry lon/lat
        description=desc, fileFormat='GeoJSON')
    task.start()
    return task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='ee-gsingh')
    ap.add_argument('--target-se', type=float, default=0.015)
    ap.add_argument('--halfwidth', type=float, default=0.075)
    ap.add_argument('--floor', type=int, default=50)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--recompute-areas', action='store_true')
    ap.add_argument('--design-only', action='store_true',
                    help='compute design + top-up plan, skip drawing the new points')
    ap.add_argument('--export-fallback', action='store_true',
                    help='use a batch Drive export for the top-up instead of computeFeatures')
    args = ap.parse_args()

    t0 = time.time()
    ee.Initialize(project=args.project)
    os.makedirs(os.path.join(HERE, 'results'), exist_ok=True)

    print('== per-stratum U_i from OOF (precision of argmax within each EFG) ==', flush=True)
    U, U_n = per_stratum_U()
    for s in sorted(U):
        print(f'  {stratum9_label(s):<22} U={U[s]:.3f}  (oof n_pred={U_n[s]})', flush=True)

    print('== build 9-value stratum map (efg_id*10 + cls, as steph.js) ==', flush=True)
    strat_image, cls_image, efg_image, aoi = build_stratum_map()

    areas_cache = res_path('stratum_areas_efg.json')
    area_m2 = None
    if not args.recompute_areas and os.path.exists(areas_cache):
        try:
            area_m2 = {int(k): v for k, v in json.load(open(areas_cache))['area_m2'].items()}
            print('  reusing cached stratum areas (results/stratum_areas_efg.json)', flush=True)
        except Exception:
            area_m2 = None
    if area_m2 is None:
        print('== per-stratum area weights from the map (Earth Engine) ==', flush=True)
        area_m2, used_scale = stratum_areas(strat_image, aoi)
        print('  areas (ha): ' + ', '.join(f'{stratum9_label(s)}={v/1e4:,.0f}'
              for s, v in sorted(area_m2.items())), flush=True)
        json.dump({'area_m2': {str(k): v for k, v in area_m2.items()},
                   'used_scale_m': used_scale}, open(areas_cache, 'w'), indent=2)

    print('== design (Olofsson Eq.13 + Neyman + rare-class floor, 9 strata) ==', flush=True)
    r = design(area_m2, U, args.target_se, args.halfwidth, args.floor)

    print('== assign the existing 846 points to their EFG cell ==', flush=True)
    existing = load_existing_points()
    existing = tag_existing_with_efg(existing, efg_image)
    have = defaultdict(int)
    for p in existing:
        if p['strat9'] is not None:
            have[p['strat9']] += 1
    n_offmap = sum(1 for p in existing if p['strat9'] is None)
    if n_offmap:
        print(f'  NOTE: {n_offmap} existing points fell off the efg raster (efg_id null)', flush=True)

    topup = {s: max(0, r['alloc'][s] - have.get(s, 0)) for s in r['strata']}

    print(fmt(r, dict(have), topup), flush=True)

    # ---- write design + plan summary (fully solved already) ----
    def keymap(d): return {stratum9_label(s): v for s, v in d.items()}
    summary = dict(
        method='Olofsson et al. 2014; strata = EFG x severity (9 cells); augments the '
               'condition-only 846-pt sample by topping up each cell',
        year=YEAR, export_scale_m=EXPORT_SCALE, seed=args.seed,
        target_se=args.target_se, halfwidth=args.halfwidth, floor=args.floor,
        strata=[stratum9_label(s) for s in r['strata']],
        U_expected=keymap(r['U']), oof_n=keymap({s: U_n[s] for s in r['strata']}),
        area_ha=keymap(r['area_ha']), area_total_ha=r['area_total_ha'],
        W=keymap(r['W']), S=keymap(r['S']), sumWS=r['sumWS'], n_tot=r['n_tot'],
        neyman=keymap(r['neyman']), ci_n=keymap(r['ci_n']), alloc=keymap(r['alloc']),
        n_alloc_total=r['n_alloc_total'],
        existing_counts=keymap(dict(have)), existing_offmap=n_offmap,
        topup_counts=keymap(topup), n_topup_total=sum(topup.values()),
    )
    cache = res_path('sample_design_efg.json')
    json.dump(summary, open(cache, 'w'), indent=2)
    print('  wrote results/sample_design_efg.json', flush=True)

    if args.design_only:
        print(f'\n[design-only] top-up total = {sum(topup.values())} new points needed.', flush=True)
        print(f'DONE in {round(time.time()-t0,1)}s', flush=True)
        return

    if sum(topup.values()) == 0:
        print('  no top-up needed; existing sample already meets all targets.', flush=True)
        new_pts = []
    elif args.export_fallback:
        desc = f'efg_topup_seed{args.seed}'
        start_topup_export(strat_image, aoi, topup, args.seed, desc)
        # persist the EFG-tagged existing points so 13_merge_topup can reuse them
        # without re-sampling the efg raster (which is the slow part).
        json.dump({'existing': existing}, open(res_path('existing_tagged_efg.json'), 'w'), indent=2)
        print(f'\n[export] started batch Drive task "{desc}" for {sum(topup.values())} '
              f'top-up points. Download it, then merge with 13_merge_topup.py.', flush=True)
        print('  wrote results/existing_tagged_efg.json (846 points tagged with EFG)', flush=True)
        print(f'DONE in {round(time.time()-t0,1)}s', flush=True)
        return
    else:
        print(f'\n== drawing {sum(topup.values())} top-up points (computeFeatures) ==', flush=True)
        new_pts = draw_topup(strat_image, aoi, topup, args.seed)
        from collections import Counter
        print('  drew: ' + ', '.join(f'{stratum9_label(s)}={c}'
              for s, c in sorted(Counter(p['strat9'] for p in new_pts).items())), flush=True)

    # ---- merge existing + new, write augmented outputs ----
    write_augmented(existing, new_pts, summary, cache)
    print(f'\n[OK] wrote sample_design_efg.json, sample_points_efg.geojson, .csv', flush=True)
    print(f'DONE in {round(time.time()-t0,1)}s', flush=True)


def write_augmented(existing, new_pts, summary, cache):
    feats = []
    nid = 0
    for p in existing:
        feats.append({'type': 'Feature',
                      'properties': {'id': nid, 'source': 'existing', 'stratum': p['stratum'],
                                     'cls': p['cls'], 'efg_id': p['efg_id'],
                                     'efg': EFG_NAME.get(p['efg_id']),
                                     'strat9': p['strat9'],
                                     'strat9_label': stratum9_label(p['strat9']) if p['strat9'] else None,
                                     'lon': p['lon'], 'lat': p['lat']},
                      'geometry': {'type': 'Point', 'coordinates': [p['lon'], p['lat']]}})
        nid += 1
    for p in new_pts:
        feats.append({'type': 'Feature',
                      'properties': {'id': nid, 'source': 'new', 'stratum': p['stratum'],
                                     'cls': p['cls'], 'efg_id': p['efg_id'],
                                     'efg': EFG_NAME.get(p['efg_id']),
                                     'strat9': p['strat9'],
                                     'strat9_label': stratum9_label(p['strat9']),
                                     'lon': p['lon'], 'lat': p['lat']},
                      'geometry': {'type': 'Point', 'coordinates': [p['lon'], p['lat']]}})
        nid += 1
    fc = {'type': 'FeatureCollection',
          'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
          'features': feats}
    json.dump(fc, open(res_path('sample_points_efg.geojson'), 'w'))
    import csv
    with open(res_path('sample_points_efg.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'source', 'stratum', 'cls', 'efg_id', 'efg', 'strat9', 'strat9_label', 'lon', 'lat'])
        for ft in feats:
            p = ft['properties']
            w.writerow([p['id'], p['source'], p['stratum'], p['cls'], p['efg_id'],
                        p['efg'], p['strat9'], p['strat9_label'], p['lon'], p['lat']])
    summary['n_existing'] = len(existing)
    summary['n_new'] = len(new_pts)
    summary['n_total'] = len(feats)
    json.dump(summary, open(cache, 'w'), indent=2)


if __name__ == '__main__':
    main()
