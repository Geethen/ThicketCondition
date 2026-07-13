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
- Imagery to judge canopy cover: **Esri World Imagery**, **Esri Wayback** (multi-date
  time slider), **Google Satellite**, and **Sentinel-2 cloudless (10 m)** — all keyless.
  Plus one-click deep links to Google Maps and Google Earth.
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

## Rebuild after editing

Edit `app.js` or `thicket_inspector.html`, then:

```bash
python inspector/build.py        # or the geo env's python on this machine
```

This re-reads `analysis/results/sample_points.csv`, so a new sample draw is
picked up automatically.

## Deploy (shareable link)

`index.html` is fully self-contained, so any static host works:

**GitHub Pages**
```bash
# put index.html at the repo root of a Pages-enabled repo (or /docs)
cp inspector/index.html docs/index.html
git add docs/index.html && git commit -m "Deploy thicket inspector"
git push
# enable Pages: Settings > Pages > Deploy from branch > /docs
```
Share the resulting `https://<user>.github.io/<repo>/` link.

**Netlify / Cloudflare Pages / Vercel**: drag-and-drop `index.html` (rename the
folder's entry to `index.html`) — no build command needed.

## Collecting labels back

Each labeler clicks **Download** and sends you their
`thicket_labels_<name>_<timestamp>.csv` (and `.json`). Merge the CSVs; the `id`
column joins back to `sample_points.csv`, and `label` is the reference class for
the Olofsson accuracy/area estimation.

## Notes / limitations

- Wayback tiles are fetched from Esri's public config at runtime; if that endpoint
  is unreachable the Wayback slider is simply disabled — the other basemaps still work.
- Sentinel-2 cloudless is the EOX 2023 annual composite (keyless). It is a
  reference/whole-canopy aid, not a per-date source.
- No probability-surface overlay is included (keeps the page keyless and static).
  If you later want the RF `p_intact`/`p_severe` layers on the map, they need EE
  tile URLs, which expire — that would reintroduce a small token endpoint.
