#!/usr/bin/env python
"""Generate the three publication figures for the thicket-condition paper.

Figure 1  Design-based area estimates by condition and ecosystem type.
Figure 2  Area-adjusted map-accuracy table.
Figure 3  Incremental value of additional probability layers, including PySR.

The area and accuracy panels read the frozen final estimator output. Figure 3
uses the cached out-of-fold probabilities and fixed, training-selected rule
thresholds. Its intervals are paired spatial-block bootstrap intervals over the
held-out predictions; no rule is selected by its held-out score for display.

Usage:  py analysis/24_publication_figures.py
Output: analysis/figures/figure{1,2,3}_*.{png,svg}
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
DATA = ROOT / "data"
FIGDIR = ROOT / "figures"
FIGDIR.mkdir(exist_ok=True)

AREA_JSON = RESULTS / "area_estimation_random_adjudication_2026-08-06.json"
SYMBOLIC_JSON = RESULTS / "symbolic_results.json"
OOF_JSON = DATA / "oof_3band.json"
COORDS_JSON = DATA / "oof_coords.json"

ARM = "random"
SCENARIO = "B_nothicket_to_severe"

# Nature standard widths and compact figure heights at final print size.
MM = 1 / 25.4
ONE_AND_HALF_COL = 136 * MM
DOUBLE_COL = 183 * MM

INK = "#111111"
INK_2 = "#444444"
INK_3 = "#666666"
SURFACE = "#ffffff"
GRID = "#d9d9d9"
OTHER = "#8b8b86"

# Okabe-Ito-derived, colour-vision-deficiency-safe subset. Meaning is also
# carried by marker shape and direct text, never by colour alone.
COND = {
    "intact": "#0072B2",
    "moderate": "#E69F00",
    "severe": "#D55E00",
}
MARKER = {"intact": "o", "moderate": "s", "severe": "^"}
COND_LABEL = {"intact": "Intact", "moderate": "Moderate", "severe": "Severe"}
CLASSES = ["intact", "moderate", "severe"]

mpl.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 7,
        "axes.titlesize": 7,
        "axes.labelsize": 7,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 6.2,
        "axes.edgecolor": INK_3,
        "axes.linewidth": 0.5,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "savefig.dpi": 600,
        "figure.dpi": 180,
        "axes.grid": False,
        "legend.frameon": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    }
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load():
    return load_json(AREA_JSON), load_json(SYMBOLIC_JSON)


def despine(ax, keep=("left", "bottom")):
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(side in keep)


def panel_label(fig, x, letter):
    fig.text(x, 0.945, letter, fontsize=8, fontweight="bold", ha="left", va="top")


def save_publication_figure(fig, stem):
    """Write an exact-size raster preview and editable SVG master."""
    paths = []
    for ext in ("png", "svg"):
        out = FIGDIR / f"{stem}.{ext}"
        kwargs = {"dpi": 600} if ext == "png" else {}
        fig.savefig(out, bbox_inches=None, pad_inches=0, **kwargs)
        paths.append(out)
    plt.close(fig)
    for path in paths:
        print(f"wrote {path}")


def condition_legend_handles():
    return [
        Line2D(
            [], [], marker=MARKER[c], ls="none", ms=5.2,
            markerfacecolor=COND[c], markeredgecolor=SURFACE,
            markeredgewidth=0.7, label=COND_LABEL[c],
        )
        for c in CLASSES
    ]


# ============================================================== FIGURE 1 ====
def figure1(area):
    """Design-based area estimates overall and by ecosystem type, all with CI."""
    scenario = area["sensitivity_adjudication"][ARM]["final"]["scenarios"][SCENARIO]
    area_ha = scenario["olofsson"]["area_ha"]
    total = area["area_total_ha"]
    efg = area["efg_area_estimates"][ARM][SCENARIO]["reference_area_by_efg"]

    fig = plt.figure(figsize=(DOUBLE_COL, 2.80))
    gs = fig.add_gridspec(
        1, 2, width_ratios=[0.95, 1.35], wspace=0.34,
        left=0.085, right=0.985, top=0.80, bottom=0.18,
    )

    # a: overall condition estimates.
    ax = fig.add_subplot(gs[0, 0])
    ypos = np.arange(len(CLASSES))[::-1]
    for y, cls in zip(ypos, CLASSES):
        rec = area_ha[cls]
        estimate = rec["area"] / 1e3
        lo, hi = np.asarray(rec["ci95"]) / 1e3
        pct_lo = 100 * rec["ci95"][0] / total
        pct_hi = 100 * rec["ci95"][1] / total
        ax.errorbar(
            estimate, y, xerr=np.array([[estimate - lo], [hi - estimate]]),
            fmt=MARKER[cls], ms=6.0, color=COND[cls], ecolor=COND[cls],
            elinewidth=1.3, capsize=3.0, capthick=0.8,
            markeredgecolor=SURFACE, markeredgewidth=0.8, zorder=3,
        )
        ax.text(
            hi + 18, y, f"{estimate:,.0f}\n({pct_lo:.1f}-{pct_hi:.1f}%)",
            ha="left", va="center", fontsize=6.2, color=INK, linespacing=1.25,
        )

    ax.set_yticks(ypos, [COND_LABEL[c] for c in CLASSES])
    ax.set_xlim(0, 1180)
    ax.set_ylim(-0.55, 2.55)
    ax.set_xticks([0, 250, 500, 750, 1000])
    ax.set_xlabel("Area (10³ ha)")
    ax.set_title("Total area by condition", loc="left", pad=6)
    ax.xaxis.grid(True, color=GRID, lw=0.5, zorder=0)
    ax.tick_params(axis="y", length=0)
    ax.set_axisbelow(True)
    despine(ax, keep=("bottom",))

    # b: three condition estimates within each ecosystem type. Offsetting the
    # forest-plot marks preserves all nine confidence intervals without the
    # ambiguity of uncertainty bars on a stacked composition.
    ax2 = fig.add_subplot(gs[0, 1])
    efg_names = ["Arid", "Valley", "Mesic"]
    efg_labels = ["Arid thicket", "Valley thicket", "Mesic thicket"]
    base_y = np.arange(len(efg_names))[::-1]
    offsets = {"intact": 0.22, "moderate": 0.0, "severe": -0.22}

    for y0, name in zip(base_y, efg_names):
        for cls in CLASSES:
            rec = efg[name]["composition"][cls]
            estimate = rec["area_ha"] / 1e3
            lo, hi = np.asarray(rec["ci95_ha"]) / 1e3
            y = y0 + offsets[cls]
            ax2.errorbar(
                estimate, y, xerr=np.array([[estimate - lo], [hi - estimate]]),
                fmt=MARKER[cls], ms=5.2, color=COND[cls], ecolor=COND[cls],
                elinewidth=1.15, capsize=2.5, capthick=0.75,
                markeredgecolor=SURFACE, markeredgewidth=0.7, zorder=3,
            )
            ax2.text(hi + 7, y, f"{estimate:,.0f}", ha="left", va="center", fontsize=5.8)

    ax2.set_yticks(base_y, efg_labels)
    ax2.set_xlim(0, 615)
    ax2.set_ylim(-0.55, 2.55)
    ax2.set_xticks([0, 100, 200, 300, 400, 500, 600])
    ax2.set_xlabel("Area (10³ ha)")
    ax2.set_title("Area within ecosystem types", loc="left", pad=6)
    ax2.xaxis.grid(True, color=GRID, lw=0.5, zorder=0)
    ax2.tick_params(axis="y", length=0)
    ax2.set_axisbelow(True)
    despine(ax2, keep=("bottom",))
    ax2.legend(
        handles=condition_legend_handles(), ncol=3, loc="lower right",
        bbox_to_anchor=(1.0, 1.025), handletextpad=0.35,
        columnspacing=0.9, borderaxespad=0,
    )

    panel_label(fig, 0.015, "a")
    panel_label(fig, 0.455, "b")
    save_publication_figure(fig, "figure1_area_estimates")


# ============================================================== FIGURE 2 ====
def figure2(area):
    """Monochrome Nature-style map-accuracy table with interval estimates."""
    acc = area["map_accuracy"][ARM][SCENARIO]
    olof = area["sensitivity_adjudication"][ARM]["final"]["scenarios"][SCENARIO]["olofsson"]
    conf = olof["confusion_map_x_ref"]
    n_h = olof["n_h"]

    fig = plt.figure(figsize=(ONE_AND_HALF_COL, 2.14))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    x_class, x_n = 0.025, 0.205
    x_ref, dx_ref = 0.315, 0.105
    x_ua, x_pa = 0.735, 0.955
    row_y = [0.60, 0.435, 0.27]

    def rule(y, lw=0.55, x0=0.015, x1=0.985):
        ax.plot([x0, x1], [y, y], color=INK, lw=lw, clip_on=False)

    rule(0.955, 0.8)
    ax.text(x_ref + dx_ref, 0.875, "Reference class (count)", ha="center", va="bottom", color=INK_2)
    ax.plot(
        [x_ref - 0.055, x_ref + 2 * dx_ref + 0.055], [0.855, 0.855],
        color=INK_3, lw=0.45,
    )
    ax.text(x_class, 0.775, "Mapped class", ha="left", va="bottom", color=INK_2)
    ax.text(x_n, 0.775, "n", ha="right", va="bottom", color=INK_2)
    for j, cls in enumerate(CLASSES):
        ax.text(x_ref + j * dx_ref, 0.775, COND_LABEL[cls], ha="center", va="bottom", color=INK_2)
    ax.text(x_ua, 0.775, "User's accuracy (%)", ha="right", va="bottom", color=INK_2)
    ax.text(x_pa, 0.775, "Producer's accuracy (%)", ha="right", va="bottom", color=INK_2)
    rule(0.74, 0.55)

    for y, cls in zip(row_y, CLASSES):
        ax.text(x_class, y, COND_LABEL[cls], ha="left", va="center")
        ax.text(x_n, y, f"{n_h[cls]}", ha="right", va="center")
        for j, ref in enumerate(CLASSES):
            ax.text(
                x_ref + j * dx_ref, y, f"{conf[cls][ref]}",
                ha="center", va="center",
                fontweight="bold" if cls == ref else "normal",
                color=INK if cls == ref else INK_2,
            )

        for x, rec, key in (
            (x_ua, acc["users_accuracy"][cls], "U"),
            (x_pa, acc["producers_accuracy"][cls], "P"),
        ):
            lo, hi = np.asarray(rec["ci95"]) * 100
            ax.text(x, y + 0.020, f"{100 * rec[key]:.1f}", ha="right", va="center")
            ax.text(x, y - 0.047, f"({lo:.1f}–{hi:.1f})", ha="right", va="center", fontsize=5.8, color=INK_3)

    rule(0.18, 0.55)
    oa = acc["overall_accuracy"]
    oa_lo, oa_hi = np.asarray(oa["ci95"]) * 100
    ax.text(x_class, 0.105, "Overall accuracy", ha="left", va="center", fontweight="bold")
    ax.text(
        x_pa, 0.105, f"{100 * oa['OA']:.1f}%  (95% CI, {oa_lo:.1f}–{oa_hi:.1f}%)",
        ha="right", va="center", fontweight="bold",
    )
    rule(0.035, 0.8)

    save_publication_figure(fig, "figure2_accuracy_table")


# ============================================================== FIGURE 3 ====
def f1_score(pred, truth):
    pred = np.asarray(pred, dtype=bool)
    truth = np.asarray(truth, dtype=bool)
    tp = np.sum(pred & truth)
    fp = np.sum(pred & ~truth)
    fn = np.sum(~pred & truth)
    denominator = 2 * tp + fp + fn
    return float(2 * tp / denominator) if denominator else np.nan


def heldout_rule_comparison(sym, n_boot=10_000, seed=20260806):
    """Paired 0.2-degree spatial-block bootstrap of held-out F1 differences."""
    rows = load_json(OOF_JSON)["rows"]
    coords = load_json(COORDS_JSON)
    if len(rows) != len(coords):
        raise ValueError("OOF probability and coordinate caches have different lengths")

    y = np.asarray([r["ClassId"] == 0 for r in rows], dtype=bool)
    fold = np.asarray([r["fold"] for r in rows], dtype=int)
    pi = np.asarray([r["p_intact"] for r in rows], dtype=float)
    pm = np.asarray([r["p_moderate"] for r in rows], dtype=float)
    ps = np.asarray([r["p_severe"] for r in rows], dtype=float)
    coord_y = np.asarray([r["ClassId"] == 0 for r in coords], dtype=bool)
    coord_pi = np.asarray([r["p_intact"] for r in coords], dtype=float)
    if not np.array_equal(y, coord_y) or not np.allclose(pi, coord_pi, atol=1e-12, rtol=0):
        raise ValueError("OOF coordinate cache is not row-aligned with the probability cache")

    lon = np.asarray([r["lon"] for r in coords], dtype=float)
    lat = np.asarray([r["lat"] for r in coords], dtype=float)
    block_col = np.floor((lon - 20.0) / 0.2).astype(int)
    block_row = np.floor((lat + 35.0) / 0.2).astype(int)
    block_id = block_row * 10_000 + block_col

    test_mask = np.isin(fold, sym["split"]["test_folds"])
    test_idx = np.flatnonzero(test_mask)
    blocks = np.unique(block_id[test_idx])
    block_members = {b: test_idx[block_id[test_idx] == b] for b in blocks}

    scores = {
        "p_intact (baseline)": pi,
        "p_i / (p_i + p_m)": pi / (pi + pm + 1e-9),
        "p_i - max(p_m, p_s)": pi - np.maximum(pm, ps),
        "p_i - 0.5*p_s": pi - 0.5 * ps,
        "p_i / p_s          [PySR motif]": pi / (ps + 1e-9),
        "p_i * (p_m / p_s)  [PySR motif]": pi * (pm / (ps + 1e-9)),
    }
    predictions = {
        name: score >= sym["crafted_rules"][name]["threshold"]
        for name, score in scores.items()
    }
    baseline_key = "p_intact (baseline)"
    baseline_f1 = f1_score(predictions[baseline_key][test_idx], y[test_idx])

    comparison_keys = [key for key in scores if key != baseline_key]
    boot = {key: np.empty(n_boot, dtype=float) for key in comparison_keys}
    rng = np.random.default_rng(seed)
    for i in range(n_boot):
        sampled_blocks = rng.choice(blocks, size=len(blocks), replace=True)
        sampled_idx = np.concatenate([block_members[b] for b in sampled_blocks])
        f1_base = f1_score(predictions[baseline_key][sampled_idx], y[sampled_idx])
        for key in comparison_keys:
            boot[key][i] = f1_score(predictions[key][sampled_idx], y[sampled_idx]) - f1_base

    records = []
    for key in comparison_keys:
        delta = f1_score(predictions[key][test_idx], y[test_idx]) - baseline_f1
        lo, hi = np.nanquantile(boot[key], [0.025, 0.975])
        records.append({"key": key, "delta": delta, "ci95": (float(lo), float(hi))})
    return baseline_f1, records, len(test_idx), len(blocks)


def figure3(sym):
    """Held-out comparison without selecting a winner on the held-out scores."""
    base_f1, comparison, _, _ = heldout_rule_comparison(sym)
    front = sorted(sym["symbolic"]["front"], key=lambda rec: rec["complexity"])

    pretty = {
        "p_i / (p_i + p_m)": r"$p_i\, /\, (p_i+p_m)$",
        "p_i - max(p_m, p_s)": "$p_i-\\max(p_m,p_s)$",
        "p_i - 0.5*p_s": "$p_i-0.5p_s$",
        "p_i / p_s          [PySR motif]": r"$p_i\,/\,p_s$",
        "p_i * (p_m / p_s)  [PySR motif]": "$p_i(p_m/p_s)$",
    }

    fig = plt.figure(figsize=(DOUBLE_COL, 2.82))
    gs = fig.add_gridspec(
        1, 2, width_ratios=[1.10, 1.0], wspace=0.38,
        left=0.195, right=0.985, top=0.81, bottom=0.19,
    )

    # a: paired uncertainty for pre-specified score transformations.
    ax = fig.add_subplot(gs[0, 0])
    ypos = np.arange(len(comparison))[::-1]
    for y, rec in zip(ypos, comparison):
        lo, hi = rec["ci95"]
        ax.errorbar(
            rec["delta"], y,
            xerr=np.array([[rec["delta"] - lo], [hi - rec["delta"]]]),
            fmt="o", ms=4.8, color=OTHER, ecolor=OTHER,
            elinewidth=1.1, capsize=2.6, capthick=0.75,
            markeredgecolor=SURFACE, markeredgewidth=0.7, zorder=3,
        )
        ax.text(
            hi + 0.003, y, f"{rec['delta']:+.3f}",
            ha="left", va="center", fontsize=5.8, color=INK_2,
        )

    ax.axvline(0, color=COND["intact"], lw=0.8, zorder=1)
    ax.set_yticks(ypos, [pretty[rec["key"]] for rec in comparison])
    ax.set_xlim(-0.105, 0.052)
    ax.set_xticks([-0.10, -0.05, 0.00, 0.05])
    ax.set_ylim(-0.65, len(comparison) - 0.35)
    ax.set_xlabel("Held-out Δ$F_1$ relative to $p_{intact}$")
    ax.set_title("Paired spatial-block comparison", loc="left", pad=6)
    ax.xaxis.grid(True, color=GRID, lw=0.5, zorder=0)
    ax.tick_params(axis="y", length=0)
    ax.set_axisbelow(True)
    despine(ax, keep=("bottom",))
    ax.text(
        0.985, 0.035, f"baseline $F_1$ = {base_f1:.3f}",
        transform=ax.transAxes, ha="right", va="bottom", fontsize=6.0, color=COND["intact"],
    )

    # b: all PySR Pareto candidates are displayed; held-out performance is not
    # used to choose a highlighted "winner".
    ax2 = fig.add_subplot(gs[0, 1])
    pi_only = [rec for rec in front if "p_m" not in rec["equation"] and "p_s" not in rec["equation"]]
    multi = [rec for rec in front if rec not in pi_only]
    ax2.axhline(base_f1, color=COND["intact"], lw=0.8, zorder=1)
    ax2.scatter(
        [rec["complexity"] for rec in multi], [rec["test_f1"] for rec in multi],
        s=24, marker="^", facecolor=OTHER, edgecolor=SURFACE, linewidth=0.7, zorder=3,
        label="Uses additional layer",
    )
    ax2.scatter(
        [rec["complexity"] for rec in pi_only], [rec["test_f1"] for rec in pi_only],
        s=29, marker="o", facecolor=COND["intact"], edgecolor=SURFACE, linewidth=0.7, zorder=4,
        label="$p_i$-only expression",
    )
    first = min(front, key=lambda rec: rec["complexity"])
    ax2.annotate(
        "$p_i$-only", xy=(first["complexity"], first["test_f1"]),
        xytext=(first["complexity"] + 0.9, first["test_f1"] + 0.0065),
        fontsize=6.4, ha="left", va="bottom",
        arrowprops=dict(arrowstyle="-", color=INK_3, lw=0.5, shrinkA=1, shrinkB=3),
    )
    representative_multi = next(rec for rec in multi if rec["complexity"] == 6)
    ax2.annotate(
        "additional layer", xy=(representative_multi["complexity"], representative_multi["test_f1"]),
        xytext=(representative_multi["complexity"] - 3.2, representative_multi["test_f1"] + 0.006),
        fontsize=6.0, ha="left", va="bottom", color=INK_2,
        arrowprops=dict(arrowstyle="-", color=INK_3, lw=0.5, shrinkA=1, shrinkB=3),
    )
    ax2.text(16.7, base_f1 + 0.0017, "$p_i$ baseline", ha="right", va="bottom", fontsize=5.8, color=COND["intact"])

    ax2.set_xlim(0, 17)
    ax2.set_ylim(0.815, 0.872)
    ax2.set_xticks([1, 4, 8, 12, 16])
    ax2.set_yticks([0.82, 0.83, 0.84, 0.85, 0.86, 0.87])
    ax2.set_xlabel("Expression complexity")
    ax2.set_ylabel("Held-out $F_1$ (intact class)")
    ax2.set_title("PySR candidates", loc="left", pad=6)
    ax2.yaxis.grid(True, color=GRID, lw=0.5, zorder=0)
    ax2.set_axisbelow(True)
    despine(ax2)

    panel_label(fig, 0.015, "a")
    panel_label(fig, 0.585, "b")
    save_publication_figure(fig, "figure3_symbolic_regression")


def main():
    area, sym = load()
    figure1(area)
    figure2(area)
    figure3(sym)


if __name__ == "__main__":
    main()
