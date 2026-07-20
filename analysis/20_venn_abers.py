"""
20_venn_abers.py
================
Venn-ABERS calibration of the 3-class thicket-condition RF probabilities.

Why: the RF's raw softmax scores are used directly as the continuous predictor in
the PPI/area scripts (15/16/17/18). If those scores are mis-calibrated the PPI++
rectifier still corrects the point estimate, but calibrated scores give a stronger
predictor (smaller SE) and are the honest thing to report. Venn-ABERS is a
distribution-free calibrator with a validity guarantee, well suited to the small
labelled set here.

Design:
  * 3 classes (intact / moderate / severe). Venn-ABERS is binary, so we calibrate
    ONE-VS-REST: for class c, positive = 1(ref==c), score = p_c(x).
  * Only 462 unique labelled points -> no room for a held-out calibration split.
    We use CROSS Venn-ABERS: K-fold, each fold calibrated on the other K-1 folds,
    so every labelled point gets an out-of-fold calibrated prob (no leakage). This
    matches the library's VennAbersCV(inductive=False) behaviour.
  * Multiclass probs are the K one-vs-rest p' renormalised to sum to 1 (the
    library's `loss='multiclass'` / L1 normalisation).
  * Labels are DEDUPLICATED + adjudicated via _labels_common (Finding 1). Run under
    both ARP and SVM adjudication.

Outputs (analysis/results/):
  venn_abers.json                 metrics: uncalibrated vs VA (ECE, Brier, logloss)
  _va_labelled_oof.csv            per-point OOF probs (raw + VA) -> R comparison
  _va_calib_vectors.json          the fold-0 (p0,p1,c) vectors per class -> R check
  _va_population.csv              a small population subsample calibrated (raw + VA)

Run: C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/20_venn_abers.py
"""
import json, os, csv
import numpy as np
from sklearn.model_selection import StratifiedKFold

from _venn_abers_core import VennAbers
from _labels_common import load_sampled_probs

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
CLASSES = ["intact", "moderate", "severe"]
CIDX = {c: i for i, c in enumerate(CLASSES)}
N_FOLDS = 5
SEED = 42
N_POP_EXPORT = 4000   # population subsample size written for R cross-check


# ---------------------------------------------------------------- data prep
def remap_ref(label):
    """Collapse reference labels to the 3 modelled classes (matches 16_*.py mode
    'severe': transformed->severe, nothicket->severe). Returns None to drop."""
    if label == "transformed":
        return "severe"
    if label == "nothicket":
        return "severe"
    if label in CIDX:
        return label
    return None


def load_labelled(adjudicate):
    rows = load_sampled_probs(adjudicate)
    P, y, ids = [], [], []
    for r in rows:
        ref = remap_ref(r["label"])
        if ref is None:
            continue
        P.append(r["p"])
        y.append(CIDX[ref])
        ids.append(r["id"])
    return np.array(P, dtype=np.float64), np.array(y, dtype=int), np.array(ids)


# ---------------------------------------------------------------- cross-VA
def fold_assignment(P, y):
    """Deterministic fold id per point (0..N_FOLDS-1). Exported so R reproduces the
    identical OOF splits, making the Python<->R comparison exact rather than
    approximate."""
    fold = np.full(len(y), -1, dtype=int)
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=SEED)
    for k, (_, te) in enumerate(skf.split(P, y)):
        fold[te] = k
    return fold


def cross_va_oof(P, y, fold=None):
    """Out-of-fold multiclass Venn-ABERS probabilities for the labelled set.
    Returns (n,3) calibrated probs (renormalised)."""
    n = len(y)
    oof = np.zeros((n, 3))
    if fold is None:
        fold = fold_assignment(P, y)
    for k in range(N_FOLDS):
        te = np.where(fold == k)[0]
        tr = np.where(fold != k)[0]
        for c in range(3):
            score2 = np.column_stack([1 - P[:, c], P[:, c]])   # (,2) for binary VA
            yb = (y == c).astype(int)
            va = VennAbers().fit(score2[tr], yb[tr])
            pprime, _ = va.predict_proba(score2[te])
            oof[te, c] = pprime[:, 1]
    # L1 renormalise so the 3 one-vs-rest calibrated probs form a distribution
    s = oof.sum(axis=1, keepdims=True)
    s[s == 0] = 1.0
    return oof / s


def fit_full_va(P, y):
    """Fit VA on ALL labelled points per class -> calibrators used to transform the
    population. Also return the (p0,p1,c) vectors for the R cross-check."""
    cals, vectors = {}, {}
    for c in range(3):
        score2 = np.column_stack([1 - P[:, c], P[:, c]])
        yb = (y == c).astype(int)
        va = VennAbers().fit(score2, yb)
        cals[c] = va
        vectors[CLASSES[c]] = {
            "c": va.c.tolist(),
            "p0": va.p0[:, 1].tolist(),
            "p1": va.p1[:, 1].tolist(),
        }
    return cals, vectors


def apply_va(cals, P):
    out = np.zeros((len(P), 3))
    for c in range(3):
        score2 = np.column_stack([1 - P[:, c], P[:, c]])
        pprime, _ = cals[c].predict_proba(score2)
        out[:, c] = pprime[:, 1]
    s = out.sum(axis=1, keepdims=True)
    s[s == 0] = 1.0
    return out / s


# ---------------------------------------------------------------- metrics
def onehot(y, k=3):
    m = np.zeros((len(y), k))
    m[np.arange(len(y)), y] = 1.0
    return m


def brier(P, y):
    return float(np.mean(np.sum((P - onehot(y)) ** 2, axis=1)))


def logloss(P, y, eps=1e-12):
    Pc = np.clip(P, eps, 1)
    return float(-np.mean(np.log(Pc[np.arange(len(y)), y])))


def ece(conf, correct, n_bins=10):
    """Expected Calibration Error on the top-label confidence."""
    edges = np.linspace(0, 1, n_bins + 1)
    e = 0.0
    n = len(conf)
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        m = (conf > lo) & (conf <= hi) if b > 0 else (conf >= lo) & (conf <= hi)
        if not m.any():
            continue
        e += m.sum() / n * abs(correct[m].mean() - conf[m].mean())
    return float(e)


def reliability(conf, correct, n_bins=10):
    edges = np.linspace(0, 1, n_bins + 1)
    out = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        m = (conf > lo) & (conf <= hi) if b > 0 else (conf >= lo) & (conf <= hi)
        if not m.any():
            out.append({"bin": [lo, hi], "n": 0, "conf": None, "acc": None})
        else:
            out.append({"bin": [lo, hi], "n": int(m.sum()),
                        "conf": float(conf[m].mean()), "acc": float(correct[m].mean())})
    return out


def classwise_ece(P, y, n_bins=10):
    """Mean over classes of one-vs-rest ECE (calibration of each prob column)."""
    vals = {}
    for c in range(3):
        p = P[:, c]
        yb = (y == c).astype(float)
        vals[CLASSES[c]] = ece(p, yb, n_bins)
    vals["mean"] = float(np.mean([vals[c] for c in CLASSES]))
    return vals


def metrics_block(P, y):
    top = np.argmax(P, axis=1)
    conf = P[np.arange(len(y)), top]
    correct = (top == y).astype(float)
    return {
        "brier": brier(P, y),
        "logloss": logloss(P, y),
        "ece_toplabel": ece(conf, correct),
        "ece_classwise": classwise_ece(P, y),
        "accuracy": float(correct.mean()),
        "reliability_toplabel": reliability(conf, correct),
    }


# ---------------------------------------------------------------- driver
def run_adjudication(adjudicate, write_exports):
    P, y, ids = load_labelled(adjudicate)
    raw = P / P.sum(axis=1, keepdims=True)          # ensure raw rows sum to 1
    fold = fold_assignment(P, y)
    va_oof = cross_va_oof(P, y, fold)

    report = {
        "adjudicate": adjudicate,
        "n_points": int(len(y)),
        "n_per_class": {CLASSES[c]: int((y == c).sum()) for c in range(3)},
        "n_folds": N_FOLDS,
        "uncalibrated": metrics_block(raw, y),
        "venn_abers_oof": metrics_block(va_oof, y),
    }
    # improvement summary
    u, v = report["uncalibrated"], report["venn_abers_oof"]
    report["delta"] = {
        "brier": v["brier"] - u["brier"],
        "logloss": v["logloss"] - u["logloss"],
        "ece_toplabel": v["ece_toplabel"] - u["ece_toplabel"],
        "ece_classwise_mean": v["ece_classwise"]["mean"] - u["ece_classwise"]["mean"],
    }

    if write_exports:
        # per-point OOF export for the R comparison
        with open(os.path.join(RESULTS, "_va_labelled_oof.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["id", "y", "fold",
                         "raw_intact", "raw_moderate", "raw_severe",
                         "va_intact", "va_moderate", "va_severe"])
            for i in range(len(y)):
                wr.writerow([int(ids[i]), int(y[i]), int(fold[i]),
                             *[f"{x:.10f}" for x in raw[i]],
                             *[f"{x:.10f}" for x in va_oof[i]]])
        # full-fit calibrators -> population + vectors for R
        cals, vectors = fit_full_va(P, y)
        json.dump(vectors, open(os.path.join(RESULTS, "_va_calib_vectors.json"), "w"))

        pop = np.load(os.path.join(RESULTS, "_pop_prob_sample.npy")).astype(np.float64)
        rng = np.random.default_rng(SEED)
        idx = rng.choice(len(pop), size=min(N_POP_EXPORT, len(pop)), replace=False)
        idx.sort()
        pops = pop[idx]
        pops = pops / pops.sum(axis=1, keepdims=True)
        pop_va = apply_va(cals, pops)
        with open(os.path.join(RESULTS, "_va_population.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["row",
                         "raw_intact", "raw_moderate", "raw_severe",
                         "va_intact", "va_moderate", "va_severe"])
            for j, r in enumerate(idx):
                wr.writerow([int(r),
                             *[f"{x:.10f}" for x in pops[j]],
                             *[f"{x:.10f}" for x in pop_va[j]]])
    return report


def main():
    reports = {}
    for adj in ("ARP", "SVM"):
        reports[adj] = run_adjudication(adj, write_exports=(adj == "ARP"))
    out = {"method": "venn_abers_cross_ovr", "n_folds": N_FOLDS,
           "sensitivity_adjudication": reports}
    json.dump(out, open(os.path.join(RESULTS, "venn_abers.json"), "w"), indent=2)
    print("wrote", os.path.join(RESULTS, "venn_abers.json"))

    for adj, rep in reports.items():
        print("\n" + "=" * 78)
        print(f"ADJUDICATION = {adj}   n={rep['n_points']}   per-class={rep['n_per_class']}")
        print("=" * 78)
        u, v = rep["uncalibrated"], rep["venn_abers_oof"]
        rows = [
            ("Brier (lower=better)", u["brier"], v["brier"]),
            ("Log-loss (lower)", u["logloss"], v["logloss"]),
            ("ECE top-label (lower)", u["ece_toplabel"], v["ece_toplabel"]),
            ("ECE class-wise mean (lower)", u["ece_classwise"]["mean"], v["ece_classwise"]["mean"]),
            ("Accuracy (unchanged~)", u["accuracy"], v["accuracy"]),
        ]
        print(f"{'metric':<30}{'raw':>12}{'venn-abers':>14}{'delta':>12}")
        for name, a, b in rows:
            print(f"{name:<30}{a:>12.5f}{b:>14.5f}{b-a:>12.5f}")


if __name__ == "__main__":
    main()
