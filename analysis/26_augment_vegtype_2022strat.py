#!/usr/bin/env python
"""AUGMENT the reference sample using vegetation type x 2022 condition class as
the stratification, keeping every existing point.

Design
------
Strata = T_MAPCODE (18 solid-thicket vegetation types) x cls (0/1/2 intact /
moderate / severe), where `cls` is the SAME 2022 argmax surface the original 846
points were drawn from (reproduced exactly via 12_augment_efg_stratify.
build_stratum_map, verified to agree 250/250 on the existing points).

Keeping the 2022 stratification means the whole reference sample -- old and new
-- sits under ONE design with known inclusion probabilities. The 2025 map is then
assessed with it: the stratification used to select a sample need not be the map
being evaluated, it only supplies the design weights (Stehman 2009). The 2025
class is carried on each point as an attribute so the error matrix can be built
on 2025 directly.

Allocation (deliberately free of any accuracy estimate)
------------------------------------------------------
  core       : n_core points spread PROPORTIONAL to stratum area (self-weighting)
  veg floor  : top up so every vegetation type reaches --floor-veg points total
  cell floor : top up so every EFG x class cell reaches --floor-cell points total
  (floor top-ups are split within their group proportional to stratum area)

Because the allocation uses only areas and fixed minimums, it does not depend on
observed or modelled accuracies, so the design is not a function of the reference
data and the ordinary stratified variance applies.

Sampling mechanics
------------------
Vegetation-type polygons live only in the local geodatabase (the EE ThicketEFGs
asset carries just 8 dissolved EFG polygons), and are far too detailed to push
inline to EE. So candidates are drawn uniformly at random INSIDE each vegetation
type from the exact GDB geometry, and Earth Engine is used only to look up the
2022 class at those candidate points. Selecting the required number at random
from the candidates that landed in a given (vegtype, class) cell yields exactly a
stratified random sample of that cell.

Run:
  python -u analysis/26_augment_vegtype_2022strat.py --core 800 \
      --floor-veg 50 --floor-cell 100 --oversample 0.10 --seed 42

Outputs (kept separate from earlier runs):
  analysis/results/sample_design_vegtype2022.json
  analysis/results/sample_points_vegtype2022.geojson
  analysis/results/sample_points_vegtype2022.csv
"""
import argparse
import csv as csvmod
import importlib.util
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

os.environ.setdefault('GDAL_DATA',
                      r'C:\Users\geethen.singh\.pixi\envs\geo\Library\share\gdal')

import ee
import pandas as pd
import pyogrio
import rasterio
import shapely
from shapely.geometry import Point

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RESULTS = HERE / 'results'

# Local-only input: BioregionsSimple.gdb.zip is ~239 MB, over GitHub's 100 MB
# file limit, so it is gitignored. Place it at the repo root before running.
GDB = (f'/vsizip/{ROOT.as_posix()}/BioregionsSimple.gdb.zip/BioregionsSimple.gdb')
VEG_LAYER = 'IEM_5_13_4_1_260326VegetationTypesL5'
VEG_CSV = ROOT / 'Solid_Thicket_condition_by_vegetation_type_2025_solid_EFGs_D3_preliminary.csv'
TIF_GLOB = 'RF_prob_surfaces_solid_thicket_2025-*.tif'

CLASS_NAME = {0: 'intact', 1: 'moderate', 2: 'severe'}
EFG_OF_FVG = {'Arid Thicket': 1, 'Valley Thicket': 2, 'Mesic Thicket': 3}
EFG_NAME = {1: 'AridThicket', 2: 'ValleyThicket', 3: 'MesicThicket'}
EE_CHUNK = 3000          # points per computeFeatures call


# --------------------------------------------------------------- 2022 surface
def load_build_stratum_map():
    """Import build_stratum_map from script 12 so the 2022 surface is identical."""
    path = HERE / '12_augment_efg_stratify.py'
    spec = importlib.util.spec_from_file_location('aug12', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['aug12'] = mod
    spec.loader.exec_module(mod)
    return mod


def ee_class_at(cls_img, lonlat, scale, label=''):
    """Look up the 2022 class at points, in chunks. Returns array with -1 where
    the point falls on a masked (non-thicket / water / non-natural) pixel."""
    out = np.full(len(lonlat), -1, dtype=int)
    for start in range(0, len(lonlat), EE_CHUNK):
        chunk = lonlat[start:start + EE_CHUNK]
        feats = [ee.Feature(ee.Geometry.Point([float(x), float(y)]), {'i': start + k})
                 for k, (x, y) in enumerate(chunk)]
        fc = cls_img.rename('cls').sampleRegions(
            collection=ee.FeatureCollection(feats), properties=['i'],
            scale=scale, geometries=False, tileScale=4)
        gdf = ee.data.computeFeatures({'expression': fc,
                                       'fileFormat': 'GEOPANDAS_GEODATAFRAME'})
        for _, row in gdf.iterrows():
            out[int(row['i'])] = int(row['cls'])
        print(f'    {label} {min(start+EE_CHUNK, len(lonlat)):>6}/{len(lonlat)}',
              flush=True)
    return out


# ------------------------------------------------------------------ geometry
def load_vegtypes():
    veg = pd.read_csv(VEG_CSV)
    codes = list(veg['T_MAPCODE'])
    g = pyogrio.read_dataframe(GDB, layer=VEG_LAYER, columns=['T_MAPCODE'])
    g['T_MAPCODE'] = [('' if v is None or (isinstance(v, float) and v != v)
                       else str(v)).strip() for v in g['T_MAPCODE'].tolist()]
    g = g[g['T_MAPCODE'].isin(codes)].copy()
    equal_area = g.crs                      # GDB is already Albers Equal Area
    area_ha = (g.assign(a=g.geometry.area / 1e4)
                 .groupby('T_MAPCODE')['a'].sum())
    g4326 = g.to_crs(4326)
    diss = g4326.dissolve(by='T_MAPCODE')['geometry']
    meta = {r['T_MAPCODE']: dict(vegtype=r['vegtype_name'],
                                 efg_id=EFG_OF_FVG[r['RevisedFVG']],
                                 efg=EFG_NAME[EFG_OF_FVG[r['RevisedFVG']]],
                                 area_ha=float(area_ha[r['T_MAPCODE']]))
            for _, r in veg.iterrows()}
    print(f'  {len(g)} polygons, {len(meta)} vegetation types, '
          f'{sum(m["area_ha"] for m in meta.values()):,.0f} ha '
          f'(equal-area CRS: {equal_area.name})')
    return diss, meta, codes


def uniform_points_in(geom, n, rng):
    """Uniform random points inside a (multi)polygon, by rejection in its bbox."""
    minx, miny, maxx, maxy = geom.bounds
    got_x, got_y = [], []
    need = n
    while need > 0:
        k = int(need * 3) + 64
        xs = rng.uniform(minx, maxx, k)
        ys = rng.uniform(miny, maxy, k)
        inside = shapely.contains_xy(geom, xs, ys)
        got_x.append(xs[inside])
        got_y.append(ys[inside])
        need = n - sum(len(a) for a in got_x)
    x = np.concatenate(got_x)[:n]
    y = np.concatenate(got_y)[:n]
    return np.column_stack([x, y])


# ---------------------------------------------------------------- allocation
def allocate(strata, core, floor_veg, floor_cell, have):
    """strata: {(mapcode, cls): area_ha} -> {(mapcode, cls): NEW points}.

    `core` is the number of NEW points spread proportional to stratum area. It is
    an increment ON TOP of the existing sample, not a total the existing points
    count toward: the existing sample is itself already near proportional to area
    across these strata, so a proportional increment keeps the combined sample
    self-weighting. Topping up to a proportional TOTAL would instead leave the
    strata that are already over-represented untouched and break that property.

    The floors are applied to the COMBINED count (existing + new), so a stratum
    only receives floor points if the final sample would otherwise fall short.
    """
    Atot = sum(strata.values())
    tgt = {k: core * a / Atot for k, a in strata.items()}

    def group_topup(keyfn, floor):
        if not floor:
            return
        tot = defaultdict(float)
        for k in strata:
            tot[keyfn(k)] += tgt[k] + have.get(k, 0)
        for grp, cur in tot.items():
            if cur >= floor:
                continue
            members = [k for k in strata if keyfn(k) == grp]
            garea = sum(strata[k] for k in members)
            deficit = floor - cur
            for k in members:
                tgt[k] += deficit * strata[k] / garea

    group_topup(lambda k: k[0], floor_veg)                     # per vegetation type
    return tgt


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default='ee-gsingh')
    ap.add_argument('--core', type=int, default=800)
    ap.add_argument('--floor-veg', type=int, default=50)
    ap.add_argument('--floor-cell', type=int, default=100)
    ap.add_argument('--oversample', type=float, default=0.10,
                    help='extra fraction drawn to absorb unlabellable points')
    ap.add_argument('--cand-per-target', type=int, default=60,
                    help='candidate points drawn per point needed, per vegtype')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    t0 = time.time()
    rng = np.random.default_rng(args.seed)
    RESULTS.mkdir(exist_ok=True)

    print('== vegetation types from the geodatabase ==', flush=True)
    diss, meta, codes = load_vegtypes()

    print('== 2022 condition surface (reproduced from script 12) ==', flush=True)
    ee.Initialize(project=args.project)
    m12 = load_build_stratum_map()
    _, cls_img, _, _ = m12.build_stratum_map()
    scale = m12.EXPORT_SCALE

    # ---------------------------------------- existing points -> (vegtype, cls)
    print('== placing the existing points in the new strata ==', flush=True)
    gj = json.load(open(RESULTS / 'sample_points.geojson'))
    old = [f['properties'] for f in gj['features']]
    old_xy = np.array([[p['lon'], p['lat']] for p in old])
    tree = shapely.STRtree(list(diss.values))
    keys = list(diss.index)
    old_mc = []
    for x, y in old_xy:
        p = Point(x, y)
        hit = ''
        for j in tree.query(p):
            if diss.values[j].contains(p):
                hit = keys[j]
                break
        old_mc.append(hit)
    have = Counter()
    for p, mc in zip(old, old_mc):
        if mc:
            have[(mc, int(p['cls']))] += 1
    n_out = sum(1 for mc in old_mc if not mc)
    print(f'  {len(old)} existing points: {len(old)-n_out} inside the 18 vegetation '
          f'types, {n_out} outside')

    # -------------------------------------------------- candidates + 2022 class
    print('== drawing candidates and looking up their 2022 class ==', flush=True)
    rough = {mc: max(args.floor_veg, args.core * meta[mc]['area_ha']
                     / sum(v['area_ha'] for v in meta.values()))
             for mc in codes}
    cand = {}
    for mc in codes:
        n_cand = int(rough[mc] * args.cand_per_target)
        pts = uniform_points_in(diss[mc], n_cand, rng)
        cand[mc] = pts
        print(f'  {mc}: {n_cand} candidates', flush=True)

    all_xy = np.vstack([cand[mc] for mc in codes])
    owner = np.concatenate([[mc] * len(cand[mc]) for mc in codes])

    # The EE lookup is by far the slowest step, and it depends only on the
    # candidate coordinates -- cache it so re-running with different allocation
    # settings is cheap.
    cache = RESULTS / f'candidates_2022cls_seed{args.seed}_c{args.core}_' \
                      f'f{args.floor_veg}_x{args.cand_per_target}.npz'
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if z['xy'].shape == all_xy.shape and np.allclose(z['xy'], all_xy):
            cls_of = z['cls']
            print(f'  reusing cached 2022 classes for {len(all_xy):,} candidates '
                  f'({cache.name})', flush=True)
        else:
            cls_of = None
    else:
        cls_of = None
    if cls_of is None:
        print(f'  querying Earth Engine for {len(all_xy):,} candidate points ...',
              flush=True)
        cls_of = ee_class_at(cls_img, all_xy, scale, label='  ')
        np.savez_compressed(cache, xy=all_xy, cls=cls_of)
    valid = cls_of >= 0
    print(f'  {valid.sum():,}/{len(all_xy):,} candidates fell on valid '
          f'thicket pixels ({100*valid.mean():.0f}%)')

    # ---------------------------------------------- stratum areas from the draw
    strata = {}
    for mc in codes:
        sel = (owner == mc) & valid
        cc = cls_of[sel]
        if len(cc) == 0:
            continue
        # vegtype area apportioned by the class split of its valid candidates
        valid_frac = sel.sum() / (owner == mc).sum()
        a_valid = meta[mc]['area_ha'] * valid_frac
        for c in (0, 1, 2):
            share = (cc == c).mean()
            if share > 0:
                strata[(mc, c)] = a_valid * share
    print(f'  {len(strata)} non-empty strata, '
          f'{sum(strata.values()):,.0f} ha of mapped thicket')

    # ------------------------------------------------------------- allocation
    print('== allocation ==', flush=True)
    tgt = allocate(strata, args.core, args.floor_veg, args.floor_cell, have)

    # EFG x class cell floor
    if args.floor_cell:
        tot = defaultdict(float)
        for k in strata:
            tot[(meta[k[0]]['efg'], k[1])] += tgt[k] + have.get(k, 0)
        for cell, cur in tot.items():
            if cur >= args.floor_cell:
                continue
            members = [k for k in strata if (meta[k[0]]['efg'], k[1]) == cell]
            garea = sum(strata[k] for k in members)
            for k in members:
                tgt[k] += (args.floor_cell - cur) * strata[k] / garea

    # tgt is already the NEW-point allocation, so the existing count is NOT
    # subtracted here -- doing so would turn the increment into a top-up.
    topup = {k: int(np.ceil(tgt[k] * (1 + args.oversample))) for k in strata}
    n_topup = sum(topup.values())
    print(f'  core={args.core}  floor_veg={args.floor_veg}  '
          f'floor_cell={args.floor_cell}  oversample={args.oversample:.0%}')
    print(f'  existing placed = {sum(have.values())}   NEW = {n_topup}   '
          f'TOTAL = {sum(have.values()) + n_topup}')

    # --------------------------------------------------------------- selection
    print('== selecting the new points ==', flush=True)
    chosen, short = [], {}
    for (mc, c), k in sorted(topup.items()):
        if k == 0:
            continue
        pool = np.where((owner == mc) & (cls_of == c))[0]
        if len(pool) < k:
            short[(mc, c)] = (len(pool), k)
        take = rng.permutation(pool)[:k]
        for i in take:
            chosen.append(dict(mapcode=mc, cls=int(c), lon=float(all_xy[i, 0]),
                               lat=float(all_xy[i, 1])))
    if short:
        print('  WARNING: candidate pool exhausted in these strata '
              '(re-run with a larger --cand-per-target):')
        for k, (got, want) in sorted(short.items()):
            print(f'    {k[0]}_{CLASS_NAME[k[1]]}: had {got}, needed {want}')
    print(f'  selected {len(chosen)} new points')

    # ------------------------------------------- attach the 2025 class locally
    print('== attaching the 2025 class to every point ==', flush=True)
    tifs = sorted(ROOT.glob(TIF_GLOB))
    allpts = ([dict(source='existing', mapcode=mc, cls=int(p['cls']),
                    lon=float(p['lon']), lat=float(p['lat']))
               for p, mc in zip(old, old_mc)] + [dict(source='new', **c) for c in chosen])
    lons = np.array([p['lon'] for p in allpts])
    lats = np.array([p['lat'] for p in allpts])
    cls25 = np.full(len(allpts), -1, dtype=int)
    for tif in tifs:
        with rasterio.open(tif) as src:
            b = src.bounds
            ins = ((lons >= b.left) & (lons < b.right)
                   & (lats > b.bottom) & (lats <= b.top))
            idx = np.where(ins)[0]
            if not len(idx):
                continue
            for i, v in zip(idx, src.sample(list(zip(lons[idx], lats[idx])),
                                            indexes=[5])):
                val = float(v[0])
                cls25[i] = int(val) if np.isfinite(val) and val >= 0 else -1
    for p, c in zip(allpts, cls25):
        p['cls2025'] = int(c)

    # ------------------------------------------------------------------ write
    feats = []
    for i, p in enumerate(allpts):
        mc = p['mapcode']
        mt = meta.get(mc, {})
        props = dict(id=i, source=p['source'], mapcode=mc or None,
                     vegtype=mt.get('vegtype'), efg=mt.get('efg'),
                     efg_id=mt.get('efg_id'),
                     cls2022=p['cls'], cls2022_name=CLASS_NAME[p['cls']],
                     cls2025=p['cls2025'],
                     cls2025_name=CLASS_NAME.get(p['cls2025']),
                     stratum=f'{mc}_{CLASS_NAME[p["cls"]]}' if mc else None,
                     lon=p['lon'], lat=p['lat'])
        feats.append({'type': 'Feature', 'properties': props,
                      'geometry': {'type': 'Point',
                                   'coordinates': [p['lon'], p['lat']]}})
    json.dump({'type': 'FeatureCollection',
               'crs': {'type': 'name',
                       'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
               'features': feats},
              open(RESULTS / 'sample_points_vegtype2022.geojson', 'w'))

    cols = ['id', 'source', 'mapcode', 'vegtype', 'efg', 'efg_id', 'cls2022',
            'cls2022_name', 'cls2025', 'cls2025_name', 'stratum', 'lon', 'lat']
    with open(RESULTS / 'sample_points_vegtype2022.csv', 'w', newline='') as fh:
        w = csvmod.writer(fh)
        w.writerow(cols)
        for ft in feats:
            w.writerow([ft['properties'][c] for c in cols])

    def skey(k):
        return f'{k[0]}_{CLASS_NAME[k[1]]}'

    design = dict(
        method='stratified random; strata = vegetation type x 2022 condition class; '
               'allocation = area-proportional core + fixed vegtype and EFG x class '
               'minimums (independent of any accuracy estimate); the 2022 '
               'stratification is retained so the whole sample sits under one '
               'design, and the 2025 map is assessed with it',
        stratification_year=2022, assessment_year=2025,
        export_scale_m=scale, seed=args.seed,
        core=args.core, floor_veg=args.floor_veg, floor_cell=args.floor_cell,
        oversample=args.oversample,
        area_ha={skey(k): v for k, v in strata.items()},
        area_total_ha=sum(strata.values()),
        existing_counts={skey(k): v for k, v in have.items()},
        existing_outside_vegtypes=n_out,
        target={skey(k): float(v) for k, v in tgt.items()},
        topup={skey(k): v for k, v in topup.items() if v},
        n_existing=len(old), n_existing_placed=sum(have.values()),
        n_new=len(chosen), n_total=len(allpts),
        candidate_pool=len(all_xy),
        candidate_valid_fraction=float(valid.mean()),
        short_strata={skey(k): dict(had=v[0], needed=v[1])
                      for k, v in short.items()},
        design_weight_note='inclusion probability of a NEW point in stratum g is '
                           'm_g / N_g with N_g the stratum area; the existing '
                           'points retain their original 3-class 2022 design '
                           'weights (see sample_design.json)',
    )
    json.dump(design, open(RESULTS / 'sample_design_vegtype2022.json', 'w'), indent=2)

    # ------------------------------------------------------------------ report
    print()
    print(f"{'stratum':<28}{'area_ha':>11}{'have':>6}{'target':>8}{'NEW':>6}")
    print('-' * 59)
    for mc in codes:
        for c in (0, 1, 2):
            k = (mc, c)
            if k not in strata:
                continue
            print(f'{skey(k):<28}{strata[k]:>11,.0f}{have.get(k,0):>6}'
                  f'{tgt[k]:>8.0f}{topup.get(k,0):>6}')
    print('-' * 59)
    print(f"{'TOTAL':<28}{sum(strata.values()):>11,.0f}"
          f"{sum(have.values()):>6}{sum(tgt.values()):>8.0f}{n_topup:>6}")
    print(f'\n[OK] wrote sample_points_vegtype2022.{{geojson,csv}} and '
          f'sample_design_vegtype2022.json')
    print(f'DONE in {round(time.time()-t0,1)}s')


if __name__ == '__main__':
    main()
