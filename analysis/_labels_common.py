"""
_labels_common.py
=================
Shared label loading, deduplication and adjudication for the area-estimation
scripts (14, 15, 17, 18, 19).

Problem this solves (review Finding 1):
  The two label files together hold 474 rows for only 462 UNIQUE points. 12
  points were deliberately double-labelled as a QA check; 4 of those disagree
  (ids 150, 211, 291, 550). Concatenating both files counts the QA duplicates as
  independent samples, corrupting stratum counts, SEs and n.

This module returns ONE adjudicated reference label per unique point. Because the
adjudication rule genuinely changes some class areas (~7,000 ha), it is exposed
as a SENSITIVITY: callers run every estimator under both `adjudicate='ARP'` and
`adjudicate='SVM'` and report the spread as interpreter variability
(Olofsson et al. 2014 recommend explicitly evaluating interpreter variability).

Agreement on the 12 QA points is reported separately via `qa_agreement()`.

Deterministic: given the two label files, the deduplicated set is fixed.
"""
import json, csv, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "results")

ARP_FILE = os.path.join(ROOT, "thicket_labels_ARP_ARP_2026-07-17-14-19-13.json")
SVM_FILE = os.path.join(ROOT, "thicket_labels_SVM_SVM_2026-07-15-13-41-42.csv")

# labeller whose label wins when the two disagree (the OTHER is the sensitivity arm)
_PREFER_ORDER = {"ARP": ["ARP", "SVM"], "SVM": ["SVM", "ARP"]}


def _norm_label(lab):
    return "nothicket" if lab == "notthicket" else lab


def _load_raw():
    """Return {id: {labeller: row}} where row has id, stratum, label, labeler."""
    by_id = {}
    d = json.load(open(ARP_FILE))
    for l in d["labels"]:
        r = dict(id=int(l["id"]), stratum=l["stratum"],
                 label=_norm_label(l["label"]), labeler="ARP")
        by_id.setdefault(r["id"], {})["ARP"] = r
    with open(SVM_FILE) as f:
        for row in csv.DictReader(f):
            r = dict(id=int(row["id"]), stratum=row["stratum"],
                     label=_norm_label(row["label"]), labeler="SVM")
            by_id.setdefault(r["id"], {})["SVM"] = r
    return by_id


def load_adjudicated(adjudicate="ARP"):
    """One reference label per UNIQUE point.

    adjudicate in {'ARP','SVM'}: on a double-labelled point whose labels DIFFER,
    keep the preferred labeller's label; singly-labelled points keep their only
    label regardless of preference. Returns a list of dicts:
        {id, stratum, label, labeler}   (stratum is the map stratum, identical
        across labellers for a given id).
    """
    if adjudicate not in _PREFER_ORDER:
        raise ValueError(f"adjudicate must be 'ARP' or 'SVM', got {adjudicate!r}")
    by_id = _load_raw()
    out = []
    for i in sorted(by_id):
        entries = by_id[i]
        chosen = None
        for lab in _PREFER_ORDER[adjudicate]:
            if lab in entries:
                chosen = entries[lab]
                break
        out.append(dict(chosen))
    return out


def qa_agreement():
    """Summary of the deliberate double-labelling QA subset.
    Returns dict with n_duplicated, n_agree, n_disagree and the disagreeing ids
    with each labeller's label."""
    by_id = _load_raw()
    dup = {i: e for i, e in by_id.items() if len(e) > 1}
    agree, disagree = 0, []
    for i, e in sorted(dup.items()):
        la, ls = e["ARP"]["label"], e["SVM"]["label"]
        if la == ls:
            agree += 1
        else:
            disagree.append({"id": i, "stratum": e["ARP"]["stratum"],
                             "ARP": la, "SVM": ls})
    return {"n_duplicated": len(dup), "n_agree": agree,
            "n_disagree": len(disagree), "disagreements": disagree}


def adjudicated_ids(adjudicate="ARP"):
    """{id -> adjudicated raw label} for callers that key on point id
    (e.g. the _sampled_probs / EFG-tagged scripts)."""
    return {r["id"]: r["label"] for r in load_adjudicated(adjudicate)}


def load_sampled_probs(adjudicate="ARP"):
    """Deduplicated per-point rows from results/_sampled_probs.json with the
    adjudicated reference label. The two labeller rows for a QA-duplicated point
    carry the SAME probability vector (same pixel); only the label differs, so we
    keep one row per unique id and attach the adjudicated label.
    Returns list of {id, stratum, label, p} with p a length-3 list, dropping rows
    whose p is null."""
    pts = json.load(open(os.path.join(RESULTS, "_sampled_probs.json")))
    adj = adjudicated_ids(adjudicate)          # id -> adjudicated raw label
    seen, out = set(), []
    for r in pts:
        i = r["id"]
        if i in seen or r.get("p") is None:
            continue
        if i not in adj:
            continue
        seen.add(i)
        out.append(dict(id=i, stratum=r["stratum"], label=adj[i], p=r["p"]))
    return out


def load_sampled_probs_efg(adjudicate="ARP"):
    """Deduplicated per-point rows joined to their EFG/strat9 tag.
    Returns list of {id, efg_id, strat9, map_cls, label, p} using the adjudicated
    reference label. Points without an EFG tag or with null p are dropped."""
    tag = {r["id"]: r for r in
           json.load(open(os.path.join(RESULTS, "existing_tagged_efg.json")))["existing"]}
    cls_name = {0: "intact", 1: "moderate", 2: "severe"}
    out = []
    for r in load_sampled_probs(adjudicate):
        t = tag.get(r["id"])
        if t is None:
            continue
        out.append(dict(id=r["id"], efg_id=t["efg_id"], strat9=t["strat9"],
                        map_cls=cls_name[t["strat9"] % 10], label=r["label"], p=r["p"]))
    return out


# ------------------------------------------------ population calibration weights
def pop_calibration_weights(P, efg=None, areas_json="stratum_areas_efg.json"):
    """Per-point weights so the population probability sample matches the KNOWN
    EFG x mapped-class area weights (review Finding 3).

    The `_pop_prob_sample*.npy` files are a raw pixel sample whose EFG x hard-class
    shares drift from the true nine-cell area weights. Treating them as uniformly
    representative biases every PPI unlabelled mean. Here we reweight so each
    EFG x mapped-class cell carries exactly its area share.

    P    : (N,3) predicted probabilities (columns intact/moderate/severe).
    efg  : (N,) EFG id per point (1/2/3). If None (the 3-class, non-EFG setting),
           calibration is done on the 3 hard classes against the 3-class marginal
           area weights derived by summing the nine cells within each class.
    Returns w of length N, normalised to mean 1.

    The mapped class of each point is argmax(P) (the map's hard label), matching
    how the strata were defined.
    """
    P = np.asarray(P, dtype=np.float64)
    hard = np.argmax(P, axis=1)                       # 0/1/2 mapped class
    areas = json.load(open(os.path.join(RESULTS, areas_json)))["area_m2"]
    areas = {int(k): float(v) for k, v in areas.items()}
    total = sum(areas.values())

    if efg is not None:
        efg = np.asarray(efg).astype(int)
        key = efg * 10 + hard                         # strat9 cell per point
        target = {s: areas[s] / total for s in areas}
    else:
        # 3-class marginal: sum nine cells within each mapped class
        cls_area = {c: sum(areas[s] for s in areas if s % 10 == c) for c in (0, 1, 2)}
        key = hard
        target = {c: cls_area[c] / total for c in (0, 1, 2)}

    # empirical share per cell
    keys, inv = np.unique(key, return_inverse=True)
    share = np.bincount(inv) / len(key)
    share_map = {k: share[i] for i, k in enumerate(keys.tolist())}

    w = np.array([(target[k] / share_map[k]) if share_map.get(k) else 0.0
                  for k in key], dtype=np.float64)
    m = w.mean()
    return w / m if m > 0 else w


if __name__ == "__main__":
    for adj in ("ARP", "SVM"):
        rows = load_adjudicated(adj)
        print(f"adjudicate={adj}: {len(rows)} unique points")
    print("QA agreement:", json.dumps(qa_agreement(), indent=2))
