#!/usr/bin/env python
"""Assemble the single-file deployable inspector.

Reads the template (thicket_inspector.html), the app logic (app.js), and the
sample points (../analysis/results/sample_points.csv), and writes a fully
self-contained index.html: points embedded, app.js inlined. No build tooling,
no server -- drop index.html on any static host (GitHub Pages, Netlify).

Run:
    python inspector/build.py
"""
import argparse, csv, hashlib, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, '..', 'analysis', 'results', 'sample_points.csv')
TPL = os.path.join(HERE, 'thicket_inspector.html')
APP = os.path.join(HERE, 'app.js')
OUT = os.path.join(HERE, 'index.html')
ASSIGNMENTS = os.path.join(HERE, 'assignment_manifest.json')


def load_points():
    pts = []
    with open(CSV, newline='') as fh:
        for r in csv.DictReader(fh):
            pts.append({'id': int(r['id']), 's': r['stratum'],
                        'lon': round(float(r['lon']), 6), 'lat': round(float(r['lat']), 6)})
    return pts


def dataset_id(pts):
    """Stable fingerprint of the sample draw: id + rounded coords + stratum.

    Changes whenever the point set or its geometry changes, so labels saved
    against one draw can be detected as belonging to a different draw."""
    h = hashlib.sha256()
    for p in pts:
        h.update(f"{p['id']}|{p['lon']:.6f}|{p['lat']:.6f}|{p['s']}\n".encode())
    return h.hexdigest()[:16]


def load_assignments(path, ds_id, pts):
    if not path or not os.path.exists(path):
        return {'version': 1, 'dataset': ds_id, 'campaign': '', 'overlap_fraction': 0,
                'labelers': {}, 'qa_overlap_point_ids': []}
    with open(path, encoding='utf-8') as fh:
        manifest = json.load(fh)
    if manifest.get('dataset') not in (None, '', ds_id):
        raise ValueError(f'assignment manifest belongs to dataset {manifest.get("dataset")}, expected {ds_id}')
    valid_ids = {p['id'] for p in pts}
    seen_codes = set()
    for code, record in manifest.get('labelers', {}).items():
        if code.lower() in seen_codes:
            raise ValueError(f'duplicate assignment code (case-insensitive): {code}')
        seen_codes.add(code.lower())
        ids = record.get('point_ids', [])
        if len(ids) != len(set(ids)) or any(i not in valid_ids for i in ids):
            raise ValueError(f'assignment {code} contains duplicate or unknown point IDs')
    manifest['dataset'] = ds_id
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assignments', default=ASSIGNMENTS,
                        help='assignment manifest to embed (default: assignment_manifest.json)')
    parser.add_argument('--out', default=OUT, help='output HTML path')
    args = parser.parse_args(argv)
    pts = load_points()
    pts_js = ',\n'.join(json.dumps(p, separators=(',', ':')) for p in pts)
    ds_id = dataset_id(pts)
    assignments = load_assignments(args.assignments, ds_id, pts)

    tpl = open(TPL, encoding='utf-8').read()
    app = open(APP, encoding='utf-8').read()

    tpl = tpl.replace('__POINTS__', pts_js)
    tpl = tpl.replace('__DATASET_ID__', ds_id)
    tpl = tpl.replace('__ASSIGNMENTS__', json.dumps(assignments, separators=(',', ':')))
    # inline app.js in place of the external <script src="app.js"></script>
    marker = '<script src="app.js"></script>'
    assert marker in tpl, 'app.js script tag not found in template'
    tpl = tpl.replace(marker, '<script>\n' + app + '\n</script>')

    with open(args.out, 'w', encoding='utf-8') as fh:
        fh.write(tpl)
    print(f'wrote {args.out}  ({len(pts)} points, dataset {ds_id}, '
          f'{len(assignments.get("labelers", {}))} assignments, {len(tpl)//1024} KB)')


if __name__ == '__main__':
    main()
