# ThicketCondition

Analysis and visualisation artifacts for thresholding thicket ecological condition model outputs. The project focuses on choosing a defensible `p_intact` threshold for mapping intact thicket and testing whether symbolic combinations of the three Random Forest probability bands improve the result.

## Contents

- `threshold_sensitivity.html` - self-contained interactive threshold sensitivity report.
- `steph.js` - Google Earth Engine workflow for the thicket condition model.
- `analysis/` - reproducible Python scripts, cached out-of-fold samples, and generated result JSON used by the report.

## Key Result

The recommended deployment rule is:

```text
p_intact >= 0.485
```

This single-band threshold is near-optimal in the held-out spatial validation. Symbolic combinations of `p_intact`, `p_moderate`, and `p_severe` provide at most a marginal gain and are not recommended for the default map.

## Reproducing the Analysis

Use a Python environment with `earthengine-api`, `numpy`, `pandas`, `scikit-learn`, `scipy`, `gplearn`, and optionally `pysr` installed. Authenticate Earth Engine before running scripts and initialize with a project that can read the shared thicket assets.

```powershell
cd analysis
python -u 01_threshold_sensitivity.py
python -u 02_sample_oof_3band.py
python -u 03_symbolic_intact.py
python -u 05_symbolic_artifact_data.py
python inject_artifact_data.py results/artifact_data.json ../threshold_sensitivity.html
```

If the PySR/Julia backend is unavailable, set `USE_PYSR = False` in `analysis/03_symbolic_intact.py` to use the gplearn fallback.

## Repository Notes

Local authentication notebooks, logs, and PySR checkpoint directories are ignored. The cached `analysis/data/` and `analysis/results/` files are kept in the repository so the HTML report and downstream review can be reproduced without rerunning Earth Engine jobs.
