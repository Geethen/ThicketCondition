"""
14_area_estimation.py
=====================
Design-based area estimation for the 3-class thicket-condition map, using the
reference labels returned by two labellers (ARP, SVM/Steph).

Two estimators are computed and compared:
  1. Olofsson et al. (2014) stratified estimator (design-based, analytic SE/CI).
  2. PPI++ (Angelopoulos, Duchi, Zrnic 2023) prediction-powered mean CI, using
     the map class indicator as the auxiliary prediction and inverse-inclusion-
     probability weights to respect the stratified design.

Corrections applied after review:
  * Finding 1 - labels are DEDUPLICATED to one adjudicated reference label per
    unique point (474 rows -> 462 unique). The 12 QA duplicates no longer count
    as independent samples. Adjudication (ARP vs SVM on the 4 disagreements) is a
    SENSITIVITY arm; see analysis/_labels_common.py.
  * Finding 2 - the PPI comparison now reports the PROPER stratified variance
    (Sigma_h W_h^2 s_h^2 / n_h) as the classical baseline, not ppi_py's IID
    std(weighted residual)/sqrt(n). The earlier IID "classical" SE understated
    the design baseline and made PPI look better than it is; the corrected
    baseline is what PPI must beat.
  * Finding 4 - `drop_nothicket` no longer renormalises the thicket classes over
    the full mapped area. `nothicket` is retained as a fourth REFERENCE class so
    the true non-thicket area is estimated explicitly; the intact/moderate/severe
    areas then sum to (A_total - A_nothicket), not A_total.

Reference-class remapping (per user):
  - `transformed` -> `severe`  (always)
  - `nothicket`   -> tested TWO ways:
        Scenario A ("nothicket_class"): nothicket kept as its own reference class
          (its area is estimated; thicket classes sum to A_total - A_nothicket).
        Scenario B ("nothicket_severe"): nothicket -> severe (ecological choice:
          fully transformed land counted as severe).

Map (stratum) classes: intact, moderate, severe. Strata weights W_h and mapped
areas come from analysis/results/sample_design.json (Olofsson design).

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/14_area_estimation.py
"""
import json, os
import numpy as np
from ppi_py import ppi_mean_ci, ppi_mean_pointestimate
from _labels_common import load_adjudicated, qa_agreement

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "results")

STRATA = ["intact", "moderate", "severe"]        # map classes
Z = 1.959963984540054                            # 95% two-sided normal quantile
ALPHA = 0.05


def remap(rows, nothicket_mode):
    """Apply transformed->severe always; nothicket per scenario.

    nothicket_mode:
      'class'  -> keep 'nothicket' as its own reference class (4th class). The
                  reference set is STRATA + ['nothicket']; thicket-class areas do
                  NOT get the non-thicket area reallocated to them.
      'severe' -> nothicket folded into 'severe' (ecological definition).
    Returns (rows_out, ref_classes) where each row has {stratum, ref}."""
    if nothicket_mode == "class":
        ref_classes = STRATA + ["nothicket"]
    else:
        ref_classes = STRATA
    out = []
    for r in rows:
        ref = r["label"]
        if ref == "transformed":
            ref = "severe"
        elif ref == "nothicket" and nothicket_mode == "severe":
            ref = "severe"
        if ref not in ref_classes:
            continue  # safety
        out.append(dict(stratum=r["stratum"], ref=ref))
    return out, ref_classes


# ------------------------------------------------------- Olofsson 2014 estimator
def olofsson(rows, W, area_total_ha, ref_classes):
    """Stratified estimator of area proportion per reference class (Olofsson 2014).

    Estimator of proportion of class k:
        p_hat_k = sum_h W_h * (n_hk / n_h)                              (Eq. 9)
    Variance (Eq. 10):
        V(p_hat_k) = sum_h W_h^2 * (n_hk/n_h)(1 - n_hk/n_h) / (n_h - 1)
    Reports area = p_hat * A_total, with 95% CI = +/- Z*SE.

    ref_classes may include 'nothicket' as a 4th class; the map strata are always
    STRATA (intact/moderate/severe). Accuracy metrics (OA/UA/PA) are computed only
    over the 3 map classes (diagonal defined only where ref==map class)."""
    strata = STRATA
    n_h = {h: sum(1 for r in rows if r["stratum"] == h) for h in strata}
    # confusion counts n_hk : stratum h (map) x ref class k (k over ref_classes)
    n_hk = {h: {k: sum(1 for r in rows if r["stratum"] == h and r["ref"] == k)
                for k in ref_classes} for h in strata}

    # --- area proportion per ref class k (including nothicket if present) ---
    p_hat, se_p = {}, {}
    for k in ref_classes:
        p = sum(W[h] * n_hk[h][k] / n_h[h] for h in strata)
        var = sum(W[h]**2 * (n_hk[h][k]/n_h[h]) * (1 - n_hk[h][k]/n_h[h]) / (n_h[h] - 1)
                  for h in strata)
        p_hat[k] = p
        se_p[k] = np.sqrt(var)

    # --- overall accuracy: O = sum_h W_h * (n_hh / n_h) ---
    OA = sum(W[h] * n_hk[h][h] / n_h[h] for h in strata)
    varOA = sum(W[h]**2 * (n_hk[h][h]/n_h[h]) * (1 - n_hk[h][h]/n_h[h]) / (n_h[h] - 1)
                for h in strata)
    seOA = np.sqrt(varOA)

    # --- user's accuracy U_h = n_hh / n_h (per map class h) ---
    U, seU = {}, {}
    for h in strata:
        u = n_hk[h][h] / n_h[h]
        U[h] = u
        seU[h] = np.sqrt(u * (1 - u) / (n_h[h] - 1))

    # --- producer's accuracy P_k (Olofsson Eq 15), over map classes only ---
    PA, sePA = {}, {}
    Nk = {k: sum(W[h] * n_hk[h][k] / n_h[h] for h in strata) for k in strata}  # = p_hat_k
    for k in strata:
        Nkk = W[k] * n_hk[k][k] / n_h[k]
        Pk = Nkk / Nk[k] if Nk[k] > 0 else float("nan")
        PA[k] = Pk
        Uj = U[k]
        term1 = (W[k]**2 * (1 - Pk)**2 * Uj * (1 - Uj)) / (n_h[k] - 1)
        term2 = 0.0
        for h in strata:
            if h == k:
                continue
            nhk = n_hk[h][k]
            phat = nhk / n_h[h]
            term2 += W[h]**2 * phat * (1 - phat) / (n_h[h] - 1)
        varPk = (1.0 / Nk[k]**2) * (term1 + Pk**2 * term2) if Nk[k] > 0 else float("nan")
        sePA[k] = np.sqrt(varPk)

    A = area_total_ha
    out = {
        "ref_classes": ref_classes,
        "n_h": n_h,
        "confusion_map_x_ref": n_hk,
        "area_proportion": {k: {"p": p_hat[k], "se": se_p[k],
                                "ci95": [p_hat[k]-Z*se_p[k], p_hat[k]+Z*se_p[k]]}
                            for k in ref_classes},
        "area_ha": {k: {"area": p_hat[k]*A, "se": se_p[k]*A,
                        "ci95": [(p_hat[k]-Z*se_p[k])*A, (p_hat[k]+Z*se_p[k])*A]}
                    for k in ref_classes},
        "overall_accuracy": {"OA": OA, "se": seOA, "ci95": [OA-Z*seOA, OA+Z*seOA]},
        "users_accuracy": {h: {"U": U[h], "se": seU[h]} for h in strata},
        "producers_accuracy": {k: {"P": PA[k], "se": sePA[k]} for k in strata},
        "area_total_ha": A,
    }
    return out


# ------------------------------------------------ proper stratified variance
def stratified_mean(rows, W, ref_class):
    """Design-based stratified estimate of the proportion in `ref_class` and its
    PROPER stratified SE:  p = Sigma_h W_h p_h ,  Var = Sigma_h W_h^2 s_h^2/n_h
    with s_h^2 = p_h(1-p_h) n_h/(n_h-1) (the finite-sample binomial variance).
    This is the honest design baseline the PPI estimator must beat (Finding 2)."""
    p = 0.0
    var = 0.0
    for h in STRATA:
        y = np.array([1.0 if r["ref"] == ref_class else 0.0
                      for r in rows if r["stratum"] == h])
        n = len(y)
        ph = y.mean() if n else 0.0
        p += W[h] * ph
        if n > 1:
            # sample variance of the 0/1 indicator within the stratum
            sh2 = ph * (1 - ph) * n / (n - 1)
            var += W[h]**2 * sh2 / n
    se = np.sqrt(var)
    return p, se


# ----------------------------------------------------------------- PPI++ estimator
def ppi_area(rows, W, area_total_ha, ref_classes, n_pop=2_000_000):
    """PPI++ estimate of area proportion per class using the map indicator as the
    auxiliary prediction and inverse-inclusion-probability weights.

    The comparison baseline reported here is the PROPER stratified estimator
    (`stratified_mean`), NOT ppi_py's classical IID interval. ppi_py's
    classical_mean_ci computes std(weighted residual)/sqrt(n), which does not
    reproduce Sigma_h W_h^2 s_h^2/n_h and understates the design baseline."""
    strata = STRATA
    n_h = {h: sum(1 for r in rows if r["stratum"] == h) for h in strata}
    strat = np.array([r["stratum"] for r in rows])
    ref = np.array([r["ref"] for r in rows])

    # inverse-inclusion-probability weights (up to a constant): W_h / n_h, per point
    w_lab = np.array([W[h] / n_h[h] for h in strat])

    # synthetic population of map indicators matching W exactly.
    # NOTE: for the hard (argmax) predictor the population mean of the indicator
    # is exactly W_k, so this synthetic population is not strictly necessary; it
    # is retained only so ppi_mean_ci sees an unlabelled set. The continuous
    # predictor version (script 15) is the one that needs a real pop sample.
    counts = {h: int(round(W[h] * n_pop)) for h in strata}
    pop_stratum = np.concatenate([np.full(counts[h], h) for h in strata])

    A = area_total_ha
    out = {}
    for k in ref_classes:
        Y = (ref == k).astype(float)
        Yhat = (strat == k).astype(float)
        Yhat_unlab = (pop_stratum == k).astype(float)
        w_unlab = np.ones(len(Yhat_unlab))

        # PPI++ (lam estimated from data)
        lo, hi = ppi_mean_ci(Y, Yhat, Yhat_unlab, alpha=ALPHA,
                             w=w_lab, w_unlabeled=w_unlab)
        lo, hi = float(np.ravel(lo)[0]), float(np.ravel(hi)[0])
        pt = float(np.ravel(ppi_mean_pointestimate(
            Y, Yhat, Yhat_unlab, w=w_lab, w_unlabeled=w_unlab))[0])

        # PROPER stratified (design) baseline (Finding 2)
        sp, sse = stratified_mean(rows, W, k)

        out[k] = {
            "ppi": {"p": pt, "ci95": [float(lo), float(hi)],
                    "se_implied": float((hi - lo) / (2*Z)),
                    "area": pt*A, "area_ci95": [float(lo)*A, float(hi)*A]},
            "stratified": {"p": sp, "se": sse,
                           "ci95": [sp - Z*sse, sp + Z*sse],
                           "area": sp*A, "area_ci95": [(sp-Z*sse)*A, (sp+Z*sse)*A]},
            "ppi_beats_stratified": bool((hi - lo) / (2*Z) < sse),
        }
    return out


# ------------------------------------------------------------------------- driver
def run_one(adjudicate):
    design = json.load(open(os.path.join(RESULTS, "sample_design.json")))
    W = design["W"]
    area_total_ha = design["area_total_ha"]
    rows_raw = load_adjudicated(adjudicate)

    report = {
        "adjudicate": adjudicate,
        "inputs": {
            "labellers": ["ARP", "SVM"],
            "adjudication": f"on disagreement keep {adjudicate}'s label (sensitivity arm)",
            "n_unique_points": len(rows_raw),
            "qa_agreement": qa_agreement(),
            "W": W, "area_total_ha": area_total_ha,
            "remap": {"transformed": "severe",
                      "nothicket": "tested: class (kept) | severe"},
        },
        "scenarios": {},
    }

    for mode, tag in [("class", "A_nothicket_class"), ("severe", "B_nothicket_to_severe")]:
        rows, ref_classes = remap(rows_raw, mode)
        olof = olofsson(rows, W, area_total_ha, ref_classes)
        ppi = ppi_area(rows, W, area_total_ha, ref_classes)
        report["scenarios"][tag] = {
            "nothicket_mode": mode,
            "ref_classes": ref_classes,
            "n_used": len(rows),
            "olofsson": olof,
            "ppi_pp": ppi,
        }
    return report


def run():
    reports = {adj: run_one(adj) for adj in ("ARP", "SVM")}
    outp = os.path.join(RESULTS, "area_estimation.json")
    json.dump({"sensitivity_adjudication": reports}, open(outp, "w"), indent=2)
    print("wrote", outp)

    qa = qa_agreement()
    print(f"\nQA double-labelling: {qa['n_duplicated']} points, "
          f"{qa['n_agree']} agree, {qa['n_disagree']} disagree "
          f"(ids {[d['id'] for d in qa['disagreements']]})")

    for adj, report in reports.items():
        print("\n" + "#" * 82)
        print(f"# ADJUDICATION = {adj}   (n_unique = {report['inputs']['n_unique_points']})")
        print("#" * 82)
        for tag, sc in report["scenarios"].items():
            print("\n" + "=" * 78)
            print(f"SCENARIO {tag}  (nothicket -> {sc['nothicket_mode']}, "
                  f"n={sc['n_used']}, ref_classes={sc['ref_classes']})")
            print("=" * 78)
            o = sc["olofsson"]
            print(f"Overall accuracy: {o['overall_accuracy']['OA']:.3f} "
                  f"+/- {Z*o['overall_accuracy']['se']:.3f}")
            print(f"{'class':>10} | {'Olofsson area (ha)':>26} | "
                  f"{'PPI++ area (ha)':>26} | {'stratified (ha)':>24}")
            for k in sc["ref_classes"]:
                oa = o["area_ha"][k]
                pk = sc["ppi_pp"][k]
                print(f"{k:>10} | {oa['area']:>10,.0f} +/-{Z*oa['se']:>9,.0f} | "
                      f"{pk['ppi']['area']:>10,.0f} "
                      f"+/-{(pk['ppi']['area_ci95'][1]-pk['ppi']['area_ci95'][0])/2:>9,.0f} | "
                      f"{pk['stratified']['area']:>9,.0f} "
                      f"+/-{Z*pk['stratified']['se']*sc['olofsson']['area_total_ha']:>8,.0f}")
            tot_o = sum(o["area_ha"][k]["area"] for k in sc["ref_classes"])
            print(f"{'TOTAL':>10} | {tot_o:>10,.0f}{'':>13} |")


if __name__ == "__main__":
    run()
