"""
Threshold sensitivity analysis for the thicket-condition Random Forest model.

Faithful Python port of steph.js:
  - 3 per-EFG (arid/valley/mesic) smileRandomForest MULTIPROBABILITY models
  - P(intact) = probability that a pixel is 'intact' (healthy) thicket

Produces:
  (A) threshold vs accuracy  -> via SPATIAL 5-fold cross-validation (out-of-fold P(intact))
  (B) threshold vs mapped intact area -> from the real EE probability surface (full models)

Outputs JSON in the script directory:
  oof_points.json, threshold_accuracy.json, threshold_area.json, summary.json
"""
import os, json, time
import numpy as np
import ee

HERE = os.path.dirname(os.path.abspath(__file__))
def out_path(name): return os.path.join(HERE, name)

ee.Initialize(project='ee-gsingh')

# ----------------------------------------------------------------------
# Constants (from steph.js)
# ----------------------------------------------------------------------
YEAR = 2022
SCALE = 10
EXPORT_SCALE = 30
AREA_SCALE = 100   # reduction scale for the area curve (30m surface, area-weighted)
K_FOLDS = 5
BLOCK_DEG = 0.2          # spatial block size for CV blocking
ORIGIN_LON, ORIGIN_LAT = 20.0, -35.0

geometry = ee.Geometry.Polygon(
    [[[20.651320170862977, -31.977939185448044],
      [20.651320170862977, -34.55873881519996],
      [29.286574077112977, -34.55873881519996],
      [29.286574077112977, -31.977939185448044]]], None, False)
aoi = geometry.bounds(1)

# ----------------------------------------------------------------------
# EFG masks
# ----------------------------------------------------------------------
EFG = ee.FeatureCollection('projects/thicket-ecological-condition/assets/ThicketEFGs')
solidEFG = EFG.filter(ee.Filter.inList('RevisedFVG', ['Arid Thicket', 'Valley Thicket', 'Mesic Thicket']))

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

# ----------------------------------------------------------------------
# Water + land-cover masks
# ----------------------------------------------------------------------
nlcBand = ee.Image('projects/thicket-ecological-condition/assets/SA_NLC_2022_GEO').select(0).rename('nlc').clip(aoi)
water = nlcBand.remap([14, 15, 16, 17, 18, 19, 20, 21], [1, 1, 1, 1, 1, 1, 1, 1], 0)
notWaterMask = water.Not()

lcBand = ee.Image('projects/thicket-ecological-condition/assets/nlc2022_7class').select(0).rename('LC').clip(aoi)
naturalMask = lcBand.eq(1).Or(lcBand.eq(2))

validSolidMask = allThicketMask.updateMask(notWaterMask)

# ----------------------------------------------------------------------
# Embeddings (predictors)
# ----------------------------------------------------------------------
emb = (ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
       .filterDate(f'{YEAR}-01-01', f'{YEAR+1}-01-01')
       .mosaic().toFloat().clip(aoi).updateMask(notWaterMask))
predictors = emb.bandNames()

# ----------------------------------------------------------------------
# Training points -> intact/moderate/severe, attach efg_id + lon/lat
# ----------------------------------------------------------------------
validClasses = ['intact', 'moderate', 'severe']
classToId = ee.Dictionary({'intact': 0, 'moderate': 1, 'severe': 2})

trainFC = ee.FeatureCollection('projects/thicket-ecological-condition/assets/training_collated_withFLC').filterBounds(aoi)

def _clean(f):
    cls = ee.String(f.get('Class')).toLowerCase()
    return ee.Feature(f.geometry(), {'Class': cls})

train_clean = trainFC.map(_clean).filter(ee.Filter.inList('Class', validClasses))

def _setid(f):
    return f.set('ClassId', ee.Number(classToId.get(ee.String(f.get('Class')))))

train_id = train_clean.map(_setid)

train_solid = (efgRaster.sampleRegions(collection=train_id, properties=['Class', 'ClassId'],
                                        scale=SCALE, geometries=True, tileScale=2)
               .filter(ee.Filter.notNull(['efg_id'])))

def _add_lonlat_and_block(f):
    c = f.geometry().coordinates()
    lon = ee.Number(c.get(0))
    lat = ee.Number(c.get(1))
    block_col = lon.subtract(ORIGIN_LON).divide(BLOCK_DEG).floor()
    block_row = lat.subtract(ORIGIN_LAT).divide(BLOCK_DEG).floor()
    block_id = block_row.multiply(10000).add(block_col)
    fold = block_id.mod(K_FOLDS)
    return f.set({'lon': lon, 'lat': lat, 'block_id': block_id, 'fold': fold})

train_solid = train_solid.map(_add_lonlat_and_block)

# ----------------------------------------------------------------------
# STEP 1 - sample embeddings at all points ONCE (reuse across folds)
# ----------------------------------------------------------------------
def make_rf():
    return (ee.Classifier.smileRandomForest(numberOfTrees=300, seed=123,
                                             minLeafPopulation=1, bagFraction=0.632)
            .setOutputMode('MULTIPROBABILITY'))

def sample_embeddings():
    return emb.sampleRegions(
        collection=train_solid,
        properties=['ClassId', 'efg_id', 'lon', 'lat', 'fold'],
        scale=SCALE, geometries=False, tileScale=4)

# ----------------------------------------------------------------------
# STEP 3 - out-of-fold P(intact) via per-EFG models
# ----------------------------------------------------------------------
def p_intact_of(fc, model):
    """classify fc with model, add p_intact = arrayGet([0]) of MULTIPROBABILITY array."""
    classified = fc.classify(model)
    def _extract(f):
        arr = ee.Array(f.get('classification'))
        p_int = ee.Number(arr.get([0]))
        return f.set('p_intact', p_int)
    return classified.map(_extract)

def run_spatial_cv(samples, efg_counts):
    """Return a FeatureCollection of OOF predictions (ClassId, p_intact, efg_id, fold)."""
    oof_parts = []
    for k in range(K_FOLDS):
        train_k = samples.filter(ee.Filter.neq('fold', k))
        test_k = samples.filter(ee.Filter.eq('fold', k))
        for efg in (1, 2, 3):
            tr = train_k.filter(ee.Filter.eq('efg_id', efg))
            te = test_k.filter(ee.Filter.eq('efg_id', efg))
            # Guard: need training and test points for this efg/fold combo.
            # efg_counts[efg] is the total; folds rarely empty, but classify on empty FC is fine (yields empty).
            model = make_rf().train(features=tr, classProperty='ClassId', inputProperties=predictors)
            pred = p_intact_of(te, model).select(['ClassId', 'p_intact', 'efg_id', 'fold'])
            oof_parts.append(pred)
    oof = ee.FeatureCollection(oof_parts).flatten()
    return oof

# ----------------------------------------------------------------------
# STEP 5 - real P(intact) surface from full per-EFG models
# ----------------------------------------------------------------------
def build_p_intact_surface(samples):
    surfaces = []
    for efg in (1, 2, 3):
        tr = samples.filter(ee.Filter.eq('efg_id', efg))
        model = make_rf().train(features=tr, classProperty='ClassId', inputProperties=predictors)
        naturalPredictionMask = mask_efg[efg].updateMask(naturalMask).updateMask(notWaterMask)
        probArray = emb.updateMask(naturalPredictionMask).classify(model).select(0)
        p_int = probArray.arrayGet([0]).rename('p_intact').toFloat()
        surfaces.append(p_int)
    surface = ee.ImageCollection(surfaces).mosaic().updateMask(validSolidMask).rename('p_intact')
    return surface

# ----------------------------------------------------------------------
# Client-side threshold metrics
# ----------------------------------------------------------------------
def threshold_accuracy(df_classid, df_pintact):
    y_true_intact = (np.asarray(df_classid) == 0).astype(int)   # positive = intact
    p = np.asarray(df_pintact, dtype=float)
    N = len(p)
    n_int = int(y_true_intact.sum())
    n_not = int(N - n_int)

    taus = np.round(np.arange(0.0, 1.0001, 0.01), 4)
    res = {k: [] for k in ['thresholds', 'overall_accuracy', 'precision', 'recall',
                           'specificity', 'f1', 'balanced_accuracy', 'youden_J']}
    for tau in taus:
        pred_pos = (p >= tau).astype(int)
        tp = int(np.sum((pred_pos == 1) & (y_true_intact == 1)))
        fp = int(np.sum((pred_pos == 1) & (y_true_intact == 0)))
        tn = int(np.sum((pred_pos == 0) & (y_true_intact == 0)))
        fn = int(np.sum((pred_pos == 0) & (y_true_intact == 1)))
        acc = (tp + tn) / N if N else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0        # sensitivity
        spec = tn / (tn + fp) if (tn + fp) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        bal = (rec + spec) / 2.0
        youden = rec + spec - 1.0
        res['thresholds'].append(float(tau))
        res['overall_accuracy'].append(round(acc, 6))
        res['precision'].append(round(prec, 6))
        res['recall'].append(round(rec, 6))
        res['specificity'].append(round(spec, 6))
        res['f1'].append(round(f1, 6))
        res['balanced_accuracy'].append(round(bal, 6))
        res['youden_J'].append(round(youden, 6))

    def pick(metric):
        arr = np.asarray(res[metric])
        i = int(np.argmax(arr))
        t = res['thresholds'][i]
        return {'threshold': t, 'youden_J': res['youden_J'][i], 'f1': res['f1'][i],
                'overall_accuracy': res['overall_accuracy'][i], 'recall': res['recall'][i],
                'specificity': res['specificity'][i], 'balanced_accuracy': res['balanced_accuracy'][i]}

    # ROC AUC (trapezoid over recall vs (1-specificity))
    fpr = 1.0 - np.asarray(res['specificity'])
    tpr = np.asarray(res['recall'])
    order = np.argsort(fpr)
    _trap = getattr(np, 'trapezoid', None) or np.trapz  # NumPy 2.x renamed trapz->trapezoid
    auc = float(_trap(tpr[order], fpr[order]))

    res['ideal'] = {'by_youden': pick('youden_J'),
                    'by_f1': pick('f1'),
                    'by_overall_accuracy': pick('overall_accuracy')}
    res['roc_auc'] = round(abs(auc), 6)
    res['n_points'] = int(N)
    res['n_intact'] = n_int
    res['n_not_intact'] = n_not
    return res

def threshold_area(surface):
    """Single-pass: bin p_intact into 100 bins, sum pixelArea per bin (grouped
    reduction = ONE server call), then derive area-above-threshold as a client-side
    cumulative sum. Gives a 0.01-resolution curve without 100 round-trips."""
    NBINS = 100
    # bin index 0..99 for p_intact in [0,1); clamp 1.0 into bin 99
    binned = surface.multiply(NBINS).floor().min(NBINS - 1).toInt().rename('bin')
    px_area = ee.Image.pixelArea().rename('area')
    stack = px_area.addBands(binned)  # band 0 = area (value), band 1 = bin (group)

    # The surface is already masked to valid solid thicket, so reducing over the AOI
    # rectangle counts exactly the valid pixels while avoiding the very expensive
    # tiling of the complex EFG multipolygon. Coarser scale keeps the area curve
    # accurate (fractional-coverage weighting) at a fraction of the compute cost.
    # Escalate scale if EE still times out.
    groups = None
    last_scale = None
    for area_scale in (AREA_SCALE, 200, 300):
        try:
            print(f'  reducing area at scale={area_scale} m over AOI ...')
            grouped = stack.reduceRegion(
                reducer=ee.Reducer.sum().group(groupField=1, groupName='bin'),
                geometry=aoi,
                scale=area_scale, maxPixels=int(1e13), bestEffort=True, tileScale=4)
            groups = grouped.getInfo().get('groups', [])
            last_scale = area_scale
            break
        except Exception as e:
            print(f'  scale={area_scale} failed ({str(e).splitlines()[0][:60]}); escalating')
    if groups is None:
        raise RuntimeError('area reduction failed at all scales')
    print(f'  area computed at scale={last_scale} m')
    # area (km2) per bin
    per_bin = np.zeros(NBINS, dtype=float)
    for g in groups:
        b = int(g['bin'])
        if 0 <= b < NBINS:
            per_bin[b] = g['sum'] / 1e6
    total_valid = float(per_bin.sum())
    # area where p_intact >= tau : cumulative from the top bin down
    # threshold at bin edge i/NBINS -> sum of bins >= i
    taus = np.round(np.arange(0.0, 1.0001, 0.01), 4)
    area_km2 = []
    for tau in taus:
        i = int(round(tau * NBINS))
        i = max(0, min(NBINS, i))
        area = float(per_bin[i:].sum())  # bins i..99 have p_intact >= tau
        area_km2.append(round(area, 4))
    print(f'  total valid area = {round(total_valid,2)} km2; '
          f'area@0.5 = {area_km2[50]} km2; area@0.7 = {area_km2[70]} km2')
    return {'thresholds': [float(t) for t in taus],
            'area_km2': area_km2,
            'per_bin_km2': [round(x, 4) for x in per_bin.tolist()],
            'total_valid_area_km2': round(total_valid, 4),
            'area_scale_m': last_scale}

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    t0 = time.time()
    print('== Counting solid training points ==')
    efg_counts = train_solid.aggregate_histogram('efg_id').getInfo()
    print('  efg_id histogram:', efg_counts)

    print('== STEP 1: sampling embeddings at points (once) ==')
    samples = sample_embeddings()  # lazy EE object; reused by STEP 5

    cache = out_path('oof_points.json')
    if os.path.exists(cache):
        print('== STEP 2-3: loading cached out-of-fold predictions ==')
        with open(cache) as fh:
            rows = json.load(fh)
        print(f'  loaded {len(rows)} cached OOF rows (skipping CV re-run)')
    else:
        n_samp = samples.size().getInfo()
        print(f'  sampled {n_samp} points')
        print('== STEP 2-3: spatial 5-fold CV, out-of-fold P(intact) ==')
        oof = run_spatial_cv(samples, {int(k): v for k, v in efg_counts.items()})
        # ONE getInfo of ~2083 small rows
        print('  evaluating OOF predictions (single getInfo)...')
        oof_list = oof.getInfo()['features']
        rows = [{'ClassId': int(f['properties']['ClassId']),
                 'p_intact': float(f['properties']['p_intact']),
                 'efg_id': int(f['properties']['efg_id']),
                 'fold': int(f['properties']['fold'])} for f in oof_list]
        print(f'  OOF rows: {len(rows)}')
        with open(cache, 'w') as fh:
            json.dump(rows, fh)

    classids = [r['ClassId'] for r in rows]
    pints = [r['p_intact'] for r in rows]

    print('== STEP 4: threshold vs accuracy ==')
    acc = threshold_accuracy(classids, pints)
    with open(out_path('threshold_accuracy.json'), 'w') as fh:
        json.dump(acc, fh, indent=2)
    print('  ideal (Youden):', acc['ideal']['by_youden'])
    print('  ideal (F1):', acc['ideal']['by_f1'])
    print('  ROC AUC:', acc['roc_auc'])

    print('== STEP 5: threshold vs mapped intact area (real surface) ==')
    surface = build_p_intact_surface(samples)
    area = threshold_area(surface)
    with open(out_path('threshold_area.json'), 'w') as fh:
        json.dump(area, fh, indent=2)

    # area at ideal youden threshold (interpolate from the 0.05 grid)
    ideal_tau = acc['ideal']['by_youden']['threshold']
    ta = np.asarray(area['thresholds'])
    va = np.asarray([np.nan if x is None else x for x in area['area_km2']], dtype=float)
    area_at_ideal = float(np.interp(ideal_tau, ta, va))

    summary = {
        'n_points': acc['n_points'],
        'n_intact': acc['n_intact'],
        'n_not_intact': acc['n_not_intact'],
        'efg_counts': efg_counts,
        'roc_auc': acc['roc_auc'],
        'ideal_threshold_youden': acc['ideal']['by_youden'],
        'ideal_threshold_f1': acc['ideal']['by_f1'],
        'ideal_threshold_overall_accuracy': acc['ideal']['by_overall_accuracy'],
        'total_valid_area_km2': area['total_valid_area_km2'],
        'intact_area_at_ideal_km2': round(area_at_ideal, 4),
        'k_folds': K_FOLDS,
        'block_deg': BLOCK_DEG,
        'year': YEAR,
        'elapsed_sec': round(time.time() - t0, 1),
    }
    with open(out_path('summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=2)

    # Combined bundle for the artifact (single JSON to inject)
    bundle = {
        'summary': summary,
        'threshold_accuracy': acc,
        'threshold_area': {k: v for k, v in area.items() if k != 'per_bin_km2'},
        'generated_on': time.strftime('%Y-%m-%d'),
        'generated_by': 'sensitivity_analysis.py',
    }
    with open(out_path('artifact_data.json'), 'w') as fh:
        json.dump(bundle, fh)

    print('\n== SUMMARY ==')
    print(json.dumps(summary, indent=2))
    print(f'\nDONE in {summary["elapsed_sec"]}s')

if __name__ == '__main__':
    main()
