#!/usr/bin/env python
"""Assemble the single-file deployable inspector.

Reads the template (thicket_inspector.html), the app logic (app.js), and the
Round 2 sample points (../analysis/results/sample_points_vegtype2022.csv), and writes a fully
self-contained index.html: points embedded, app.js inlined. No build tooling,
no server -- drop index.html on any static host (GitHub Pages, Netlify).

Run:
    python inspector/build.py
"""
import argparse, csv, hashlib, itertools, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = os.path.join(ROOT, 'analysis', 'results', 'sample_points_vegtype2022.csv')
TPL = os.path.join(HERE, 'thicket_inspector.html')
APP = os.path.join(HERE, 'app.js')
OUT = os.path.join(HERE, 'index.html')
ASSIGNMENTS = os.path.join(HERE, 'assignment_manifest.json')
SYNC_CONFIG = os.path.join(HERE, 'sync_config.json')

# Round 1 submissions are used only to identify and describe determinate
# disagreements. They are not imported as Round 2 labels.
DISAGREEMENT_FILES = {
    'ARP': os.path.join(ROOT, 'thicket_labels_ARP_ARP_2026-07-17-14-19-13.json'),
    'SVM': os.path.join(ROOT, 'thicket_labels_SVM_SVM_2026-07-15-13-41-42.csv'),
    'AP': os.path.join(ROOT, 'thicket_labels_AlastairPotts_AP_2026-07-21-05-04-45.json'),
    'MP': os.path.join(ROOT, 'thicket_labels_MichaelPowell_MP_2026-07-31-10-35-52.json'),
}
VALID_LABELS = {'intact', 'moderate', 'severe', 'transformed', 'nothicket', 'unsure'}


def _integer(value, default=-1):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_points(path=CSV):
    pts = []
    with open(path, newline='', encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh):
            # Keep short keys because every row is embedded into index.html.
            # s = design stratum; m = blind 2022 map class used for map styling.
            model_class = r.get('cls2022_name') or r.get('stratum', '').rsplit('_', 1)[-1]
            pts.append({
                'id': int(r['id']),
                's': r.get('stratum') or model_class,
                'm': model_class,
                'src': r.get('source') or 'new',
                'mc': r.get('mapcode') or '',
                'veg': r.get('vegtype') or '',
                'efg': r.get('efg') or '',
                'c22': _integer(r.get('cls2022', r.get('cls'))),
                'c25': _integer(r.get('cls2025')),
                'lon': round(float(r['lon']), 6),
                'lat': round(float(r['lat']), 6),
            })
    return pts


def dataset_id(pts):
    """Stable fingerprint of the sample draw: id + rounded coords + stratum.

    Changes whenever the point set or its geometry changes, so labels saved
    against one draw can be detected as belonging to a different draw."""
    h = hashlib.sha256()
    for p in pts:
        h.update(f"{p['id']}|{p['lon']:.6f}|{p['lat']:.6f}|{p['s']}\n".encode())
    return h.hexdigest()[:16]


def _load_label_rows(path):
    if path.lower().endswith('.csv'):
        with open(path, newline='', encoding='utf-8-sig') as fh:
            return list(csv.DictReader(fh))
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    return doc.get('labels', [])


def load_disagreements(paths, pts):
    """Return Round 1 points with at least one determinate pairwise conflict.

    This follows analysis/23_area_estimation_final_labeller.py: pairs involving
    ``unsure`` are excluded from the disagreement definition.
    """
    valid_ids = {p['id'] for p in pts}
    by_id = {}
    for code, path in paths.items():
        if not os.path.exists(path):
            raise FileNotFoundError(f'disagreement source not found: {path}')
        for row in _load_label_rows(path):
            try:
                point_id = int(row['id'])
            except (KeyError, TypeError, ValueError):
                continue
            label = str(row.get('label', '')).strip().lower()
            if point_id in valid_ids and label in VALID_LABELS:
                by_id.setdefault(point_id, {})[code] = label

    result = {}
    for point_id, labels in sorted(by_id.items()):
        pairs = []
        for (code_a, label_a), (code_b, label_b) in itertools.combinations(labels.items(), 2):
            if 'unsure' not in (label_a, label_b) and label_a != label_b:
                pairs.append([code_a, code_b])
        if pairs:
            result[str(point_id)] = {'labels': labels, 'pairs': pairs}
    return result


def load_sync_config(path):
    default = {'enabled_by_default': True, 'endpoint': '', 'sheet_url': ''}
    value = default
    if path and os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            value = json.load(fh)
        if not isinstance(value, dict):
            raise ValueError('sync config must be a JSON object')
    config = {
        'enabled_by_default': bool(value.get('enabled_by_default', True)),
        'endpoint': str(value.get('endpoint', '')).strip(),
        'sheet_url': str(value.get('sheet_url', '')).strip(),
    }
    # Repository variables can configure the deployed build without requiring a
    # campaign-specific URL in source control. These URLs are not secrets.
    config['endpoint'] = os.environ.get('GOOGLE_SHEETS_SYNC_ENDPOINT', '').strip() or config['endpoint']
    config['sheet_url'] = os.environ.get('GOOGLE_SHEET_URL', '').strip() or config['sheet_url']
    return config


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
    parser.add_argument('--sync-config', default=SYNC_CONFIG,
                        help='Google Sheets sync config to embed (default: sync_config.json)')
    parser.add_argument('--out', default=OUT, help='output HTML path')
    args = parser.parse_args(argv)
    pts = load_points()
    pts_js = ',\n'.join(json.dumps(p, separators=(',', ':')) for p in pts)
    ds_id = dataset_id(pts)
    assignments = load_assignments(args.assignments, ds_id, pts)
    disagreements = load_disagreements(DISAGREEMENT_FILES, pts)
    sync_config = load_sync_config(args.sync_config)

    tpl = open(TPL, encoding='utf-8').read()
    app = open(APP, encoding='utf-8').read()

    tpl = tpl.replace('__POINTS__', pts_js)
    tpl = tpl.replace('__DATASET_ID__', ds_id)
    tpl = tpl.replace('__ASSIGNMENTS__', json.dumps(assignments, separators=(',', ':')))
    tpl = tpl.replace('__DISAGREEMENTS__', json.dumps(disagreements, separators=(',', ':')))
    tpl = tpl.replace('__SYNC_CONFIG__', json.dumps(sync_config, separators=(',', ':')))
    # inline app.js in place of the external <script src="app.js"></script>
    marker = '<script src="app.js"></script>'
    assert marker in tpl, 'app.js script tag not found in template'
    tpl = tpl.replace(marker, '<script>\n' + app + '\n</script>')

    with open(args.out, 'w', encoding='utf-8') as fh:
        fh.write(tpl)
    n_new = sum(p['src'] == 'new' for p in pts)
    print(f'wrote {args.out}  ({len(pts)} points, {n_new} Round 2, '
          f'{len(disagreements)} disagreements, dataset {ds_id}, '
          f'{len(assignments.get("labelers", {}))} assignments, {len(tpl)//1024} KB)')


if __name__ == '__main__':
    main()
