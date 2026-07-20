"""
21_compare_va_py_r.py
=====================
Compare the Python and R Venn-ABERS implementations, and the calibration metrics
across methods (uncalibrated vs Venn-ABERS).

Two things are reported:
 (A) IMPLEMENTATION AGREEMENT -- per-point |VA_python - VA_R| on the identical OOF
     splits. Both read the same raw probs + fold ids, so any difference is purely
     numerical/implementation. We report max, mean, RMS abs diff per class and the
     correlation, plus how many points differ by > 1e-6.
 (B) METHOD COMPARISON -- the calibration metrics table (Brier, log-loss, ECE
     top-label, ECE class-wise) for raw vs VA, from both languages, confirming the
     two agree, and quantifying the calibration gain.

Writes results/venn_abers_compare.json and prints a summary.

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/21_compare_va_py_r.py
"""
import json, os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
CLASSES = ["intact", "moderate", "severe"]


def load():
    py = pd.read_csv(os.path.join(RESULTS, "_va_labelled_oof.csv"))
    r = pd.read_csv(os.path.join(RESULTS, "_va_labelled_oof_R.csv"))
    m = py.merge(r, on="id", suffixes=("_py", "_r"))
    assert (m["y_py"] == m["y_r"]).all() and (m["fold_py"] == m["fold_r"]).all(), \
        "label/fold mismatch between Python and R exports"
    return m


def implementation_agreement(m):
    out = {"n": int(len(m)), "per_class": {}, "overall": {}}
    all_diffs = []
    for c in CLASSES:
        a = m[f"va_{c}_py"].to_numpy()
        b = m[f"va_{c}_r"].to_numpy()
        d = np.abs(a - b)
        all_diffs.append(d)
        out["per_class"][c] = {
            "max_abs_diff": float(d.max()),
            "mean_abs_diff": float(d.mean()),
            "rms_abs_diff": float(np.sqrt((d ** 2).mean())),
            "n_gt_1e6": int((d > 1e-6).sum()),
            "pearson_r": float(np.corrcoef(a, b)[0, 1]),
        }
    d = np.concatenate(all_diffs)
    out["overall"] = {
        "max_abs_diff": float(d.max()),
        "mean_abs_diff": float(d.mean()),
        "rms_abs_diff": float(np.sqrt((d ** 2).mean())),
        "n_gt_1e6": int((d > 1e-6).sum()),
        "n_gt_1e9": int((d > 1e-9).sum()),
    }
    return out


def metric_tables():
    py = json.load(open(os.path.join(RESULTS, "venn_abers.json")))
    rj = json.load(open(os.path.join(RESULTS, "venn_abers_R.json")))
    py_arp = py["sensitivity_adjudication"]["ARP"]
    keys = [("brier", "brier"), ("logloss", "logloss"),
            ("ece_toplabel", "ece_toplabel"),
            ("ece_classwise", "ece_classwise")]
    table = {}
    for lang, blocks in [("python", (py_arp["uncalibrated"], py_arp["venn_abers_oof"])),
                         ("R", (rj["uncalibrated"], rj["venn_abers_oof"]))]:
        unc, va = blocks
        table[lang] = {
            "uncalibrated": {"brier": unc["brier"], "logloss": unc["logloss"],
                             "ece_toplabel": unc["ece_toplabel"],
                             "ece_classwise_mean": unc["ece_classwise"]["mean"]},
            "venn_abers": {"brier": va["brier"], "logloss": va["logloss"],
                           "ece_toplabel": va["ece_toplabel"],
                           "ece_classwise_mean": va["ece_classwise"]["mean"]},
        }
    # cross-language metric agreement
    diff = {}
    for k in ["brier", "logloss", "ece_toplabel", "ece_classwise_mean"]:
        for state in ["uncalibrated", "venn_abers"]:
            diff.setdefault(state, {})[k] = abs(
                table["python"][state][k] - table["R"][state][k])
    table["python_vs_R_abs_diff"] = diff
    return table


def main():
    m = load()
    agree = implementation_agreement(m)
    metrics = metric_tables()
    report = {"implementation_agreement": agree, "method_metrics": metrics}
    json.dump(report, open(os.path.join(RESULTS, "venn_abers_compare.json"), "w"), indent=2)

    print("=" * 74)
    print("(A) PYTHON vs R IMPLEMENTATION AGREEMENT  (identical OOF splits)")
    print("=" * 74)
    print(f"{'class':<12}{'max|diff|':>14}{'mean|diff|':>14}{'n>1e-6':>10}{'pearson r':>12}")
    for c in CLASSES:
        s = agree["per_class"][c]
        print(f"{c:<12}{s['max_abs_diff']:>14.2e}{s['mean_abs_diff']:>14.2e}"
              f"{s['n_gt_1e6']:>10d}{s['pearson_r']:>12.8f}")
    o = agree["overall"]
    print(f"{'OVERALL':<12}{o['max_abs_diff']:>14.2e}{o['mean_abs_diff']:>14.2e}"
          f"{o['n_gt_1e6']:>10d}")
    verdict = "IDENTICAL (< 1e-9)" if o["max_abs_diff"] < 1e-9 else (
        "MATCH (< 1e-6)" if o["max_abs_diff"] < 1e-6 else "DIFFER (>= 1e-6)")
    print(f"\nVerdict: Python and R produce {verdict} per-point calibrated probs.")

    print("\n" + "=" * 74)
    print("(B) CALIBRATION METRICS BY METHOD  (ARP adjudication, n=462)")
    print("=" * 74)
    t = metrics
    print(f"{'metric':<22}{'raw(py)':>11}{'VA(py)':>11}{'raw(R)':>11}{'VA(R)':>11}")
    for k, lab in [("brier", "Brier"), ("logloss", "Log-loss"),
                   ("ece_toplabel", "ECE top-label"),
                   ("ece_classwise_mean", "ECE class-wise")]:
        print(f"{lab:<22}"
              f"{t['python']['uncalibrated'][k]:>11.5f}"
              f"{t['python']['venn_abers'][k]:>11.5f}"
              f"{t['R']['uncalibrated'][k]:>11.5f}"
              f"{t['R']['venn_abers'][k]:>11.5f}")
    md = max(max(v.values()) for v in t["python_vs_R_abs_diff"].values())
    print(f"\nMax cross-language metric |diff|: {md:.2e}")
    print("\nwrote", os.path.join(RESULTS, "venn_abers_compare.json"))


if __name__ == "__main__":
    main()
