"""
Capture out-of-fold values of ALL THREE class probabilities (p_intact, p_moderate,
p_severe) plus the true label, via the same spatial 5-fold CV and per-EFG RF models
as steph.js. This feeds the symbolic-model search for the best band combination.

Sampling resolution: try SCALE=10 with escalating tileScale (4->8->16); if all fail,
fall back to SCALE=30 with escalating tileScale. Writes oof_3band.json.
"""
import os, json, time
import ee

HERE = os.path.dirname(os.path.abspath(__file__))
def out_path(name): return os.path.join(HERE, name)
ee.Initialize(project='ee-gsingh')

YEAR = 2022
K_FOLDS = 5
BLOCK_DEG = 0.2
ORIGIN_LON, ORIGIN_LAT = 20.0, -35.0

geometry = ee.Geometry.Polygon(
    [[[20.651320170862977, -31.977939185448044],
      [20.651320170862977, -34.55873881519996],
      [29.286574077112977, -34.55873881519996],
      [29.286574077112977, -31.977939185448044]]], None, False)
aoi = geometry.bounds(1)

# --- EFG ---
EFG = ee.FeatureCollection('projects/thicket-ecological-condition/assets/ThicketEFGs')
solidEFG = EFG.filter(ee.Filter.inList('RevisedFVG', ['Arid Thicket', 'Valley Thicket', 'Mesic Thicket']))
def _add_efg_id(f):
    name = ee.String(f.get('RevisedFVG'))
    idv = ee.Number(ee.Algorithms.If(name.equals('Arid Thicket'), 1,
                    ee.Algorithms.If(name.equals('Valley Thicket'), 2, 3)))
    return f.set('efg_id', idv)
efgRaster = (solidEFG.map(_add_efg_id).reduceToImage(properties=['efg_id'], reducer=ee.Reducer.first())
             .rename('efg_id').toByte())

# --- water mask (for embeddings) ---
nlcBand = ee.Image('projects/thicket-ecological-condition/assets/SA_NLC_2022_GEO').select(0).rename('nlc').clip(aoi)
water = nlcBand.remap([14, 15, 16, 17, 18, 19, 20, 21], [1]*8, 0)
notWaterMask = water.Not()

emb = (ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
       .filterDate(f'{YEAR}-01-01', f'{YEAR+1}-01-01')
       .mosaic().toFloat().clip(aoi).updateMask(notWaterMask))
predictors = emb.bandNames()

# --- training points ---
validClasses = ['intact', 'moderate', 'severe']
classToId = ee.Dictionary({'intact': 0, 'moderate': 1, 'severe': 2})
trainFC = ee.FeatureCollection('projects/thicket-ecological-condition/assets/training_collated_withFLC').filterBounds(aoi)
train_clean = trainFC.map(lambda f: ee.Feature(f.geometry(), {'Class': ee.String(f.get('Class')).toLowerCase()})
                          ).filter(ee.Filter.inList('Class', validClasses))
train_id = train_clean.map(lambda f: f.set('ClassId', ee.Number(classToId.get(ee.String(f.get('Class'))))))
train_solid = (efgRaster.sampleRegions(collection=train_id, properties=['Class', 'ClassId'],
                                        scale=10, geometries=True, tileScale=2)
               .filter(ee.Filter.notNull(['efg_id'])))
def _add_block(f):
    c = f.geometry().coordinates()
    lon = ee.Number(c.get(0)); lat = ee.Number(c.get(1))
    bcol = lon.subtract(ORIGIN_LON).divide(BLOCK_DEG).floor()
    brow = lat.subtract(ORIGIN_LAT).divide(BLOCK_DEG).floor()
    fold = brow.multiply(10000).add(bcol).mod(K_FOLDS)
    return f.set({'fold': fold})
train_solid = train_solid.map(_add_block)

def make_rf():
    return (ee.Classifier.smileRandomForest(numberOfTrees=300, seed=123,
                                             minLeafPopulation=1, bagFraction=0.632)
            .setOutputMode('MULTIPROBABILITY'))

def probs_of(fc, model):
    """add p_intact/p_moderate/p_severe from MULTIPROBABILITY array."""
    classified = fc.classify(model)
    def _extract(f):
        arr = ee.Array(f.get('classification'))
        return f.set({'p_intact': ee.Number(arr.get([0])),
                      'p_moderate': ee.Number(arr.get([1])),
                      'p_severe': ee.Number(arr.get([2]))})
    return classified.map(_extract)

def sample_at(scale, tile_scale):
    """Sample embeddings at points at given scale+tileScale, then run spatial CV -> OOF 3-band FC."""
    samples = emb.sampleRegions(
        collection=train_solid, properties=['ClassId', 'efg_id', 'fold'],
        scale=scale, geometries=False, tileScale=tile_scale)
    oof_parts = []
    for k in range(K_FOLDS):
        train_k = samples.filter(ee.Filter.neq('fold', k))
        test_k = samples.filter(ee.Filter.eq('fold', k))
        for efg in (1, 2, 3):
            tr = train_k.filter(ee.Filter.eq('efg_id', efg))
            te = test_k.filter(ee.Filter.eq('efg_id', efg))
            model = make_rf().train(features=tr, classProperty='ClassId', inputProperties=predictors)
            pred = probs_of(te, model).select(['ClassId', 'p_intact', 'p_moderate', 'p_severe', 'efg_id', 'fold'])
            oof_parts.append(pred)
    return ee.FeatureCollection(oof_parts).flatten()

def main():
    t0 = time.time()
    attempts = ([(10, ts) for ts in (4, 8, 16)] +
                [(30, ts) for ts in (4, 8, 16)])
    rows = None
    used = None
    for scale, tile_scale in attempts:
        try:
            print(f'>> attempting OOF sample at scale={scale} m, tileScale={tile_scale} ...', flush=True)
            oof = sample_at(scale, tile_scale)
            feats = oof.getInfo()['features']
            rows = [{'ClassId': int(f['properties']['ClassId']),
                     'p_intact': float(f['properties']['p_intact']),
                     'p_moderate': float(f['properties']['p_moderate']),
                     'p_severe': float(f['properties']['p_severe']),
                     'efg_id': int(f['properties']['efg_id']),
                     'fold': int(f['properties']['fold'])} for f in feats]
            used = {'scale_m': scale, 'tileScale': tile_scale}
            print(f'   SUCCESS scale={scale} tileScale={tile_scale} -> {len(rows)} rows', flush=True)
            break
        except Exception as e:
            msg = str(e).splitlines()[0][:90]
            print(f'   FAILED scale={scale} tileScale={tile_scale}: {msg}', flush=True)
    if rows is None:
        raise RuntimeError('all sampling attempts failed')

    # sanity: probabilities should sum ~1
    import numpy as np
    P = np.array([[r['p_intact'], r['p_moderate'], r['p_severe']] for r in rows])
    sums = P.sum(axis=1)
    print(f'prob-sum: min={sums.min():.4f} max={sums.max():.4f} mean={sums.mean():.4f}', flush=True)
    labels = np.array([r['ClassId'] for r in rows])
    print(f'N={len(rows)} intact={int((labels==0).sum())} moderate={int((labels==1).sum())} severe={int((labels==2).sum())}', flush=True)

    payload = {'rows': rows, 'sampling': used, 'n': len(rows),
               'elapsed_sec': round(time.time()-t0, 1)}
    with open(out_path('oof_3band.json'), 'w') as fh:
        json.dump(payload, fh)
    print(f'wrote oof_3band.json ({used}) in {payload["elapsed_sec"]}s', flush=True)
    print('DONE', flush=True)

if __name__ == '__main__':
    main()
