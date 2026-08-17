#!/usr/bin/env python
"""Re-allocate the reference sample so every vegetation type carries all FOUR
classes -- intact / moderate / severe / **transformed** -- at reportable precision.

Why this supersedes 26_augment_vegtype_2022strat.py
---------------------------------------------------
Script 26 drew 1,252 new points area-proportionally across the 54 thicket cells.
That is right for the national estimate and wrong for per-type estimates: AT49
and AT32 end up with 257 and 162 new points while several small types sit at the
floor. It also ignores the transformed class entirely -- the `-1` pixels in the
2022 probability tifs were never in the sampling frame, so ~95,000 ha of the
biome footprint had zero chance of selection.

Because those 1,252 are drawn but NOT labelled, they are still free to move. This
script re-solves the allocation over all 72 (vegetation type x class) strata,
keeps whatever part of the existing draw fits the new plan, and tops up the rest.

Allocation
----------
  transformed strata : ~300 points spread PROPORTIONAL to transformed area, i.e.
                       self-weighting within the transformed domain. The
                       transformed row of the confusion matrix is a property of
                       the land-cover mask, not of vegetation, so it is estimated
                       well pooled; forcing an equal quota per type would cost
                       ~800 points and buy almost nothing (density would range
                       8-462 ha/pt, Kish deff 1.95 within the domain alone).
  thicket strata     : the remaining budget by greedy minimax -- each increment
                       goes to the type whose WORST class interval is currently
                       widest, and within that type to the stratum that shrinks
                       it most. Capped per type so a 1,460 ha type cannot eat the
                       budget (AT15 would otherwise take ~350 points).

The allocation depends on stratum AREAS and on a confusion matrix estimated from
the already-returned Round 1 labels. That makes it a two-phase design in the
Neyman sense; the phase-1 labels are re-used in estimation, so report variances
with the standard stratified formula and treat the allocation as fixed.

Sampling mechanics
------------------
No Earth Engine. The 2022 and 2025 class surfaces are read straight from the
local tifs -- verified to reproduce the stored `cls2022` and `cls2025` for all
2,098 existing points (2098/2098 agreement, both years). Candidates are drawn
uniformly inside each vegetation-type polygon from the GDB geometry and kept when
they land on the target class, which is a stratified random draw of that cell.

Point IDs are stable: the 846 Round 1 points keep 0-845, retained Round 2 points
keep their 846-2097 ids, and only genuinely new draws get fresh ids from 2098.
Labels already keyed to an id therefore stay valid.

Run:
  python -u analysis/28_redesign_4class.py --budget 900 --seed 42

Outputs:
  analysis/results/sample_points_v3.csv        (feeds inspector/build.py)
  analysis/results/sample_points_v3.geojson
  analysis/results/sample_design_v3.json
  analysis/results/transformed_area_by_vegtype.json   (cache)
"""
import argparse
import collections
import csv as csvmod
import glob
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault('GDAL_DATA',
                      r'C:\Users\geethen.singh\.pixi\envs\geo\Library\share\gdal')

import pandas as pd
import pyogrio
import rasterio
import shapely
from rasterio.features import rasterize
from rasterio.windows import Window
from shapely.geometry import Point

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RESULTS = HERE / 'results'

GDB = f'/vsizip/{ROOT.as_posix()}/BioregionsSimple.gdb.zip/BioregionsSimple.gdb'
VEG_LAYER = 'IEM_5_13_4_1_260326VegetationTypesL5'
VEG_CSV = ROOT / 'Solid_Thicket_condition_by_vegetation_type_2025_solid_EFGs_D3_preliminary.csv'
TIF22 = sorted(ROOT.glob('RF_probability_surfaces_solid_thicket_2022-*.tif'))
TIF25 = sorted(ROOT.glob('RF_prob_surfaces_solid_thicket_2025-*.tif'))
PREV_CSV = RESULTS / 'sample_points_vegtype2022.csv'
PREV_DESIGN = RESULTS / 'sample_design_vegtype2022.json'
TRANS_CACHE = RESULTS / 'transformed_area_by_vegtype.json'

STRATA = ['intact', 'moderate', 'severe', 'transformed']
CLS_ID = {'intact': 0, 'moderate': 1, 'severe': 2, 'transformed': -1}
ID_CLS = {v: k for k, v in CLS_ID.items()}
EFG_OF_FVG = {'Arid Thicket': 1, 'Valley Thicket': 2, 'Mesic Thicket': 3}
EFG_NAME = {1: 'AridThicket', 2: 'ValleyThicket', 3: 'MesicThicket'}
Z = 1.959964
R_EARTH = 6378137.0

# Confusion matrix P(reference = k | 2022 stratum h), from the 846 Round 1 points
# (majority vote over four labellers; `nothicket` and `unsure` are errors). The
# transformed row has no observations yet and is a stated prior -- see README.
REF_CLASSES = STRATA + ['nothicket']
_RAW = {
    'intact':   dict(intact=152, moderate=57, severe=6, transformed=3, nothicket=13),
    'moderate': dict(intact=30, moderate=118, severe=78, transformed=9, nothicket=28),
    'severe':   dict(intact=5, moderate=36, severe=224, transformed=11, nothicket=45),
}
P_REF = {h: {k: d[k] / sum(d.values()) for k in REF_CLASSES} for h, d in _RAW.items()}
P_REF['transformed'] = dict(intact=.02, moderate=.03, severe=.08,
                            transformed=.85, nothicket=.02)
PVAR = {h: np.array([P_REF[h][k] * (1 - P_REF[h][k]) for k in STRATA]) for h in STRATA}


# ------------------------------------------------------------------ geometry
def load_vegtypes():
    veg = pd.read_csv(VEG_CSV)
    codes = list(veg['T_MAPCODE'])
    g = pyogrio.read_dataframe(GDB, layer=VEG_LAYER, columns=['T_MAPCODE'])
    g['T_MAPCODE'] = [('' if v is None or (isinstance(v, float) and v != v)
                       else str(v)).strip() for v in g['T_MAPCODE'].tolist()]
    g = g[g['T_MAPCODE'].isin(codes)].copy()
    g4326 = g.to_crs(4326)
    diss = g4326.dissolve(by='T_MAPCODE')['geometry']
    meta = {r['T_MAPCODE']: dict(vegtype=r['vegtype_name'],
                                 efg_id=EFG_OF_FVG[r['RevisedFVG']],
                                 efg=EFG_NAME[EFG_OF_FVG[r['RevisedFVG']]])
            for _, r in veg.iterrows()}
    return diss, meta, codes, g4326


def transformed_area_by_vegtype(g4326, codes, refresh=False):
    """Hectares of transformed (-1) pixel per vegetation type, from the 2022 tifs."""
    if TRANS_CACHE.exists() and not refresh:
        return {k: float(v) for k, v in json.load(open(TRANS_CACHE)).items()}
    ix = {c: i + 1 for i, c in enumerate(codes)}
    shapes = [(geom, ix[mc]) for geom, mc in
              zip(g4326.geometry.values, g4326['T_MAPCODE'].values)]
    out = {c: 0.0 for c in codes}
    for f in TIF22:
        with rasterio.open(f) as src:
            T = src.transform
            dx, dy = T.a, -T.e
            for r0 in range(0, src.height, 512):
                h = min(512, src.height - r0)
                win = Window(0, r0, src.width, h)
                a = src.read([1, 3, 4], window=win).astype(np.float32)
                pi, ps, efg = a
                good = np.isfinite(efg) & (efg != -9999) & (efg > 0)
                tr = good & (pi <= -0.5) & (ps <= -0.5)
                if not tr.any():
                    continue
                vt = rasterize(shapes, out_shape=(h, src.width),
                               transform=src.window_transform(win),
                               fill=0, dtype='uint8')
                lat = T.f + (r0 + np.arange(h) + 0.5) * T.e
                px = np.repeat((((math.radians(dx) * R_EARTH)
                                 * (math.radians(dy) * R_EARTH * np.cos(np.radians(lat))))
                                / 1e4)[:, None], src.width, axis=1)
                for c in codes:
                    m = tr & (vt == ix[c])
                    if m.any():
                        out[c] += float(px[m].sum())
        print(f'  scanned {f.name}', flush=True)
    TRANS_CACHE.write_text(json.dumps(out, indent=1))
    return out


# ------------------------------------------------------------- raster lookup
class ClassGrid:
    """2022 and 2025 condition class at arbitrary lon/lat, straight from the tifs."""

    def __init__(self):
        self.s22 = [rasterio.open(f) for f in TIF22]
        self.s25 = [rasterio.open(f) for f in TIF25]

    @staticmethod
    def _pick(srcs, lon, lat):
        for s in srcs:
            b = s.bounds
            if b.left <= lon <= b.right and b.bottom <= lat <= b.top:
                return s
        return None

    def cls22(self, xy):
        out = np.full(len(xy), -99, dtype=int)
        for s in self.s22:
            b = s.bounds
            sel = np.where((xy[:, 0] >= b.left) & (xy[:, 0] <= b.right)
                           & (xy[:, 1] >= b.bottom) & (xy[:, 1] <= b.top)
                           & (out == -99))[0]
            if not len(sel):
                continue
            vals = np.array(list(s.sample(xy[sel], indexes=[1, 2, 3])), dtype=np.float32)
            ok = np.all(np.isfinite(vals), axis=1) & (vals[:, 0] != -9999)
            trans = ok & (vals[:, 0] <= -0.5)
            nat = ok & ~trans
            res = np.full(len(sel), -99, dtype=int)
            res[trans] = -1
            if nat.any():
                res[nat] = np.argmax(vals[nat], axis=1)
            out[sel] = res
        return out

    def cls25(self, xy):
        out = np.full(len(xy), -99, dtype=int)
        for s in self.s25:
            b = s.bounds
            sel = np.where((xy[:, 0] >= b.left) & (xy[:, 0] <= b.right)
                           & (xy[:, 1] >= b.bottom) & (xy[:, 1] <= b.top)
                           & (out == -99))[0]
            if not len(sel):
                continue
            vals = np.array(list(s.sample(xy[sel], indexes=[5])), dtype=np.float32).ravel()
            res = np.full(len(sel), -99, dtype=int)
            good = np.isfinite(vals)
            res[good] = vals[good].astype(int)
            out[sel] = res
        return out


def uniform_points_in(geom, n, rng):
    minx, miny, maxx, maxy = geom.bounds
    xs, ys = [], []
    have = 0
    while have < n:
        k = int((n - have) * 3) + 128
        px = rng.uniform(minx, maxx, k)
        py = rng.uniform(miny, maxy, k)
        inside = shapely.contains_xy(geom, px, py)
        xs.append(px[inside])
        ys.append(py[inside])
        have = sum(len(a) for a in xs)
    return np.column_stack([np.concatenate(xs)[:n], np.concatenate(ys)[:n]])


# ------------------------------------------------------------------ allocate
def allocate(A, fixed, budget, n_trans, cap_small, cap_big, small_ha):
    """Greedy minimax on each type's worst-class interval half-width.

    A[v]     : np.array of 4 stratum areas (ha)
    fixed[v] : np.array of 4 already-labelled counts (cannot be given back)
    """
    types = list(A)
    Wt = {v: A[v] / A[v].sum() for v in types}

    At = {v: A[v][3] for v in types}
    pool = sum(a for a in At.values() if a >= 300)
    n = {}
    for v in types:
        b = np.maximum(fixed[v].astype(float), 4.0)
        b[3] = max(5.0, round(n_trans * At[v] / pool)) if At[v] >= 300 else 0.0
        for j in range(3):
            if A[v][j] <= 1.0:
                b[j] = 0.0
        n[v] = b

    def worst(v, nv):
        var = np.zeros(4)
        for j, h in enumerate(STRATA):
            if A[v][j] <= 1.0 or nv[j] < 1:
                continue
            var += (Wt[v][j] ** 2) * PVAR[h] / nv[j]
        return Z * math.sqrt(var.max())

    cap = {v: (cap_small if A[v].sum() < small_ha else cap_big) for v in types}
    spent = sum(int((n[v] - fixed[v]).clip(0).sum()) for v in types)
    step = 4
    while spent + step <= budget:
        live = [v for v in types if n[v].sum() + step <= cap[v]]
        if not live:
            break
        v = max(live, key=lambda t: worst(t, n[t]))
        j = min((k for k in range(3) if A[v][k] > 1.0),
                key=lambda k: worst(v, n[v] + step * np.eye(4)[k]))
        n[v][j] += step
        spent += step
    return {v: n[v].astype(int) for v in types}, spent


def precision(A, n):
    """95% CI half-width on each class's area share, per type."""
    W = A / A.sum()
    var = np.zeros(4)
    for j, h in enumerate(STRATA):
        if A[j] <= 1.0 or n[j] < 1:
            continue
        var += (W[j] ** 2) * PVAR[h] / n[j]
    share = np.zeros(4)
    for j, h in enumerate(STRATA):
        if A[j] > 1.0:
            share += W[j] * np.array([P_REF[h][k] for k in STRATA])
    return share, Z * np.sqrt(var)


# ---------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--budget', type=int, default=900,
                    help='NEW labels beyond the 1,252 already drawn (default 900)')
    ap.add_argument('--n-transformed', type=int, default=300)
    ap.add_argument('--cap-big', type=int, default=220)
    ap.add_argument('--cap-small', type=int, default=90)
    ap.add_argument('--small-ha', type=float, default=6000.0)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--refresh-areas', action='store_true')
    args = ap.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    RESULTS.mkdir(exist_ok=True)

    print('== vegetation types ==', flush=True)
    diss, meta, codes, g4326 = load_vegtypes()
    print(f'  {len(codes)} types, {len(g4326)} polygons', flush=True)

    print('== transformed area per type (2022 tifs) ==', flush=True)
    trans_ha = transformed_area_by_vegtype(g4326, codes, args.refresh_areas)
    print(f'  {sum(trans_ha.values()):,.0f} ha transformed inside the 18 types', flush=True)

    prev_design = json.load(open(PREV_DESIGN))
    area_cell = prev_design['area_ha']
    A = {v: np.array([area_cell.get(f'{v}_{c}', 0.0) for c in STRATA[:3]]
                     + [trans_ha.get(v, 0.0)]) for v in codes}

    prev = list(csvmod.DictReader(open(PREV_CSV, encoding='utf-8-sig')))
    existing = [r for r in prev if r['source'] == 'existing']
    drawn = [r for r in prev if r['source'] != 'existing']
    fixed = {v: np.zeros(4, dtype=int) for v in codes}
    for r in existing:
        mc = r['mapcode']
        if mc in fixed:
            fixed[mc][STRATA.index(r['cls2022_name'])] += 1
    print(f'== previous sample: {len(existing)} labelled, {len(drawn)} drawn-unlabelled ==')

    print('== allocation ==', flush=True)
    plan, spent = allocate(A, fixed, 1252 + args.budget, args.n_transformed,
                           args.cap_small, args.cap_big, args.small_ha)
    total = sum(int(plan[v].sum()) for v in codes)
    print(f'  budget {1252 + args.budget}, allocated {spent}, total sample {total}')

    # ---- how much of the existing draw survives
    drawn_by = collections.defaultdict(list)
    for r in drawn:
        drawn_by[(r['mapcode'], r['cls2022_name'])].append(r)
    keep_rows, need = [], collections.Counter()
    n_reuse = n_discard = 0
    for v in codes:
        for j, c in enumerate(STRATA):
            want = int(plan[v][j]) - int(fixed[v][j])
            if want < 0:
                want = 0
            pool = drawn_by.get((v, c), [])
            take = min(len(pool), want)
            if take:
                idx = rng.permutation(len(pool))[:take]
                keep_rows.extend(pool[i] for i in idx)
            n_reuse += take
            n_discard += len(pool) - take
            if want - take:
                need[(v, c)] = want - take
    print(f'  of the {len(drawn)} drawn: {n_reuse} reused, {n_discard} discarded')
    print(f'  still to draw: {sum(need.values())} '
          f'({sum(k for (v, c), k in need.items() if c == "transformed")} transformed)')

    # ---- draw the shortfall straight from the tifs
    print('== drawing new points ==', flush=True)
    grid = ClassGrid()
    new_rows = []
    next_id = max(int(r['id']) for r in prev) + 1
    for v in codes:
        wants = {c: need.get((v, c), 0) for c in STRATA}
        if not sum(wants.values()):
            continue
        geom = diss[v]
        got = {c: [] for c in STRATA}
        tries = 0
        while any(len(got[c]) < wants[c] for c in STRATA) and tries < 40:
            short = sum(max(0, wants[c] - len(got[c])) for c in STRATA)
            cand = uniform_points_in(geom, max(400, short * 40), rng)
            cc = grid.cls22(cand)
            for c in STRATA:
                if len(got[c]) >= wants[c]:
                    continue
                hit = cand[cc == CLS_ID[c]]
                room = wants[c] - len(got[c])
                got[c].extend(map(tuple, hit[:room]))
            tries += 1
        for c in STRATA:
            if len(got[c]) < wants[c]:
                print(f'  ! {v}_{c}: only {len(got[c])}/{wants[c]} found '
                      f'(stratum may be too small)', flush=True)
            for lon, lat in got[c]:
                new_rows.append(dict(mapcode=v, cls=c, lon=lon, lat=lat))
        print(f'  {v}: ' + ' '.join(f'{c[:3]}={len(got[c])}' for c in STRATA
                                    if wants[c]), flush=True)

    if new_rows:
        xy = np.array([[r['lon'], r['lat']] for r in new_rows])
        c25 = grid.cls25(xy)
        for r, k in zip(new_rows, c25):
            r['cls2025'] = int(k)

    # ---- assemble the output table
    def row(rid, source, rnd, mc, cls22, lon, lat, cls25, prev_row=None):
        # 21 Round 1 points fall outside the 18 vegetation-type polygons. They keep
        # their Round 1 label and stay in the file for the national estimate, but
        # they belong to no per-type stratum, so their type fields stay blank.
        if mc not in meta:
            p = prev_row or {}
            return dict(id=rid, source=source, round=rnd, mapcode='',
                        vegtype=p.get('vegtype', ''), efg=p.get('efg', ''),
                        efg_id=p.get('efg_id', ''),
                        cls2022=CLS_ID[cls22], cls2022_name=cls22,
                        cls2025=cls25, cls2025_name=ID_CLS.get(cls25, 'unknown'),
                        stratum=f'outside_{cls22}',
                        lon=round(float(lon), 6), lat=round(float(lat), 6))
        m = meta[mc]
        return dict(id=rid, source=source, round=rnd, mapcode=mc,
                    vegtype=m['vegtype'], efg=m['efg'], efg_id=m['efg_id'],
                    cls2022=CLS_ID[cls22], cls2022_name=cls22,
                    cls2025=cls25,
                    cls2025_name=ID_CLS.get(cls25, 'unknown'),
                    stratum=f'{mc}_{cls22}',
                    lon=round(float(lon), 6), lat=round(float(lat), 6))

    out = []
    for r in existing:
        out.append(row(int(r['id']), 'existing', 1, r['mapcode'], r['cls2022_name'],
                       r['lon'], r['lat'], int(r['cls2025']), r))
    for r in keep_rows:
        out.append(row(int(r['id']), 'new', 2, r['mapcode'], r['cls2022_name'],
                       r['lon'], r['lat'], int(r['cls2025']), r))
    for r in new_rows:
        out.append(row(next_id, 'new', 3, r['mapcode'], r['cls'],
                       r['lon'], r['lat'], r['cls2025']))
        next_id += 1
    out.sort(key=lambda d: d['id'])

    cols = ['id', 'source', 'round', 'mapcode', 'vegtype', 'efg', 'efg_id',
            'cls2022', 'cls2022_name', 'cls2025', 'cls2025_name', 'stratum',
            'lon', 'lat']
    with open(RESULTS / 'sample_points_v3.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csvmod.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)

    gj = dict(type='FeatureCollection', features=[
        dict(type='Feature', geometry=dict(type='Point', coordinates=[d['lon'], d['lat']]),
             properties=d) for d in out])
    (RESULTS / 'sample_points_v3.geojson').write_text(json.dumps(gj))

    # ---- design record + projected precision
    achieved = {}
    for v in codes:
        share, ci = precision(A[v], plan[v].astype(float))
        achieved[v] = {
            'area_ha': float(A[v].sum()),
            'efg': meta[v]['efg'],
            'n_total': int(plan[v].sum()),
            'n_by_stratum': {c: int(plan[v][j]) for j, c in enumerate(STRATA)},
            'share_pct': {c: round(100 * share[j], 2) for j, c in enumerate(STRATA)},
            'ci95_pp': {c: round(100 * ci[j], 2) for j, c in enumerate(STRATA)},
            'ci95_relative_pct': {
                c: (round(100 * ci[j] / share[j]) if share[j] > 0 else None)
                for j, c in enumerate(STRATA)},
        }
    counts = collections.Counter(d['round'] for d in out)
    design = {
        'method': 'stratified random; strata = vegetation type x {intact, moderate, '
                  'severe, transformed}; transformed domain self-weighting, thicket '
                  'domain greedy-minimax on the per-type worst-class interval',
        'supersedes': 'sample_design_vegtype2022.json',
        'stratification_year': 2022,
        'assessment_year': 2025,
        'seed': args.seed,
        'budget_new_labels_beyond_round2': args.budget,
        'n_transformed_target': args.n_transformed,
        'cap_big': args.cap_big, 'cap_small': args.cap_small, 'small_ha': args.small_ha,
        'n_total': len(out),
        'n_round1_labelled': counts[1],
        'n_round2_retained': counts[2],
        'n_round3_new_draws': counts[3],
        'n_round2_discarded': n_discard,
        'n_unlabelled_to_do': counts[2] + counts[3],
        'area_ha': {f'{v}_{c}': float(A[v][j]) for v in codes
                    for j, c in enumerate(STRATA) if A[v][j] > 0},
        'area_total_ha': float(sum(A[v].sum() for v in codes)),
        'confusion_prior': P_REF,
        'confusion_note': 'intact/moderate/severe rows are the Round 1 majority-vote '
                          'confusion (nothicket and unsure counted as errors); the '
                          'transformed row is a prior (UA 0.85) -- no point has ever '
                          'been drawn in that stratum.',
        'per_type': achieved,
    }
    (RESULTS / 'sample_design_v3.json').write_text(json.dumps(design, indent=1))

    print()
    print(f'wrote sample_points_v3.csv  ({len(out)} points: '
          f'{counts[1]} labelled / {counts[2]} retained / {counts[3]} newly drawn)')
    print(f'wrote sample_points_v3.geojson, sample_design_v3.json')
    rel = np.array([[achieved[v]['ci95_relative_pct'][c] or 999 for c in STRATA]
                    for v in codes])
    print(f'projected: worst per-type per-class CI '
          f'{max(max(achieved[v]["ci95_pp"].values()) for v in codes):.1f} pp; '
          f'{(rel <= 33).sum()}/72 cells within +/-33%')


if __name__ == '__main__':
    main()
