"""
22_area_estimation_added_labeller.py
====================================
Versioned update of the design-based thicket-condition area estimates after the
Alastair Potts (AP) labelling submission.

The previous results are read, never overwritten.  AP rows are handled as:
  * IDs already labelled by ARP or SVM: interpreter-QA duplicates only.
  * Previously unseen IDs with label "unsure": reported, but excluded.
  * Previously unseen IDs with a determinate label: added to the estimator.

The existing ARP- and SVM-preferred adjudication sensitivity arms are retained.
Outputs:
  results/area_estimation_added_labeller_2026-07-21.json
  results/area_estimation_added_labeller_2026-07-21.html

Run:
  C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/22_area_estimation_added_labeller.py
"""
from __future__ import annotations

import csv
import html
import importlib.util
import json
import os
from collections import Counter
from datetime import datetime, timezone


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "results")
VERSION = "2026-07-21"
AP_FILE = os.path.join(ROOT, "thicket_labels_AlastairPotts_AP_2026-07-21-05-04-45.json")
ARP_FILE = os.path.join(ROOT, "thicket_labels_ARP_ARP_2026-07-17-14-19-13.json")
SVM_FILE = os.path.join(ROOT, "thicket_labels_SVM_SVM_2026-07-15-13-41-42.csv")
OLD_AREA_FILE = os.path.join(RESULTS, "area_estimation.json")
OLD_EFG_FILE = os.path.join(RESULTS, "area_estimation_efg.json")
OUT_JSON = os.path.join(RESULTS, f"area_estimation_added_labeller_{VERSION}.json")
OUT_HTML = os.path.join(RESULTS, f"area_estimation_added_labeller_{VERSION}.html")
Z = 1.959963984540054
STRATA = ["intact", "moderate", "severe"]
SCENARIO_LABELS = {
    "A_nothicket_class": "A — nothicket retained",
    "B_nothicket_to_severe": "B — nothicket counted as severe",
}


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


area_mod = _load_module("area_estimation_v14", "14_area_estimation.py")
efg_mod = _load_module("area_estimation_efg_v17", "17_area_estimation_efg.py")


def norm_label(label):
    return "nothicket" if label == "notthicket" else label


def load_json_labels(path, code):
    doc = json.load(open(path, encoding="utf-8"))
    rows = []
    for r in doc["labels"]:
        rows.append({
            "id": int(r["id"]),
            "stratum": r["stratum"],
            "label": norm_label(r["label"]),
            "labeler": code,
        })
    return rows, doc


def load_svm_labels():
    rows = []
    with open(SVM_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({
                "id": int(r["id"]),
                "stratum": r["stratum"],
                "label": norm_label(r["label"]),
                "labeler": "SVM",
            })
    return rows


def preferred_old_rows(old_by_id, adjudicate):
    order = [adjudicate, "SVM" if adjudicate == "ARP" else "ARP"]
    out = []
    for point_id in sorted(old_by_id):
        entries = old_by_id[point_id]
        chosen = next(entries[k] for k in order if k in entries)
        out.append(dict(chosen))
    return out


def qa_pair(ap_rows, other_rows, other_code):
    ap = {r["id"]: r for r in ap_rows}
    other = {r["id"]: r for r in other_rows}
    ids = sorted(set(ap) & set(other))
    determinate = [i for i in ids if ap[i]["label"] != "unsure"]
    agree = [i for i in determinate if ap[i]["label"] == other[i]["label"]]
    disagreements = [
        {
            "id": i,
            "stratum": ap[i]["stratum"],
            "AP": ap[i]["label"],
            other_code: other[i]["label"],
        }
        for i in determinate
        if ap[i]["label"] != other[i]["label"]
    ]
    return {
        "other_labeller": other_code,
        "n_overlap": len(ids),
        "n_ap_unsure": len(ids) - len(determinate),
        "n_determinate": len(determinate),
        "n_agree": len(agree),
        "agreement_rate": len(agree) / len(determinate) if determinate else None,
        "n_disagree": len(disagreements),
        "disagreements": disagreements,
    }


def count_by(rows, field):
    c = Counter(r[field] for r in rows)
    return {k: c.get(k, 0) for k in sorted(c)}


def compare_scenarios(old_report, updated_report):
    comparison = {}
    for tag, updated in updated_report["scenarios"].items():
        old = old_report["scenarios"][tag]
        classes = updated["ref_classes"]
        class_comparison = {}
        for k in classes:
            old_area = old["olofsson"]["area_ha"][k]
            new_area = updated["olofsson"]["area_ha"][k]
            old_moe = Z * old_area["se"]
            new_moe = Z * new_area["se"]
            delta = new_area["area"] - old_area["area"]
            class_comparison[k] = {
                "old_area_ha": old_area["area"],
                "updated_area_ha": new_area["area"],
                "delta_area_ha": delta,
                "delta_percent_of_old": 100 * delta / old_area["area"] if old_area["area"] else None,
                "old_moe95_ha": old_moe,
                "updated_moe95_ha": new_moe,
                "moe95_change_ha": new_moe - old_moe,
                "moe95_reduction_percent": (
                    100 * (old_moe - new_moe) / old_moe if old_moe else None
                ),
            }
        comparison[tag] = {
            "old_n_used": old["n_used"],
            "updated_n_used": updated["n_used"],
            "old_overall_accuracy": old["olofsson"]["overall_accuracy"]["OA"],
            "updated_overall_accuracy": updated["olofsson"]["overall_accuracy"]["OA"],
            "classes": class_comparison,
        }
    return comparison


def updated_area_report(adjudicate, rows, design, qa_old, qa_ap):
    W = design["W"]
    total = design["area_total_ha"]
    report = {
        "adjudicate": adjudicate,
        "inputs": {
            "labellers": ["ARP", "SVM/Steph", "Alastair Potts"],
            "adjudication": (
                f"retain the old {adjudicate}-preferred sensitivity arm; "
                "AP overlap rows are QA only; determinate AP-only rows are added"
            ),
            "n_unique_points": len(rows),
            "qa_agreement_old": qa_old,
            "qa_agreement_ap": qa_ap,
            "W": W,
            "area_total_ha": total,
            "remap": {
                "transformed": "severe",
                "nothicket": "tested: class (kept) | severe",
                "unsure": "excluded",
            },
        },
        "scenarios": {},
    }
    for mode, tag in [
        ("class", "A_nothicket_class"),
        ("severe", "B_nothicket_to_severe"),
    ]:
        remapped, ref_classes = area_mod.remap(rows, mode)
        report["scenarios"][tag] = {
            "nothicket_mode": mode,
            "ref_classes": ref_classes,
            "n_used": len(remapped),
            "olofsson": area_mod.olofsson(remapped, W, total, ref_classes),
            "ppi_pp": area_mod.ppi_area(remapped, W, total, ref_classes),
        }
    return report


def validate_old_reproduction(adjudicate, rows, design, old_report):
    """Confirm imported estimator functions still reproduce the saved baseline."""
    total = design["area_total_ha"]
    checks = []
    for mode, tag in [
        ("class", "A_nothicket_class"),
        ("severe", "B_nothicket_to_severe"),
    ]:
        remapped, ref_classes = area_mod.remap(rows, mode)
        calc = area_mod.olofsson(remapped, design["W"], total, ref_classes)
        saved = old_report["scenarios"][tag]["olofsson"]
        for k in ref_classes:
            diff = abs(calc["area_ha"][k]["area"] - saved["area_ha"][k]["area"])
            checks.append({
                "scenario": tag,
                "class": k,
                "absolute_difference_ha": diff,
            })
    max_diff = max(x["absolute_difference_ha"] for x in checks)
    if max_diff > 1e-6:
        raise RuntimeError(
            f"{adjudicate} baseline reproduction failed; max difference={max_diff} ha"
        )
    return {"passed": True, "max_absolute_difference_ha": max_diff}


def efg_updates(rows_by_arm):
    areas_m2 = json.load(open(os.path.join(RESULTS, "stratum_areas_efg.json")))["area_m2"]
    areas_ha = {int(k): v / 1e4 for k, v in areas_m2.items()}
    tag_rows = json.load(open(os.path.join(RESULTS, "existing_tagged_efg.json")))["existing"]
    tags = {int(r["id"]): r for r in tag_rows}
    old_efg = json.load(open(OLD_EFG_FILE))["sensitivity_adjudication"]

    out = {}
    for adjudicate, rows in rows_by_arm.items():
        updated = {}
        comparisons = {}
        for mode, scenario in [
            ("class", "A_nothicket_class"),
            ("severe", "B_nothicket_to_severe"),
        ]:
            sc = efg_mod.run_scenario(rows, tags, areas_ha, mode)
            updated[scenario] = sc
            cells = {}
            for key, cell in sc["per_cell"].items():
                old_cell = old_efg[adjudicate]["scenarios"][scenario]["per_cell"][str(key)]
                cells[str(key)] = {
                    "efg": cell["efg"],
                    "map_class": cell["map_class"],
                    "old_n": old_cell["n"],
                    "updated_n": cell["n"],
                    "added_n": cell["n"] - old_cell["n"],
                    "old_low_n": old_cell["low_n"],
                    "updated_low_n": cell["low_n"],
                    "updated_estimable": not cell["non_estimable"],
                }
            comparisons[scenario] = {"cells": cells}
        out[adjudicate] = {
            "updated": updated,
            "comparison_to_old": comparisons,
        }
    return out


def fnum(x, nd=0, sign=False):
    if x is None:
        return "—"
    prefix = "+" if sign and x > 0 else ""
    return f"{prefix}{x:,.{nd}f}"


def pct(x, nd=1, sign=False):
    return f"{fnum(x, nd, sign)}%"


def contribution_bar(value, maximum):
    width = 100 * value / maximum if maximum else 0
    return (
        '<div class="bar"><span style="width:'
        f'{width:.1f}%"></span></div><small>{value}</small>'
    )


def build_html(result):
    contribution = result["new_labeller_contribution"]
    comparisons = result["sensitivity_adjudication"]
    efg = result["efg_updates"]
    max_new_stratum = max(contribution["usable_new_by_stratum"].values())
    primary = comparisons["ARP"]["comparison_to_old"]["B_nothicket_to_severe"]

    css = """
    :root{--ink:#173042;--muted:#667985;--navy:#173e59;--teal:#168c82;
      --gold:#d99b27;--pale:#eef5f6;--line:#d9e2e7;--red:#a43c35}
    *{box-sizing:border-box} body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,
      Segoe UI,sans-serif;max-width:1180px;margin:0 auto;padding:38px 24px 60px;
      color:var(--ink);line-height:1.45;background:#fff}
    header{border-bottom:4px solid var(--teal);padding-bottom:22px;margin-bottom:28px}
    h1{font-size:2.05rem;letter-spacing:-.025em;margin:0 0 8px}
    h2{font-size:1.35rem;margin:38px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}
    h3{font-size:1.05rem;margin:24px 0 8px}.subtitle{color:var(--muted);max-width:850px}
    .badge{display:inline-block;background:var(--navy);color:white;border-radius:999px;
      padding:4px 10px;font-size:.78rem;font-weight:700;margin-right:6px}
    .cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:18px 0}
    .card{background:var(--pale);border:1px solid #d7e7e8;border-radius:10px;padding:14px}
    .card b{font-size:1.55rem;display:block;color:var(--navy)}.card small{color:var(--muted)}
    .callout{border-left:5px solid var(--teal);background:#edf8f6;padding:13px 16px;margin:16px 0}
    .note{border-left:5px solid var(--gold);background:#fff8e8;padding:13px 16px;margin:16px 0}
    table{border-collapse:collapse;width:100%;margin:12px 0 24px;font-size:.88rem}
    th{background:var(--navy);color:white;font-weight:650}th,td{padding:8px 9px;
      border-bottom:1px solid var(--line);text-align:right;vertical-align:top}
    td:first-child,th:first-child{text-align:left}tbody tr:nth-child(even){background:#f8fafb}
    caption{text-align:left;font-weight:750;margin:7px 0;color:var(--navy)}
    .delta-pos{color:#0b7169}.delta-neg{color:var(--red)}.muted{color:var(--muted)}
    .bar{width:130px;height:8px;background:#dce8ea;border-radius:4px;display:inline-block;
      margin-right:8px;vertical-align:middle;overflow:hidden}.bar span{display:block;height:100%;
      background:var(--teal)}.ok{color:#0b7169;font-weight:700}.warn{color:#9b6814;font-weight:700}
    .scenario{margin-top:25px}.foot{margin-top:36px;color:var(--muted);font-size:.8rem}
    @media(max-width:850px){.cards{grid-template-columns:repeat(2,1fr)}
      body{padding:22px 12px}.scroll{overflow-x:auto}table{min-width:760px}}
    @media print{body{max-width:none;padding:16px}.cards{grid-template-columns:repeat(5,1fr)}
      .scenario{break-inside:avoid}.scroll{overflow:visible}}
    """

    parts = [
        "<header>",
        "<span class='badge'>UPDATED</span><span class='badge'>DESIGN-BASED</span>",
        "<h1>Thicket condition area estimation</h1>",
        "<div class='subtitle'>Added-labeller update using the Alastair Potts submission. "
        "Previous estimates are retained below as the comparison baseline; no prior result "
        "files were overwritten.</div>",
        "</header>",
        "<div class='cards'>",
        f"<div class='card'><b>{contribution['raw_submission_rows']}</b><small>AP rows submitted</small></div>",
        f"<div class='card'><b>{contribution['qa_overlap_rows']}</b><small>overlap rows used for QA</small></div>",
        f"<div class='card'><b>{contribution['new_unique_rows']}</b><small>previously unseen IDs</small></div>",
        f"<div class='card'><b>+{contribution['usable_new_rows']}</b><small>usable new points added</small></div>",
        f"<div class='card'><b>{contribution['updated_unique_points']}</b><small>updated analysis n</small></div>",
        "</div>",
        "<div class='callout'><b>Net contribution.</b> The AP submission increases the "
        f"independent usable sample from {contribution['old_unique_points']} to "
        f"{contribution['updated_unique_points']} points "
        f"({pct(contribution['sample_size_increase_percent'])}). "
        f"{contribution['unsure_new_rows']} new-only rows marked <i>unsure</i> are reported "
        "but excluded; overlap rows are not double-counted.</div>",
        "<h2>1. How the new points contribute</h2>",
        "<div class='scroll'><table><caption>Usable independent points added by mapped stratum</caption>",
        "<tr><th>mapped stratum</th><th>old n</th><th>AP added</th><th>updated n</th>"
        "<th>increase</th><th>relative contribution</th></tr>",
    ]
    for h in STRATA:
        old_n = contribution["old_by_stratum"][h]
        added = contribution["usable_new_by_stratum"][h]
        updated = old_n + added
        parts.append(
            f"<tr><td>{h}</td><td>{old_n}</td><td>+{added}</td><td>{updated}</td>"
            f"<td>{pct(100*added/old_n)}</td>"
            f"<td>{contribution_bar(added, max_new_stratum)}</td></tr>"
        )
    parts.extend(["</table></div>",
        "<div class='scroll'><table><caption>New-only AP labels entering the estimator</caption>",
        "<tr><th>reference label as submitted</th><th>n</th><th>treatment</th></tr>"])
    label_order = ["intact", "moderate", "severe", "transformed", "nothicket", "unsure"]
    for k in label_order:
        n = contribution["new_unique_by_label"].get(k, 0)
        treatment = (
            "excluded" if k == "unsure"
            else "remapped to severe" if k == "transformed"
            else "scenario-dependent" if k == "nothicket"
            else "included"
        )
        parts.append(f"<tr><td>{k}</td><td>{n}</td><td>{treatment}</td></tr>")
    parts.append("</table></div>")

    parts.extend([
        "<h2>2. Updated area estimates and AP-induced change</h2>",
        "<p class='muted'>Olofsson stratified estimates, hectares. “±95%” is the normal "
        "95% margin of error. Area shifts reflect both the added observations and their "
        "mapped-stratum weights; they are not the literal footprint of the sample points.</p>",
    ])
    for adjudicate in ("ARP", "SVM"):
        arm = comparisons[adjudicate]
        for scenario in ("A_nothicket_class", "B_nothicket_to_severe"):
            comp = arm["comparison_to_old"][scenario]
            parts.extend([
                "<div class='scenario'>",
                f"<h3>{SCENARIO_LABELS[scenario]} · old disagreements adjudicated to {adjudicate}</h3>",
                "<div class='scroll'><table>",
                "<tr><th>reference class</th><th>previous area (ha)</th><th>previous ±95%</th>"
                "<th>updated area (ha)</th><th>updated ±95%</th><th>area change (ha)</th>"
                "<th>area change</th><th>MoE reduction</th></tr>",
            ])
            for k, d in comp["classes"].items():
                delta_cls = "delta-pos" if d["delta_area_ha"] >= 0 else "delta-neg"
                moe_cls = "ok" if d["moe95_reduction_percent"] >= 0 else "warn"
                parts.append(
                    f"<tr><td>{k}</td><td>{fnum(d['old_area_ha'])}</td>"
                    f"<td>{fnum(d['old_moe95_ha'])}</td>"
                    f"<td>{fnum(d['updated_area_ha'])}</td>"
                    f"<td>{fnum(d['updated_moe95_ha'])}</td>"
                    f"<td class='{delta_cls}'>{fnum(d['delta_area_ha'], sign=True)}</td>"
                    f"<td class='{delta_cls}'>{pct(d['delta_percent_of_old'], sign=True)}</td>"
                    f"<td class='{moe_cls}'>{pct(d['moe95_reduction_percent'], sign=True)}</td></tr>"
                )
            parts.extend([
                "</table></div>",
                f"<small>n used: {comp['old_n_used']} → {comp['updated_n_used']}; "
                f"overall accuracy: {comp['old_overall_accuracy']:.3f} → "
                f"{comp['updated_overall_accuracy']:.3f}</small>",
                "</div>",
            ])

    parts.extend([
        "<div class='note'><b>Primary sensitivity reading.</b> Under scenario B and the "
        "ARP-preferred arm, the updated class-area changes are "
        + ", ".join(
            f"{k} {fnum(d['delta_area_ha'], sign=True)} ha"
            for k, d in primary["classes"].items()
        )
        + ". Both adjudication arms and both nothicket treatments remain visible because "
        "those choices are part of the uncertainty analysis.</div>",
        "<h2>3. EFG × mapped-class coverage added</h2>",
        "<p class='muted'>The AP points are EFG-tagged, so they also strengthen the nine "
        "post-strata used for EFG-specific area estimation. Counts below are identical "
        "between adjudication arms.</p>",
        "<div class='scroll'><table><tr><th>EFG</th><th>mapped class</th><th>old n</th>"
        "<th>AP added</th><th>updated n</th><th>updated status</th></tr>",
    ])
    cells = efg["ARP"]["comparison_to_old"]["A_nothicket_class"]["cells"]
    for key in sorted(cells, key=int):
        d = cells[key]
        status = "estimable" if d["updated_estimable"] else "non-estimable"
        if d["updated_low_n"]:
            status += ", low n"
        status_class = "ok" if d["updated_estimable"] and not d["updated_low_n"] else "warn"
        parts.append(
            f"<tr><td>{d['efg']}</td><td>{d['map_class']}</td><td>{d['old_n']}</td>"
            f"<td>+{d['added_n']}</td><td>{d['updated_n']}</td>"
            f"<td class='{status_class}'>{status}</td></tr>"
        )
    parts.extend([
        "</table></div>",
        "<h2>4. Interpreter QA for the AP overlap points</h2>",
        "<div class='scroll'><table><tr><th>comparison</th><th>overlap</th><th>AP unsure</th>"
        "<th>determinate</th><th>agree</th><th>agreement</th><th>disagree</th></tr>",
    ])
    for q in result["qa"]["ap_pairwise"]:
        parts.append(
            f"<tr><td>AP vs {q['other_labeller']}</td><td>{q['n_overlap']}</td>"
            f"<td>{q['n_ap_unsure']}</td><td>{q['n_determinate']}</td>"
            f"<td>{q['n_agree']}</td><td>{pct(100*q['agreement_rate'])}</td>"
            f"<td>{q['n_disagree']}</td></tr>"
        )
    parts.extend([
        "</table></div>",
        "<div class='note'><b>Scope.</b> This update recomputes the design-based class-area "
        "estimates, hard-map PPI comparison, and EFG post-stratification. Continuous-"
        "probability PPI and degradation-index analyses are not carried forward because the "
        "new AP-only points do not yet have cached probability vectors in "
        "<code>_sampled_probs.json</code>.</div>",
        "<div class='foot'>Generated by analysis/22_area_estimation_added_labeller.py · "
        f"version {VERSION} · total mapped domain "
        f"{fnum(result['area_total_ha'])} ha · baseline reproduction check passed.</div>",
    ])

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Updated thicket area estimation</title>"
        f"<style>{css}</style></head><body>{''.join(parts)}</body></html>"
    )


def main():
    arp_rows, _ = load_json_labels(ARP_FILE, "ARP")
    svm_rows = load_svm_labels()
    ap_rows, ap_doc = load_json_labels(AP_FILE, "AP")

    old_by_id = {}
    for code, rows in [("ARP", arp_rows), ("SVM", svm_rows)]:
        for r in rows:
            old_by_id.setdefault(r["id"], {})[code] = r
    old_ids = set(old_by_id)
    ap_new = [r for r in ap_rows if r["id"] not in old_ids]
    ap_new_usable = [r for r in ap_new if r["label"] != "unsure"]
    ap_overlap = [r for r in ap_rows if r["id"] in old_ids]

    qa_old = area_mod.qa_agreement()
    qa_ap = [qa_pair(ap_rows, arp_rows, "ARP"), qa_pair(ap_rows, svm_rows, "SVM")]
    design = json.load(open(os.path.join(RESULTS, "sample_design.json")))
    old_area = json.load(open(OLD_AREA_FILE))["sensitivity_adjudication"]

    rows_by_arm = {}
    reports = {}
    for adjudicate in ("ARP", "SVM"):
        old_rows = preferred_old_rows(old_by_id, adjudicate)
        validation = validate_old_reproduction(
            adjudicate, old_rows, design, old_area[adjudicate]
        )
        updated_rows = old_rows + [dict(r) for r in ap_new_usable]
        rows_by_arm[adjudicate] = updated_rows
        updated_report = updated_area_report(
            adjudicate, updated_rows, design, qa_old, qa_ap
        )
        reports[adjudicate] = {
            "baseline_reproduction": validation,
            "updated": updated_report,
            "comparison_to_old": compare_scenarios(
                old_area[adjudicate], updated_report
            ),
        }

    old_strata = Counter(
        preferred_old_rows(old_by_id, "ARP")[i]["stratum"]
        for i in range(len(old_by_id))
    )
    new_strata = Counter(r["stratum"] for r in ap_new_usable)
    contribution = {
        "raw_submission_rows": len(ap_rows),
        "qa_overlap_rows": len(ap_overlap),
        "new_unique_rows": len(ap_new),
        "unsure_new_rows": sum(r["label"] == "unsure" for r in ap_new),
        "usable_new_rows": len(ap_new_usable),
        "old_unique_points": len(old_ids),
        "updated_unique_points": len(old_ids) + len(ap_new_usable),
        "sample_size_increase_percent": 100 * len(ap_new_usable) / len(old_ids),
        "old_by_stratum": {h: old_strata[h] for h in STRATA},
        "usable_new_by_stratum": {h: new_strata[h] for h in STRATA},
        "new_unique_by_label": count_by(ap_new, "label"),
        "usable_new_by_label": count_by(ap_new_usable, "label"),
        "treatment": {
            "overlaps": "QA only; excluded from estimator to avoid double-counting",
            "new_unsure": "excluded",
            "new_determinate": "added",
        },
    }

    result = {
        "artifact": "updated design-based thicket-condition area estimation",
        "version": VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "area_total_ha": design["area_total_ha"],
        "source_submission": {
            "file": os.path.basename(AP_FILE),
            "labeler": ap_doc.get("labeler"),
            "assignment": ap_doc.get("assignment"),
            "exported": ap_doc.get("exported"),
            "completion": ap_doc.get("completion"),
            "checksum": ap_doc.get("checksum"),
        },
        "preserved_baseline": {
            "area_file": os.path.basename(OLD_AREA_FILE),
            "efg_file": os.path.basename(OLD_EFG_FILE),
            "report_file": "area_estimation_report.html",
            "files_overwritten": False,
        },
        "new_labeller_contribution": contribution,
        "qa": {
            "old_arp_svm": qa_old,
            "ap_pairwise": qa_ap,
        },
        "sensitivity_adjudication": reports,
        "efg_updates": efg_updates(rows_by_arm),
        "method_notes": [
            "Olofsson et al. stratified estimator retained as the primary estimator.",
            "ARP/SVM disagreement adjudication remains a two-arm sensitivity.",
            "AP overlap rows are QA duplicates and do not increase independent n.",
            "AP-only unsure rows are excluded.",
            "transformed is remapped to severe; nothicket is reported under two scenarios.",
        ],
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, allow_nan=False)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(result))

    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_HTML}")
    print(
        f"AP contribution: {len(ap_rows)} submitted; {len(ap_overlap)} QA overlap; "
        f"{len(ap_new_usable)} usable new points; updated n={len(old_ids)+len(ap_new_usable)}"
    )
    for adjudicate in ("ARP", "SVM"):
        comp = reports[adjudicate]["comparison_to_old"]["B_nothicket_to_severe"]
        print(f"\n{adjudicate} / scenario B")
        for k, d in comp["classes"].items():
            print(
                f"  {k:9s}: {d['updated_area_ha']:,.0f} ha "
                f"({d['delta_area_ha']:+,.0f}); "
                f"MoE {d['updated_moe95_ha']:,.0f} ha "
                f"({d['moe95_reduction_percent']:+.1f}% reduction)"
            )


if __name__ == "__main__":
    main()
