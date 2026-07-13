#!/usr/bin/env python
"""Assemble the single-file deployable inspector.

Reads the template (thicket_inspector.html), the app logic (app.js), and the
sample points (../analysis/results/sample_points.csv), and writes a fully
self-contained index.html: points embedded, app.js inlined. No build tooling,
no server -- drop index.html on any static host (GitHub Pages, Netlify).

Run:
    python inspector/build.py
"""
import csv, hashlib, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, '..', 'analysis', 'results', 'sample_points.csv')
TPL = os.path.join(HERE, 'thicket_inspector.html')
APP = os.path.join(HERE, 'app.js')
OUT = os.path.join(HERE, 'index.html')


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


def main():
    pts = load_points()
    pts_js = ',\n'.join(json.dumps(p, separators=(',', ':')) for p in pts)
    ds_id = dataset_id(pts)

    tpl = open(TPL, encoding='utf-8').read()
    app = open(APP, encoding='utf-8').read()

    tpl = tpl.replace('__POINTS__', pts_js)
    tpl = tpl.replace('__DATASET_ID__', ds_id)
    # inline app.js in place of the external <script src="app.js"></script>
    marker = '<script src="app.js"></script>'
    assert marker in tpl, 'app.js script tag not found in template'
    tpl = tpl.replace(marker, '<script>\n' + app + '\n</script>')

    with open(OUT, 'w', encoding='utf-8') as fh:
        fh.write(tpl)
    print(f'wrote {OUT}  ({len(pts)} points, dataset {ds_id}, {len(tpl)//1024} KB)')


if __name__ == '__main__':
    main()
