#!/usr/bin/env python
"""Assemble the single-file deployable inspector.

Reads the template (thicket_inspector.html), the app logic (app.js), and the
sample points (../analysis/results/sample_points_v4.csv), and writes a fully
self-contained index.html: points embedded, app.js inlined. No build tooling,
no server -- drop index.html on any static host (GitHub Pages, Netlify).

Run:
    python inspector/build.py
"""
import argparse, csv, hashlib, itertools, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = os.path.join(ROOT, 'analysis', 'results', 'sample_points_v4.csv')
TPL = os.path.join(HERE, 'thicket_inspector.html')
APP = os.path.join(HERE, 'app.js')
OUT = os.path.join(HERE, 'index.html')
ASSIGNMENTS = os.path.join(HERE, 'assignment_manifest.json')
LINEAGE = os.path.join(HERE, 'dataset_lineage.json')
SYNC_CONFIG = os.path.join(HERE, 'sync_config.json')
SW = os.path.join(HERE, 'sw.js')
AREA_ESTIMATION = os.path.join(ROOT, 'analysis', 'results', 'area_estimation_vegtype2022.json')

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


def campaign_dataset_id(pts, path=LINEAGE):
    """The fingerprint the CAMPAIGN is keyed by, which may outlive one draw.

    The id keys three things that must survive a mid-campaign top-up: every
    labeller's browser storage (app.js STORAGE_SCOPE), their assignment ids
    (create_assignments.digest), and the Google Sheet upsert key. Minting a new
    one because the draw grew would strand every assignment already in flight --
    labels still in localStorage under a key nothing reads, and their own
    exports refused by the import guard.

    Reusing it is only sound while the draw grows by APPEND. So prove that
    first: the baseline draw must still fingerprint as the pinned id, and every
    one of its points must still be here with the same id, coordinates and
    stratum. Anything else is a re-draw and must not inherit the identity.
    """
    if not path or not os.path.exists(path):
        return dataset_id(pts)
    with open(path, encoding='utf-8') as fh:
        lineage = json.load(fh)
    pinned = lineage['dataset']
    baseline_csv = os.path.join(ROOT, *lineage['baseline_csv'].split('/'))
    baseline = load_points(baseline_csv)
    actual = dataset_id(baseline)
    if actual != pinned:
        raise ValueError(
            f'{lineage["baseline_csv"]} fingerprints as {actual}, not the pinned '
            f'{pinned}. The baseline draw has been edited; the deployed campaign '
            f'no longer matches it and the id must not be reused.')
    here = {p['id']: p for p in pts}
    for b in baseline:
        p = here.get(b['id'])
        if p is None:
            raise ValueError(f'point {b["id"]} of the pinned draw is missing from '
                             f'the current draw: this is a re-draw, not a top-up')
        if (p['lon'], p['lat'], p['s']) != (b['lon'], b['lat'], b['s']):
            raise ValueError(f'point {b["id"]} moved ({b["lon"]},{b["lat"]},{b["s"]}'
                             f' -> {p["lon"]},{p["lat"]},{p["s"]}): this is a '
                             f're-draw, not a top-up')
    return pinned


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


def load_area_estimation(path):
    """Load only the fields needed by the in-app area chart.

    The analysis artifact contains full error matrices and per-stratum details;
    omitting those keeps the single-file inspector compact without changing the
    displayed estimates or their 95% margins of error.
    """
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f'area-estimation artifact not found: {path}')
    with open(path, encoding='utf-8') as fh:
        raw = json.load(fh)

    def metric(value):
        return {
            'area_ha': float(value['area_ha']),
            'moe95_ha': float(value['moe95_ha']),
        }

    scenarios = {}
    for key, scenario in raw.get('scenarios', {}).items():
        groups = {}
        for level in ('by_efg', 'by_vegtype'):
            groups[level] = {}
            for name, group in scenario.get(level, {}).items():
                groups[level][name] = {
                    'area_ha': float(group['area_ha']),
                    'n': int(group['n']),
                    'estimable': bool(group['estimable']),
                    'composition': {
                        cls: metric(value)
                        for cls, value in group.get('composition', {}).items()
                    },
                }
        scenarios[key] = {
            'nothicket_mode': scenario['nothicket_mode'],
            'ref_classes': scenario['ref_classes'],
            'n_used': int(scenario['n_used']),
            'area_total_ha': float(scenario['area_total_ha']),
            'area_covered_ha': float(scenario['area_covered_ha']),
            'area_uncovered_ha': float(scenario['area_uncovered_ha']),
            'strata_total': int(scenario['strata_total']),
            'strata_covered': int(scenario['strata_covered']),
            'strata_without_variance': int(scenario['strata_without_variance']),
            'reference_area': {
                cls: metric(value)
                for cls, value in scenario.get('reference_area', {}).items()
            },
            **groups,
        }
    if not scenarios:
        raise ValueError('area-estimation artifact has no scenarios')
    return {
        'artifact': str(raw.get('artifact', '')),
        'generated_utc': str(raw.get('generated_utc', '')),
        'stratification_year': int(raw['stratification_year']),
        'assessment_year': int(raw['assessment_year']),
        'n_reference_labels': int(raw['n_reference_labels']),
        'n_reference_labels_on_new_points': int(raw['n_reference_labels_on_new_points']),
        'scenarios': scenarios,
    }


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


SW_CACHE_RE = re.compile(r"(const CACHE = 'thicket-inspector-shell-)[^']*(')")


def build_id(html):
    """Fingerprint of the shipped page: changes on ANY build change.

    The draw fingerprint only moves when points move, so an app-only fix leaves
    it untouched. The service worker is what tells an open tab that a new
    version exists, so it has to turn over on every build, not just on a
    re-draw."""
    return hashlib.sha256(html.encode('utf-8')).hexdigest()[:16]


def stamp_service_worker(bid, path=SW):
    """Point the shell cache at this build.

    The service worker keeps an offline copy of the app, so its cache name must
    change whenever the build does -- otherwise `activate` never purges and
    returning visitors keep an older index.html indefinitely. Rewriting in place
    is idempotent: the previous id is replaced, not appended.

    Changing sw.js is also how an already-open tab learns a deploy happened:
    app.js polls the registration, and a byte-different worker installs, claims
    the page and triggers a reload.
    """
    sw = open(path, encoding='utf-8').read()
    stamped, n = SW_CACHE_RE.subn(lambda m: m.group(1) + bid + m.group(2), sw)
    assert n == 1, f'expected exactly one CACHE constant in {path}, found {n}'
    if stamped != sw:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(stamped)
    return n


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assignments', default=ASSIGNMENTS,
                        help='assignment manifest to embed (default: assignment_manifest.json)')
    parser.add_argument('--sync-config', default=SYNC_CONFIG,
                        help='Google Sheets sync config to embed (default: sync_config.json)')
    parser.add_argument('--area-estimates', default=AREA_ESTIMATION,
                        help='area-estimation JSON to embed in compact form')
    parser.add_argument('--out', default=OUT, help='output HTML path')
    args = parser.parse_args(argv)
    pts = load_points()
    pts_js = ',\n'.join(json.dumps(p, separators=(',', ':')) for p in pts)
    draw_id = dataset_id(pts)
    ds_id = campaign_dataset_id(pts)
    assignments = load_assignments(args.assignments, ds_id, pts)
    # Provenance: which exact draw this build shipped. Inert in the app (it reads
    # only `campaign` and `labelers`), but it keeps the draw recoverable once the
    # campaign id stops tracking it.
    assignments['draw'] = draw_id
    disagreements = load_disagreements(DISAGREEMENT_FILES, pts)
    sync_config = load_sync_config(args.sync_config)
    area_estimation = load_area_estimation(args.area_estimates)

    tpl = open(TPL, encoding='utf-8').read()
    app = open(APP, encoding='utf-8').read()

    tpl = tpl.replace('__POINTS__', pts_js)
    tpl = tpl.replace('__DATASET_ID__', ds_id)
    tpl = tpl.replace('__ASSIGNMENTS__', json.dumps(assignments, separators=(',', ':')))
    tpl = tpl.replace('__DISAGREEMENTS__', json.dumps(disagreements, separators=(',', ':')))
    tpl = tpl.replace('__SYNC_CONFIG__', json.dumps(sync_config, separators=(',', ':')))
    tpl = tpl.replace('__AREA_ESTIMATION__', json.dumps(area_estimation, separators=(',', ':')))
    # inline app.js in place of the external <script src="app.js"></script>
    marker = '<script src="app.js"></script>'
    assert marker in tpl, 'app.js script tag not found in template'
    tpl = tpl.replace(marker, '<script>\n' + app + '\n</script>')

    with open(args.out, 'w', encoding='utf-8') as fh:
        fh.write(tpl)
    bid = build_id(tpl)
    stamp_service_worker(bid)
    n_new = sum(p['src'] == 'new' for p in pts)
    print(f'wrote {args.out}  ({len(pts)} points, {n_new} unlabelled, '
          f'{len(disagreements)} disagreements, dataset {ds_id}'
          f'{"" if draw_id == ds_id else f" (pinned; this draw is {draw_id})"}, '
          f'{len(assignments.get("labelers", {}))} assignments, {len(tpl)//1024} KB)')
    print(f'stamped {SW} shell cache -> thicket-inspector-shell-{bid}')


if __name__ == '__main__':
    main()
