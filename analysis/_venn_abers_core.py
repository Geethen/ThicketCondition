"""
_venn_abers_core.py
===================
Vendored core of the Venn-ABERS calibrator (binary, classification setting) from
ip200/venn-abers (src/venn_abers.py), so the pipeline runs with no extra install.

Only the two functions the algorithm needs are copied verbatim (classification
branch): `calc_p0p1` fits the two isotonic-regression vectors (label appended 0 /
appended 1) via the GCM-of-CSD PAVA representation; `calc_probs` looks the test
probabilities up in those vectors and combines the two-sided outputs p0,p1 into a
single calibrated probability  p' = p1 / (1 - p0 + p1).

Reference: Vovk, Petej, Fedorova (2015), "Large-scale probabilistic predictors
with and without guarantees of validity", NeurIPS 28. arXiv:1511.00213.

The R port in `venn_abers_R.R` reproduces these two functions line for line; keep
them in sync if you ever pull a newer upstream version.

Validated: this vendored core produces bit-identical output (0.0 max abs diff) to
the installed upstream `venn-abers` (v1.5.3) VennAbers class on the labelled data;
see 21_compare_va_py_r.py. So `pip install venn-abers` is NOT required to run the
pipeline -- it is only used for the one-off equivalence check.
"""
import numpy as np


def calc_p0p1(p_cal, y_cal):
    """Isotonic calibration vectors (classification setting).

    p_cal : (n,2) calibration probabilities, column 1 = P(class==1).
    y_cal : (n,) binary {0,1} labels.
    Returns p0, p1 (each (m+1, 2): [threshold, isotonic value]) and c (unique
    sorted calibration scores).
    """
    p_cal = np.asarray(p_cal, dtype=np.float64)
    y_cal = np.asarray(y_cal, dtype=np.float64)
    cal = np.hstack((p_cal[:, 1].reshape(-1, 1), y_cal.reshape(-1, 1)))

    ix = np.argsort(cal[:, 0])
    k_sort = cal[ix, 0]
    k_label_sort = cal[ix, 1]

    c = np.unique(k_sort)
    ia = np.searchsorted(k_sort, c)

    w = np.zeros(len(c))
    w[:-1] = np.diff(ia)
    w[-1] = len(k_sort) - ia[-1]

    k_dash = len(c)

    P = np.zeros((k_dash + 2, 2))
    P[0, 0] = -1
    P[2:, 0] = np.cumsum(w)
    P[2:-1, 1] = np.cumsum(k_label_sort)[(ia - 1)[1:]]
    P[-1, 1] = np.cumsum(k_label_sort)[-1]

    # 0/0 divisions inside the GCM search are expected and filtered by nanmin/
    # nanmax below; silence the benign warnings (upstream behaviour is identical).
    _err = np.seterr(divide="ignore", invalid="ignore")
    # ---- p1 : append label 1, greatest convex minorant from the left ----
    p1 = np.zeros((len(c) + 1, 2))
    p1[1:, 0] = c
    P1 = P[1:] + 1
    grad = np.nan
    c_point = 0
    for i in range(len(p1)):
        P1[i, :] = P1[i, :] - 1
        if i == 0:
            grads = np.divide(P1[:, 1], P1[:, 0])
            grad = np.nanmin(grads)
            p1[i, 1] = grad
            c_point = 0
        else:
            imp_point = P1[c_point, 1] + (P1[i, 0] - P1[c_point, 0]) * grad
            if P1[i, 1] < imp_point:
                grads = np.divide((P1[i:, 1] - P1[i, 1]), (P1[i:, 0] - P1[i, 0]))
                if np.sum(np.isnan(np.nanmin(grads))) == 0:
                    grad = np.nanmin(grads)
                c_point = i
                p1[i, 1] = grad
            else:
                p1[i, 1] = grad

    # ---- p0 : append label 0, least concave majorant from the right ----
    p0 = np.zeros((len(c) + 1, 2))
    p0[1:, 0] = c
    P0 = P[1:]
    for i in range(len(p1) - 1, -1, -1):
        P0[i, 0] = P0[i, 0] + 1
        if i == len(p1) - 1:
            grads = np.divide((P0[:, 1] - P0[i, 1]), (P0[:, 0] - P0[i, 0]))
            grad = np.nanmax(grads)
            p0[i, 1] = grad
            c_point = i
        else:
            imp_point = P0[c_point, 1] + (P0[i, 0] - P0[c_point, 0]) * grad
            if P0[i, 1] < imp_point:
                grads = np.divide((P0[:, 1] - P0[i, 1]), (P0[:, 0] - P0[i, 0]))
                grads[i:] = 0
                grad = np.nanmax(grads)
                c_point = i
                p0[i, 1] = grad
            else:
                p0[i, 1] = grad

    np.seterr(**_err)
    return p0, p1, c


def calc_probs(p0, p1, c, p_test):
    """Calibrate p_test (n,2) -> (p_prime (n,2), p0_p1 (n,2))."""
    p_test = np.asarray(p_test, dtype=np.float64)
    out = p_test[:, 1]
    p0_p1 = np.hstack((
        p0[np.searchsorted(c, out, 'right'), 1].reshape(-1, 1),
        p1[np.searchsorted(c, out, 'left'), 1].reshape(-1, 1),
    ))
    p_prime = np.zeros((len(out), 2))
    p_prime[:, 1] = p0_p1[:, 1] / (1 - p0_p1[:, 0] + p0_p1[:, 1])
    p_prime[:, 0] = 1 - p_prime[:, 1]
    return p_prime, p0_p1


class VennAbers:
    """Minimal binary Venn-ABERS calibrator (fit on calibration probs+labels)."""

    def fit(self, p_cal, y_cal):
        self.p0, self.p1, self.c = calc_p0p1(p_cal, y_cal)
        return self

    def predict_proba(self, p_test):
        return calc_probs(self.p0, self.p1, self.c, p_test)
