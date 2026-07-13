# Thicket Condition Inspector

A single-file, no-backend web app for labeling the reference sample of South
African solid-thicket condition points (intact / moderate / severe). Adapted
from the DIST-ALERT inspector but stripped to a **static page** so it can be
shared as a link and run entirely in the browser.

## What it does

- Shows all **846 stratified sample points** (from `analysis/results/sample_points.csv`)
  on a satellite basemap, colored by their model stratum.
- Labelers step through points and record the condition class they observe:
  **Intact**, **Moderate**, **Severe**, plus **Not thicket / transformed** and **Unsure**.
- Imagery to judge canopy cover: **Esri World Imagery**, **Esri Wayback**, **Google
  Satellite**, and **Sentinel-2 cloudless (10 m)** — all keyless. Plus one-click deep
  links to Google Maps and Google Earth.
- **Esri Wayback** (ported to match the DIST-ALERT inspector):
  - a **date dropdown** + `‹`/`›` step buttons over every Wayback release;
  - **"only dates with new imagery here"** — walks Esri's tilemap service at the current
    point so the list collapses to just the releases whose imagery actually changed there;
  - a **true acquisition date** (`SRC_DATE`) from the release metadata service, not just
    the release date;
  - **⇆ swipe compare** — a second synced map clipped by a draggable divider to compare
    two dates side by side.
- Optional **Earth Engine Sentinel-2 composites** (extra year basemaps) baked offline —
  see *Earth Engine layers* below. Keyless in the browser; no login required.
- Labels **auto-save to the browser** (localStorage). Nothing is uploaded anywhere.
- **Download** exports the labels as JSON *and* CSV. **Upload / resume** re-loads a
  previously downloaded file so a labeler can continue where they left off (and to
  merge/QA everyone's files later).
- Keyboard: `1`–`5` set the class, `←/→` or `space` move between points, `n` jumps
  to the next unlabeled point.

## Files

| File | Purpose |
|------|---------|
| `index.html` | **The deployable app** — self-contained, points embedded, JS inlined. This is the only file you host. |
| `thicket_inspector.html` | HTML template (with a `__POINTS__` placeholder). |
| `app.js` | App logic (source of truth; inlined into `index.html` at build). |
| `build.py` | Regenerates `index.html` from the template + `app.js` + the sample CSV. |
| `bake_gee_layers.py` | One-shot Earth Engine bake → `gee_layers.json` (keyless S2 tile URLs). |
| `gee_layers.json` | **Generated, git-ignored** manifest of baked EE tile URLs the page fetches at runtime. Tokens are temporary — re-bake to refresh. |

## Rebuild after editing

Edit `app.js` or `thicket_inspector.html`, then:

```bash
python inspector/build.py        # or the geo env's python on this machine
```

This re-reads `analysis/results/sample_points.csv`, so a new sample draw is
picked up automatically. The build stamps a short **dataset id** (a hash of the
point ids + coordinates + strata) into the page; browser labels are namespaced
by it, so a new draw never shows stale labels, and importing a file exported for
a different draw prompts before overwriting.

## Tests

Headless Playwright checks (need `npm install` in `inspector/` once):

```bash
node inspector/smoke_test.mjs          # core labeling flow, counts, export, tiles
node inspector/verify_wayback.mjs      # Wayback dropdown, capture date, local filter, compare
node inspector/verify_data_integrity.mjs  # corrupt storage, dataset namespacing,
                                          # CSV round-trip + injection safety, import validation
```

## Deploy (shareable link)

Live at **https://geethen.github.io/ThicketCondition/**.

Deployment is automated: **every push to `main` that touches `inspector/**` (or the
sample CSV)** triggers `.github/workflows/bake-gee-and-deploy.yml`, which runs
`build.py`, optionally bakes the EE layers, and publishes to Pages. You don't copy
anything into `docs/` anymore — just edit `app.js`/the template and push. (Pages
Source is set to **GitHub Actions**; the old `main/docs/` copy is no longer served.)

To publish manually / on demand: Actions tab → *Deploy inspector to Pages* → Run
workflow. Any static host also works since `index.html` is fully self-contained.

**Netlify / Cloudflare Pages / Vercel**: drag-and-drop `index.html` (rename the
folder's entry to `index.html`) — no build command needed.

## Collecting labels back

Each labeler clicks **Download** and sends you their
`thicket_labels_<name>_<timestamp>.csv` (and `.json`). Merge the CSVs; the `id`
column joins back to `sample_points.csv`, and `label` is the reference class for
the Olofsson accuracy/area estimation.

## Earth Engine layers (keyless in the browser)

The inspector can show live Earth Engine composites **without any key or login in
the page**. A service-account private key must never ship in a static file — a page
on GitHub Pages is world-readable, and anyone could lift the key and burn your EE
quota. Instead, Earth Engine's privileged work happens **offline / in CI**, and only
the resulting *keyless, temporary* tile URLs reach the browser:

```
service-account key ──► bake_gee_layers.py ──► gee_layers.json ──► the page loads it
   (secret, server-side)   (calls getMapId)      (public tile URLs)   as a raster source
```

**Bake locally** (uses your `earthengine authenticate` login):

```bash
~/.pixi/envs/geo/python.exe inspector/bake_gee_layers.py
```

**Bake with a service account** (what CI does) — set env vars, key never committed:

```bash
export EE_SA_KEY_FILE=/path/to/key.json     # or EE_SA_KEY_JSON=<contents>
export EE_SA_EMAIL=<sa>@<project>.iam.gserviceaccount.com
python inspector/bake_gee_layers.py
```

Edit the `COMPOSITES` list in `bake_gee_layers.py` to change which years/layers appear.

### Keeping tiles fresh with GitHub Actions

EE `getMapId` tokens expire (hours–days), so a one-off bake goes stale. The workflow
`.github/workflows/bake-gee-and-deploy.yml` re-bakes on a **weekly schedule** (and on
demand / on push) and deploys the site with a fresh `gee_layers.json`. One-time setup:

1. Create an EE-registered service account + JSON key
   ([guide](https://developers.google.com/earth-engine/guides/service_account)) and
   register it with your EE project (`ee-gsingh`).
2. Repo → **Settings → Secrets and variables → Actions**: add
   `EE_SA_KEY_JSON` = full key contents (and optionally `EE_SA_EMAIL`).
3. Repo → **Settings → Pages → Source = GitHub Actions** ← *required* for the workflow
   to publish. (If instead you keep Pages on "Deploy from a branch", the git-ignored
   `gee_layers.json` won't reach the site — either commit a manifest, or switch the
   source to GitHub Actions.)

Secrets are **not** exposed to fork PRs, so the workflow deliberately triggers only on
`push` / `schedule` / `workflow_dispatch` — never on `pull_request`.

> Prefer not to run Earth Engine at all? The page works fine without the manifest —
> the S2-composite buttons simply don't appear, and every other basemap still works.

## Notes / limitations

- Wayback tiles + release list come from Esri's public config at runtime; if that
  endpoint is unreachable the Wayback panel is simply disabled — other basemaps work.
- The "only new imagery here" filter and capture-date lookup call Esri's public
  tilemap / metadata services per point; they degrade gracefully (fall back to the
  release date) if a request fails.
- Sentinel-2 cloudless is the EOX 2023 annual composite (keyless), a whole-canopy
  reference aid. The EE-baked S2 composites are per-year medians over the sample AOI.
