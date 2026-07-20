"""
15_ppi_continuous.py
====================
"Real" PPI++ area estimation using the CONTINUOUS RF probability surfaces as the
auxiliary prediction, instead of the discrete argmax stratum used in
14_area_estimation.py. This is the setting where PPI++ *could* tighten CIs below
the stratified estimator, because the per-pixel probability carries information
within strata.

Per class c (intact/moderate/severe):
  estimand   : population proportion truly class c = E[ 1(ref==c) ]
  labelled   : Y = 1(ref==c) ; Yhat = p_c(x)  (RF predicted prob at the point)
  unlabelled : Yhat = p_c(x) over the population probability sample
  weights    : labelled points weighted by W_h/n_h (inverse inclusion prob) to
               respect the stratified design; unlabelled points weighted so the
               EFG x mapped-class cells match the known area weights (Finding 3).

Corrections applied after review:
  * Finding 1 - labels DEDUPLICATED to one adjudicated ref per point (462 unique);
    ARP vs SVM adjudication run as a sensitivity.
  * Finding 2 - the honest baseline is the PROPER stratified SE
    (Sigma_h W_h^2 s_h^2 / n_h), not ppi_py's classical_mean_ci (IID
    std(weighted residual)/sqrt(n)). The PPI CI is compared against THAT.
  * Finding 3 - the population probability sample is reweighted with
    pop_calibration_weights so its class shares match the known mapped areas,
    instead of being treated as uniformly representative.

Reference remap: transformed->severe always; nothicket tested class(kept) | severe.

Inputs:
  analysis/results/_sampled_probs.json     per-point sampled probabilities
  analysis/results/_pop_prob_sample.npy    (N,3) population probability sample
  analysis/results/sample_design.json      W_h, total area
  analysis/results/area_estimation.json    Olofsson baseline to compare against

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/15_ppi_continuous.py
"""
import json, os
import numpy as np
from ppi_py import ppi_mean_ci, ppi_mean_pointestimate
from _labels_common import load_sampled_probs, pop_calibration_weights, qa_agreement

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
STRATA = ["intact", "moderate", "severe"]
Z = 1.959963984540054
ALPHA = 0.05
CIDX = {"intact": 0, "moderate": 1, "severe": 2}


def remap(pts, mode):
    """transformed->severe always; nothicket: 'class' keeps it (excluded from the
    3 thicket-class estimands here since this script estimates intact/moderate/
    severe proportions only), 'severe' folds it into severe."""
    out = []
    for r in pts:
        ref = r["label"]
        if ref == "transformed":
            ref = "severe"
        elif ref == "nothicket":
            if mode == "class":
                # keep as its own class -> not one of the 3 thicket estimands;
                # such a point is 0 for all three indicators, which is correct.
                ref = "nothicket"
            else:
                ref = "severe"
        if ref not in STRATA + ["nothicket"]:
            continue
        out.append(dict(stratum=r["stratum"], ref=ref, p=r["p"]))
    return out


def stratified_mean(rows, W, ref_class):
    """Proper stratified proportion + SE (Finding 2 baseline)."""
    p, var = 0.0, 0.0
    for h in STRATA:
        y = np.array([1.0 if r["ref"] == ref_class else 0.0
                      for r in rows if r["stratum"] == h])
        n = len(y)
        ph = y.mean() if n else 0.0
        p += W[h] * ph
        if n > 1:
            sh2 = ph * (1 - ph) * n / (n - 1)
            var += W[h]**2 * sh2 / n
    return p, np.sqrt(var)


def run_one(adjudicate, W, A, Ppop, w_pop, baseline):
    pts_all = load_sampled_probs(adjudicate)
    report = {"adjudicate": adjudicate,
              "method": "PPI++ with continuous RF probability predictor",
              "pop_sample_n": int(len(Ppop)),
              "pop_calibrated": True,
              "pop_mean_prob_raw": {c: float(Ppop[:, CIDX[c]].mean()) for c in STRATA},
              "pop_mean_prob_calibrated": {
                  c: float(np.average(Ppop[:, CIDX[c]], weights=w_pop)) for c in STRATA},
              "scenarios": {}}

    for mode, tag in [("class", "A_nothicket_class"), ("severe", "B_nothicket_to_severe")]:
        pts = remap(pts_all, mode)
        strat = np.array([r["stratum"] for r in pts])
        ref = np.array([r["ref"] for r in pts])
        Plab = np.array([r["p"] for r in pts])  # (n,3)
        n_h = {h: int((strat == h).sum()) for h in STRATA}
        w_lab = np.array([W[h] / n_h[h] for h in strat])

        base = baseline["sensitivity_adjudication"][adjudicate]["scenarios"][tag]
        olof_area = base["olofsson"]["area_ha"]
        olof_prop = base["olofsson"]["area_proportion"]
        sc = {"nothicket_mode": mode, "n_used": len(pts), "n_h": n_h, "classes": {}}
        for c in STRATA:
            ci = CIDX[c]
            Y = (ref == c).astype(float)
            Yhat = Plab[:, ci]
            Yhat_un = Ppop[:, ci]

            lo, hi = ppi_mean_ci(Y, Yhat, Yhat_un, alpha=ALPHA,
                                 w=w_lab, w_unlabeled=w_pop)
            lo, hi = float(np.ravel(lo)[0]), float(np.ravel(hi)[0])
            pt = float(np.ravel(ppi_mean_pointestimate(
                Y, Yhat, Yhat_un, w=w_lab, w_unlabeled=w_pop))[0])
            se_ppi = (hi - lo) / (2 * Z)

            # PROPER stratified (design) baseline, no predictor (Finding 2)
            sp, sse = stratified_mean(pts, W, c)

            se_olof = olof_prop[c]["se"]
            sc["classes"][c] = {
                "ppi_continuous": {"p": pt, "se": se_ppi,
                                   "area": pt * A, "area_pm": Z * se_ppi * A,
                                   "ci95_area": [(pt - Z*se_ppi)*A, (pt + Z*se_ppi)*A]},
                "stratified": {"p": sp, "se": sse, "area": sp * A},
                "olofsson": {"p": olof_prop[c]["p"], "se": se_olof,
                             "area": olof_area[c]["area"]},
                "se_ratio_ppi_over_stratified": se_ppi / sse if sse else float("nan"),
                "ci_reduction_vs_stratified_pct": (1 - se_ppi / sse) * 100 if sse else float("nan"),
                "ppi_beats_stratified": bool(se_ppi < sse),
            }
        report["scenarios"][tag] = sc
    return report


def run():
    design = json.load(open(os.path.join(RESULTS, "sample_design.json")))
    W = design["W"]
    A = design["area_total_ha"]
    Ppop = np.load(os.path.join(RESULTS, "_pop_prob_sample.npy")).astype(np.float64)
    # 3-class calibration (no EFG column in this file): match the 3-class marginal
    # area weights derived from the nine cells.
    w_pop = pop_calibration_weights(Ppop, efg=None)
    baseline = json.load(open(os.path.join(RESULTS, "area_estimation.json")))

    reports = {adj: run_one(adj, W, A, Ppop, w_pop, baseline) for adj in ("ARP", "SVM")}
    outp = os.path.join(RESULTS, "ppi_continuous.json")
    json.dump({"sensitivity_adjudication": reports}, open(outp, "w"), indent=2)
    print("wrote", outp)

    for adj, report in reports.items():
        print("\n" + "#" * 84)
        print(f"# ADJUDICATION = {adj}")
        print("#" * 84)
        print("Pop mean predicted prob (raw -> calibrated):")
        for c in STRATA:
            print(f"  {c:>9}: {report['pop_mean_prob_raw'][c]:.4f} -> "
                  f"{report['pop_mean_prob_calibrated'][c]:.4f}")
        for tag, sc in report["scenarios"].items():
            print("\n" + "=" * 84)
            print(f"SCENARIO {tag}  (nothicket -> {sc['nothicket_mode']}, n={sc['n_used']})")
            print("=" * 84)
            print(f"{'class':>9} | {'stratified area+/-':>22} | {'PPI++ (cont) area+/-':>24} | "
                  f"{'SE ratio':>8} | {'beats?':>6}")
            for c in STRATA:
                d = sc["classes"][c]
                sa = d["stratified"]["area"]; spm = Z * d["stratified"]["se"] * A
                pa = d["ppi_continuous"]["area"]; ppm = d["ppi_continuous"]["area_pm"]
                beats = "yes" if d["ppi_beats_stratified"] else "no"
                print(f"{c:>9} | {sa:>10,.0f} +/-{spm:>8,.0f} | {pa:>11,.0f} +/-{ppm:>9,.0f} | "
                      f"{d['se_ratio_ppi_over_stratified']:>8.3f} | {beats:>6}")


if __name__ == "__main__":
    run()
