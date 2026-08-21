#!/usr/bin/env python
"""Design-based area and accuracy under the vegetation-type x 2022 stratification,
assessing the 2025 map.

What is different from scripts 17/23/25
---------------------------------------
Those estimators assume the stratum IS the mapped class (17_area_estimation_efg
derives `map_cls` from the stratum id and reads user's accuracy off the diagonal
of a single cell). That holds when the sample was stratified by the map being
assessed. It does NOT hold here: the sample is stratified by

    stratum g = vegetation type x 2022 condition class      (54 cells)

while the map being assessed is the 2025 condition surface. Stratum and map class
are now separate dimensions, so the error matrix has to be accumulated over both.

Estimator (Olofsson et al. 2014 / Stehman 2014, general stratified form)
-----------------------------------------------------------------------
For map class i (2025) and reference class j, with A_g the known area of stratum g:

    A_hat(i,j) = sum_g  A_g * n_gij / n_g
    V(A_hat)   = sum_g  A_g^2 * p_gij (1 - p_gij) / (n_g - 1)

Reference-class area is the row sum over i, user's / producer's accuracy are
ratios of these areas with variances by the delta method:

    UA_i = A(i,i) / A(i,.)      PA_j = A(j,j) / A(.,j)      OA = sum_i A(i,i) / A

    Cov(A(i,i), A(i,.)) = sum_g A_g^2 * p_gii (1 - p_gi.) / (n_g - 1)

F1 is the same kind of ratio, F1_j = 2 A(j,j) / (A(j,.) + A(.,j)) -- the harmonic
mean of UA and PA, equivalently the Dice coefficient of the mapped and reference
extents of class j -- and gets a delta-method CI the same way. Its denominator
scores the diagonal cell twice, so the variance uses the general per-point-score
form (`_terms_w`) rather than the 0/1 indicator form.

Validity of pooling the old and new points
------------------------------------------
The 54 strata nest exactly inside the 3 severity strata the original 846 were
drawn under, and both draws are uniform within their stratum, so within any
stratum g the combined sample is a simple random sample of g. Plain n_gij / n_g
is therefore unbiased and no per-point design weight is needed -- this is ordinary
post-stratification, and it is why the 2022 stratification was retained.

Reference labels come from the same random duplicate-selection rule as script 25.

Run:
  python -u analysis/27_area_estimation_vegtype2022.py
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RESULTS = HERE / 'results'

Z = 1.959963984540054
STRATA = ['intact', 'moderate', 'severe']
CLS = {0: 'intact', 1: 'moderate', 2: 'severe'}
LOW_N = 10             # stratum flagged unstable below this
MIN_N_VAR = 2          # need n>=2 for any variance contribution

POINTS_CSV = RESULTS / 'sample_points_vegtype2022.csv'
DESIGN_JSON = RESULTS / 'sample_design_vegtype2022.json'
OUT_JSON = RESULTS / 'area_estimation_vegtype2022.json'


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def remap_label(lab, mode):
    """transformed -> severe always; nothicket kept as its own class (mode
    'class') or folded into severe (mode 'severe'). None => exclude."""
    if lab == 'transformed':
        return 'severe'
    if lab == 'nothicket':
        return 'nothicket' if mode == 'class' else 'severe'
    return lab if lab in STRATA else None


# ------------------------------------------------------------------ inputs --
def load_points():
    pts = {}
    with open(POINTS_CSV, encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            pts[int(row['id'])] = dict(
                stratum=row['stratum'] or None,
                mapcode=row['mapcode'] or None,
                vegtype=row['vegtype'] or None,
                efg=row['efg'] or None,
                cls2022=int(row['cls2022']),
                cls2025=int(row['cls2025']),
                source=row['source'])
    return pts


def load_reference_rows():
    """One reference label per point, by the random duplicate-selection rule of
    script 25 (same seed), so this is consistent with the published arm."""
    m23 = _load(HERE / '23_area_estimation_final_labeller.py', 'final_area_estimation')
    m25 = _load(HERE / '25_area_estimation_random_adjudication.py', 'random_adjudication')
    arp, _ = m23.load_json_labels(m23.ARP_FILE, 'ARP')
    svm = m23.load_svm_labels()
    ap, _ = m23.load_json_labels(m23.AP_FILE, 'AP')
    mp, _ = m23.load_json_labels(m23.MP_FILE, 'MP')
    rows, draws, excluded, by_id = m25.select_random_rows(
        {'ARP': arp, 'SVM': svm, 'AP': ap, 'MP': mp})
    return rows, draws, excluded, by_id


# ------------------------------------------------------------- accumulation --
def accumulate(rows, pts, areas, mode):
    """counts[g][(map2025, ref)] and n[g], restricted to usable points."""
    counts = defaultdict(lambda: defaultdict(int))
    n = defaultdict(int)
    dropped = defaultdict(int)
    for r in rows:
        p = pts.get(r['id'])
        if p is None:
            dropped['not_in_sample'] += 1
            continue
        if not p['stratum'] or p['stratum'] not in areas:
            dropped['outside_vegtypes'] += 1
            continue
        if p['cls2025'] < 0:
            dropped['no_2025_class'] += 1
            continue
        ref = remap_label(r['label'], mode)
        if ref is None:
            dropped['indeterminate_label'] += 1
            continue
        counts[p['stratum']][(CLS[p['cls2025']], ref)] += 1
        n[p['stratum']] += 1
    return counts, n, dict(dropped)


def _terms(counts, n, areas, pick):
    """sum_g A_g * p_g(pick) and its variance, where pick(cell_counts)->count."""
    est = 0.0
    var = 0.0
    for g, A in areas.items():
        ng = n.get(g, 0)
        if ng == 0:
            continue
        c = pick(counts[g])
        p = c / ng
        est += A * p
        if ng >= MIN_N_VAR:
            var += A * A * p * (1 - p) / (ng - 1)
    return est, var


def _cov(counts, n, areas, pick_a, pick_b):
    """Cov of two nested area estimators (a subset of b) across strata."""
    cov = 0.0
    for g, A in areas.items():
        ng = n.get(g, 0)
        if ng < MIN_N_VAR:
            continue
        pa = pick_a(counts[g]) / ng
        pb = pick_b(counts[g]) / ng
        cov += A * A * pa * (1 - pb) / (ng - 1)
    return cov


def _terms_w(counts, n, areas, w):
    """sum_g A_g * mean_g(w) and its variance, for a per-point SCORE w(cell).

    `_terms`/`_cov` above assume the picked quantity is a 0/1 indicator, so the
    within-stratum variance is p(1-p). F1 needs a score that takes the value 2 on
    the diagonal cell, where that no longer holds. This is the general form,
        V = A_g^2 * (E[w^2] - E[w]^2) / (n_g - 1),
    which reduces to `_terms` exactly when w is an indicator."""
    est = 0.0
    var = 0.0
    for g, A in areas.items():
        ng = n.get(g, 0)
        if ng == 0:
            continue
        cells = counts[g]
        m1 = sum(w(cell) * c for cell, c in cells.items()) / ng
        m2 = sum(w(cell) ** 2 * c for cell, c in cells.items()) / ng
        est += A * m1
        if ng >= MIN_N_VAR:
            var += A * A * (m2 - m1 * m1) / (ng - 1)
    return est, var


def _cov_w(counts, n, areas, wa, wb):
    """Cov of two per-point-score area estimators (general form of `_cov`)."""
    cov = 0.0
    for g, A in areas.items():
        ng = n.get(g, 0)
        if ng < MIN_N_VAR:
            continue
        cells = counts[g]
        ma = sum(wa(cell) * c for cell, c in cells.items()) / ng
        mb = sum(wb(cell) * c for cell, c in cells.items()) / ng
        mab = sum(wa(cell) * wb(cell) * c for cell, c in cells.items()) / ng
        cov += A * A * (mab - ma * mb) / (ng - 1)
    return cov


def ratio_se(x, vx, y, vy, cxy):
    """Delta-method SE of R = x / y."""
    if y <= 0:
        return float('nan')
    r = x / y
    v = (vx - 2 * r * cxy + r * r * vy) / (y * y)
    return math.sqrt(max(v, 0.0))


def logit_ci(p, se):
    """95% CI for a bounded ratio, built on the logit scale and back-transformed,
    so it cannot leave [0,1] the way the symmetric Wald interval can."""
    if not (0 < p < 1) or not math.isfinite(se) or se <= 0:
        return [float('nan'), float('nan')]
    l = math.log(p / (1 - p))
    sl = se / (p * (1 - p))
    return [1 / (1 + math.exp(-(l - Z * sl))), 1 / (1 + math.exp(-(l + Z * sl)))]


# ------------------------------------------------------------------ scenario --
def run_scenario(rows, pts, areas, mode):
    ref_classes = STRATA + (['nothicket'] if mode == 'class' else [])
    map_classes = STRATA
    counts, n, dropped = accumulate(rows, pts, areas, mode)

    covered = {g: A for g, A in areas.items() if n.get(g, 0) > 0}
    A_cov = sum(covered.values())
    A_all = sum(areas.values())

    # ---- error matrix in estimated area (ha) -------------------------------
    matrix, matrix_se = {}, {}
    for i in map_classes:
        matrix[i], matrix_se[i] = {}, {}
        for j in ref_classes:
            e, v = _terms(counts, n, covered,
                          lambda c, i=i, j=j: c.get((i, j), 0))
            matrix[i][j] = e
            matrix_se[i][j] = math.sqrt(v)

    # ---- reference-class area (column totals) ------------------------------
    ref_area = {}
    for j in ref_classes:
        e, v = _terms(counts, n, covered,
                      lambda c, j=j: sum(cc for (mi, rj), cc in c.items() if rj == j))
        se = math.sqrt(v)
        ref_area[j] = dict(area_ha=e, se_ha=se, moe95_ha=Z * se,
                           ci95_ha=[e - Z * se, e + Z * se])

    # ---- estimated area of each 2025 map class (row totals) ----------------
    map_area = {}
    for i in map_classes:
        e, v = _terms(counts, n, covered,
                      lambda c, i=i: sum(cc for (mi, rj), cc in c.items() if mi == i))
        se = math.sqrt(v)
        map_area[i] = dict(area_ha=e, se_ha=se, moe95_ha=Z * se)

    # ---- accuracies --------------------------------------------------------
    users, producers = {}, {}
    for i in map_classes:
        x, vx = _terms(counts, n, covered, lambda c, i=i: c.get((i, i), 0))
        y, vy = _terms(counts, n, covered,
                       lambda c, i=i: sum(cc for (mi, rj), cc in c.items() if mi == i))
        cxy = _cov(counts, n, covered,
                   lambda c, i=i: c.get((i, i), 0),
                   lambda c, i=i: sum(cc for (mi, rj), cc in c.items() if mi == i))
        ua = x / y if y > 0 else float('nan')
        se = ratio_se(x, vx, y, vy, cxy)
        users[i] = dict(value=ua, se=se, moe95=Z * se)
    for j in map_classes:
        x, vx = _terms(counts, n, covered, lambda c, j=j: c.get((j, j), 0))
        y, vy = _terms(counts, n, covered,
                       lambda c, j=j: sum(cc for (mi, rj), cc in c.items() if rj == j))
        cxy = _cov(counts, n, covered,
                   lambda c, j=j: c.get((j, j), 0),
                   lambda c, j=j: sum(cc for (mi, rj), cc in c.items() if rj == j))
        pa = x / y if y > 0 else float('nan')
        se = ratio_se(x, vx, y, vy, cxy)
        producers[j] = dict(value=pa, se=se, moe95=Z * se)

    # ---- F1 = harmonic mean of UA and PA, as an area ratio -----------------
    # 2*A(j,j) / (A(j,.) + A(.,j)) -- the Dice coefficient of the mapped and
    # reference extents of class j. Numerator and denominator share the diagonal
    # cell (UA and PA are positively correlated through it), so the delta method
    # needs the covariance; combining the separate UA and PA intervals by hand
    # would give the wrong width. Unlike scripts 14/17/23, A(j,.) is estimated
    # here too, because the stratum is not the mapped class.
    f1 = {}
    for j in map_classes:
        w_num = lambda cell, j=j: 2.0 if cell == (j, j) else 0.0
        w_den = lambda cell, j=j: (1.0 if cell[0] == j else 0.0) + (1.0 if cell[1] == j else 0.0)
        x, vx = _terms_w(counts, n, covered, w_num)
        y, vy = _terms_w(counts, n, covered, w_den)
        cxy = _cov_w(counts, n, covered, w_num, w_den)
        value = x / y if y > 0 else float('nan')
        se = ratio_se(x, vx, y, vy, cxy)
        f1[j] = dict(value=value, se=se, moe95=Z * se,
                     ci95=[value - Z * se, value + Z * se],
                     ci95_logit=logit_ci(value, se))

    xd, vd = _terms(counts, n, covered,
                    lambda c: sum(cc for (mi, rj), cc in c.items() if mi == rj))
    oa = xd / A_cov if A_cov else float('nan')
    oa_se = math.sqrt(vd) / A_cov if A_cov else float('nan')

    # ---- composition by vegetation type and by EFG -------------------------
    def composition(group_of):
        groups = defaultdict(dict)
        for g, A in covered.items():
            groups[group_of(g)][g] = A
        out = {}
        for name, sub in groups.items():
            comp = {}
            for j in ref_classes:
                e, v = _terms(counts, n, sub,
                              lambda c, j=j: sum(cc for (mi, rj), cc in c.items()
                                                 if rj == j))
                se = math.sqrt(v)
                comp[j] = dict(area_ha=e, se_ha=se, moe95_ha=Z * se)
            tot = sum(sub.values())
            ns = sum(n.get(g, 0) for g in sub)
            weak = [g for g in sub if n.get(g, 0) < MIN_N_VAR]
            out[name] = dict(area_ha=tot, n=ns, composition=comp,
                             fraction={j: (comp[j]['area_ha'] / tot if tot else None)
                                       for j in ref_classes},
                             strata=len(sub), weak_strata=weak,
                             estimable=len(weak) == 0)
        return out

    veg_of = {}
    efg_of = {}
    for pid, p in pts.items():
        if p['stratum']:
            veg_of[p['stratum']] = p['mapcode']
            efg_of[p['stratum']] = p['efg']

    per_stratum = {}
    for g, A in areas.items():
        ng = n.get(g, 0)
        per_stratum[g] = dict(area_ha=A, n=ng, low_n=ng < LOW_N,
                              no_variance=ng < MIN_N_VAR, covered=ng > 0)

    return dict(
        nothicket_mode=mode, ref_classes=ref_classes, map_classes=map_classes,
        n_used=sum(n.values()), dropped=dropped,
        area_total_ha=A_all, area_covered_ha=A_cov,
        area_uncovered_ha=A_all - A_cov,
        strata_total=len(areas), strata_covered=len(covered),
        strata_without_variance=sum(1 for g in areas if n.get(g, 0) < MIN_N_VAR),
        error_matrix_area_ha=matrix, error_matrix_se_ha=matrix_se,
        reference_area=ref_area, map_class_area=map_area,
        users_accuracy=users, producers_accuracy=producers, f1=f1,
        overall_accuracy=dict(value=oa, se=oa_se, moe95=Z * oa_se),
        by_vegtype=composition(lambda g: veg_of.get(g, '?')),
        by_efg=composition(lambda g: efg_of.get(g, '?')),
        per_stratum=per_stratum,
    )


# ---------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    pts = load_points()
    design = json.load(open(DESIGN_JSON))
    areas = {k: float(v) for k, v in design['area_ha'].items()}
    rows, draws, excluded, by_id = load_reference_rows()

    print(f'strata: {len(areas)}   design area: {sum(areas.values()):,.0f} ha')
    print(f'points in file: {len(pts)}   reference labels available: {len(rows)}')
    n_new_labelled = sum(1 for r in rows
                         if pts.get(r['id'], {}).get('source') == 'new')
    print(f'  of which on NEW points: {n_new_labelled}')

    out = dict(
        artifact='design-based area and accuracy; strata = vegetation type x 2022 '
                 'condition class; map assessed = 2025 condition surface',
        generated_utc=datetime.now(timezone.utc).isoformat(),
        stratification_year=2022, assessment_year=2025,
        design_file=DESIGN_JSON.name, points_file=POINTS_CSV.name,
        adjudication='random duplicate selection (script 25 rule, seed '
                     f'{_load(HERE / "25_area_estimation_random_adjudication.py", "ra25").SEED})',
        n_reference_labels=len(rows),
        n_reference_labels_on_new_points=n_new_labelled,
        scenarios={},
    )
    for mode, tag in (('class', 'A_nothicket_class'),
                      ('severe', 'B_nothicket_to_severe')):
        out['scenarios'][tag] = run_scenario(rows, pts, areas, mode)

    json.dump(out, open(OUT_JSON, 'w'), indent=2)
    print(f'wrote {OUT_JSON.name}')

    if args.quiet:
        return
    for tag, sc in out['scenarios'].items():
        print('\n' + '=' * 86)
        print(f'SCENARIO {tag}   n_used={sc["n_used"]}   dropped={sc["dropped"]}')
        print(f'  strata covered {sc["strata_covered"]}/{sc["strata_total"]}, '
              f'{sc["strata_without_variance"]} with n<{MIN_N_VAR} (no variance)')
        print(f'  area covered {sc["area_covered_ha"]:,.0f} ha of '
              f'{sc["area_total_ha"]:,.0f} ha '
              f'({100*sc["area_covered_ha"]/sc["area_total_ha"]:.1f}%)')
        print('=' * 86)
        refs = sc['ref_classes']
        print('Error matrix, estimated area (ha):  rows = 2025 map, cols = reference')
        print(f'{"map2025":<12}' + ''.join(f'{c:>14}' for c in refs) + f'{"total":>14}')
        for i in sc['map_classes']:
            row = sc['error_matrix_area_ha'][i]
            print(f'{i:<12}' + ''.join(f'{row[c]:>14,.0f}' for c in refs)
                  + f'{sum(row.values()):>14,.0f}')
        print()
        print(f'{"reference":<12}' + ''.join(
            f'{sc["reference_area"][c]["area_ha"]:>14,.0f}' for c in refs))
        print(f'{"+/-95%":<12}' + ''.join(
            f'{sc["reference_area"][c]["moe95_ha"]:>14,.0f}' for c in refs))
        print()
        print(f'{"class":<12}{"users_acc":>18}{"producers_acc":>20}{"F1":>20}')
        for c in sc['map_classes']:
            u, p = sc['users_accuracy'][c], sc['producers_accuracy'][c]
            f = sc['f1'][c]
            print(f'{c:<12}{u["value"]:>10.3f} +/-{u["moe95"]:<6.3f}'
                  f'{p["value"]:>12.3f} +/-{p["moe95"]:<6.3f}'
                  f'{f["value"]:>12.3f} +/-{f["moe95"]:<6.3f}')
        o = sc['overall_accuracy']
        print(f'{"OVERALL":<12}{o["value"]:>10.3f} +/-{o["moe95"]:<6.3f}')
        print()
        print('By EFG (error-adjusted reference area, ha):')
        for name, d in sorted(sc['by_efg'].items()):
            flag = '' if d['estimable'] else '   [NOT ESTIMABLE: weak strata]'
            print(f'  {name:<16} area {d["area_ha"]:>10,.0f}  n={d["n"]:<5}' + flag)
            for j in refs:
                c = d['composition'][j]
                print(f'      {j:<10} {c["area_ha"]:>10,.0f} +/-{c["moe95_ha"]:>9,.0f}')


if __name__ == '__main__':
    main()
