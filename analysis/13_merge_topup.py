#!/usr/bin/env python
"""Merge the batch-exported EFG top-up points with the existing 846 condition-only
points to produce the final augmented 9-stratum (EFG x severity) sample.

Why a separate step: drawing the top-up directly from Earth Engine
(stratifiedSample -> computeFeatures / getInfo) times out over the 1.9M-ha AOI
(the same gotcha 11_subsample_from_export.py works around). So 12_augment_efg_
stratify.py --export-fallback starts an Export.table.toDrive batch task instead.
Once that task finishes and you download its GeoJSON, this script merges it in.

The existing 846 points keep their identity and their EFG assignment (already
recorded in results/sample_design_efg.json's existing_counts, and re-derived here
from results/sample_points.geojson tagged against the same efg raster is NOT
needed -- we reuse the tagged geojson written alongside, see --existing). Every
top-up point carries strat9 = efg_id*10 + cls in its properties.

Input : the downloaded top-up export geojson (Drive file "efg_topup_seed42...").
        Its MultiPoint geometry may be empty, so we rebuild Point geoms from the
        lon/lat properties (same as script 11).
Output: analysis/results/sample_points_efg.geojson  (existing + new)
        analysis/results/sample_points_efg.csv

Run:
  python analysis/13_merge_topup.py --topup <downloaded_efg_topup.geojson> --seed 42
"""
import argparse, json, os, csv, base64
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
def res_path(n): return os.path.join(HERE, 'results', n)

CLASS_NAME = {0: 'intact', 1: 'moderate', 2: 'severe'}
EFG_NAME = {1: 'AridThicket', 2: 'ValleyThicket', 3: 'MesicThicket'}
def stratum9_label(s): return f'{EFG_NAME[s // 10]}_{CLASS_NAME[s % 10]}'


def load_features(src):
    """Accept a raw GeoJSON file or the Drive-download wrapper {content: b64}."""
    with open(src, 'r', encoding='utf-8') as fh:
        obj = json.load(fh)
    if isinstance(obj, dict) and 'content' in obj and 'features' not in obj:
        obj = json.loads(base64.b64decode(obj['content']))
    return obj['features']


def load_existing_tagged():
    """Existing 846 points with their EFG assignment, as recorded by
    12_augment_efg_stratify.py --export-fallback in results/existing_tagged_efg.json
    (each point already sampled against the same efg raster). Falls back to the
    'existing' rows of a previously-written augmented geojson if that's all there is."""
    cache = res_path('existing_tagged_efg.json')
    if os.path.exists(cache):
        rows = json.load(open(cache))['existing']
        return [{'stratum': r['stratum'], 'cls': int(r['cls']),
                 'efg_id': r['efg_id'], 'strat9': r['strat9'],
                 'lon': float(r['lon']), 'lat': float(r['lat'])} for r in rows]
    aug = res_path('sample_points_efg.geojson')
    if os.path.exists(aug):
        gj = json.load(open(aug))
        rows = [f['properties'] for f in gj['features'] if f['properties'].get('source') == 'existing']
        if rows:
            return [{'stratum': r['stratum'], 'cls': int(r['cls']),
                     'efg_id': r['efg_id'], 'strat9': r['strat9'],
                     'lon': float(r['lon']), 'lat': float(r['lat'])} for r in rows]
    raise SystemExit(
        'No EFG-tagged existing points found. Run 12_augment_efg_stratify.py '
        '--export-fallback first (it assigns each of the 846 points to its EFG and '
        'writes results/existing_tagged_efg.json); the merge reuses those assignments.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--topup', required=True, help='downloaded (or Drive-wrapped) top-up geojson')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    design = json.load(open(res_path('sample_design_efg.json')))

    existing = load_existing_tagged()

    feats = load_features(args.topup)
    new_pts = []
    for f in feats:
        p = f['properties']
        s = int(p['strat9'])
        new_pts.append({'strat9': s, 'efg_id': s // 10, 'cls': s % 10,
                        'stratum': CLASS_NAME[s % 10],
                        'lon': float(p['lon']), 'lat': float(p['lat'])})

    # sanity: drawn top-up counts vs plan
    plan = {stratum9_label(s): 0 for s in
            [e * 10 + c for e in (1, 2, 3) for c in (0, 1, 2)]}
    got = Counter(stratum9_label(p['strat9']) for p in new_pts)
    print('  top-up drawn vs planned:')
    for lab in design['topup_counts']:
        want = design['topup_counts'][lab]
        have = got.get(lab, 0)
        flag = '' if have == want else '  <-- MISMATCH'
        print(f'    {lab:<22} planned={want:>4}  drawn={have:>4}{flag}')

    out_feats = []
    nid = 0
    for src, pts in (('existing', existing), ('new', new_pts)):
        for p in pts:
            s = p['strat9']
            out_feats.append({
                'type': 'Feature',
                'properties': {'id': nid, 'source': src, 'stratum': p['stratum'],
                               'cls': p['cls'], 'efg_id': p['efg_id'],
                               'efg': EFG_NAME.get(p['efg_id']),
                               'strat9': s,
                               'strat9_label': stratum9_label(s) if s else None,
                               'lon': p['lon'], 'lat': p['lat']},
                'geometry': {'type': 'Point', 'coordinates': [p['lon'], p['lat']]}})
            nid += 1

    fc = {'type': 'FeatureCollection',
          'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
          'features': out_feats}
    json.dump(fc, open(res_path('sample_points_efg.geojson'), 'w'))
    with open(res_path('sample_points_efg.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['id', 'source', 'stratum', 'cls', 'efg_id', 'efg', 'strat9', 'strat9_label', 'lon', 'lat'])
        for ft in out_feats:
            p = ft['properties']
            w.writerow([p['id'], p['source'], p['stratum'], p['cls'], p['efg_id'],
                        p['efg'], p['strat9'], p['strat9_label'], p['lon'], p['lat']])

    design['n_existing'] = len(existing)
    design['n_new'] = len(new_pts)
    design['n_total'] = len(out_feats)
    design['draw_method'] = ('EFG x severity 9-stratum augmentation: existing 846 kept, '
                             'top-up drawn via Export.table.toDrive batch (stratifiedSample '
                             'computeFeatures times out over the AOI) then merged')
    design['topup_drawn'] = {lab: got.get(lab, 0) for lab in design['topup_counts']}
    json.dump(design, open(res_path('sample_design_efg.json'), 'w'), indent=2)

    print(f'\n[OK] merged {len(existing)} existing + {len(new_pts)} new = {len(out_feats)} '
          f'points -> results/sample_points_efg.geojson + .csv')


if __name__ == '__main__':
    main()
