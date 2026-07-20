"""
18_ppi_all_methods_efg.py
=========================
PART 2, all estimators: EFG x condition-class area with every method from Part 1,
run PER EFG group (Arid / Valley / Mesic).

Within an EFG the three condition cells (strat9) are the strata. The estimand for
class k within EFG e is E[1(ref==k)] over that EFG's mapped area, times the EFG's
mapped area -> hectares. Design weights within the EFG make the labelled subsample
representative of the EFG population: w_i = Area_cell / n_cell (inverse inclusion).

Methods (mirror 16_ppi_all_methods.py, restricted to each EFG):
  olofsson       - stratified over the EFG's 3 cells (analytic binomial SE)
  classical      - design-weighted mean, no predictor
  ppi_continuous - PPI++ mean, predictor = p_k(x), per-EFG unlabeled pop
  ppi_bootstrap  - ppboot, weighted-mean estimator, per-EFG unlabeled (20k)
  crossppi       - crossppi_mean_ci (single fixed model -> caveat)
  label_shift    - ppi_distribution_label_shift_ci over the EFG's hard preds

Corrections applied after review:
  * Finding 1 - labels DEDUPLICATED to one adjudicated ref per point (462 unique);
    ARP vs SVM adjudication run as a sensitivity.
  * Finding 2 - the honest baseline in this per-EFG setting is the olofsson
    stratified estimator over the EFG's cells (already computed here); PPI SEs are
    to be judged against THAT, not against ppi_py's classical IID interval.
  * Finding 3 - the per-EFG population probability sample is reweighted with
    pop_calibration_weights so its cell shares match the known area weights.

Inputs: _sampled_probs.json (per-point p, joined to EFG via existing_tagged_efg.json),
_pop_prob_sample_efg.npy (cols p_i,p_m,p_s,efg), stratum_areas_efg.json (cell areas).

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/18_ppi_all_methods_efg.py
"""
import json, os
import numpy as np
from ppi_py import (ppi_mean_ci, ppi_mean_pointestimate, classical_mean_ci,
                    ppboot, crossppi_mean_ci, crossppi_mean_pointestimate,
                    ppi_distribution_label_shift_ci)
from _labels_common import (load_sampled_probs_efg, pop_calibration_weights,
                            qa_agreement)

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
EFG = {1: "Arid", 2: "Valley", 3: "Mesic"}
CLSN = {0: "intact", 1: "moderate", 2: "severe"}
STRATA = ["intact", "moderate", "severe"]
CIDX = {"intact": 0, "moderate": 1, "severe": 2}
Z = 1.959963984540054
ALPHA = 0.05
MIN_N_ESTIMABLE = 2
RNG = np.random.default_rng(42)


def remap(lab, mode):
    if lab == "transformed":
        return "severe"
    if lab == "nothicket":
        return "nothicket" if mode == "class" else "severe"
    return lab if lab in STRATA else None


def se_ci(lo, hi):
    return (hi - lo) / (2 * Z)


def efg_methods(rows_efg, cell_area_ha, efg_area_ha, Ppop_efg, mode, w_pop=None):
    """All estimators for one EFG. rows_efg: list of dicts for this EFG.
    cell_area_ha: {strat9 -> ha}. Ppop_efg: (M,3) population probs in this EFG."""
    # remap refs, keep those with a valid ref
    rr = []
    for r in rows_efg:
        ref = remap(r["label"], mode)
        if ref is None:
            continue
        rr.append(dict(map_cls=r["map_cls"], strat9=r["strat9"], ref=ref, p=r["p"]))
    if not rr:
        return None
    strat9 = np.array([r["strat9"] for r in rr])
    ref = np.array([r["ref"] for r in rr])
    P = np.array([r["p"] for r in rr])            # (n,3)
    cells = sorted(set(strat9.tolist()))
    n_cell = {c: int((strat9 == c).sum()) for c in cells}
    # design weight per point = Area_cell / n_cell  (only cells present)
    w = np.array([cell_area_ha[c] / n_cell[c] for c in strat9])
    A = efg_area_ha
    # calibrated unlabelled weights so the pop cell shares match the area weights
    w_unlab = (w_pop if w_pop is not None else np.ones(len(Ppop_efg)))
    non_estimable = any(n_cell[c] < MIN_N_ESTIMABLE for c in cells) or len(cells) < 3

    # label-shift needs hard preds; map_cls -> int. Under nothicket='class' a
    # nothicket ref has no CIDX entry; it is not one of the 3 thicket classes, so
    # map it to its map class's severe-ish slot is wrong -> instead only the
    # binary indicator Y=(ref==c) is used for the mean estimators; label-shift is
    # skipped when nothicket refs are present (K=3 class model doesn't cover it).
    has_nothicket = bool((ref == "nothicket").any())
    yhat_lab = np.array([CIDX[r["map_cls"]] for r in rr])
    yhat_pop = np.argmax(Ppop_efg, axis=1)
    ref_int = np.array([CIDX[r] if r in CIDX else -1 for r in ref])

    sub = RNG.choice(len(Ppop_efg), size=min(20000, len(Ppop_efg)), replace=False)

    res = {}
    for c in STRATA:
        i = CIDX[c]
        Y = (ref == c).astype(float)
        Yhat = P[:, i]
        Yun = Ppop_efg[:, i]
        d = {}

        # olofsson stratified over cells present in this EFG
        p_ol = 0.0
        var_ol = 0.0
        for cc in cells:
            m = strat9 == cc
            nn = int(m.sum())
            Wc = cell_area_ha[cc] / A       # cell weight within EFG
            phat = Y[m].mean()
            p_ol += Wc * phat
            if nn > 1:
                var_ol += Wc**2 * phat * (1 - phat) / (nn - 1)
        d["olofsson"] = {"area": p_ol * A, "se_area": np.sqrt(var_ol) * A}

        # classical weighted
        clo, chi = classical_mean_ci(Y, alpha=ALPHA, w=w)
        clo, chi = float(np.ravel(clo)[0]), float(np.ravel(chi)[0])
        d["classical"] = {"area": float(np.average(Y, weights=w)) * A, "se_area": se_ci(clo, chi) * A}

        # PPI++ continuous
        lo, hi = ppi_mean_ci(Y, Yhat, Yun, alpha=ALPHA, w=w, w_unlabeled=w_unlab)
        lo, hi = float(np.ravel(lo)[0]), float(np.ravel(hi)[0])
        pt = float(np.ravel(ppi_mean_pointestimate(Y, Yhat, Yun, w=w, w_unlabeled=w_unlab))[0])
        d["ppi_continuous"] = {"area": pt * A, "se_area": se_ci(lo, hi) * A}

        # ppboot (weighted-mean via stacked columns)
        def est(arr):
            return np.array([(arr[:, 1]).sum() / arr[:, 0].sum()])
        Ycol = np.column_stack([w, w * Y])
        Yhc = np.column_stack([w, w * Yhat])
        Yhc_un = np.column_stack([np.ones(len(sub)), Yun[sub]])
        try:
            blo, bhi = ppboot(est, Ycol, Yhc, Yhc_un, alpha=ALPHA,
                              n_resamples=300, n_resamples_lam=30)
            d["ppi_bootstrap"] = {"area": None, "se_area": se_ci(float(np.ravel(blo)[0]), float(np.ravel(bhi)[0])) * A}
        except Exception as e:
            d["ppi_bootstrap"] = {"error": str(e)[:80]}

        # cross-ppi
        try:
            xlo, xhi = crossppi_mean_ci(Y, Yhat, Yun[sub].reshape(-1, 1), alpha=ALPHA)
            xpt = float(np.ravel(crossppi_mean_pointestimate(Y, Yhat, Yun[sub].reshape(-1, 1)))[0])
            d["crossppi"] = {"area": xpt * A, "se_area": se_ci(float(np.ravel(xlo)[0]), float(np.ravel(xhi)[0])) * A}
        except Exception as e:
            d["crossppi"] = {"error": str(e)[:80]}

        res[c] = d

    # label-shift: one distribution, all K. Skipped when nothicket refs present:
    # the K=3 label-shift model only covers the 3 thicket classes.
    for c in STRATA:
        if has_nothicket:
            res[c]["label_shift"] = {"error": "skipped: nothicket ref not in K=3 model"}
            continue
        nu = np.zeros(3); nu[CIDX[c]] = 1.0
        try:
            lo, hi = ppi_distribution_label_shift_ci(ref_int, yhat_lab, yhat_pop, K=3,
                                                     nu=nu, alpha=ALPHA, return_counts=False)
            lo, hi = float(np.ravel(lo)[0]), float(np.ravel(hi)[0])
            res[c]["label_shift"] = {"area": (lo + hi) / 2 * A, "se_area": se_ci(lo, hi) * A}
        except Exception as e:
            res[c]["label_shift"] = {"error": str(e)[:80]}

    return {"n": len(rr), "n_cell": n_cell, "non_estimable": non_estimable,
            "classes": res}


def run_one(adjudicate, cell_area_ha, efg_area_ha, Ppop, Epop):
    rows = load_sampled_probs_efg(adjudicate)
    report = {"adjudicate": adjudicate,
              "part": "2b - EFG x class area, all Part-1 methods (per EFG)",
              "n_unique_points": len(rows),
              "qa_agreement": qa_agreement(),
              "methods": ["olofsson", "ppi_continuous", "ppi_bootstrap", "crossppi",
                          "label_shift", "classical"],
              "baseline_method": "olofsson",
              "scenarios": {}}
    for mode, tag in [("class", "A_nothicket_class"), ("severe", "B_nothicket_to_severe")]:
        sc = {"nothicket_mode": mode, "efg": {}}
        for eid, ename in EFG.items():
            rows_e = [r for r in rows if r["efg_id"] == eid]
            mask = Epop == eid
            Pe = Ppop[mask]
            w_pop_e = pop_calibration_weights(Pe, efg=Epop[mask])
            sc["efg"][ename] = efg_methods(rows_e, cell_area_ha, efg_area_ha[eid],
                                           Pe, mode, w_pop=w_pop_e)
        report["scenarios"][tag] = sc
    return report


def main():
    areas = json.load(open(os.path.join(RESULTS, "stratum_areas_efg.json")))["area_m2"]
    cell_area_ha = {int(k): v / 1e4 for k, v in areas.items()}
    efg_area_ha = {e: sum(cell_area_ha[s] for s in cell_area_ha if s // 10 == e) for e in EFG}
    pop = np.load(os.path.join(RESULTS, "_pop_prob_sample_efg.npy"))
    Ppop, Epop = pop[:, :3].astype(np.float64), pop[:, 3].astype(int)

    reports = {adj: run_one(adj, cell_area_ha, efg_area_ha, Ppop, Epop)
               for adj in ("ARP", "SVM")}
    outp = os.path.join(RESULTS, "ppi_all_methods_efg.json")
    json.dump({"sensitivity_adjudication": reports}, open(outp, "w"), indent=2)
    print("wrote", outp)

    lab = {"olofsson": "Olofsson", "ppi_continuous": "PPI++", "ppi_bootstrap": "PPIboot",
           "crossppi": "CrossPPI", "label_shift": "LblShift", "classical": "Classic"}
    for adj, report in reports.items():
        print("\n" + "#" * 92)
        print(f"# ADJUDICATION = {adj}  (n_unique = {report['n_unique_points']})   "
              f"baseline = olofsson (Finding 2)")
        print("#" * 92)
        for tag, sc in report["scenarios"].items():
            print("\n" + "=" * 92)
            print(f"SCENARIO {tag}  (nothicket -> {sc['nothicket_mode']})   SE(area, ha) per method")
            print("=" * 92)
            for ename in ["Arid", "Valley", "Mesic"]:
                e = sc["efg"][ename]
                flag = "  [NON-ESTIMABLE]" if e.get("non_estimable") else ""
                print(f"\n{ename}  (n={e['n']}, cells {e['n_cell']}){flag}")
                print(f"{'class':>9} | " + " | ".join(f"{lab[m]:>9}" for m in report["methods"]))
                for c in STRATA:
                    cells = []
                    for m in report["methods"]:
                        v = e["classes"][c].get(m, {})
                        cells.append(f"{v.get('se_area', float('nan')):>9,.0f}" if "se_area" in v else f"{'err':>9}")
                    print(f"{c:>9} | " + " | ".join(cells))


if __name__ == "__main__":
    main()
