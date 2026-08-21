# Multi-labeller options

## Current constraint

The inspector is a static GitHub Pages app with deterministic assignments and an
optional Google Apps Script outbox. Labels save locally first and can sync into
the campaign Sheet, but the app does not use that Sheet for atomic point claims.
Assignments therefore remain the mechanism that prevents accidental duplicate
work; deliberate overlap is recorded for QA.

## Options

| Approach | Avoids duplicate effort | Offline | Operational cost | Best use |
|---|---:|---:|---:|---|
| Pre-assigned point lists | Yes | Yes | Low | Immediate field campaign or small team |
| Shared spreadsheet as assignment register | Mostly | Limited | Low | Short pilot with a coordinator |
| Central claim service and database | Yes, in real time | With sync design | Medium | Recommended for sustained multi-person work |
| General annotation platform | Yes | Varies | Medium–high | When many datasets and annotation types need one system |

### 1. Pre-assigned point lists — implemented

Generate a small assignment manifest for each labeler and let the app show only
that person's points. Assign within every model stratum and spatial block so all
labelers receive balanced work. A stable assignment can be regenerated from the
dataset ID, point ID, and campaign seed.

Keep most assignments disjoint, but deliberately give 10–15% of points to two
labelers for blind agreement measurement. This distinguishes useful QA overlap
from accidental duplication. Exports retain point ID and labeler, so they remain
easy to merge and adjudicate.

The implemented interface uses a URL such as `?assignment=GS`. The campaign and
point list are read from the manifest embedded during the build. Progress,
browser storage, imports, and exports are scoped to the stable assignment ID.
Invalid codes are blocked rather than falling back to the whole dataset.

Create a campaign and its shareable link register with:

```powershell
py -3 inspector/create_assignments.py `
  --campaign thicket-2026-r2 `
  --labelers GS AB CD EF `
  --overlap 0.12 `
  --base-url https://geethen.github.io/ThicketCondition/
```

This writes `assignment_manifest.json` and `assignment_links.csv`. Commit the
manifest and push it; the Pages workflow embeds it into `index.html`. Send each
person only their row's URL and [LABELLER_INSTRUCTIONS.md](LABELLER_INSTRUCTIONS.md).
The link CSV is a coordinator register and is git-ignored because it maps people
to their assignments.

Once a populated campaign is deployed, the bare site URL is blocked to prevent
accidental all-point labelling. Coordinators can inspect the full dataset with
`?mode=coordinator`; do not share that URL with labellers.

### Extending a live campaign

Adding points or people to a campaign that is already being worked through is
the one operation that can destroy work, so it has its own path.

Labels live only in `localStorage`, under a key built from the campaign's dataset
fingerprint and the labeller's assignment id — both embedded in `index.html` at
build time. The fingerprint hashes every point, so simply adding points to the
draw renames every key: the labeller opens the same link, finds an empty app,
and their labels sit under a key nothing reads. The import guard then refuses
their own backup, because it belongs to "a different dataset". The same
fingerprint keys the Google Sheet upsert and the Apps Script `EXPECTED_DATASET`
check, so a new one silently orphans the Sheet rows too.

Two rules keep that from happening:

1. **Grow the draw by append.** Every existing point keeps its id, coordinates
   and stratum; new points get fresh ids. `analysis/29_topup_r4.py` is the worked
   example. `dataset_lineage.json` then pins the campaign fingerprint, and
   `build.py` re-proves the append-only property on every build before reusing
   it — if a point ever moves, the build fails rather than shipping.
2. **Never re-run `create_assignments.py` on a live campaign.** It deals the
   whole campaign at once and would re-shuffle assignments already in progress.
   Use `extend_assignments.py`, which copies existing records through verbatim
   and only deals points no one holds:

   ```powershell
   py -3 inspector/extend_assignments.py `
     --add AM RLO --per-labeler 600 --qa-overlap 72 `
     --base-url https://geethen.github.io/ThicketCondition/
   ```

   `--qa-overlap` gives each new labeller second reads on points the existing
   team already holds, so the newcomers are measured against the established
   labellers rather than only against each other.

Then prove it before pushing:

```powershell
py -3 inspector/build.py
node inspector/verify_round4_migration.mjs
```

That test serves the currently deployed `index.html`, labels points as an
existing labeller, swaps the new build in at the same origin — exactly what the
Pages deploy does — and reloads. The labels, storage keys, assignment id and
workload all have to come back unchanged, and the pre-deploy backup still has to
import. Existing links keep working because their assignment ids are unchanged;
only the two new links are new.

### 2. Shared assignment register — workable pilot

A protected Google Sheet or similar table can hold `point_id`, `assigned_to`,
`status`, and `updated_at`. The static app would still need a small API or manual
imports to use it. Concurrent edits, weak validation, credentials, and offline
work make this less reliable than it first appears. It is reasonable for a
coordinator-led pilot, not ideal as the long-term source of truth.

### 3. Central claim service — recommended long-term option

Use a small API with Postgres/Supabase or a Cloudflare Worker plus D1. The app
requests a batch, and the server atomically creates assignments so two labelers
cannot claim the same point in the same review round.

Minimum records:

- `datasets`: immutable dataset ID and schema version.
- `assignments`: dataset, point, round, labeler, status, claimed/updated times;
  unique on `(dataset_id, point_id, round)`.
- `labels`: append-only submissions with class, confidence, notes, labeler, and
  timestamps. Do not overwrite another labeler's observation.
- `adjudications`: final decision for deliberate overlaps or disagreements.

Claim batches rather than single points, and use renewable leases only for
abandoned work. A browser-side outbox can retain labels offline and sync later.
The server must reject dataset/schema mismatches and return conflicts for human
resolution instead of applying “latest write wins”.

### 4. General annotation platform

Label Studio or a similar system supplies accounts, queues, and review roles,
but reproducing this inspector's synchronized satellite/Wayback map experience
would require custom frontend work. This becomes attractive only if the project
will manage several annotation projects, media types, or large teams.

## Recommended rollout

1. Generate and deploy a deterministic assignment manifest for the named team.
2. Allocate 85–90% disjoint work and 10–15% blind duplicate QA, stratified by
   condition stratum and geography.
3. Report agreement by class and send disagreements, unsure points, legacy
   combined labels, and low-confidence records to an adjudicator.
4. Move to the central claim service if active coordination, live dashboards,
   or repeated campaigns justify maintaining authentication and a database.

This preserves the app's easy deployment now while leaving a clean path to
real-time collaboration later.
