"""
17_area_estimation_efg.py
=========================
PART 2 of the area estimation: design-based area by EFG group x condition class.

The UNIQUE labelled points (462 after deduplication -- see analysis/_labels_common.py)
are post-stratified into the 9 EFG x class cells (strat9 = efg_id*10 + cls; EFG
1/2/3 = Arid/Valley/Mesic; cls 0/1/2 = intact/moderate/severe). strat9 nests
exactly inside the 3 severity strata the sample was drawn under, and the draw is
random within each severity stratum, so each strat9 cell holds a random subset ->
post-stratification is valid.

Estimator (Olofsson 2014, 9 strata):
  Within EFG e, the error-adjusted area of true class k is
      A_hat(e,k) = sum_{h in cells of e} Area_h * p_hat(ref=k | h)
  where p_hat(ref=k | h) = n_hk / n_h and Area_h is the KNOWN mapped area of
  cell h (from results/stratum_areas_efg.json). Variance per cell is binomial:
      V = sum_h Area_h^2 * (phat)(1-phat)/(n_h - 1).
  Reported: per-EFG condition composition (area + 95% CI) and per-cell user's
  accuracy (diagonal reliability of each mapped EFG x class cell).

Corrections applied after review:
  * Finding 1 - labels DEDUPLICATED to one adjudicated ref per point; ARP vs SVM
    adjudication run as a sensitivity (both arms written out).
  * Finding 4 - nothicket kept as its own reference class (scenario A) instead of
    being dropped and reallocated; scenario B still folds nothicket->severe.
  * Finding 5 - cells below MIN_N_ESTIMABLE are marked non_estimable and an EFG
    containing any non-estimable / empty cell is flagged estimable=False. An n<=1
    cell contributes NO estimable variance, so such an EFG composition is NOT to
    be reported as a final estimate. Mesic-severe (cell 32) has n=3 vs a target of
    129 and trips this flag.

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/17_area_estimation_efg.py
"""
import json, os
import numpy as np
from _labels_common import load_adjudicated, qa_agreement

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "results")

EFG = {1: "Arid", 2: "Valley", 3: "Mesic"}
CLS = {0: "intact", 1: "moderate", 2: "severe"}
STRATA = ["intact", "moderate", "severe"]
Z = 1.959963984540054
LOW_N = 10             # cells below this are flagged unstable (soft warning)
MIN_N_ESTIMABLE = 2   # a cell needs n>=2 to contribute any variance; below this
                      # the EFG composition is NOT estimable and must not be reported


def remap_label(lab, mode):
    """transformed->severe always. nothicket: 'class' keeps it as 'nothicket'
    (4th ref class); 'severe' folds it into severe. Returns the ref label or None
    if it should be excluded."""
    if lab == "transformed":
        return "severe"
    if lab == "nothicket":
        return "nothicket" if mode == "class" else "severe"
    return lab if lab in STRATA else None


def run_scenario(rows, tag, areas_ha, mode):
    ref_classes = STRATA + (["nothicket"] if mode == "class" else [])
    # group labelled points by strat9 cell
    cells = {}  # strat9 -> list of ref labels (remapped)
    for r in rows:
        t = tag.get(r["id"])
        if t is None:
            continue
        ref = remap_label(r["label"], mode)
        if ref is None:
            continue
        cells.setdefault(t["strat9"], []).append(ref)

    # per-cell stats
    per_cell = {}
    for s9, area in areas_ha.items():
        refs = cells.get(s9, [])
        n = len(refs)
        e, c = s9 // 10, s9 % 10
        map_cls = CLS[c]
        counts = {k: refs.count(k) for k in ref_classes}
        # user's accuracy of this mapped cell = fraction truly its own map class
        ua = counts[map_cls] / n if n else float("nan")
        ua_se = np.sqrt(ua * (1 - ua) / (n - 1)) if n > 1 else float("nan")
        per_cell[s9] = {
            "efg": EFG[e], "efg_id": e, "map_class": map_cls,
            "area_mapped_ha": area, "n": n, "ref_counts": counts,
            "users_acc": ua, "users_acc_se": ua_se,
            "low_n": n < LOW_N,
            "non_estimable": n < MIN_N_ESTIMABLE,   # n<2 -> no variance estimable
        }

    # per-EFG condition composition: A_hat(e,k) = sum_h area_h * p(ref=k|h)
    per_efg = {}
    for eid, ename in EFG.items():
        cell_ids = [s9 for s9 in areas_ha if s9 // 10 == eid]
        efg_area = sum(areas_ha[s] for s in cell_ids)
        # estimability: every cell in the EFG must have n>=MIN_N_ESTIMABLE.
        non_est_cells = [s for s in cell_ids if per_cell[s]["non_estimable"]]
        estimable = len(non_est_cells) == 0
        comp = {}
        for k in ref_classes:
            a = 0.0
            var = 0.0
            for s9 in cell_ids:
                refs = cells.get(s9, [])
                n = len(refs)
                area = areas_ha[s9]
                if n == 0:
                    continue
                p = refs.count(k) / n
                a += area * p
                if n > 1:
                    var += area**2 * p * (1 - p) / (n - 1)
            se = np.sqrt(var)
            comp[k] = {"area": a, "se": se, "ci95": [a - Z*se, a + Z*se],
                       "frac": a / efg_area if efg_area else float("nan")}
        per_efg[ename] = {
            "efg_id": eid, "area_total_ha": efg_area,
            "n_labelled": sum(per_cell[s]["n"] for s in cell_ids),
            "composition": comp,
            "any_low_n": any(per_cell[s]["low_n"] for s in cell_ids),
            "estimable": estimable,
            "non_estimable_cells": non_est_cells,
        }

    return {"nothicket_mode": mode, "ref_classes": ref_classes,
            "per_cell": per_cell, "per_efg": per_efg}


def run_one(adjudicate, areas_ha, tag):
    rows = load_adjudicated(adjudicate)
    report = {
        "adjudicate": adjudicate,
        "part": "2 - area by EFG group x condition class",
        "strat9_def": "efg_id*10 + cls; EFG 1/2/3=Arid/Valley/Mesic; cls 0/1/2=intact/moderate/severe",
        "n_unique_points": len(rows),
        "qa_agreement": qa_agreement(),
        "area_total_ha": sum(areas_ha.values()),
        "areas_ha_by_cell": {str(k): v for k, v in areas_ha.items()},
        "low_n_threshold": LOW_N,
        "min_n_estimable": MIN_N_ESTIMABLE,
        "scenarios": {},
    }
    for mode, t in [("class", "A_nothicket_class"), ("severe", "B_nothicket_to_severe")]:
        report["scenarios"][t] = run_scenario(rows, tag, areas_ha, mode)
    return report


def main():
    areas_m2 = json.load(open(os.path.join(RESULTS, "stratum_areas_efg.json")))["area_m2"]
    areas_ha = {int(k): v / 1e4 for k, v in areas_m2.items()}
    tag = {r["id"]: r for r in
           json.load(open(os.path.join(RESULTS, "existing_tagged_efg.json")))["existing"]}

    reports = {adj: run_one(adj, areas_ha, tag) for adj in ("ARP", "SVM")}
    outp = os.path.join(RESULTS, "area_estimation_efg.json")
    json.dump({"sensitivity_adjudication": reports}, open(outp, "w"), indent=2)
    print("wrote", outp)

    qa = qa_agreement()
    print(f"\nQA double-labelling: {qa['n_duplicated']} points, "
          f"{qa['n_agree']} agree, {qa['n_disagree']} disagree")

    for adj, report in reports.items():
        print("\n" + "#" * 82)
        print(f"# ADJUDICATION = {adj}   (n_unique = {report['n_unique_points']})")
        print("#" * 82)
        for t, sc in report["scenarios"].items():
            print("\n" + "=" * 82)
            print(f"SCENARIO {t}  (nothicket -> {sc['nothicket_mode']})")
            print("=" * 82)
            print("Per-EFG condition composition (error-adjusted area, ha, +/-95% CI):")
            hdr_classes = sc["ref_classes"]
            print(f"{'EFG':>7} {'total ha':>10} {'n':>4} {'est?':>5} | "
                  + " | ".join(f"{c:>18}" for c in hdr_classes))
            for ename in ["Arid", "Valley", "Mesic"]:
                e = sc["per_efg"][ename]
                cells = []
                for k in hdr_classes:
                    d = e["composition"][k]
                    cells.append(f"{d['area']:>7,.0f}+/-{Z*d['se']:>6,.0f}")
                est = "OK" if e["estimable"] else "NO"
                print(f"{ename:>7} {e['area_total_ha']:>10,.0f} {e['n_labelled']:>4} "
                      f"{est:>5} | " + " | ".join(cells))
                if not e["estimable"]:
                    print(f"        ^ NON-ESTIMABLE: cells {e['non_estimable_cells']} "
                          f"have n<{MIN_N_ESTIMABLE}; composition must NOT be reported.")
            print("\nPer-cell user's accuracy (reliability of each mapped EFG x class cell):")
            for s9 in sorted(sc["per_cell"]):
                pc = sc["per_cell"][s9]
                ua = pc["users_acc"]
                flags = []
                if pc["non_estimable"]:
                    flags.append("NON-ESTIMABLE")
                elif pc["low_n"]:
                    flags.append("low-n")
                flag = ("  [" + ",".join(flags) + "]") if flags else ""
                uas = f"{ua:.2f}" if ua == ua else "n/a"
                print(f"  {pc['efg']:>7} {pc['map_class']:>9} (cell {s9}): "
                      f"n={pc['n']:>3}  UA={uas}{flag}")


if __name__ == "__main__":
    main()
