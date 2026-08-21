#!/usr/bin/env python
"""Round 4: add 1,200 points to the v3 design WITHOUT moving anything already drawn.

How this differs from 28_redesign_4class.py
-------------------------------------------
Script 28 was free to discard 531 of the Round 2 draws because nobody had
labelled them yet. That freedom is gone: all 2,997 v3 points are live in the
inspector and four labellers are working through 2,151 of them. So this is a
pure top-up -- every v3 row is copied through byte-identical (same id, same
coordinates, same stratum) and 1,200 genuinely new points are appended with
fresh ids from 3528.

That append-only property is what lets inspector/build.py keep the campaign's
dataset fingerprint (6bda2aff9e69194f) instead of minting a new one. The
fingerprint keys the labellers' browser storage, their assignment ids, and the
Google Sheet upsert key, so holding it fixed is what keeps four in-flight
assignments intact. build.py re-derives and re-checks the property; it does not
take this script's word for it.

Allocation
----------
Same objective as Round 3 -- greedy minimax on each vegetation type's WORST
reference-class interval -- but seeded with the v3 counts as an untouchable
floor rather than with the labelled counts:

  transformed strata : +150, proportional to transformed area (self-weighting
                       within the transformed domain, exactly as Round 3). Left
                       out entirely, the greedy wastes ~220 points on AT22
                       chasing a transformed-driven interval that thicket points
                       cannot move.
  thicket strata     : the remaining 1,050 by greedy minimax, capped at 330 per
                       type (150 for types under 6,000 ha) so a small type
                       cannot absorb the budget.

Projected effect: worst per-type worst-class 95% CI 6.5pp -> 5.4pp, and 53/72
-> 57/72 (type x class) cells inside +/-33% relative. The gain is modest
because precision goes as 1/sqrt(n) and 2,976 -> 4,176 is only 1.18x.

Run:
  python -u analysis/29_topup_r4.py --budget 1200 --seed 43

Outputs:
  analysis/results/sample_points_v4.csv       (feeds inspector/build.py)
  analysis/results/sample_points_v4.geojson
  analysis/results/sample_design_v4.json
"""
import argparse
import collections
import csv as csvmod
import importlib.util
import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RESULTS = HERE / 'results'
PREV_CSV = RESULTS / 'sample_points_v3.csv'
PREV_DESIGN = RESULTS / 'sample_design_v3.json'
TRANS_CACHE = RESULTS / 'transformed_area_by_vegtype.json'

# The Round 3 script owns the geometry, the raster class lookup and the vegetation
# type metadata. Import it rather than restating any of it; a second copy of the
# sampling mechanics is a second thing that can drift.
_spec = importlib.util.spec_from_file_location('r3', HERE / '28_redesign_4class.py')
r3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r3)

STRATA, CLS_ID, ID_CLS, Z = r3.STRATA, r3.CLS_ID, r3.ID_CLS, r3.Z


# ------------------------------------------------------------------ allocate
def topup(A, cur, budget, n_trans, cap_big, cap_small, small_ha, step=4):
    """Greedy minimax on each type's worst reference-class interval half-width.

    A[v]   : np.array of 4 stratum areas (ha)
    cur[v] : np.array of 4 counts already drawn -- a floor, never given back
    """
    types = list(A)
    W = {v: A[v] / A[v].sum() for v in types}
    PVAR = r3.PVAR

    def worst(v, nv):
        var = np.zeros(4)
        for j, h in enumerate(STRATA):
            if A[v][j] <= 1.0 or nv[j] < 1:
                continue
            var += (W[v][j] ** 2) * PVAR[h] / nv[j]
        return Z * math.sqrt(var.max())

    n = {v: cur[v].astype(float).copy() for v in types}

    # transformed first: proportional to transformed area, over the types that
    # actually have a transformed domain worth sampling
    At = {v: A[v][3] for v in types}
    pool = sum(a for a in At.values() if a >= 300)
    spent = 0
    if n_trans:
        for v in types:
            if At[v] >= 300:
                add = int(round(n_trans * At[v] / pool))
                n[v][3] += add
                spent += add

    cap = {v: (cap_small if A[v].sum() < small_ha else cap_big) for v in types}
    while spent + step <= budget:
        live = [v for v in types if n[v].sum() + step <= cap[v]]
        if not live:
            break
        v = max(live, key=lambda t: worst(t, n[t]))
        j = min((k for k in range(3) if A[v][k] > 1.0),
                key=lambda k: worst(v, n[v] + step * np.eye(4)[k]))
        n[v][j] += step
        spent += step
    return {v: n[v].astype(int) for v in types}, spent, worst


# ---------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--budget', type=int, default=1200, help='new points to add')
    ap.add_argument('--n-transformed', type=int, default=150)
    ap.add_argument('--cap-big', type=int, default=330)
    ap.add_argument('--cap-small', type=int, default=150)
    ap.add_argument('--small-ha', type=float, default=6000.0)
    ap.add_argument('--seed', type=int, default=43)
    args = ap.parse_args(argv)

    rng = np.random.default_rng(args.seed)

    print('== vegetation types ==', flush=True)
    diss, meta, codes, _ = r3.load_vegtypes()
    print(f'  {len(codes)} types', flush=True)

    prev_design = json.load(open(PREV_DESIGN))
    trans_ha = {k: float(v) for k, v in json.load(open(TRANS_CACHE)).items()}
    area_cell = prev_design['area_ha']
    A = {v: np.array([area_cell.get(f'{v}_{c}', 0.0) for c in STRATA[:3]]
                     + [trans_ha.get(v, 0.0)]) for v in codes}

    prev = list(csvmod.DictReader(open(PREV_CSV, encoding='utf-8-sig')))
    cur = {v: np.zeros(4, dtype=int) for v in codes}
    for r in prev:
        if r['mapcode'] in cur:
            cur[r['mapcode']][STRATA.index(r['cls2022_name'])] += 1
    outside = sum(1 for r in prev if r['mapcode'] not in cur)
    print(f'== carried forward: {len(prev)} v3 points '
          f'({outside} outside the 18 type polygons) ==')

    print('== allocation ==', flush=True)
    plan, spent, worst = topup(A, cur, args.budget, args.n_transformed,
                               args.cap_big, args.cap_small, args.small_ha)
    print(f'  budget {args.budget}, allocated {spent}')
    for v in sorted(codes, key=lambda t: -A[t].sum()):
        add = plan[v] - cur[v]
        if add.sum():
            print(f'  {v:6} +{add.sum():3d}  ' +
                  ' '.join(f'{c[:3]}+{add[j]}' for j, c in enumerate(STRATA) if add[j])
                  + f'   worst {100*worst(v, cur[v]):.1f}pp -> {100*worst(v, plan[v]):.1f}pp')

    # ---- draw the shortfall straight from the tifs
    print('== drawing new points ==', flush=True)
    grid = r3.ClassGrid()
    new_rows = []
    for v in codes:
        wants = {c: int(plan[v][j] - cur[v][j]) for j, c in enumerate(STRATA)}
        if not sum(wants.values()):
            continue
        geom = diss[v]
        got = {c: [] for c in STRATA}
        tries = 0
        while any(len(got[c]) < wants[c] for c in STRATA) and tries < 40:
            short = sum(max(0, wants[c] - len(got[c])) for c in STRATA)
            cand = r3.uniform_points_in(geom, max(400, short * 40), rng)
            cc = grid.cls22(cand)
            for c in STRATA:
                if len(got[c]) >= wants[c]:
                    continue
                hit = cand[cc == CLS_ID[c]]
                got[c].extend(map(tuple, hit[:wants[c] - len(got[c])]))
            tries += 1
        for c in STRATA:
            if len(got[c]) < wants[c]:
                print(f'  ! {v}_{c}: only {len(got[c])}/{wants[c]} found', flush=True)
            for lon, lat in got[c]:
                new_rows.append(dict(mapcode=v, cls=c, lon=lon, lat=lat))
        print(f'  {v}: ' + ' '.join(f'{c[:3]}={len(got[c])}' for c in STRATA
                                    if wants[c]), flush=True)

    xy = np.array([[r['lon'], r['lat']] for r in new_rows])
    for r, k in zip(new_rows, grid.cls25(xy)):
        r['cls2025'] = int(k)

    # ---- assemble: every v3 row verbatim, then the new draws
    cols = list(prev[0].keys())
    assert cols == ['id', 'source', 'round', 'mapcode', 'vegtype', 'efg', 'efg_id',
                    'cls2022', 'cls2022_name', 'cls2025', 'cls2025_name', 'stratum',
                    'lon', 'lat'], cols
    out = [dict(r) for r in prev]
    next_id = max(int(r['id']) for r in prev) + 1
    for r in new_rows:
        m = meta[r['mapcode']]
        out.append(dict(id=next_id, source='new', round=4, mapcode=r['mapcode'],
                        vegtype=m['vegtype'], efg=m['efg'], efg_id=m['efg_id'],
                        cls2022=CLS_ID[r['cls']], cls2022_name=r['cls'],
                        cls2025=r['cls2025'],
                        cls2025_name=ID_CLS.get(r['cls2025'], 'unknown'),
                        stratum=f"{r['mapcode']}_{r['cls']}",
                        lon=round(float(r['lon']), 6), lat=round(float(r['lat']), 6)))
        next_id += 1

    with open(RESULTS / 'sample_points_v4.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csvmod.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)

    gj = dict(type='FeatureCollection', features=[
        dict(type='Feature', geometry=dict(type='Point',
                                           coordinates=[float(d['lon']), float(d['lat'])]),
             properties=d) for d in out])
    (RESULTS / 'sample_points_v4.geojson').write_text(json.dumps(gj))

    # ---- design record
    achieved = {}
    for v in codes:
        share, ci = r3.precision(A[v], plan[v].astype(float))
        achieved[v] = {
            'area_ha': float(A[v].sum()),
            'efg': meta[v]['efg'],
            'n_total': int(plan[v].sum()),
            'n_added_round4': int((plan[v] - cur[v]).sum()),
            'n_by_stratum': {c: int(plan[v][j]) for j, c in enumerate(STRATA)},
            'n_added_by_stratum': {c: int((plan[v] - cur[v])[j])
                                   for j, c in enumerate(STRATA)},
            'share_pct': {c: round(100 * share[j], 2) for j, c in enumerate(STRATA)},
            'ci95_pp': {c: round(100 * ci[j], 2) for j, c in enumerate(STRATA)},
            'ci95_relative_pct': {c: (round(100 * ci[j] / share[j]) if share[j] > 0 else None)
                                  for j, c in enumerate(STRATA)},
        }
    counts = collections.Counter(int(d['round']) for d in out)
    design = {
        'method': 'stratified random top-up of sample_design_v3.json; strata = '
                  'vegetation type x {intact, moderate, severe, transformed}; '
                  'transformed domain self-weighting, thicket domain greedy-minimax '
                  'on the per-type worst-class interval',
        'extends': 'sample_design_v3.json',
        'append_only': 'every v3 point is carried through with its id, coordinates '
                       'and stratum unchanged; new ids start at 3528',
        'stratification_year': 2022, 'assessment_year': 2025,
        'seed': args.seed, 'budget': args.budget,
        'n_transformed_target': args.n_transformed,
        'cap_big': args.cap_big, 'cap_small': args.cap_small, 'small_ha': args.small_ha,
        'n_total': len(out),
        'n_carried_from_v3': len(prev),
        'n_new_round4': len(new_rows),
        'n_by_round': {str(k): v for k, v in sorted(counts.items())},
        'area_ha': prev_design['area_ha'],
        'area_total_ha': prev_design['area_total_ha'],
        'confusion_prior': prev_design['confusion_prior'],
        'confusion_note': prev_design['confusion_note'] +
                          ' Round 4 re-uses the Round 1 confusion because Round 3 '
                          'labels were still in progress when it was allocated.',
        'per_type': achieved,
    }
    (RESULTS / 'sample_design_v4.json').write_text(json.dumps(design, indent=1))

    print()
    print(f'wrote sample_points_v4.csv ({len(out)} points = {len(prev)} carried '
          f'+ {len(new_rows)} new, ids {min(int(d["id"]) for d in out)}-{next_id - 1})')
    print('wrote sample_points_v4.geojson, sample_design_v4.json')
    rel = np.array([[achieved[v]['ci95_relative_pct'][c] or 999 for c in STRATA]
                    for v in codes])
    print(f'projected: worst per-type per-class CI '
          f'{max(max(achieved[v]["ci95_pp"].values()) for v in codes):.1f} pp; '
          f'{(rel <= 33).sum()}/72 cells within +/-33%')


if __name__ == '__main__':
    main()
