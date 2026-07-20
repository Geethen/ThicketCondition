"""
19_degradation_index.py
=======================
PART 3: continuous degradation index -- the estimand where PPI++ has the best
chance to beat the design-based baseline, because it carries within-stratum signal
that hard-class stratification throws away.

Degradation index D in [0,1]:
    predicted  D_pred(x) = 0*p_intact + 0.5*p_moderate + 1*p_severe   (from the tif)
    gold       D_true    = {intact:0, moderate:0.5, severe:1}[ref label]

Estimand: mean TRUE degradation, overall and per EFG (Arid/Valley/Mesic), 95% CI.

Two estimators:
  stratified : PROPER design stratified mean of gold D_true (Sigma_cell
               W_cell^2 s_cell^2/n_cell) -- the honest baseline PPI must beat.
  ppi++      : ppi_mean_ci with predictor D_pred and the (calibrated) population
               D_pred distribution.

Corrections applied after review:
  * Finding 1 - labels DEDUPLICATED to one adjudicated ref per unique point;
    ARP vs SVM run as a sensitivity.
  * Finding 2 - the baseline reported is the PROPER stratified SE, computed
    directly here, NOT ppi_py's classical_mean_ci (which returns the IID
    std(weighted residual)/sqrt(n) and understates the design SE). This is what
    the earlier "36-48% PPI improvement" was measured against; against the proper
    baseline PPI is NOT better.
  * Finding 3 - the population D_pred distribution is reweighted per EFG with
    pop_calibration_weights so each EFG x mapped-class cell matches the known
    nine-cell area weights, instead of the raw uniform pixel sample.

nothicket handling: transformed->severe (D=1) always; scenario B nothicket->severe.
Scenario A keeps nothicket as its own class with D_true=1 (fully transformed land
is maximally degraded) -- documented ECOLOGICAL choice, stated explicitly.

Inputs: _sampled_probs.json (+ EFG via existing_tagged_efg.json),
_pop_prob_sample_efg.npy (cols p_i,p_m,p_s,efg), stratum_areas_efg.json.

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/19_degradation_index.py
"""
import json, os
import numpy as np
from ppi_py import ppi_mean_ci, ppi_mean_pointestimate
from _labels_common import (load_sampled_probs_efg, pop_calibration_weights,
                            qa_agreement)

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
EFG = {1: "Arid", 2: "Valley", 3: "Mesic"}
CLSN = {0: "intact", 1: "moderate", 2: "severe"}
STRATA = ["intact", "moderate", "severe"]
# D_true for the reference label. nothicket = fully transformed => D=1 (see header).
D_TRUE = {"intact": 0.0, "moderate": 0.5, "severe": 1.0, "nothicket": 1.0}
W_D = np.array([0.0, 0.5, 1.0])   # weights on (p_i, p_m, p_s) for predicted D
Z = 1.959963984540054
ALPHA = 0.05


def dpred(p):
    return float(np.dot(W_D, p))


def remap(lab, mode):
    """transformed->severe; nothicket: 'class' keeps it (D_true=1), 'severe'
    folds to severe (also D_true=1). Either way non-thicket contributes D=1."""
    if lab == "transformed":
        return "severe"
    if lab == "nothicket":
        return "nothicket" if mode == "class" else "severe"
    return lab if lab in STRATA else None


def se_ci(lo, hi):
    return (hi - lo) / (2 * Z)


def stratified_domain_mean(strat9, Ytrue, cell_area_ha):
    """Proper design stratified mean of Ytrue over the cells in a domain and its
    SE (Sigma_cell W_cell^2 s_cell^2/n_cell), W_cell = area_cell/domain_area."""
    cells = sorted(set(strat9.tolist()))
    domain_area = sum(cell_area_ha[c] for c in cells)
    mean, var = 0.0, 0.0
    for c in cells:
        m = strat9 == c
        y = Ytrue[m]
        n = len(y)
        Wc = cell_area_ha[c] / domain_area
        yb = y.mean()
        mean += Wc * yb
        if n > 1:
            s2 = y.var(ddof=1)
            var += Wc**2 * s2 / n
    return mean, np.sqrt(var)


def estimate_domain(rows, cell_area_ha, Dpop, w_pop, mode):
    """Mean true degradation for one domain (whole map or one EFG).
    rows: labelled points in the domain. Dpop: population D_pred sample for the
    domain; w_pop: calibration weights for Dpop."""
    rr = []
    for r in rows:
        ref = remap(r["label"], mode)
        if ref is None:
            continue
        rr.append(dict(strat9=r["strat9"], dtrue=D_TRUE[ref], dpred=r["dpred"]))
    if len(rr) < 2:
        return None
    strat9 = np.array([r["strat9"] for r in rr])
    Ytrue = np.array([r["dtrue"] for r in rr])
    Yhat = np.array([r["dpred"] for r in rr])
    cells = sorted(set(strat9.tolist()))
    n_cell = {c: int((strat9 == c).sum()) for c in cells}
    w = np.array([cell_area_ha[c] / n_cell[c] for c in strat9])
    w_unlab = w_pop

    # PROPER stratified design baseline (Finding 2)
    spt, sse = stratified_domain_mean(strat9, Ytrue, cell_area_ha)

    # PPI++ with continuous D_pred predictor (calibrated population)
    lo, hi = ppi_mean_ci(Ytrue, Yhat, Dpop, alpha=ALPHA, w=w, w_unlabeled=w_unlab)
    lo, hi = float(np.ravel(lo)[0]), float(np.ravel(hi)[0])
    ppt = float(np.ravel(ppi_mean_pointestimate(
        Ytrue, Yhat, Dpop, w=w, w_unlabeled=w_unlab))[0])
    se_ppi = se_ci(lo, hi)

    # calibration diagnostics
    dpred_lab = float(np.average(Yhat, weights=w))
    dpred_pop = float(np.average(Dpop, weights=w_pop))
    corr = float(np.corrcoef(Yhat, Ytrue)[0, 1])

    return {
        "n": len(rr), "n_cell": n_cell,
        "stratified": {"D": spt, "se": sse, "ci95": [spt - Z*sse, spt + Z*sse]},
        "ppi": {"D": ppt, "se": se_ppi, "ci95": [lo, hi]},
        "se_reduction_vs_stratified_pct": (1 - se_ppi / sse) * 100 if sse > 0 else float("nan"),
        "ppi_beats_stratified": bool(se_ppi < sse),
        "calib": {"dpred_lab_wtd": dpred_lab, "dpred_pop_calibrated": dpred_pop,
                  "bias_pred_minus_true": dpred_lab - spt, "corr_pred_true": corr},
    }


def run_one(adjudicate, cell_area_ha, Ppop, Epop, w_pop_all):
    Dpop_all = Ppop @ W_D
    rows = load_sampled_probs_efg(adjudicate)
    for r in rows:
        r["dpred"] = dpred(r["p"])

    report = {
        "adjudicate": adjudicate,
        "part": "3 - continuous degradation index (mean true D)",
        "index_def": "D = 0*p_intact + 0.5*p_moderate + 1*p_severe; "
                     "gold D_true={intact:0,moderate:.5,severe:1,nothicket:1}",
        "n_unique_points": len(rows),
        "qa_agreement": qa_agreement(),
        "area_total_ha": sum(cell_area_ha.values()),
        "scenarios": {},
    }
    for mode, tag in [("class", "A_nothicket_class"), ("severe", "B_nothicket_to_severe")]:
        sc = {"nothicket_mode": mode}
        sc["overall"] = estimate_domain(rows, cell_area_ha, Dpop_all, w_pop_all, mode)
        sc["efg"] = {}
        for eid, ename in EFG.items():
            rows_e = [r for r in rows if r["efg_id"] == eid]
            mask = Epop == eid
            Dpop_e = Ppop[mask] @ W_D
            # per-EFG calibration weights (efg fixed -> calibrate over the 3 cells)
            w_pop_e = pop_calibration_weights(Ppop[mask], efg=Epop[mask])
            sc["efg"][ename] = estimate_domain(rows_e, cell_area_ha, Dpop_e, w_pop_e, mode)
        report["scenarios"][tag] = sc
    return report


def main():
    areas = json.load(open(os.path.join(RESULTS, "stratum_areas_efg.json")))["area_m2"]
    cell_area_ha = {int(k): v / 1e4 for k, v in areas.items()}
    pop = np.load(os.path.join(RESULTS, "_pop_prob_sample_efg.npy"))
    Ppop, Epop = pop[:, :3].astype(np.float64), pop[:, 3].astype(int)
    # whole-map calibration weights (EFG x mapped-class cells)
    w_pop_all = pop_calibration_weights(Ppop, efg=Epop)

    reports = {adj: run_one(adj, cell_area_ha, Ppop, Epop, w_pop_all)
               for adj in ("ARP", "SVM")}
    outp = os.path.join(RESULTS, "degradation_index.json")
    json.dump({"sensitivity_adjudication": reports}, open(outp, "w"), indent=2)
    print("wrote", outp)

    for adj, report in reports.items():
        print("\n" + "#" * 84)
        print(f"# ADJUDICATION = {adj}   (n_unique = {report['n_unique_points']})")
        print("#" * 84)
        for tag, sc in report["scenarios"].items():
            print("\n" + "=" * 84)
            print(f"SCENARIO {tag}  (nothicket -> {sc['nothicket_mode']})")
            print("=" * 84)
            print("Mean TRUE degradation index D (0=intact .. 1=severe), 95% CI:")
            print(f"{'domain':>8} {'n':>4} | {'stratified D +/-':>22} | {'PPI++ D +/-':>22} | "
                  f"{'SE red':>7} | {'beats':>5} | {'corr':>5}")
            def line(name, d):
                if d is None:
                    print(f"{name:>8}  n/a"); return
                c = d["stratified"]; p = d["ppi"]
                beats = "yes" if d["ppi_beats_stratified"] else "no"
                print(f"{name:>8} {d['n']:>4} | {c['D']:>10.3f} +/-{Z*c['se']:>8.3f} | "
                      f"{p['D']:>10.3f} +/-{Z*p['se']:>8.3f} | "
                      f"{d['se_reduction_vs_stratified_pct']:>6.1f}% | {beats:>5} | "
                      f"{d['calib']['corr_pred_true']:>5.2f}")
            line("Overall", sc["overall"])
            for ename in ["Arid", "Valley", "Mesic"]:
                line(ename, sc["efg"][ename])


if __name__ == "__main__":
    main()
