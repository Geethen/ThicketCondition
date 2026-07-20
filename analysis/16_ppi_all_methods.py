"""
16_ppi_all_methods.py
=====================
Test ALL applicable PPI / PPI++ estimators for the thicket-condition area problem
and line them up against the Olofsson (2014) design-based baseline.

Estimand (per class c in intact/moderate/severe): population proportion truly c,
i.e. E[1(ref==c)], times total area A -> hectares.

Methods:
  0. Olofsson 2014 stratified            (design-based baseline; from 14_*.py output)
  1. Classical weighted mean CI          (design SRS baseline, no predictor)
  2. PPI++ mean, discrete stratum        (predictor = 1(map==c))          [14_*.py]
  3. PPI++ mean, continuous prob         (predictor = p_c(x))             [15_*.py]
  4. PPI++ bootstrap (ppboot)            (continuous predictor, weighted estimator)
  5. Cross-PPI mean (crossppi_mean_ci)   (continuous predictor; SINGLE model -> caveat)
  6. PPI label-shift (distribution)      (hard argmax preds, confusion-matrix correction)

Weighting: labelled sample is stratified -> design weights w_i = W_h/n_h make the
labelled set representative of the population. ppi_mean_ci/classical take w= directly.
ppboot bakes weights into the estimator. crossppi and label-shift take no weights:
  - crossppi: we pass the raw sample (caveat: ignores stratification; shown for
    completeness only).
  - label-shift: assumes labelled Yhat ~ unlabeled Yhat distribution; it corrects
    via the confusion matrix so the stratified draw is handled by design.

Corrections applied after review:
  * Finding 1 - labels DEDUPLICATED to one adjudicated ref per point; ARP vs SVM
    sensitivity. * Finding 2 - Olofsson (proper stratified) is the baseline PPI is
    judged against. * Finding 3 - population sample calibrated to known area weights.

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/16_ppi_all_methods.py
"""
import json, os
import numpy as np
from ppi_py import (ppi_mean_ci, ppi_mean_pointestimate, classical_mean_ci,
                    ppboot, crossppi_mean_ci, crossppi_mean_pointestimate,
                    ppi_distribution_label_shift_ci)
from _labels_common import (load_sampled_probs, pop_calibration_weights,
                            qa_agreement)

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
STRATA = ["intact", "moderate", "severe"]
CIDX = {"intact": 0, "moderate": 1, "severe": 2}
Z = 1.959963984540054
ALPHA = 0.05
RNG = np.random.default_rng(42)


def remap(pts, mode):
    out = []
    for r in pts:
        ref = r["label"]
        if ref == "transformed":
            ref = "severe"
        elif ref == "nothicket":
            if mode == "class":
                ref = "nothicket"    # own class; 0 for all 3 thicket indicators
            else:
                ref = "severe"
        if ref not in STRATA + ["nothicket"]:
            continue
        out.append(dict(stratum=r["stratum"], ref=ref, p=r["p"]))
    return out


def se_from_ci(lo, hi):
    return (hi - lo) / (2 * Z)


def run_scenario(pts, W, A, Ppop, yhat_pop, olof_prop, w_pop):
    strat = np.array([r["stratum"] for r in pts])
    ref = np.array([r["ref"] for r in pts])
    Plab = np.array([r["p"] for r in pts])          # (n,3) probs at labelled points
    yhat_lab = np.argmax(Plab, axis=1)              # hard pred (== stratum argmax)
    n_h = {h: int((strat == h).sum()) for h in STRATA}
    w_lab = np.array([W[h] / n_h[h] for h in strat])
    w_unlab = w_pop
    has_nothicket = bool((ref == "nothicket").any())

    out = {}
    # ---- label-shift: one call gives all K class counts. Skipped if nothicket
    # refs present (K=3 model doesn't cover a 4th class). ----
    ref_int = np.array([CIDX[c] if c in CIDX else -1 for c in ref])
    ls_ci = {}
    for c in STRATA:
        if has_nothicket:
            ls_ci[c] = None
            continue
        nu = np.zeros(3); nu[CIDX[c]] = 1.0
        lo, hi = ppi_distribution_label_shift_ci(
            ref_int, yhat_lab, yhat_pop, K=3, nu=nu, alpha=ALPHA, return_counts=False)
        ls_ci[c] = (float(np.ravel(lo)[0]), float(np.ravel(hi)[0]))

    for c in STRATA:
        i = CIDX[c]
        Y = (ref == c).astype(float)
        Yhat_cont = Plab[:, i]
        Yhat_un_cont = Ppop[:, i]

        rec = {}

        # 1. classical weighted
        clo, chi = classical_mean_ci(Y, alpha=ALPHA, w=w_lab)
        clo, chi = float(np.ravel(clo)[0]), float(np.ravel(chi)[0])
        rec["classical"] = {"p": float(np.average(Y, weights=w_lab)),
                            "se": se_from_ci(clo, chi)}

        # 3. PPI++ continuous
        lo, hi = ppi_mean_ci(Y, Yhat_cont, Yhat_un_cont, alpha=ALPHA,
                             w=w_lab, w_unlabeled=w_unlab)
        lo, hi = float(np.ravel(lo)[0]), float(np.ravel(hi)[0])
        pt = float(np.ravel(ppi_mean_pointestimate(
            Y, Yhat_cont, Yhat_un_cont, w=w_lab, w_unlabeled=w_unlab))[0])
        rec["ppi_continuous"] = {"p": pt, "se": se_from_ci(lo, hi)}

        # 4. ppboot with weighted-mean estimator (weights baked in via closure)
        #    ppboot resamples rows; to keep design weighting we use a weighted mean
        #    estimator that takes Y (and uses module-captured weights aligned by value
        #    is unsafe under resampling) -> instead fold weights into pseudo-values.
        #    Cleanest: estimator = weighted mean, but ppboot resamples indices without
        #    weights. We therefore bootstrap on the weighted transform: since weighted
        #    mean = sum(w*Y)/sum(w), define estimator on stacked (w, w*Y) columns.
        def est(arr):
            wcol = arr[:, 0]; wy = arr[:, 1]
            return np.array([wy.sum() / wcol.sum()])
        Ycol = np.column_stack([w_lab, w_lab * Y])
        # predictor analogue for unlabeled: use w=1, wy = Yhat
        Yhatcol = np.column_stack([w_lab, w_lab * Yhat_cont])
        Yhatcol_un = np.column_stack([np.ones(len(Yhat_un_cont)), Yhat_un_cont])
        # subsample unlabeled for the resampling-heavy methods (mean/dist is all
        # they use; 20k is ample and keeps runtime sane)
        sub = RNG.choice(len(Yhat_un_cont), size=min(20000, len(Yhat_un_cont)),
                         replace=False)
        Yhatcol_un_s = np.column_stack([np.ones(len(sub)), Yhat_un_cont[sub]])
        try:
            blo, bhi = ppboot(est, Ycol, Yhatcol, Yhatcol_un_s, alpha=ALPHA,
                              n_resamples=300, n_resamples_lam=30)
            blo, bhi = float(np.ravel(blo)[0]), float(np.ravel(bhi)[0])
            rec["ppi_bootstrap"] = {"p": None, "se": se_from_ci(blo, bhi)}
        except Exception as e:
            rec["ppi_bootstrap"] = {"error": str(e)[:120]}

        # 5. cross-PPI (single model -> replicate as 1 "fold"; caveat noted)
        try:
            un_s = Yhat_un_cont[sub].reshape(-1, 1)
            xlo, xhi = crossppi_mean_ci(Y, Yhat_cont, un_s, alpha=ALPHA)
            xlo, xhi = float(np.ravel(xlo)[0]), float(np.ravel(xhi)[0])
            xpt = float(np.ravel(crossppi_mean_pointestimate(Y, Yhat_cont, un_s))[0])
            rec["crossppi"] = {"p": xpt, "se": se_from_ci(xlo, xhi),
                               "note": "single fixed model; unweighted -> illustrative only"}
        except Exception as e:
            rec["crossppi"] = {"error": str(e)[:120]}

        # 6. label-shift
        if ls_ci[c] is None:
            rec["label_shift"] = {"error": "skipped: nothicket ref not in K=3 model"}
        else:
            lo, hi = ls_ci[c]
            rec["label_shift"] = {"p": (lo + hi) / 2, "se": se_from_ci(lo, hi),
                                  "note": "hard preds + confusion-matrix correction"}

        # 0. Olofsson baseline
        rec["olofsson"] = {"p": olof_prop[c]["p"], "se": olof_prop[c]["se"]}

        # attach areas + CI for each method
        for m, d in rec.items():
            if "se" in d:
                p = d.get("p")
                se = d["se"]
                d["se_area"] = se * A
                if p is not None:
                    d["area"] = p * A
                    d["area_ci95"] = [(p - Z*se)*A, (p + Z*se)*A]
        out[c] = rec
    return out, n_h


def run_one(adjudicate, W, A, Ppop, yhat_pop, w_pop, base):
    raw = load_sampled_probs(adjudicate)
    report = {"adjudicate": adjudicate,
              "method_order": ["olofsson", "classical", "ppi_continuous",
                               "ppi_bootstrap", "crossppi", "label_shift"],
              "baseline_method": "olofsson",
              "n_unique_points": len(raw),
              "qa_agreement": qa_agreement(),
              "area_total_ha": A, "scenarios": {}}
    for mode, tag in [("class", "A_nothicket_class"), ("severe", "B_nothicket_to_severe")]:
        pts = remap(raw, mode)
        olof_prop = base["sensitivity_adjudication"][adjudicate]["scenarios"][tag]["olofsson"]["area_proportion"]
        cls_res, n_h = run_scenario(pts, W, A, Ppop, yhat_pop, olof_prop, w_pop)
        report["scenarios"][tag] = {"nothicket_mode": mode, "n_used": len(pts),
                                    "n_h": n_h, "classes": cls_res}
    return report


def main():
    design = json.load(open(os.path.join(RESULTS, "sample_design.json")))
    W = design["W"]; A = design["area_total_ha"]
    Ppop = np.load(os.path.join(RESULTS, "_pop_prob_sample.npy")).astype(np.float64)
    yhat_pop = np.load(os.path.join(RESULTS, "_pop_yhat_hard.npy")).astype(int)
    w_pop = pop_calibration_weights(Ppop, efg=None)   # Finding 3 (3-class marginal)
    base = json.load(open(os.path.join(RESULTS, "area_estimation.json")))

    reports = {adj: run_one(adj, W, A, Ppop, yhat_pop, w_pop, base)
               for adj in ("ARP", "SVM")}
    outp = os.path.join(RESULTS, "ppi_all_methods.json")
    json.dump({"sensitivity_adjudication": reports}, open(outp, "w"), indent=2)
    print("wrote", outp)

    labels = {"olofsson": "Olofsson", "classical": "Classical",
              "ppi_continuous": "PPI++ (cont)", "ppi_bootstrap": "PPI boot",
              "crossppi": "Cross-PPI", "label_shift": "Label-shift"}
    for adj, report in reports.items():
        print("\n" + "#" * 90)
        print(f"# ADJUDICATION = {adj}  (n_unique = {report['n_unique_points']})   "
              f"baseline = Olofsson (Finding 2)")
        print("#" * 90)
        for tag, sc in report["scenarios"].items():
            print("\n" + "=" * 90)
            print(f"SCENARIO {tag}  (nothicket -> {sc['nothicket_mode']}, n={sc['n_used']})")
            print("=" * 90)
            hdr = f"{'class':>9} | " + " | ".join(f"{labels[m]:>13}" for m in report["method_order"])
            print("SE on area (ha):"); print(hdr)
            for c in STRATA:
                row = f"{c:>9} | "
                row += " | ".join(f"{sc['classes'][c][m].get('se_area', float('nan')):>13,.0f}"
                                  for m in report["method_order"])
                print(row)


if __name__ == "__main__":
    main()
