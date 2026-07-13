# Thicket condition — threshold sensitivity & symbolic band-combination analysis

Self-contained analysis for choosing how to turn the per-EFG Random Forest condition
probabilities into an **intact / not-intact** map, and for discovering the best
combination of the three probability bands. Any agent can resume from here.

## What the model is (from `../steph.js`)

Three per-Ecosystem-Functional-Group (EFG) `ee.Classifier.smileRandomForest`
classifiers — **arid / valley / mesic** thicket — 300 trees, seed 123,
`minLeafPopulation=1`, `bagFraction=0.632`, `MULTIPROBABILITY` output. Predictors are the
64 bands of `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL` (2022). Each pixel gets
`(p_intact, p_moderate, p_severe)` which sum to 1.

Reference/training data `training_collated_withFLC` (3078 pts). Classes:
`intact 751 · moderate 633 · severe 820 · transformed 778 · bontveld 96`.
Only **intact/moderate/severe** are modelled (→ ~2083 points inside solid-thicket EFGs after
masking). `transformed` is handled by the land-cover mask; `bontveld` is dropped.

**Goal:** map *intact* thicket. Baseline rule is `p_intact ≥ τ`; we also test rules that
use `p_moderate`/`p_severe`, and search for an optimal closed-form combination.

## Environment

- Python environment with `earthengine-api`, numpy, pandas, sklearn, scipy, **gplearn**, and
  optionally **pysr**. Use the same environment for every script in the pipeline.
- Earth Engine init: `ee.Initialize(project='ee-gsingh')`.
  Do **not** init with `project='thicket-ecological-condition'` (403 — no serviceusage).
  Thicket assets are readable per-asset because they were shared to the user's account.
- PySR uses a Julia backend. The **first ever import precompiles for ~6 min** then caches
  under `~/.julia`. Subsequent runs are fast. If PySR ever breaks, set `USE_PYSR=False`
  in `03_symbolic_intact.py` to fall back to gplearn.

## Pipeline / scripts (run in order)

| Script | Purpose | Key outputs |
|---|---|---|
| `01_threshold_sensitivity.py` | Full P(intact) threshold sweep: accuracy-vs-τ (spatial 5-fold CV) + area-vs-τ (real 30 m→100 m surface). Caches OOF to `data/oof_points.json`; re-run reuses it. | `results/threshold_accuracy.json`, `results/threshold_area.json`, `results/summary.json`, `results/artifact_data.json` |
| `02_sample_oof_3band.py` | Spatial 5-fold CV that captures **all three** OOF probabilities per point at 10 m (escalates tileScale 4→8→16, then 30 m). | `data/oof_3band.json` |
| `03_symbolic_intact.py` | (A) standard threshold-method comparison on `p_intact`; (B) **PySR** symbolic search for the best band combination; (C) hand-crafted rules. All scored by held-out intact **F1** on a spatial block split (train folds 0/1/2, test folds 3/4). Writes the native PySR front (every formula evaluated by PySR itself). ~2 min. | `results/symbolic_results.json`, `results/pysr_hall_of_fame.csv`, `results/pysr_front_native.json` |
| `04_build_symbolic_results.py` | Rebuild `symbolic_results.json` from the cached front + OOF data **without** re-running PySR/Julia (fast, pure numpy). Merges native front with crafted rules. | `results/symbolic_results.json` |
| `05_symbolic_artifact_data.py` | Emit compact JSON for the artifact's Part-2 panels. | `results/symbolic_artifact.json` |
| `inject_artifact_data.py` | Inject a results JSON into the HTML artifact. `python inject_artifact_data.py [DATA_JSON] [HTML_FILE]`. | updates `../threshold_sensitivity.html` |

**Regenerate Part-2 results without Julia:** run `04` then `05` (they read the cached
`pysr_hall_of_fame.csv` / `pysr_front_native.json`). Only re-run `03` if you change the
OOF data or the PySR search settings. PySR writes per-run checkpoints under `outputs/`
(safe to delete).

**Artifact Part-2 data flow:** `03` → `04` (merge) → `05` (compact) → inject the
`<script id="SYMBOLIC">` block of `../threshold_sensitivity.html`, then re-publish the
artifact via the Artifact tool (same file path keeps the URL).

Run example:
```
cd analysis
python -u 03_symbolic_intact.py
```

## Data files (already generated — safe to reuse)

- `data/oof_points.json` — 2083 rows `{ClassId, p_intact, efg_id, fold}` (10 m, spatial CV).
- `data/oof_3band.json` — 2083 rows `{ClassId, p_intact, p_moderate, p_severe, efg_id, fold}`
  (10 m, tileScale 4). This is the input to the symbolic search. Probabilities sum to 1.0.
- `results/*.json` — computed results (see table).

`fold` = spatial block id mod 5, where blocks are 0.2°×0.2° cells
(`floor((lon-20)/0.2)`, `floor((lat+35)/0.2)`). Whole blocks stay in one fold → no
train/test leakage. The `03` script's held-out split uses folds {0,1,2} to fit and {3,4}
to validate the discovered formula.

## Results so far (2026-07-13)

### 1. Ideal threshold on `p_intact`
τ* = **0.48** (max Youden's J = 0.753, also max-F1). Overall accuracy 88.7%,
sensitivity 0.840, specificity 0.912, **ROC AUC 0.947**. Total valid thicket area
19,106 km²; intact area at τ* ≈ 4,418 km². Visualised in `../threshold_sensitivity.html`.

### 2. Threshold-selection method comparison (RS literature)
Most-used methods: **Youden's J / max-TSS** and **max-Kappa** (Liu et al. 2005, Ecography;
Freeman & Moisen 2008). Max-overall-accuracy is prevalence-biased and discouraged.
On this data all three land near τ≈0.48–0.49 (see `results/symbolic_results.json` →
`standard_methods`).

### 3. Band-combination / symbolic model — **conclusion: p_intact alone is near-optimal**
Scored by **held-out intact F1** on independent spatial blocks (train folds 0/1/2, test 3/4).
- Baseline `p_intact` threshold: held-out F1 = **0.860**.
- Best rule found: `p_i/(p_i+p_m)` (intact-vs-moderate, ignoring severe) = **0.867** — a gain of
  only **+0.008**, within run-to-run noise.
- **PySR Pareto front** (14 formulas, complexity 1→16): training MSE falls ~15% but held-out
  F1 **never improves** over complexity-1 (`p_i`); the complex `Piecewise`/`p_i·(p_m/p_s)`
  formulas overfit the train blocks. See `results/pysr_front_native.json`.
- **Recommendation:** deploy the single-band rule `p_intact ≥ 0.485` — no band arithmetic
  needed. If you want the marginal edge, use `p_i/(p_i+p_m) ≥ 0.596` as `ee.Image` band math on
  the `export_stack` p_intact/p_moderate bands.

## Known gotchas

- The area `reduceRegion` over `solidEFG.geometry()` at 30 m **times out** on the
  interactive endpoint. Fix used in `01`: reduce over the AOI rectangle at 100 m via one
  area-weighted grouped histogram (surface is already masked to valid thicket, so area is
  exact). For a definitive 30 m number, run it as a batch `Export`/`getInfo` off a
  `ee.batch` task instead.
- NumPy 2.x here: `np.trapz` was removed → use `np.trapezoid`.
- EE `getInfo` calls can take minutes; scripts print progress with `flush=True`. Run with
  `-u` and tail the `*.log`.
