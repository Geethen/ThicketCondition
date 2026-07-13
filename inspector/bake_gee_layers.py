#!/usr/bin/env python
"""Bake Earth Engine Sentinel-2 composites into a keyless tile manifest.

Runs Earth Engine *once* (locally or in CI), computes cloud-masked S2 median
composites for a handful of years, and calls ``getMapId`` on each. The resulting
public XYZ tile-URL templates are written to ``gee_layers.json``, which the static
inspector loads as ordinary raster basemaps.

Why this shape: a service-account private key must NEVER reach the browser (a
static page on GitHub Pages is world-readable). The key does its privileged work
here — server-side / in CI — and only the resulting keyless, temporary tile URLs
travel to the page. See the GitHub Actions workflow that runs this on a schedule
so the tokens stay fresh.

Auth (in order of preference):
  * Service account   — set EE_SA_KEY_JSON (the JSON key *contents*, e.g. a GitHub
                        Actions secret) or EE_SA_KEY_FILE (path to the key file),
                        plus EE_SA_EMAIL.
  * User credentials  — otherwise falls back to ee.Initialize(project=EE_PROJECT),
                        i.e. your normal `earthengine authenticate` login. Handy
                        for a local run.

Run locally (geo env):
    C:\\Users\\geethen.singh\\.pixi\\envs\\geo\\python.exe inspector/bake_gee_layers.py
"""
import datetime
import json
import os
import sys

import ee

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CSV = os.path.join(HERE, '..', 'analysis', 'results', 'sample_points.csv')
OUT = os.path.join(HERE, 'gee_layers.json')

EE_PROJECT = os.environ.get('EE_PROJECT', 'ee-gsingh')

# Which S2 composites to bake. Each becomes a selectable imagery source in the app.
# Keep this small — every entry is a getMapId call and a basemap button.
COMPOSITES = [
    {'id': 's2_2024', 'name': 'Sentinel-2 2024', 'year': 2024,
     'start': '2024-01-01', 'end': '2024-12-31'},
    {'id': 's2_2020', 'name': 'Sentinel-2 2020', 'year': 2020,
     'start': '2020-01-01', 'end': '2020-12-31'},
    {'id': 's2_2018', 'name': 'Sentinel-2 2018', 'year': 2018,
     'start': '2018-01-01', 'end': '2018-12-31'},
]

# True-colour visualisation for S2 surface reflectance (scaled 0-10000).
VIS = {'bands': ['B4', 'B3', 'B2'], 'min': 200, 'max': 2500, 'gamma': 1.1}


def init_ee():
    """Initialise EE with a service account if provided, else user creds."""
    key_json = os.environ.get('EE_SA_KEY_JSON')
    key_file = os.environ.get('EE_SA_KEY_FILE')
    sa_email = os.environ.get('EE_SA_EMAIL')

    if key_json or key_file:
        tmp_key = None   # a temp file we created and must delete afterwards
        try:
            if key_json and not key_file:
                # Materialise the secret to a temp file for ServiceAccountCredentials.
                import tempfile
                fd, key_file = tempfile.mkstemp(suffix='.json')
                tmp_key = key_file
                with os.fdopen(fd, 'w') as fh:
                    fh.write(key_json)
            if not sa_email:
                # Derive the account email from the key file if not given explicitly.
                with open(key_file) as fh:
                    sa_email = json.load(fh).get('client_email')
            creds = ee.ServiceAccountCredentials(sa_email, key_file)
            ee.Initialize(creds, project=EE_PROJECT)
            print(f'EE initialised via service account {sa_email} (project {EE_PROJECT})')
        finally:
            # Never leave the private key on disk once creds are constructed.
            if tmp_key and os.path.exists(tmp_key):
                os.remove(tmp_key)
    else:
        ee.Initialize(project=EE_PROJECT)
        print(f'EE initialised via user credentials (project {EE_PROJECT})')


def aoi_from_points():
    """Bounding box (with a small margin) around the sample points."""
    import csv
    lons, lats = [], []
    with open(SAMPLE_CSV, newline='') as fh:
        for r in csv.DictReader(fh):
            lons.append(float(r['lon']))
            lats.append(float(r['lat']))
    m = 0.15  # ~15 km margin in degrees
    w, e = min(lons) - m, max(lons) + m
    s, n = min(lats) - m, max(lats) + m
    return ee.Geometry.Rectangle([w, s, e, n]), [w, s, e, n]


def s2_composite(aoi, start, end):
    """Cloud-masked S2 SR median over [start, end] clipped to the AOI."""
    def mask(img):
        scl = img.select('SCL')
        # keep vegetation/soil/water/unclassified; drop cloud, shadow, snow, cirrus
        good = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9))
                .And(scl.neq(10)).And(scl.neq(11)))
        return img.updateMask(good)

    col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
           .filterBounds(aoi)
           .filterDate(start, end)
           .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
           .map(mask))
    return col.median().select(['B4', 'B3', 'B2']).clip(aoi)


def main():
    init_ee()
    aoi, bbox = aoi_from_points()
    layers = []
    for c in COMPOSITES:
        img = s2_composite(aoi, c['start'], c['end'])
        mp = img.getMapId(VIS)
        url = mp['tile_fetcher'].url_format
        layers.append({'id': c['id'], 'name': c['name'], 'year': c['year'],
                       'tiles': [url], 'attribution': 'Copernicus Sentinel-2 (ESA) via Google Earth Engine',
                       'max': 18})
        print(f"baked {c['id']}: {url[:70]}...")

    manifest = {
        'tool': 'thicket_inspector_gee',
        'baked': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'note': 'Earth Engine getMapId tile URLs are temporary; re-bake on a schedule.',
        'bbox': bbox,
        'layers': layers,
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2)
    print(f'wrote {OUT} ({len(layers)} layers)')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # surface a clear failure in CI logs
        print('bake failed:', e, file=sys.stderr)
        raise
