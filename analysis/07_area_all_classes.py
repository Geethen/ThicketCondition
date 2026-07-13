"""
Mapped-area-vs-threshold curves for ALL THREE condition classes.

Analogue of 01_threshold_sensitivity.build_p_intact_surface + threshold_area, generalised
to moderate (p band index 1) and severe (index 2). Reuses the exact per-EFG full models,
masks, AOI, and single-pass grouped-histogram area reduction from script 01.

For class C the surface is p_C = classify(...).arrayGet([C]); area>=tau is the cumulative
sum of area over p_C bins >= tau. Same 100-bin, AOI-rectangle, escalating-scale trick.

Outputs:
  results/threshold_area_moderate.json
  results/threshold_area_severe.json
  results/threshold_area_intact_check.json   (rebuild of intact area, to confirm parity)
"""
import os, json, time
import numpy as np
import ee

HERE = os.path.dirname(os.path.abspath(__file__))
def res_path(name): return os.path.join(HERE, 'results', name)

ee.Initialize(project='ee-gsingh')

# ---- constants (identical to 01_threshold_sensitivity.py) ----
YEAR = 2022
SCALE = 10
AREA_SCALE = 100
ORIGIN_LON, ORIGIN_LAT = 20.0, -35.0
CLASS_NAME = {0: 'intact', 1: 'moderate', 2: 'severe'}

geometry = ee.Geometry.Polygon(
    [[[20.651320170862977, -31.977939185448044],
      [20.651320170862977, -34.55873881519996],
      [29.286574077112977, -34.55873881519996],
      [29.286574077112977, -31.977939185448044]]], None, False)
aoi = geometry.bounds(1)

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

nlcBand = ee.Image('projects/thicket-ecological-condition/assets/SA_NLC_2022_GEO').select(0).rename('nlc').clip(aoi)
water = nlcBand.remap([14, 15, 16, 17, 18, 19, 20, 21], [1, 1, 1, 1, 1, 1, 1, 1], 0)
notWaterMask = water.Not()
lcBand = ee.Image('projects/thicket-ecological-condition/assets/nlc2022_7class').select(0).rename('LC').clip(aoi)
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
    cls = ee.String(f.get('Class')).toLowerCase()
    return ee.Feature(f.geometry(), {'Class': cls})

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

def sample_embeddings():
    return emb.sampleRegions(collection=train_solid, properties=['ClassId', 'efg_id'],
                             scale=SCALE, geometries=False, tileScale=4)

def build_p_class_surface(samples, cid):
    """P(class=cid) surface from the full per-EFG models (mosaic across EFGs)."""
    surfaces = []
    for efg in (1, 2, 3):
        tr = samples.filter(ee.Filter.eq('efg_id', efg))
        model = make_rf().train(features=tr, classProperty='ClassId', inputProperties=predictors)
        naturalPredictionMask = mask_efg[efg].updateMask(naturalMask).updateMask(notWaterMask)
        probArray = emb.updateMask(naturalPredictionMask).classify(model).select(0)
        p = probArray.arrayGet([cid]).rename('p_class').toFloat()
        surfaces.append(p)
    return ee.ImageCollection(surfaces).mosaic().updateMask(validSolidMask).rename('p_class')

def threshold_area(surface):
    """Single-pass 100-bin area histogram -> cumulative area>=tau. Identical logic to
    01_threshold_sensitivity.threshold_area, only the band name differs."""
    NBINS = 100
    binned = surface.multiply(NBINS).floor().min(NBINS - 1).toInt().rename('bin')
    px_area = ee.Image.pixelArea().rename('area')
    stack = px_area.addBands(binned)
    groups = None
    last_scale = None
    for area_scale in (AREA_SCALE, 200, 300):
        try:
            print(f'    reducing area at scale={area_scale} m over AOI ...', flush=True)
            grouped = stack.reduceRegion(
                reducer=ee.Reducer.sum().group(groupField=1, groupName='bin'),
                geometry=aoi, scale=area_scale, maxPixels=int(1e13), bestEffort=True, tileScale=4)
            groups = grouped.getInfo().get('groups', [])
            last_scale = area_scale
            break
        except Exception as e:
            print(f'    scale={area_scale} failed ({str(e).splitlines()[0][:60]}); escalating', flush=True)
    if groups is None:
        raise RuntimeError('area reduction failed at all scales')
    per_bin = np.zeros(NBINS, dtype=float)
    for g in groups:
        b = int(g['bin'])
        if 0 <= b < NBINS:
            per_bin[b] = g['sum'] / 1e6
    total_valid = float(per_bin.sum())
    taus = np.round(np.arange(0.0, 1.0001, 0.01), 4)
    area_km2 = []
    for tau in taus:
        i = int(round(tau * NBINS)); i = max(0, min(NBINS, i))
        area_km2.append(round(float(per_bin[i:].sum()), 4))
    print(f'    total valid area = {round(total_valid,2)} km2; area@0.5 = {area_km2[50]} km2', flush=True)
    return {'thresholds': [float(t) for t in taus], 'area_km2': area_km2,
            'per_bin_km2': [round(x, 4) for x in per_bin.tolist()],
            'total_valid_area_km2': round(total_valid, 4), 'area_scale_m': last_scale}


def main():
    t0 = time.time()
    print('== sampling embeddings at points (once) ==', flush=True)
    samples = sample_embeddings()
    # load the ideal thresholds so we can report area at each class' tau*
    summ = json.load(open(res_path('threshold_all_classes_summary.json')))
    for cid, suffix in [(1, 'moderate'), (2, 'severe'), (0, 'intact_check')]:
        name = CLASS_NAME[cid]
        print(f'\n== {name.upper()} area-vs-tau (p band {cid}) ==', flush=True)
        surface = build_p_class_surface(samples, cid)
        area = threshold_area(surface)
        # area at this class' ideal Youden threshold
        tau_star = summ['per_class'][name]['ideal_threshold_youden']['threshold']
        ta = np.asarray(area['thresholds']); va = np.asarray(area['area_km2'], dtype=float)
        area['ideal_threshold_youden'] = tau_star
        area['area_at_ideal_km2'] = round(float(np.interp(tau_star, ta, va)), 4)
        with open(res_path(f'threshold_area_{suffix}.json'), 'w') as fh:
            json.dump(area, fh, indent=2)
        print(f'  tau*={tau_star}  area@tau* = {area["area_at_ideal_km2"]} km2  '
              f'(total valid {area["total_valid_area_km2"]} km2)', flush=True)

    print(f'\nDONE in {round(time.time()-t0,1)}s', flush=True)


if __name__ == '__main__':
    main()
