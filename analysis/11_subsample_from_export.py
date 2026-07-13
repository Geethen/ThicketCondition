#!/usr/bin/env python
"""Draw the final SE=0.015 stratified sample by SUBSAMPLING the completed batch
export (thicket_condition_sample_seed42.geojson, 1,902 pts at SE=0.01).

Drawing the sample directly from Earth Engine (stratifiedSample -> getInfo /
computeFeatures) times out over the 1.9M-ha AOI. The batch Export.table.toDrive
job did succeed, so we already hold a valid stratified random sample with more
points per class than the lighter SE=0.015 design needs (521/618/763 vs the
232/275/339 target). A simple random subset *within each stratum* of a stratified
random sample is itself a valid stratified random sample, so we subsample to the
target allocation with a fixed seed. This sidesteps the EE compute timeout
entirely and keeps the exact argmax-class strata from steph.js.

Input : the downloaded export geojson (points carry lon/lat/stratum/cls; the
        exported MultiPoint geometry is empty, so we rebuild Point geoms from
        lon/lat).
Design: analysis/results/sample_design.json  (alloc = target per-class counts)
Output: analysis/results/sample_points.geojson
        analysis/results/sample_points.csv

Run:
  python analysis/11_subsample_from_export.py \
      --src <downloaded_export.geojson> --seed 42
"""
import argparse, json, os, csv, random, base64

HERE = os.path.dirname(os.path.abspath(__file__))
def res_path(n): return os.path.join(HERE, 'results', n)


def load_features(src):
    """Accept either a raw GeoJSON file or the Drive-download wrapper
    {content: <base64 geojson>, ...}. Returns the feature list."""
    with open(src, 'r', encoding='utf-8') as fh:
        obj = json.load(fh)
    if isinstance(obj, dict) and 'content' in obj and 'features' not in obj:
        obj = json.loads(base64.b64decode(obj['content']))
    return obj['features']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='exported (or Drive-wrapped) geojson of the 1902-pt sample')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    design = json.load(open(res_path('sample_design.json')))
    alloc = design['alloc']            # e.g. {'intact':232,'moderate':275,'severe':339}

    feats = load_features(args.src)

    # bucket by stratum, rebuilding a Point geometry from lon/lat (export geom is empty)
    buckets = {'intact': [], 'moderate': [], 'severe': []}
    for f in feats:
        p = f['properties']
        s = p['stratum']
        if s in buckets:
            buckets[s].append({'stratum': s, 'cls': int(p['cls']),
                               'lon': float(p['lon']), 'lat': float(p['lat'])})

    rng = random.Random(args.seed)
    chosen = []
    for s in ('intact', 'moderate', 'severe'):
        pool = buckets[s]
        want = int(alloc[s])
        if len(pool) < want:
            raise SystemExit(f'stratum {s}: pool {len(pool)} < needed {want}; '
                             f're-export a larger parent sample')
        chosen.extend(rng.sample(pool, want))
        print(f'  {s:9s} pool={len(pool):4d}  drew={want}')

    # stable id order: intact, moderate, severe as listed
    out_feats = []
    for i, r in enumerate(chosen):
        out_feats.append({
            'type': 'Feature',
            'properties': {'id': i, 'stratum': r['stratum'], 'cls': r['cls'],
                           'lon': r['lon'], 'lat': r['lat']},
            'geometry': {'type': 'Point', 'coordinates': [r['lon'], r['lat']]},
        })
    fc = {'type': 'FeatureCollection',
          'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
          'features': out_feats}

    with open(res_path('sample_points.geojson'), 'w') as fh:
        json.dump(fc, fh)
    with open(res_path('sample_points.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'stratum', 'cls', 'lon', 'lat'])
        for ft in out_feats:
            p = ft['properties']
            w.writerow([p['id'], p['stratum'], p['cls'], p['lon'], p['lat']])

    # record provenance in the design summary
    design['n_drawn'] = len(out_feats)
    design['drawn_counts'] = {s: int(alloc[s]) for s in ('intact', 'moderate', 'severe')}
    design['draw_method'] = ('subsampled from completed SE=0.01 batch export '
                             'thicket_condition_sample_seed42.geojson (valid: random subset '
                             'within each stratum of a stratified random sample)')
    design['subsample_seed'] = args.seed
    json.dump(design, open(res_path('sample_design.json'), 'w'), indent=2)

    print(f'\n[OK] wrote {len(out_feats)} points -> results/sample_points.geojson + .csv')


if __name__ == '__main__':
    main()
