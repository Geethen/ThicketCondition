#!/usr/bin/env python
"""Design-based stratified sample for accuracy + area estimation of the three
THICKET CONDITION classes (intact / moderate / severe) over the solid-thicket AOI.

Strata = the three condition classes themselves, defined exactly as the export
job (steph.js) produces them: for every natural solid-thicket pixel the class is
the argmax of the three RF probability bands (p_intact, p_moderate, p_severe),
which sum to 1. This is an exhaustive, mutually-exclusive partition of the mapped
area -- the correct basis for design-based stratified estimation. Transformed
pixels (all bands == -1 in the export) and non-thicket / water pixels are not part
of any condition class and are excluded.

Allocation follows Olofsson et al. 2014 "Good practices for estimating area and
assessing accuracy of land change":
  - total n from Eq.13 for a target SE of overall accuracy, and
  - Neyman optimal allocation  n_i proportional to  W_i * S_i  across strata,
    with a rare-class FLOOR so every class clears a usable per-class CI.
  S_i = sqrt(U_i (1-U_i)) from an expected user's accuracy U_i.

U_i are the honest, out-of-fold user's accuracies (precision of the argmax-mapped
class) measured on the 2,083 spatially cross-validated OOF points:
  intact 0.758, moderate 0.614, severe 0.791  (from analysis/data/oof_3band.json)

Area weights W_i are the per-class pixel counts of the argmax map, pulled from
Earth Engine using the identical masks/models as steph.js (30 m export scale).

Run:
  python -u analysis/10_sample_design.py \
      --target-se 0.01 --halfwidth 0.075 --floor 50 --seed 42

Outputs:
  analysis/results/sample_design.json     design summary (W, S, n_tot, allocation)
  analysis/results/sample_points.geojson  the drawn stratified random points
  analysis/results/sample_points.csv       same, as lon/lat/stratum table
"""
import argparse, json, math, os, time
import numpy as np
import ee

HERE = os.path.dirname(os.path.abspath(__file__))
def data_path(n): return os.path.join(HERE, 'data', n)
def res_path(n):  return os.path.join(HERE, 'results', n)

# ----------------------------------------------------------------- constants
YEAR = 2022
SCALE = 10           # embedding / model scale (as in steph.js + script 07)
EXPORT_SCALE = 30    # the export scale of the probability raster
CLASS_NAME = {0: 'intact', 1: 'moderate', 2: 'severe'}
CLASS_ID = {v: k for k, v in CLASS_NAME.items()}

# expected user's accuracy per condition class, from the argmax OOF confusion
# (analysis/data/oof_3band.json; spatially cross-validated, so honest).
U_OOF = {'intact': 0.758, 'moderate': 0.614, 'severe': 0.791}


# ----------------------------------------------------- EE map (as steph.js) ---
def build_class_map():
    """Reproduce the steph.js condition map and return (class_image, aoi).

    class_image: single band 'cls' with values 0/1/2 = intact/moderate/severe,
    = argmax(p_intact,p_moderate,p_severe) over natural solid-thicket pixels only.
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
        # argmax over the 3-length probability array -> 0/1/2
        cls = probArray.arrayArgmax().arrayGet([0]).rename('cls').toByte()
        cls_surfaces.append(cls)
    class_image = ee.ImageCollection(cls_surfaces).mosaic().updateMask(validSolidMask).rename('cls')
    return class_image, aoi


# ------------------------------------------------ per-class area from the map --
def class_areas(class_image, aoi):
    """Pixel count + area (m2) per condition class via a grouped area histogram,
    same escalating-scale trick as script 07."""
    px_area = ee.Image.pixelArea().rename('area')
    stack = px_area.addBands(class_image)
    groups = None
    for area_scale in (EXPORT_SCALE, 60, 100, 200):
        try:
            print(f'  reducing class areas at scale={area_scale} m ...', flush=True)
            grouped = stack.reduceRegion(
                reducer=ee.Reducer.sum().group(groupField=1, groupName='cls'),
                geometry=aoi, scale=area_scale, maxPixels=int(1e13),
                bestEffort=True, tileScale=4)
            groups = grouped.getInfo().get('groups', [])
            used_scale = area_scale
            break
        except Exception as e:
            print(f'    scale={area_scale} failed ({str(e).splitlines()[0][:60]}); escalating', flush=True)
    if groups is None:
        raise RuntimeError('class-area reduction failed at all scales')
    area_m2 = {name: 0.0 for name in CLASS_NAME.values()}
    for g in groups:
        c = int(g['cls'])
        if c in CLASS_NAME:
            area_m2[CLASS_NAME[c]] = float(g['sum'])
    # pixel counts implied by the area at the resolution used
    pix = {name: area_m2[name] / (used_scale * used_scale) for name in area_m2}
    return area_m2, pix, used_scale


# ------------------------------------------------------------- the design ------
def design(area_m2, target_se, halfwidth, floor):
    classes = ['intact', 'moderate', 'severe']
    A = {c: area_m2[c] for c in classes}
    Atot = sum(A.values())
    W = {c: A[c] / Atot for c in classes}
    U = {c: U_OOF[c] for c in classes}
    S = {c: math.sqrt(U[c] * (1 - U[c])) for c in classes}

    # ---- total n (Olofsson Eq.13, large-N approximation) ----
    sumWS = sum(W[c] * S[c] for c in classes)
    n_tot = math.ceil((sumWS / target_se) ** 2)

    # ---- Neyman allocation  n_i propto W_i S_i ----
    ney = {c: n_tot * W[c] * S[c] / sumWS for c in classes}

    # ---- rare-class floor: per-class CI half-width, never below `floor` ----
    z = 1.96
    def ci_n(u):
        return math.ceil((z / halfwidth) ** 2 * u * (1 - u))
    alloc = {c: max(ney[c], floor, ci_n(U[c])) for c in classes}
    alloc = {c: int(math.ceil(v)) for c, v in alloc.items()}

    return dict(classes=classes, area_m2=A, area_ha={c: A[c] / 1e4 for c in classes},
                area_total_ha=Atot / 1e4, W=W, U=U, S=S, sumWS=sumWS,
                n_tot=n_tot, neyman={c: int(math.ceil(ney[c])) for c in classes},
                alloc=alloc, n_alloc_total=sum(alloc.values()),
                target_se=target_se, halfwidth=halfwidth, floor=floor,
                ci_n={c: ci_n(U[c]) for c in classes})


def fmt(r):
    L = [f"total mapped area = {r['area_total_ha']:,.0f} ha   Sum W*S = {r['sumWS']:.4f}",
         f"target SE(overall acc) = {r['target_se']}  ->  total n (Olofsson Eq.13) = {r['n_tot']}",
         f"per-class CI half-width = +/-{r['halfwidth']}   rare-class floor = {r['floor']}",
         "",
         f"  {'class':<10} {'area_ha':>12} {'W_i':>8} {'U_i':>6} {'S_i':>6} "
         f"{'Neyman':>7} {'CI_n':>6} {'ALLOC':>7}"]
    for c in r['classes']:
        L.append(f"  {c:<10} {r['area_ha'][c]:>12,.0f} {r['W'][c]:>8.4f} {r['U'][c]:>6.3f} "
                 f"{r['S'][c]:>6.3f} {r['neyman'][c]:>7} {r['ci_n'][c]:>6} {r['alloc'][c]:>7}")
    L.append(f"  {'TOTAL':<10} {r['area_total_ha']:>12,.0f} {'':>8} {'':>6} {'':>6} "
             f"{sum(r['neyman'].values()):>7} {'':>6} {r['n_alloc_total']:>7}")
    return "\n".join(L)


# ------------------------------------------------ draw the stratified sample ---
def _stratified_fc(class_image, aoi, alloc, seed, tile_scale):
    """The stratifiedSample FeatureCollection, tagged with stratum name + lon/lat."""
    classValues = [CLASS_ID[c] for c in ['intact', 'moderate', 'severe']]
    classPoints = [alloc[c] for c in ['intact', 'moderate', 'severe']]
    fc = class_image.rename('cls').stratifiedSample(
        numPoints=0,                      # use per-class classPoints instead
        classBand='cls',
        region=aoi,
        scale=EXPORT_SCALE,
        classValues=classValues,
        classPoints=classPoints,
        seed=seed,
        geometries=True,
        tileScale=tile_scale,
    )
    def _tag(f):
        cid = ee.Number(f.get('cls'))
        name = ee.Dictionary({'0': 'intact', '1': 'moderate', '2': 'severe'}).get(cid.format('%d'))
        coords = f.geometry().coordinates()
        return f.set({'stratum': name, 'lon': coords.get(0), 'lat': coords.get(1)})
    return fc.map(_tag)


def draw_sample_converter(class_image, aoi, alloc, seed):
    """Draw the stratified sample and pull it with the Earth Engine data-converters
    API (ee.data.computeFeatures -> GeoPandas). computeFeatures pages the results
    via listFeatures rather than a single getInfo blob, so it dodges the one-shot
    interactive timeout that stratifiedSample().getInfo() hits over this AOI.
    Returns a GeoDataFrame (or None if the converter is unavailable)."""
    fc = _stratified_fc(class_image, aoi, alloc, seed, tile_scale=16)
    try:
        gdf = ee.data.computeFeatures({
            'expression': fc,
            'fileFormat': 'GEOPANDAS_GEODATAFRAME',
        })
    except TypeError:
        # older ee: computeFeatures takes positional/mixed args
        gdf = ee.data.computeFeatures(expression=fc, fileFormat='GEOPANDAS_GEODATAFRAME')
    try:
        gdf.crs = 'EPSG:4326'
    except Exception:
        pass
    return gdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='ee-gsingh')
    ap.add_argument('--target-se', type=float, default=0.015)
    ap.add_argument('--halfwidth', type=float, default=0.075)
    ap.add_argument('--floor', type=int, default=50)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--recompute-areas', action='store_true',
                    help='force recompute of per-class areas from EE (default: reuse results/class_areas.json)')
    args = ap.parse_args()

    t0 = time.time()
    ee.Initialize(project=args.project)
    os.makedirs(os.path.join(HERE, 'results'), exist_ok=True)

    print('== build condition class map (argmax of 3 RF prob bands, as steph.js) ==', flush=True)
    class_image, aoi = build_class_map()

    cache = res_path('sample_design.json')
    areas_cache = res_path('class_areas.json')
    area_m2 = None
    if not args.recompute_areas and os.path.exists(areas_cache):
        try:
            area_m2 = json.load(open(areas_cache))['area_m2']
            print('  reusing cached class areas (results/class_areas.json)', flush=True)
        except Exception:
            area_m2 = None
    if area_m2 is None:
        print('== per-class area weights from the map (Earth Engine) ==', flush=True)
        area_m2, pix, used_scale = class_areas(class_image, aoi)
        print(f'  areas (ha): ' + ', '.join(f'{k}={v/1e4:,.0f}' for k, v in area_m2.items()), flush=True)
        # cache areas immediately so a re-run (or the draw retry) skips the slow reduce
        json.dump({'area_m2': area_m2}, open(res_path('class_areas.json'), 'w'), indent=2)

    print('== design (Olofsson Eq.13 + Neyman + rare-class floor) ==', flush=True)
    r = design(area_m2, args.target_se, args.halfwidth, args.floor)
    print(fmt(r), flush=True)

    # ---- write design summary first (it's already fully solved) ----
    summary = dict(
        method='Olofsson et al. 2014 stratified design; strata = argmax condition classes',
        year=YEAR, export_scale_m=EXPORT_SCALE, seed=args.seed,
        target_se=args.target_se, halfwidth=args.halfwidth, floor=args.floor,
        U_expected=r['U'], area_m2=r['area_m2'], area_ha=r['area_ha'],
        area_total_ha=r['area_total_ha'], W=r['W'], S=r['S'], sumWS=r['sumWS'],
        n_tot=r['n_tot'], neyman=r['neyman'], ci_n=r['ci_n'], alloc=r['alloc'],
        n_alloc_total=r['n_alloc_total'],
    )
    with open(cache, 'w') as fh:
        json.dump(summary, fh, indent=2)
    print('  wrote results/sample_design.json', flush=True)

    print('\n== drawing stratified random sample (data-converters API) ==', flush=True)
    gdf = draw_sample_converter(class_image, aoi, r['alloc'], args.seed)
    counts = gdf['stratum'].value_counts().to_dict()
    print(f'  drawn {len(gdf)} points: '
          + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())), flush=True)

    summary['n_drawn'] = int(len(gdf))
    summary['drawn_counts'] = {k: int(v) for k, v in counts.items()}
    with open(cache, 'w') as fh:
        json.dump(summary, fh, indent=2)

    # ---- write the sample points (geojson + csv) ----
    gdf.to_file(res_path('sample_points.geojson'), driver='GeoJSON')
    out = gdf.copy()
    out['id'] = range(len(out))
    out[['id', 'stratum', 'cls', 'lon', 'lat']].to_csv(res_path('sample_points.csv'), index=False)

    print(f'\n[OK] wrote results/sample_design.json, sample_points.geojson, sample_points.csv', flush=True)
    print(f'DONE in {round(time.time()-t0,1)}s', flush=True)


if __name__ == '__main__':
    main()
