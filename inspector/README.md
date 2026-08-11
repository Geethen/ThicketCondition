# Thicket Condition Inspector

A single-file, no-backend web app for labeling the reference sample of South
African solid-thicket condition points (intact / moderate / severe). Adapted
from the DIST-ALERT inspector but stripped to a **static page** so it can be
shared as a link and run entirely in the browser.

## What it does

- Shows the combined **2,098-point analysis draw** from
  `analysis/results/sample_points_vegtype2022.csv`. The **1,252 new points** are
  required Round 2 work; the original 846 remain available to the coordinator.
- A dedicated **Round 1 disagreements** queue exposes the 34 points with at least
  one determinate pairwise conflict across ARP, SVM, AP, and MP. Selecting one
  shows each prior label without pre-filling a new decision. Exploration labels
  are exported but do not count against Round 2 completion.
- **Area estimates** opens an interactive, error-adjusted composition chart at
  landscape, ecosystem-functional-group, or vegetation-type level. A second
  dropdown switches between reporting no-thicket separately and combining it
  with severe. A colour-vision-deficiency-friendly palette distinguishes the
  labelled classes. The panel shows explicit 95% confidence intervals and
  margins of error, and identifies the estimate as a static Round 1-only
  snapshot that excludes Round 2 labels and does not recalculate live from Sheets.
- Labelers step through points and record the condition class they observe:
  **Intact**, **Moderate**, **Severe**, **Transformed**, **No thicket**, or **Unsure**.
  Older imports using the former combined class remain visible in a legacy review queue
  and must be resolved rather than being silently reclassified.
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
- Labels **auto-save to the browser** (localStorage). Optional Google Sheets sync
  is enabled by default; until a Web App URL is configured it remains safely
  queued locally. JSON/CSV backups remain available in either case.
- Optional pre-assigned campaigns restrict each personal `?assignment=CODE` link
  to a deterministic, balanced point list, with deliberate blind QA overlap.
- **Blind labeling is on by default**: model strata are hidden until a point has
  been labeled, reducing anchoring bias in the reference data.
- Optional **auto-advance** moves directly to the next unlabeled point. Every
  label change and explicit clear action is undoable.
- Notes save while typing, including drafts on points that have not been labeled yet.
- Browser storage and imports are tied to a fingerprint of the embedded sample draw,
  preventing labels from silently moving to different coordinates after a redraw.
- Progress counters double as review filters, and the map can show all, unlabeled,
  labeled, or any individual class. The `n` shortcut follows the active filter.
- **Completion & QA** summarizes every class, detects incomplete work, and queues
  unsure, flagged, and low-confidence points before a single JSON or CSV download.
  Exports include completion metadata and a checksum; the UI tracks the last backup.
- Flags and High / Medium / Low confidence are stored independently from the label,
  with dedicated review queues and quick structured note reasons.
- **Download** exports the chosen JSON *or* CSV format. **Upload / resume** re-loads a
  previously downloaded file through a preview that reports conflicts and invalid
  rows before anything changes. Imports support fill-only, keep-newer, and replace
  strategies, and the applied batch can be undone.
- Keyboard: `1`–`6` set the class, `←/→` or `space` move, `n` advances the active
  queue, `f` flags, `Ctrl/Cmd+Z` undoes, `i/g/s/w` switches imagery, and `?` opens
  the persistent shortcut reference.
- The responsive interface includes map zoom/recenter presets, point-ID/history
  navigation, drag-and-drop import, a collapsible desktop panel, mobile layout,
  reduced-motion support, and an optional cached PWA application shell.

## Files

| File | Purpose |
|------|---------|
| `index.html` | **The deployable app** — self-contained, points embedded, JS inlined. This is the only file you host. |
| `thicket_inspector.html` | HTML template (with a `__POINTS__` placeholder). |
| `app.js` | App logic (source of truth; inlined into `index.html` at build). |
| `build.py` | Regenerates `index.html` from the template + `app.js` + the sample CSV + compact area-estimation snapshot. |
| `create_assignments.py` | Creates balanced point assignments and shareable links. |
| `assignment_manifest.json` | Campaign assignments embedded into the built app. |
| `sync_config.json` | Default Google Sheets sync switch, Apps Script endpoint, and optional Sheet link. |
| `google_apps_script.gs` | Apps Script receiver: upserted `Labels` tab plus append-only `Events` audit. |
| `LABELLER_INSTRUCTIONS.md` | Ready-to-share operating instructions for labellers. |
| `bake_gee_layers.py` | One-shot Earth Engine bake → `gee_layers.json` (keyless S2 tile URLs). |
| `gee_layers.json` | **Generated, git-ignored** manifest of baked EE tile URLs the page fetches at runtime. Tokens are temporary — re-bake to refresh. |

## Rebuild after editing

Edit `app.js` or `thicket_inspector.html`, then:

```bash
python inspector/build.py        # or the geo env's python on this machine
```

This re-reads `analysis/results/sample_points_vegtype2022.csv` and
`analysis/results/area_estimation_vegtype2022.json`, so a new sample draw or estimate
is picked up automatically. The build stamps a short **dataset id** (a hash of the
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
node inspector/verify_assignments.mjs     # balanced coverage, links, storage isolation
node inspector/verify_round2_sync.mjs     # Round 2 scope, disagreement queue, sync outbox
node inspector/verify_sw_cache.mjs        # redeploys reach returning users; upgrade keeps
                                          # labels and offline use (serves over HTTP)
```

## Deploy (shareable link)

Live at **https://geethen.github.io/ThicketCondition/**.

Deployment is automated: **every push to `main` that touches `inspector/**` (or the
sample CSV / area-estimation artifact)** triggers `.github/workflows/bake-gee-and-deploy.yml`, which runs
`build.py`, optionally bakes the EE layers, and publishes to Pages. You don't copy
anything into `docs/` anymore — just edit `app.js`/the template and push. (Pages
Source is set to **GitHub Actions**; the old `main/docs/` copy is no longer served.)

To publish manually / on demand: Actions tab → *Deploy inspector to Pages* → Run
workflow. Any static host also works since `index.html` is fully self-contained.

**Netlify / Cloudflare Pages / Vercel**: drag-and-drop `index.html` (rename the
folder's entry to `index.html`) — no build command needed.

## Collecting labels back

Each labeler clicks **Review & download**, chooses a format, and sends you their
`thicket_labels_<name>_<timestamp>.csv` (or `.json`). Merge the CSVs; the `id`
column joins back to `sample_points_vegtype2022.csv`, and `label` is the reference class for
the Olofsson accuracy/area estimation.

Round 2 exports carry `source`, vegetation type/EFG, 2022 and 2025 classes,
`required`, and `disagreement`. The estimator should use required Round 2 rows
alongside the retained Round 1 data; optional disagreement re-reads are clearly
marked rather than silently entering the design-based sample.

## Google Sheets sync

The app uses a Google Apps Script Web App because a static GitHub Pages site
cannot safely contain Google service-account credentials. Local storage is still
the first write, and every remote change stays queued until the browser can hand
it to the Web App. The append-only `Events` tab is the delivery audit.

1. Create/open the campaign Google Sheet and choose **Extensions → Apps Script**.
2. Paste `google_apps_script.gs` into `Code.gs`. Set `EXPECTED_DATASET` to the id
   printed by `python inspector/build.py` (currently also shown on the welcome screen).
3. Deploy it as a **Web app**, execute as the sheet owner, and grant the narrowest
   access that covers the labellers. Copy its `/exec` URL.
4. Put that URL in `sync_config.json` as `endpoint`, optionally put the Sheet URL
   in `sheet_url`, then rebuild. `enabled_by_default` is already `true`.

For the GitHub Pages deployment, the same values can be kept out of source:
create repository **Settings → Secrets and variables → Actions → Variables** named
`GOOGLE_SHEETS_SYNC_ENDPOINT` and (optionally) `GOOGLE_SHEET_URL`. The workflow
injects them into the build and they override `sync_config.json`.

Labellers can also use **Configure** in the app without rebuilding. Each change
is written to an upserted `Labels` tab and an append-only `Events` tab. A static
page cannot keep an API token secret, so deployment access is the real security
boundary; `EXPECTED_DATASET` is only a guardrail. Continue taking downloaded
backups even when sync is active.

For coordinated campaigns, see [MULTI_LABELLER_OPTIONS.md](MULTI_LABELLER_OPTIONS.md).
The recommended first step is deterministic, balanced assignments with 10–15%
intentional blind overlap for agreement QA; a central claim service is the
long-term option when live coordination becomes necessary.

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
