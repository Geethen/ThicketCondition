"""
23_area_estimation_final_labeller.py
=====================================
Final versioned update of the design-based thicket-condition area estimates
after the Michael Powell (MP) submission completed the four-labeller campaign.

The 2026-07-21 AP update is read as the comparison baseline and is never
overwritten. MP rows are handled as follows:
  * IDs already carrying a determinate ARP, SVM, or AP label: QA duplicates.
  * Four IDs that AP marked unsure: MP's determinate labels enter the estimator.
  * Previously unseen IDs: MP's determinate labels enter the estimator.

The existing ARP- and SVM-preferred adjudication sensitivity arms are retained.
Outputs:
  results/area_estimation_final_2026-07-31.json
  results/area_estimation_final_2026-07-31.html

Run:
  C:/Users/geethen.singh/.pixi/envs/geo/python.exe analysis/23_area_estimation_final_labeller.py
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, "results")
VERSION = "2026-07-31"
MP_FILE = os.path.join(ROOT, "thicket_labels_MichaelPowell_MP_2026-07-31-10-35-52.json")
AP_FILE = os.path.join(ROOT, "thicket_labels_AlastairPotts_AP_2026-07-21-05-04-45.json")
ARP_FILE = os.path.join(ROOT, "thicket_labels_ARP_ARP_2026-07-17-14-19-13.json")
SVM_FILE = os.path.join(ROOT, "thicket_labels_SVM_SVM_2026-07-15-13-41-42.csv")
MANIFEST_FILE = os.path.join(ROOT, "inspector", "assignment_manifest.json")
PREVIOUS_FILE = os.path.join(RESULTS, "area_estimation_added_labeller_2026-07-21.json")
OUT_JSON = os.path.join(RESULTS, f"area_estimation_final_{VERSION}.json")
OUT_HTML = os.path.join(RESULTS, f"area_estimation_final_{VERSION}.html")
Z = 1.959963984540054
STRATA = ["intact", "moderate", "severe"]
VALID_LABELS = set(STRATA + ["transformed", "nothicket", "unsure"])
SCENARIO_LABELS = {
    "A_nothicket_class": "A — nothicket retained",
    "B_nothicket_to_severe": "B — nothicket counted as severe",
}


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


previous_mod = _load_module("area_estimation_added_labeller_v22", "22_area_estimation_added_labeller.py")
area_mod = previous_mod.area_mod
efg_mod = previous_mod.efg_mod


def norm_label(label):
    return "nothicket" if label == "notthicket" else label


def load_json_labels(path, code):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    rows = [
        {
            "id": int(r["id"]),
            "stratum": r["stratum"],
            "label": norm_label(r["label"]),
            "labeler": code,
        }
        for r in doc["labels"]
    ]
    return rows, doc


def load_svm_labels():
    rows = []
    with open(SVM_FILE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(
                {
                    "id": int(r["id"]),
                    "stratum": r["stratum"],
                    "label": norm_label(r["label"]),
                    "labeler": "SVM",
                }
            )
    return rows


def preferred_base_rows(base_by_id, adjudicate):
    order = [adjudicate, "SVM" if adjudicate == "ARP" else "ARP"]
    out = []
    for point_id in sorted(base_by_id):
        entries = base_by_id[point_id]
        chosen = next(entries[k] for k in order if k in entries)
        out.append(dict(chosen))
    return out


def fnv1a_32_js(text):
    """FNV-1a over JavaScript UTF-16 code units, matching inspector/app.js."""
    value = 2166136261
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        code_unit = raw[i] | (raw[i + 1] << 8)
        value ^= code_unit
        value = (value * 16777619) & 0xFFFFFFFF
    return f"{value:08x}"


def validate_submission(doc, rows):
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        manifest = json.load(f)
    assignment = manifest["labelers"]["MP"]
    ids = [r["id"] for r in rows]
    canonical = json.dumps(doc["labels"], ensure_ascii=False, separators=(",", ":"))
    calculated_checksum = fnv1a_32_js(canonical)
    checks = {
        "dataset_matches_manifest": doc.get("dataset") == manifest.get("dataset"),
        "campaign_matches_manifest": doc.get("assignment", {}).get("campaign") == manifest.get("campaign"),
        "assignment_code_matches": doc.get("assignment", {}).get("code") == "MP",
        "assignment_id_matches": doc.get("assignment", {}).get("id") == assignment["assignment_id"],
        "row_count_matches_assignment": len(rows) == len(assignment["point_ids"]),
        "id_set_matches_assignment": set(ids) == set(assignment["point_ids"]),
        "ids_are_unique": len(ids) == len(set(ids)),
        "completion_is_final": bool(doc.get("completion", {}).get("complete")),
        "labels_are_valid": all(r["label"] in VALID_LABELS for r in rows),
        "strata_are_valid": all(r["stratum"] in STRATA for r in rows),
        "checksum_matches": calculated_checksum == doc.get("checksum", {}).get("value"),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise RuntimeError("MP submission validation failed: " + ", ".join(failed))
    return {
        "passed": True,
        "checks": checks,
        "calculated_checksum": calculated_checksum,
    }


def qa_pair(new_rows, new_code, other_rows, other_code):
    new = {r["id"]: r for r in new_rows}
    other = {r["id"]: r for r in other_rows}
    ids = sorted(set(new) & set(other))
    determinate = [
        i
        for i in ids
        if new[i]["label"] != "unsure" and other[i]["label"] != "unsure"
    ]
    agree = [i for i in determinate if new[i]["label"] == other[i]["label"]]
    disagreements = [
        {
            "id": i,
            "stratum": new[i]["stratum"],
            new_code: new[i]["label"],
            other_code: other[i]["label"],
        }
        for i in determinate
        if new[i]["label"] != other[i]["label"]
    ]
    return {
        "new_labeller": new_code,
        "other_labeller": other_code,
        "n_overlap": len(ids),
        "n_new_unsure": sum(new[i]["label"] == "unsure" for i in ids),
        "n_other_unsure": sum(other[i]["label"] == "unsure" for i in ids),
        "n_determinate_pairs": len(determinate),
        "n_agree": len(agree),
        "agreement_rate": len(agree) / len(determinate) if determinate else None,
        "n_disagree": len(disagreements),
        "disagreements": disagreements,
    }


def count_by(rows, field):
    counts = Counter(r[field] for r in rows)
    return {key: counts[key] for key in sorted(counts)}


def final_area_report(adjudicate, rows, design, qa_pairs):
    report = {
        "adjudicate": adjudicate,
        "inputs": {
            "labellers": ["ARP", "SVM/Steph", "Alastair Potts", "Michael Powell"],
            "adjudication": (
                f"retain the old {adjudicate}-preferred sensitivity arm; "
                "determinate duplicate labels are QA only; MP resolves four AP-unsure "
                "points and supplies the remaining unseen sample IDs"
            ),
            "n_unique_points": len(rows),
            "qa_agreement_mp": qa_pairs,
            "W": design["W"],
            "area_total_ha": design["area_total_ha"],
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
            "olofsson": area_mod.olofsson(remapped, design["W"], design["area_total_ha"], ref_classes),
            "ppi_pp": area_mod.ppi_area(remapped, design["W"], design["area_total_ha"], ref_classes),
        }
    return report


def validate_previous_reproduction(adjudicate, rows, design, previous_report):
    checks = []
    for mode, tag in [
        ("class", "A_nothicket_class"),
        ("severe", "B_nothicket_to_severe"),
    ]:
        remapped, ref_classes = area_mod.remap(rows, mode)
        calculated = area_mod.olofsson(
            remapped, design["W"], design["area_total_ha"], ref_classes
        )
        saved = previous_report["scenarios"][tag]["olofsson"]
        for ref_class in ref_classes:
            checks.append(
                {
                    "scenario": tag,
                    "class": ref_class,
                    "absolute_difference_ha": abs(
                        calculated["area_ha"][ref_class]["area"]
                        - saved["area_ha"][ref_class]["area"]
                    ),
                }
            )
    maximum = max(item["absolute_difference_ha"] for item in checks)
    if maximum > 1e-6:
        raise RuntimeError(
            f"{adjudicate} previous-result reproduction failed; maximum difference={maximum} ha"
        )
    return {"passed": True, "max_absolute_difference_ha": maximum}


def compare_scenarios(previous_report, final_report):
    comparison = {}
    for tag, final in final_report["scenarios"].items():
        previous = previous_report["scenarios"][tag]
        classes = final["ref_classes"]
        class_comparison = {}
        for ref_class in classes:
            old_area = previous["olofsson"]["area_ha"][ref_class]
            new_area = final["olofsson"]["area_ha"][ref_class]
            old_moe = Z * old_area["se"]
            new_moe = Z * new_area["se"]
            delta = new_area["area"] - old_area["area"]
            class_comparison[ref_class] = {
                "previous_area_ha": old_area["area"],
                "final_area_ha": new_area["area"],
                "delta_area_ha": delta,
                "delta_percent_of_previous": 100 * delta / old_area["area"] if old_area["area"] else None,
                "previous_moe95_ha": old_moe,
                "final_moe95_ha": new_moe,
                "moe95_change_ha": new_moe - old_moe,
                "moe95_reduction_percent": 100 * (old_moe - new_moe) / old_moe if old_moe else None,
            }
        comparison[tag] = {
            "previous_n_used": previous["n_used"],
            "final_n_used": final["n_used"],
            "previous_overall_accuracy": previous["olofsson"]["overall_accuracy"]["OA"],
            "final_overall_accuracy": final["olofsson"]["overall_accuracy"]["OA"],
            "classes": class_comparison,
        }
    return comparison


def efg_updates(rows_by_arm, previous_doc):
    with open(os.path.join(RESULTS, "stratum_areas_efg.json"), encoding="utf-8") as f:
        areas_m2 = json.load(f)["area_m2"]
    areas_ha = {int(key): value / 1e4 for key, value in areas_m2.items()}
    with open(os.path.join(RESULTS, "existing_tagged_efg.json"), encoding="utf-8") as f:
        tag_rows = json.load(f)["existing"]
    tags = {int(row["id"]): row for row in tag_rows}

    out = {}
    for adjudicate, rows in rows_by_arm.items():
        final = {}
        comparisons = {}
        for mode, scenario in [
            ("class", "A_nothicket_class"),
            ("severe", "B_nothicket_to_severe"),
        ]:
            sc = efg_mod.run_scenario(rows, tags, areas_ha, mode)
            final[scenario] = sc
            cells = {}
            previous_cells = previous_doc["efg_updates"][adjudicate]["updated"][scenario]["per_cell"]
            for key, cell in sc["per_cell"].items():
                previous_cell = previous_cells[str(key)]
                cells[str(key)] = {
                    "efg": cell["efg"],
                    "map_class": cell["map_class"],
                    "previous_n": previous_cell["n"],
                    "final_n": cell["n"],
                    "added_n": cell["n"] - previous_cell["n"],
                    "previous_low_n": previous_cell["low_n"],
                    "final_low_n": cell["low_n"],
                    "final_estimable": not cell["non_estimable"],
                }
            comparisons[scenario] = {"cells": cells}
        out[adjudicate] = {
            "final": final,
            "comparison_to_previous": comparisons,
        }
    return out


def efg_area_estimates(efg_result, adjudicate="ARP", scenario="B_nothicket_to_severe"):
    """Return compact final EFG x mapped-cell and EFG x reference-area tables."""
    final = efg_result[adjudicate]["final"][scenario]
    mapped_cells = {}
    reference_classes = final["ref_classes"]
    for key, cell in final["per_cell"].items():
        ua_se = cell["users_acc_se"]
        reference_area = {}
        for ref_class in reference_classes:
            n = cell["n"]
            count = cell["ref_counts"].get(ref_class, 0)
            proportion = count / n if n else 0.0
            se_ha = (
                cell["area_mapped_ha"] * math.sqrt(proportion * (1 - proportion) / (n - 1))
                if n > 1
                else None
            )
            reference_area[ref_class] = {
                "area_ha": cell["area_mapped_ha"] * proportion,
                "se_ha": se_ha,
                "moe95_ha": Z * se_ha if se_ha is not None else None,
            }
        mapped_cells[str(key)] = {
            "efg": cell["efg"],
            "efg_id": cell["efg_id"],
            "map_class": cell["map_class"],
            "area_mapped_ha": cell["area_mapped_ha"],
            "n": cell["n"],
            "users_accuracy": cell["users_acc"],
            "users_accuracy_se": ua_se,
            "users_accuracy_moe95": Z * ua_se,
            "reference_area_by_class": reference_area,
            "low_n": cell["low_n"],
            "estimable": not cell["non_estimable"],
        }
    reference_by_efg = {}
    for efg_name, efg in final["per_efg"].items():
        reference_by_efg[efg_name] = {
            "area_total_ha": efg["area_total_ha"],
            "n_labelled": efg["n_labelled"],
            "estimable": efg["estimable"],
            "non_estimable_cells": efg["non_estimable_cells"],
            "composition": {
                ref_class: {
                    "area_ha": value["area"],
                    "se_ha": value["se"],
                    "moe95_ha": Z * value["se"],
                    "ci95_ha": value["ci95"],
                }
                for ref_class, value in efg["composition"].items()
            },
        }
    return {
        "adjudicate": adjudicate,
        "scenario": scenario,
        "ref_classes": reference_classes,
        "mapped_cells": mapped_cells,
        "reference_area_by_efg": reference_by_efg,
    }


def map_accuracy_summary(reports):
    """Extract Olofsson OA, UA, and PA with their design-based uncertainty."""
    out = {}
    for adjudicate, arm in reports.items():
        out[adjudicate] = {}
        for scenario, final_scenario in arm["final"]["scenarios"].items():
            olofsson = final_scenario["olofsson"]
            overall = olofsson["overall_accuracy"]
            out[adjudicate][scenario] = {
                "n_used": final_scenario["n_used"],
                "overall_accuracy": {
                    "OA": overall["OA"],
                    "se": overall["se"],
                    "moe95": Z * overall["se"],
                    "ci95": overall["ci95"],
                },
                "users_accuracy": {
                    cls: {
                        "U": value["U"],
                        "se": value["se"],
                        "moe95": Z * value["se"],
                        "ci95": [value["U"] - Z * value["se"], value["U"] + Z * value["se"]],
                    }
                    for cls, value in olofsson["users_accuracy"].items()
                },
                "producers_accuracy": {
                    cls: {
                        "P": value["P"],
                        "se": value["se"],
                        "moe95": Z * value["se"],
                        "ci95": [value["P"] - Z * value["se"], value["P"] + Z * value["se"]],
                    }
                    for cls, value in olofsson["producers_accuracy"].items()
                },
            }
    return out


def label_disagreement_summary(rows_by_code):
    """Summarize every label and every deliberate pairwise overlap."""
    codes = list(rows_by_code)
    pairwise = []
    disagreement_pairs = Counter()
    for i, code_a in enumerate(codes):
        for code_b in codes[i + 1 :]:
            qa = qa_pair(rows_by_code[code_a], code_a, rows_by_code[code_b], code_b)
            pairwise.append(qa)
            for disagreement in qa["disagreements"]:
                labels = sorted([disagreement[code_a], disagreement[code_b]])
                disagreement_pairs[" vs ".join(labels)] += 1
    total_overlap = sum(item["n_overlap"] for item in pairwise)
    total_determinate = sum(item["n_determinate_pairs"] for item in pairwise)
    total_agree = sum(item["n_agree"] for item in pairwise)
    return {
        "labeller_order": codes,
        "submission_rows": sum(len(rows) for rows in rows_by_code.values()),
        "unique_labelled_ids": len(set(row["id"] for rows in rows_by_code.values() for row in rows)),
        "label_counts": {code: count_by(rows, "label") for code, rows in rows_by_code.items()},
        "pairwise": pairwise,
        "aggregate": {
            "n_pairwise_overlap_rows": total_overlap,
            "n_determinate_pairs": total_determinate,
            "n_agree": total_agree,
            "n_disagree": total_determinate - total_agree,
            "agreement_rate": total_agree / total_determinate if total_determinate else None,
            "disagreement_by_label_pair": dict(sorted(disagreement_pairs.items())),
        },
    }


def sampling_se_check(design, rows_by_arm):
    """Audit the specified design SE against the actual draw and usable labels."""
    W = design["W"]
    S = design["S"]
    drawn = design["drawn_counts"]
    draw_se = math.sqrt(sum((W[cls] * S[cls]) ** 2 / drawn[cls] for cls in STRATA))
    usable_counts = Counter(row["stratum"] for row in rows_by_arm["ARP"])
    usable = {cls: usable_counts[cls] for cls in STRATA}
    usable_se = math.sqrt(sum((W[cls] * S[cls]) ** 2 / usable[cls] for cls in STRATA))
    target = design["target_se"]
    return {
        "specified_target_se": target,
        "allocation_formula": "sqrt(sum_h W_h^2 S_h^2 / n_h)",
        "expected_S_from_oof_users_accuracy": design["S"],
        "n_tot_from_target_formula": design["n_tot"],
        "allocated_counts": design["alloc"],
        "drawn_counts": drawn,
        "draw_nominal_design_se": draw_se,
        "draw_se_minus_target": draw_se - target,
        "draw_se_ratio_to_target": draw_se / target,
        "usable_determinate_counts": usable,
        "usable_nominal_design_se_if_expected_S": usable_se,
        "interpretation": (
            "The 0.015 target was the expected SE for overall accuracy at sample design time. "
            "The draw meets it (0.01499). The 0.01485–0.01628 Olofsson OA SEs in the final "
            "report are realized label-dependent SEs, not a failed sampling allocation."
        ),
    }


def efg_reference_budget_options(efg, final_unique_points, sample_draw_points):
    """Optimise new EFG x mapped-cell draws for EFG x reference-area precision.

    Reference condition is observed only after a point is labelled, so it cannot
    be the sampling stratum. New points are therefore drawn from the nine EFG x
    mapped-class cells. The allocation minimises the sum of squared relative
    standard errors for the nine primary EFG x reference-condition area
    estimates, using the final ARP-preferred scenario-B proportions as the
    planning values.
    """
    efg_order = ["Arid", "Valley", "Mesic"]
    map_classes = list(STRATA)
    ref_classes = list(efg["ref_classes"])
    cells = {
        (cell["efg"], cell["map_class"]): cell
        for cell in efg["mapped_cells"].values()
    }
    cell_keys = [(efg_name, map_class) for efg_name in efg_order for map_class in map_classes]
    if set(cells) != set(cell_keys):
        raise RuntimeError("EFG budget allocation requires all nine EFG x mapped-class cells")
    starting_counts = {key: cells[key]["n"] for key in cell_keys}
    estimate_by_target = {
        (efg_name, ref_class): efg["reference_area_by_efg"][efg_name]["composition"][ref_class]["area_ha"]
        for efg_name in efg_order
        for ref_class in ref_classes
    }

    def reference_variances(counts):
        variances = {}
        for efg_name in efg_order:
            for ref_class in ref_classes:
                variance = 0.0
                for map_class in map_classes:
                    cell = cells[(efg_name, map_class)]
                    proportion = (
                        cell["reference_area_by_class"][ref_class]["area_ha"]
                        / cell["area_mapped_ha"]
                    )
                    variance += (
                        cell["area_mapped_ha"] ** 2
                        * proportion
                        * (1 - proportion)
                        / (counts[(efg_name, map_class)] - 1)
                    )
                variances[(efg_name, ref_class)] = variance
        return variances

    def objective(counts):
        variances = reference_variances(counts)
        return sum(
            variance / estimate_by_target[target] ** 2
            for target, variance in variances.items()
        )

    def relative_moe95(counts):
        variances = reference_variances(counts)
        return {
            efg_name: {
                ref_class: Z * math.sqrt(variances[(efg_name, ref_class)])
                / estimate_by_target[(efg_name, ref_class)]
                for ref_class in ref_classes
            }
            for efg_name in efg_order
        }

    current_objective = objective(starting_counts)
    current_relative_moe95 = relative_moe95(starting_counts)
    recommendations = {
        100: "Targeted interim update: reduce the highest-uncertainty Arid and Mesic reference-area estimates.",
        200: "Practical EFG refresh: brings the largest projected relative area margin close to 21%, if current class proportions persist.",
        300: "Preferred EFG option: brings every projected EFG x reference-condition area margin below 19%, if current class proportions persist.",
    }
    options = []
    for budget in (100, 200, 300):
        final_counts = dict(starting_counts)
        for _ in range(budget):
            current_value = objective(final_counts)
            best_cell = max(
                cell_keys,
                key=lambda key: current_value - objective({**final_counts, key: final_counts[key] + 1}),
            )
            final_counts[best_cell] += 1
        added = {
            efg_name: {
                map_class: final_counts[(efg_name, map_class)] - starting_counts[(efg_name, map_class)]
                for map_class in map_classes
            }
            for efg_name in efg_order
        }
        projected_relative_moe95 = relative_moe95(final_counts)
        options.append(
            {
                "budget_usable_points": budget,
                "added_by_efg_mapped_class": added,
                "final_by_efg_mapped_class": {
                    efg_name: {
                        map_class: final_counts[(efg_name, map_class)]
                        for map_class in map_classes
                    }
                    for efg_name in efg_order
                },
                "expected_relative_moe95_by_efg_reference": projected_relative_moe95,
                "largest_expected_relative_moe95": max(
                    value
                    for values in projected_relative_moe95.values()
                    for value in values.values()
                ),
                "relative_variance_reduction_percent": 100 * (1 - objective(final_counts) / current_objective),
                "assignment_capacity_at_current_exclusion_rate": math.ceil(
                    budget * sample_draw_points / final_unique_points
                ),
                "recommendation": recommendations[budget],
            }
        )
    return {
        "scope": "Incremental independent, determinate labels added to the completed 821-point analysis sample.",
        "method": (
            "A-optimal EFG reference-area allocation: new draws in the nine EFG x mapped-class cells "
            "minimise the summed squared relative standard errors of the nine EFG x reference-condition areas."
        ),
        "planning_scenario": "ARP-preferred; nothicket counted as severe",
        "planning_proportions": "Final observed EFG x mapped-cell reference proportions",
        "starting_usable_counts": {
            efg_name: {map_class: starting_counts[(efg_name, map_class)] for map_class in map_classes}
            for efg_name in efg_order
        },
        "current_expected_relative_moe95_by_efg_reference": current_relative_moe95,
        "current_largest_expected_relative_moe95": max(
            value for values in current_relative_moe95.values() for value in values.values()
        ),
        "sample_draw_points": sample_draw_points,
        "current_excluded_points": sample_draw_points - final_unique_points,
        "current_exclusion_rate": (sample_draw_points - final_unique_points) / sample_draw_points,
        "options": options,
    }


def fnum(value, nd=0, sign=False):
    if value is None:
        return "—"
    prefix = "+" if sign and value > 0 else ""
    return f"{prefix}{value:,.{nd}f}"


def pct(value, nd=1, sign=False):
    return f"{fnum(value, nd, sign)}%"


def interactive_summary_html(result):
    """Compact, client-side charts for the comparison-heavy report sections."""
    areas = {}
    for adjudicate, arm in result["sensitivity_adjudication"].items():
        areas[adjudicate] = {}
        for scenario, comparison in arm["comparison_to_previous"].items():
            areas[adjudicate][scenario] = [
                {
                    "label": ref_class,
                    "previous": values["previous_area_ha"],
                    "previousMoe": values["previous_moe95_ha"],
                    "final": values["final_area_ha"],
                    "finalMoe": values["final_moe95_ha"],
                }
                for ref_class, values in comparison["classes"].items()
            ]
    primary_efg = result["efg_area_estimates"]["ARP"]["B_nothicket_to_severe"]
    efg = {}
    for key, cell in primary_efg["mapped_cells"].items():
        efg.setdefault(cell["efg"], []).append(
            {
                "label": cell["map_class"],
                "area": cell["area_mapped_ha"],
                "n": cell["n"],
                "ua": cell["users_accuracy"],
                "lowN": cell["low_n"],
                "parts": {
                    ref_class: values["area_ha"]
                    for ref_class, values in cell["reference_area_by_class"].items()
                },
            }
        )
    accuracy = []
    for adjudicate, scenarios in result["map_accuracy"].items():
        for scenario, values in scenarios.items():
            accuracy.append(
                {
                    "label": f"{adjudicate} · {'A' if scenario.startswith('A_') else 'B'}",
                    "oa": values["overall_accuracy"]["OA"],
                    "moe": values["overall_accuracy"]["moe95"],
                }
            )
    qa = [
        {
            "label": f"{item['new_labeller']} vs {item['other_labeller']}",
            "agreement": item["agreement_rate"],
            "n": item["n_determinate_pairs"],
        }
        for item in result["label_disagreement"]["pairwise"]
    ]
    data = {"areas": areas, "efg": efg, "accuracy": accuracy, "qa": qa}
    template = r"""
<section id="report-interactive" class="interactive-report" aria-label="Interactive estimate summary">
  <h2>Explore the results</h2>
  <div class="chart-grid">
    <section class="chart-panel" aria-labelledby="area-chart-heading">
      <h3 id="area-chart-heading">Class-area comparison</h3>
      <div class="chart-controls">
        <label>Adjudication <select id="area-arm"><option value="ARP">ARP-preferred</option><option value="SVM">SVM-preferred</option></select></label>
        <label>Nothicket treatment <select id="area-scenario"><option value="B_nothicket_to_severe">Count as severe</option><option value="A_nothicket_class">Keep as class</option></select></label>
      </div>
      <div class="chart-legend"><span class="legend previous"></span>Previous <span class="legend final"></span>Final; whiskers show 95% MOE</div>
      <svg id="area-svg" class="chart-svg" role="img" aria-label="Previous and final error-adjusted areas by reference class"></svg>
      <p id="area-detail" class="chart-detail" aria-live="polite"></p>
    </section>
    <section class="chart-panel" aria-labelledby="efg-chart-heading">
      <h3 id="efg-chart-heading">EFG mapped-cell composition</h3>
      <div class="chart-controls"><label>EFG <select id="efg-choice"><option>Arid</option><option>Valley</option><option>Mesic</option></select></label></div>
      <div class="chart-legend"><span class="legend intact"></span>Reference intact <span class="legend moderate"></span>Reference moderate <span class="legend severe"></span>Reference severe</div>
      <svg id="efg-svg" class="chart-svg" role="img" aria-label="Error-adjusted reference condition composition within selected EFG mapped cells"></svg>
      <p id="efg-detail" class="chart-detail" aria-live="polite"></p>
    </section>
    <section class="chart-panel" aria-labelledby="accuracy-chart-heading">
      <h3 id="accuracy-chart-heading">Overall accuracy by sensitivity choice</h3>
      <div class="chart-legend"><span class="legend final"></span>Overall accuracy; whiskers show 95% MOE</div>
      <svg id="accuracy-svg" class="chart-svg" role="img" aria-label="Overall accuracy by adjudication and nothicket treatment"></svg>
    </section>
    <section class="chart-panel" aria-labelledby="qa-chart-heading">
      <h3 id="qa-chart-heading">Labeller overlap agreement</h3>
      <div class="chart-legend"><span class="legend agreement"></span>Agreement across determinate overlap pairs</div>
      <svg id="qa-svg" class="chart-svg" role="img" aria-label="Pairwise labeller agreement rates"></svg>
    </section>
  </div>
</section>
<script>
(() => {
  const root = document.getElementById('report-interactive');
  if (!root) return;
  const data = __REPORT_DATA__;
  const ns = 'http://www.w3.org/2000/svg';
  const colors = {previous:'#aebdc4', final:'#168c82', intact:'#5a8fc5', moderate:'#d99b27', severe:'#a43c35', agreement:'#173e59', grid:'#d9e2e7', text:'#173042', muted:'#667985'};
  const make = (tag, attrs = {}, text = '') => {
    const node = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text) node.textContent = text;
    return node;
  };
  const clear = (svg, width, height) => {
    svg.replaceChildren(); svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  };
  const formatHa = value => `${Math.round(value).toLocaleString()} ha`;
  const formatPct = value => `${(value * 100).toFixed(1)}%`;
  const axis = (svg, max, x0, y0, width, height, unit) => {
    [0, .25, .5, .75, 1].forEach(fraction => {
      const x = x0 + width * fraction;
      svg.append(make('line', {x1:x, y1:y0, x2:x, y2:y0-height, stroke:colors.grid, 'stroke-width':'1'}));
      svg.append(make('text', {x, y:y0 + 18, 'text-anchor':'middle', fill:colors.muted, 'font-size':'12'}, unit === '%' ? `${Math.round(max * fraction)}%` : `${Math.round(max * fraction / 1000)}k`));
    });
  };
  const animateWidths = svg => requestAnimationFrame(() => svg.querySelectorAll('[data-final-width]').forEach(node => node.setAttribute('width', node.dataset.finalWidth)));

  const areaSvg = root.querySelector('#area-svg');
  const areaArm = root.querySelector('#area-arm');
  const areaScenario = root.querySelector('#area-scenario');
  const areaDetail = root.querySelector('#area-detail');
  function renderArea() {
    const rows = data.areas[areaArm.value][areaScenario.value];
    const max = Math.max(...rows.flatMap(row => [row.previous + row.previousMoe, row.final + row.finalMoe])) * 1.06;
    const width = 760, x0 = 155, chartWidth = 555, rowHeight = 57, y0 = 35, height = 65 + rowHeight * rows.length;
    clear(areaSvg, width, height); axis(areaSvg, max, x0, height - 28, chartWidth, height - 64, 'ha');
    rows.forEach((row, index) => {
      const y = y0 + index * rowHeight;
      const prevWidth = chartWidth * row.previous / max, finalWidth = chartWidth * row.final / max;
      areaSvg.append(make('text', {x:8, y:y + 20, fill:colors.text, 'font-size':'13'}, row.label));
      [['previous', row.previous, row.previousMoe, y + 3, colors.previous, prevWidth], ['final', row.final, row.finalMoe, y + 27, colors.final, finalWidth]].forEach(([kind, value, moe, barY, color, barWidth]) => {
        const rect = make('rect', {x:x0, y:barY, width:0, height:16, rx:3, fill:color, 'data-final-width':barWidth, style:'transition:width .32s ease'});
        rect.append(make('title', {}, `${row.label} ${kind}: ${formatHa(value)} ± ${formatHa(moe)}`)); areaSvg.append(rect);
        const end = x0 + barWidth, low = x0 + chartWidth * Math.max(0, value - moe) / max, high = x0 + chartWidth * Math.min(max, value + moe) / max;
        areaSvg.append(make('line', {x1:low, y1:barY + 8, x2:high, y2:barY + 8, stroke:colors.text, 'stroke-width':'1.2'}));
        areaSvg.append(make('line', {x1:low, y1:barY + 4, x2:low, y2:barY + 12, stroke:colors.text, 'stroke-width':'1.2'}));
        areaSvg.append(make('line', {x1:high, y1:barY + 4, x2:high, y2:barY + 12, stroke:colors.text, 'stroke-width':'1.2'}));
        areaSvg.append(make('text', {x:Math.min(end + 6, width - 55), y:barY + 13, fill:colors.text, 'font-size':'11'}, `${Math.round(value / 1000)}k`));
      });
    });
    const arm = areaArm.options[areaArm.selectedIndex].text;
    const scenario = areaScenario.options[areaScenario.selectedIndex].text;
    areaDetail.textContent = `${arm}; nothicket: ${scenario}. Selectors update all area bars and uncertainty whiskers.`;
    animateWidths(areaSvg);
  }

  const efgSvg = root.querySelector('#efg-svg');
  const efgChoice = root.querySelector('#efg-choice');
  const efgDetail = root.querySelector('#efg-detail');
  function renderEfg() {
    const rows = data.efg[efgChoice.value];
    const max = Math.max(...rows.map(row => row.area)) * 1.06;
    const width = 760, x0 = 155, chartWidth = 555, rowHeight = 58, y0 = 32, height = 65 + rowHeight * rows.length;
    clear(efgSvg, width, height); axis(efgSvg, max, x0, height - 28, chartWidth, height - 64, 'ha');
    rows.forEach((row, index) => {
      const y = y0 + index * rowHeight, barY = y + 5;
      efgSvg.append(make('text', {x:8, y:y + 20, fill:colors.text, 'font-size':'13'}, `Mapped ${row.label}`));
      let cursor = x0;
      [['intact', colors.intact], ['moderate', colors.moderate], ['severe', colors.severe]].forEach(([name, color]) => {
        const segment = chartWidth * row.parts[name] / max;
        const rect = make('rect', {x:cursor, y:barY, width:0, height:20, rx:2, fill:color, 'data-final-width':segment, style:'transition:width .32s ease'});
        rect.append(make('title', {}, `${row.label} mapped cell, reference ${name}: ${formatHa(row.parts[name])}`)); efgSvg.append(rect); cursor += segment;
      });
      efgSvg.append(make('text', {x:x0, y:barY + 38, fill:colors.muted, 'font-size':'11'}, `n=${row.n}; UA ${formatPct(row.ua)}${row.lowN ? ' (low n)' : ''}`));
    });
    const total = rows.reduce((sum, row) => sum + row.area, 0);
    efgDetail.textContent = `${efgChoice.value}: ${formatHa(total)} mapped area. Each bar is partitioned into its error-adjusted reference-condition area.`;
    animateWidths(efgSvg);
  }

  const accuracySvg = root.querySelector('#accuracy-svg');
  function renderAccuracy() {
    const rows = data.accuracy, width = 760, x0 = 155, chartWidth = 555, rowHeight = 42, y0 = 24, height = 58 + rowHeight * rows.length;
    clear(accuracySvg, width, height); axis(accuracySvg, 100, x0, height - 27, chartWidth, height - 53, '%');
    rows.forEach((row, index) => {
      const y = y0 + index * rowHeight, barWidth = chartWidth * row.oa;
      accuracySvg.append(make('text', {x:8, y:y + 16, fill:colors.text, 'font-size':'13'}, row.label));
      const rect = make('rect', {x:x0, y:y, width:0, height:18, rx:3, fill:colors.final, 'data-final-width':barWidth, style:'transition:width .32s ease'});
      rect.append(make('title', {}, `${row.label}: OA ${formatPct(row.oa)} ± ${formatPct(row.moe)}`)); accuracySvg.append(rect);
      const low = x0 + chartWidth * Math.max(0, row.oa - row.moe), high = x0 + chartWidth * Math.min(1, row.oa + row.moe);
      accuracySvg.append(make('line', {x1:low, y1:y + 9, x2:high, y2:y + 9, stroke:colors.text, 'stroke-width':'1.2'}));
      accuracySvg.append(make('text', {x:Math.min(x0 + barWidth + 6, width - 48), y:y + 14, fill:colors.text, 'font-size':'11'}, formatPct(row.oa)));
    });
    animateWidths(accuracySvg);
  }

  const qaSvg = root.querySelector('#qa-svg');
  function renderQa() {
    const rows = data.qa, width = 760, x0 = 155, chartWidth = 555, rowHeight = 36, y0 = 22, height = 58 + rowHeight * rows.length;
    clear(qaSvg, width, height); axis(qaSvg, 100, x0, height - 27, chartWidth, height - 53, '%');
    rows.forEach((row, index) => {
      const y = y0 + index * rowHeight, barWidth = chartWidth * row.agreement;
      qaSvg.append(make('text', {x:8, y:y + 15, fill:colors.text, 'font-size':'13'}, row.label));
      const rect = make('rect', {x:x0, y:y, width:0, height:17, rx:3, fill:colors.agreement, 'data-final-width':barWidth, style:'transition:width .32s ease'});
      rect.append(make('title', {}, `${row.label}: ${formatPct(row.agreement)} agreement across n=${row.n} determinate pairs`)); qaSvg.append(rect);
      qaSvg.append(make('text', {x:Math.min(x0 + barWidth + 6, width - 80), y:y + 14, fill:colors.text, 'font-size':'11'}, `${formatPct(row.agreement)} (n=${row.n})`));
    });
    animateWidths(qaSvg);
  }
  areaArm.addEventListener('change', renderArea); areaScenario.addEventListener('change', renderArea); efgChoice.addEventListener('change', renderEfg);
  renderArea(); renderEfg(); renderAccuracy(); renderQa();
})();
</script>
"""
    return template.replace("__REPORT_DATA__", json.dumps(data, separators=(",", ":")))


def build_html(result):
    contribution = result["final_labeller_contribution"]
    comparisons = result["sensitivity_adjudication"]
    efg = result["efg_area_estimates"]["ARP"]["B_nothicket_to_severe"]
    map_accuracy = result["map_accuracy"]
    disagreement = result["label_disagreement"]
    sampling = result["sampling_se_check"]
    budget_options = result["efg_reference_budget_options"]
    primary = comparisons["ARP"]["comparison_to_previous"]["B_nothicket_to_severe"]
    primary_accuracy = map_accuracy["ARP"]["B_nothicket_to_severe"]
    primary_cell = efg["mapped_cells"]["12"]
    primary_cell_severe = primary_cell["reference_area_by_class"]["severe"]
    primary_global_intact = primary["classes"]["intact"]
    maximum_added = max(contribution["usable_added_by_stratum"].values())

    css = """
    :root{--ink:#173042;--muted:#667985;--navy:#173e59;--teal:#168c82;
      --gold:#d99b27;--pale:#eef5f6;--line:#d9e2e7;--red:#a43c35}
    *{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,
      Segoe UI,sans-serif;max-width:1180px;margin:0 auto;padding:38px 24px 60px;
      color:var(--ink);line-height:1.45;background:#fff}
    header{border-bottom:4px solid var(--teal);padding-bottom:22px;margin-bottom:28px}
    h1{font-size:2.05rem;letter-spacing:-.025em;margin:0 0 8px}
    h2{font-size:1.35rem;margin:38px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}
    h3{font-size:1.05rem;margin:24px 0 8px}.subtitle{color:var(--muted);max-width:900px}
    .badge{display:inline-block;background:var(--navy);color:#fff;border-radius:999px;
      padding:4px 10px;font-size:.78rem;font-weight:700;margin-right:6px}
    .badge.final{background:var(--teal)}.cards{display:grid;grid-template-columns:repeat(5,1fr);
      gap:12px;margin:18px 0}.card{background:var(--pale);border:1px solid #d7e7e8;
      border-radius:10px;padding:14px}.card b{font-size:1.55rem;display:block;color:var(--navy)}
    .card small{color:var(--muted)}.callout{border-left:5px solid var(--teal);
      background:#edf8f6;padding:13px 16px;margin:16px 0}.note{border-left:5px solid var(--gold);
      background:#fff8e8;padding:13px 16px;margin:16px 0}table{border-collapse:collapse;
      width:100%;margin:12px 0 24px;font-size:.88rem}th{background:var(--navy);color:#fff;
      font-weight:650}th,td{padding:8px 9px;border-bottom:1px solid var(--line);text-align:right;
      vertical-align:top}td:first-child,th:first-child{text-align:left}tbody tr:nth-child(even){background:#f8fafb}
    caption{text-align:left;font-weight:750;margin:7px 0;color:var(--navy)}
    .delta-pos{color:#0b7169}.delta-neg{color:var(--red)}.muted{color:var(--muted)}
    .bar{width:130px;height:8px;background:#dce8ea;border-radius:4px;display:inline-block;
      margin-right:8px;vertical-align:middle;overflow:hidden}.bar span{display:block;height:100%;background:var(--teal)}
    .ok{color:#0b7169;font-weight:700}.warn{color:#9b6814;font-weight:700}.scenario{margin-top:25px}
    .interactive-report{margin:26px 0 8px}.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}
    .chart-panel{min-width:0;padding:14px 0;border-top:2px solid var(--line)}.chart-panel h3{margin:0 0 10px}
    .chart-controls{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 10px}.chart-controls label{font-size:.83rem;font-weight:650}
    .chart-controls select{margin-left:5px;padding:4px 6px;border:1px solid var(--line);border-radius:5px;background:#fff;color:var(--ink)}
    .chart-legend{font-size:.78rem;color:var(--muted);margin:0 0 7px}.legend{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 8px;vertical-align:-1px}
    .legend:first-child{margin-left:0}.legend.previous{background:#aebdc4}.legend.final{background:var(--teal)}.legend.intact{background:#5a8fc5}.legend.moderate{background:var(--gold)}.legend.severe{background:var(--red)}.legend.agreement{background:var(--navy)}
    .chart-svg{width:100%;height:auto;display:block;overflow:visible}.chart-detail{margin:6px 0 0;color:var(--muted);font-size:.8rem}
    .glossary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:12px 0 24px}.glossary-term{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:#f8fafb}
    .glossary-term summary{cursor:pointer;font-weight:750;color:var(--navy)}.glossary-term p{margin:8px 0 0;font-size:.88rem}.audit{margin:12px 0 22px;border:1px solid var(--line);border-radius:8px;padding:0 12px;background:#fbfcfc}.audit summary{cursor:pointer;padding:10px 0;font-weight:750;color:var(--navy)}
    .foot{margin-top:36px;color:var(--muted);font-size:.8rem}
    @media(max-width:850px){.cards{grid-template-columns:repeat(2,1fr)}.chart-grid,.glossary-grid{grid-template-columns:1fr}body{padding:22px 12px}
      .scroll{overflow-x:auto}table{min-width:760px}}
    @media(prefers-reduced-motion:reduce){.chart-svg *{transition:none!important}}
    @media print{body{max-width:none;padding:16px}.cards{grid-template-columns:repeat(5,1fr)}
      .scenario{break-inside:avoid}.scroll{overflow:visible}}
    """

    parts = [
        "<header>",
        "<span class='badge final'>FINAL CAMPAIGN UPDATE</span><span class='badge'>DESIGN-BASED</span>",
        "<h1>Thicket condition area estimation</h1>",
        "<div class='subtitle'>Final four-labeller update using the Michael Powell submission. "
        "The 21 July AP-added estimates are the comparison baseline; prior artifacts were not overwritten.</div>",
        "</header>",
        "<div class='cards'>",
        f"<div class='card'><b>{contribution['raw_submission_rows']}</b><small>MP rows submitted</small></div>",
        f"<div class='card'><b>{contribution['qa_overlap_rows']}</b><small>determinate overlap rows used for QA</small></div>",
        f"<div class='card'><b>{contribution['resolved_prior_unsure_rows']}</b><small>AP-unsure points resolved</small></div>",
        f"<div class='card'><b>+{contribution['usable_new_rows']}</b><small>usable points added</small></div>",
        f"<div class='card'><b>{contribution['final_unique_points']}</b><small>final analysis n</small></div>",
        "</div>",
        "<div class='callout'><b>Campaign coverage is complete.</b> All "
        f"{contribution['sample_draw_points']} sampled IDs received at least one label. "
        f"The determinate analysis sample increases from {contribution['previous_unique_points']} to "
        f"{contribution['final_unique_points']} points ({pct(contribution['sample_size_increase_percent'])}); "
        f"{contribution['remaining_excluded_unsure']} points remain excluded because their only independent label is unsure.</div>",
        "<h2>How to read the estimates</h2>",
        "<p class='muted'>The examples below use the primary ARP-preferred, nothicket-to-severe "
        "scenario. Open a term for its short definition and a live example from this report.</p>",
        "<div class='glossary-grid'>",
        "<details class='glossary-term' open><summary>Area terms</summary><p><b>Mapped area</b> is the model’s known stratum area. <b>Reference class</b> is the labeller’s observed condition. <b>Error-adjusted area</b> redistributes mapped area using those observations. Example: Arid × mapped severe has "
        f"{fnum(primary_cell['area_mapped_ha'])} ha mapped, of which {fnum(primary_cell_severe['area_ha'])} ± {fnum(primary_cell_severe['moe95_ha'])} ha is estimated reference severe.</p></details>",
        "<details class='glossary-term' open><summary>Accuracy and uncertainty</summary><p><b>OA</b> is weighted overall accuracy; <b>UA</b> is the reliability of a mapped class; <b>PA</b> is completeness of a reference class. <b>SE</b> measures sampling uncertainty; 95% MOE is 1.96×SE. Example: OA is "
        f"{pct(100 * primary_accuracy['overall_accuracy']['OA'])}; global intact area CI is {fnum(primary_global_intact['final_area_ha'] - primary_global_intact['final_moe95_ha'])}–{fnum(primary_global_intact['final_area_ha'] + primary_global_intact['final_moe95_ha'])} ha.</p></details>",
        "<details class='glossary-term'><summary>Scenario and adjudication</summary><p><b>Scenario</b> controls how nothicket is treated. <b>Adjudication</b> resolves original ARP/SVM disagreements. The primary view counts nothicket as severe and prefers ARP labels; all alternatives remain available in the interactive charts.</p></details>",
        "</div>",
        interactive_summary_html(result),
        "<h2>1. Michael Powell contribution</h2>",
        "<div class='scroll'><table><caption>Usable points added by mapped stratum</caption>",
        "<tr><th>mapped stratum</th><th>previous n</th><th>MP added</th><th>final n</th><th>increase</th><th>relative contribution</th></tr>",
    ]
    for stratum in STRATA:
        previous_n = contribution["previous_by_stratum"][stratum]
        added = contribution["usable_added_by_stratum"][stratum]
        final_n = previous_n + added
        width = 100 * added / maximum_added if maximum_added else 0
        parts.append(
            f"<tr><td>{stratum}</td><td>{previous_n}</td><td>+{added}</td><td>{final_n}</td>"
            f"<td>{pct(100 * added / previous_n)}</td><td><div class='bar'>"
            f"<span style='width:{width:.1f}%'></span></div><small>{added}</small></td></tr>"
        )
    parts.extend(
        [
            "</table></div>",
            "<div class='scroll'><table><caption>MP labels entering the estimator</caption>",
            "<tr><th>reference label</th><th>n added</th><th>estimator treatment</th></tr>",
        ]
    )
    for label in ["intact", "moderate", "severe", "transformed", "nothicket", "unsure"]:
        count = contribution["usable_added_by_label"].get(label, 0)
        treatment = (
            "excluded"
            if label == "unsure"
            else "remapped to severe"
            if label == "transformed"
            else "scenario-dependent"
            if label == "nothicket"
            else "included"
        )
        parts.append(f"<tr><td>{label}</td><td>{count}</td><td>{treatment}</td></tr>")
    parts.extend(
        [
            "</table></div>",
            "<div class='note'><b>Deduplication.</b> Of 237 submitted rows, "
            f"{contribution['new_sample_ids']} cover previously unseen sample IDs, "
            f"{contribution['resolved_prior_unsure_rows']} replace excluded AP-unsure labels, and "
            f"{contribution['qa_overlap_rows']} determinate duplicates are retained for QA only.</div>",
            "<h2>2. Final area estimates and MP-induced change</h2>",
            "<p class='muted'>Use the class-area chart above to compare choices. The exact Olofsson "
            "values and 95% margins of error are retained below for audit.</p>",
            "<details class='audit'><summary>Show exact area estimate tables</summary>",
        ]
    )
    for adjudicate in ("ARP", "SVM"):
        arm = comparisons[adjudicate]
        for scenario in ("A_nothicket_class", "B_nothicket_to_severe"):
            comp = arm["comparison_to_previous"][scenario]
            parts.extend(
                [
                    "<div class='scenario'>",
                    f"<h3>{SCENARIO_LABELS[scenario]} · original ARP/SVM disagreements adjudicated to {adjudicate}</h3>",
                    "<div class='scroll'><table><tr><th>reference class</th><th>previous area (ha)</th>"
                    "<th>previous ±95%</th><th>final area (ha)</th><th>final ±95%</th>"
                    "<th>area change (ha)</th><th>area change</th><th>MoE reduction</th></tr>",
                ]
            )
            for ref_class, values in comp["classes"].items():
                delta_class = "delta-pos" if values["delta_area_ha"] >= 0 else "delta-neg"
                moe_class = "ok" if values["moe95_reduction_percent"] >= 0 else "warn"
                parts.append(
                    f"<tr><td>{ref_class}</td><td>{fnum(values['previous_area_ha'])}</td>"
                    f"<td>{fnum(values['previous_moe95_ha'])}</td><td>{fnum(values['final_area_ha'])}</td>"
                    f"<td>{fnum(values['final_moe95_ha'])}</td><td class='{delta_class}'>"
                    f"{fnum(values['delta_area_ha'], sign=True)}</td><td class='{delta_class}'>"
                    f"{pct(values['delta_percent_of_previous'], sign=True)}</td><td class='{moe_class}'>"
                    f"{pct(values['moe95_reduction_percent'], sign=True)}</td></tr>"
                )
            parts.extend(
                [
                    "</table></div>",
                    f"<small>n used: {comp['previous_n_used']} → {comp['final_n_used']}; overall accuracy: "
                    f"{comp['previous_overall_accuracy']:.3f} → {comp['final_overall_accuracy']:.3f}</small>",
                    "</div>",
                ]
            )
    parts.extend(
        [
            "<div class='note'><b>Primary sensitivity reading.</b> Under scenario B and the "
            "ARP-preferred arm, final class-area changes relative to 21 July are "
            + ", ".join(
                f"{ref_class} {fnum(values['delta_area_ha'], sign=True)} ha"
                for ref_class, values in primary["classes"].items()
            )
            + ". Both adjudication arms and both nothicket treatments remain visible.</div>",
            "<h2>3. Final EFG × mapped-class area estimates</h2>",
            "<p class='muted'>Mapped-cell areas are the known map-stratum areas. The final user's "
            "accuracy (UA) is the Olofsson reliability estimate for each EFG × mapped-class cell. "
            "Use the EFG selector above to inspect the primary result; exact cell values remain available below.</p>",
            "<details class='audit'><summary>Show exact EFG × mapped-class tables</summary>",
            "<div class='scroll'><table><caption>Mapped EFG × mapped-class strata</caption>"
            "<tr><th>EFG</th><th>mapped class</th><th>mapped area (ha)</th><th>final n</th>"
            "<th>UA</th><th>UA ±95%</th><th>status</th></tr>",
        ]
    )
    for key in sorted(efg["mapped_cells"], key=int):
        values = efg["mapped_cells"][key]
        status = "estimable" if values["estimable"] else "non-estimable"
        if values["low_n"]:
            status += ", low n"
        status_class = "ok" if values["estimable"] and not values["low_n"] else "warn"
        parts.append(
            f"<tr><td>{values['efg']}</td><td>{values['map_class']}</td>"
            f"<td>{fnum(values['area_mapped_ha'])}</td><td>{values['n']}</td>"
            f"<td>{pct(100 * values['users_accuracy'])}</td><td>{pct(100 * values['users_accuracy_moe95'])}</td>"
            f"<td class='{status_class}'>{status}</td></tr>"
        )
    parts.append("</details>")
    parts.extend(
        [
            "</table></div>",
            "<h3>Error-adjusted area contribution by mapped cell</h3>",
            "<p class='muted'>Each row partitions the mapped-cell area into estimated reference classes; "
            "the estimates sum to the mapped area before rounding.</p>",
            "<div class='scroll'><table><tr><th>EFG</th><th>mapped class</th><th>mapped area (ha)</th>"
            "<th>intact area ±95% (ha)</th><th>moderate area ±95% (ha)</th>"
            "<th>severe area ±95% (ha)</th></tr>",
        ]
    )
    for key in sorted(efg["mapped_cells"], key=int):
        values = efg["mapped_cells"][key]
        ref = values["reference_area_by_class"]
        parts.append(
            f"<tr><td>{values['efg']}</td><td>{values['map_class']}</td>"
            f"<td>{fnum(values['area_mapped_ha'])}</td>"
            f"<td>{fnum(ref['intact']['area_ha'])} ± {fnum(ref['intact']['moe95_ha'])}</td>"
            f"<td>{fnum(ref['moderate']['area_ha'])} ± {fnum(ref['moderate']['moe95_ha'])}</td>"
            f"<td>{fnum(ref['severe']['area_ha'])} ± {fnum(ref['severe']['moe95_ha'])}</td></tr>"
        )
    parts.extend(
        [
            "</table></div>",
            "<h3>Primary error-adjusted EFG × reference-condition areas</h3>",
            "<p class='muted'>These are final Olofsson area estimates of the reference condition "
            "within each EFG, with ±95% margins of error.</p>",
            "<div class='scroll'><table><tr><th>EFG</th><th>n labelled</th>"
            "<th>intact area ±95% (ha)</th><th>moderate area ±95% (ha)</th>"
            "<th>severe area ±95% (ha)</th><th>status</th></tr>",
        ]
    )
    for efg_name, values in efg["reference_area_by_efg"].items():
        cells = values["composition"]
        status = "estimable" if values["estimable"] else "non-estimable"
        if values["non_estimable_cells"]:
            status += ", low cell: " + ", ".join(str(x) for x in values["non_estimable_cells"])
        status_class = "ok" if values["estimable"] and not values["non_estimable_cells"] else "warn"
        parts.append(
            f"<tr><td>{efg_name}</td><td>{values['n_labelled']}</td>"
            f"<td>{fnum(cells['intact']['area_ha'])} ± {fnum(cells['intact']['moe95_ha'])}</td>"
            f"<td>{fnum(cells['moderate']['area_ha'])} ± {fnum(cells['moderate']['moe95_ha'])}</td>"
            f"<td>{fnum(cells['severe']['area_ha'])} ± {fnum(cells['severe']['moe95_ha'])}</td>"
            f"<td class='{status_class}'>{status}</td></tr>"
        )
    parts.extend(
        [
            "</table></div>",
            "</details>",
            "<h2>4. Olofsson map-accuracy assessment</h2>",
            "<p class='muted'>The accuracy chart above compares all sensitivity choices. OA is overall "
            "accuracy; UA is mapped-class reliability; PA is reference-class completeness.</p>",
            "<details class='audit'><summary>Show exact Olofsson accuracy tables</summary>",
            "<div class='scroll'><table><caption>Overall accuracy across sensitivity arms</caption>"
            "<tr><th>adjudication</th><th>scenario</th><th>n</th><th>OA</th><th>OA SE</th><th>OA ±95%</th></tr>",
        ]
    )
    for adjudicate in ("ARP", "SVM"):
        for scenario in ("A_nothicket_class", "B_nothicket_to_severe"):
            accuracy = map_accuracy[adjudicate][scenario]
            overall = accuracy["overall_accuracy"]
            parts.append(
                f"<tr><td>{adjudicate}</td><td>{SCENARIO_LABELS[scenario]}</td><td>{accuracy['n_used']}</td>"
                f"<td>{pct(100 * overall['OA'])}</td><td>{overall['se']:.4f}</td>"
                f"<td>{pct(100 * overall['moe95'])}</td></tr>"
            )
    primary_accuracy = map_accuracy["ARP"]["B_nothicket_to_severe"]
    parts.extend(
        [
            "</table></div>",
            "<div class='scroll'><table><caption>Primary ARP-preferred, nothicket-to-severe class accuracies</caption>"
            "<tr><th>class</th><th>UA</th><th>UA ±95%</th><th>PA</th><th>PA ±95%</th></tr>",
        ]
    )
    for cls in STRATA:
        ua = primary_accuracy["users_accuracy"][cls]
        pa = primary_accuracy["producers_accuracy"][cls]
        parts.append(
            f"<tr><td>{cls}</td><td>{pct(100 * ua['U'])}</td><td>{pct(100 * ua['moe95'])}</td>"
            f"<td>{pct(100 * pa['P'])}</td><td>{pct(100 * pa['moe95'])}</td></tr>"
        )
    parts.extend(
        [
            "</table></div>",
            "</details>",
            "<h2>5. Label disagreement across all submissions</h2>",
            "<p class='muted'>The overlap-agreement chart above uses every submission. Unsure pairs "
            "are excluded from agreement rates; per-labeller label counts remain visible below.</p>",
            "<div class='scroll'><table><caption>Label counts by labeller</caption>"
            "<tr><th>labeller</th><th>rows</th><th>intact</th><th>moderate</th><th>severe</th>"
            "<th>transformed</th><th>nothicket</th><th>unsure</th></tr>",
        ]
    )
    for code in disagreement["labeller_order"]:
        counts = disagreement["label_counts"][code]
        parts.append(
            f"<tr><td>{code}</td><td>{sum(counts.values())}</td>"
            + "".join(f"<td>{counts.get(cls, 0)}</td>" for cls in ["intact", "moderate", "severe", "transformed", "nothicket", "unsure"])
            + "</tr>"
        )
    parts.extend(
        [
            "</table></div>",
            "<details class='audit'><summary>Show pairwise agreement and disagreement audit tables</summary>",
            "<div class='scroll'><table><caption>All pairwise overlap agreement</caption>"
            "<tr><th>comparison</th><th>overlap rows</th><th>unsure in pair</th><th>determinate pairs</th>"
            "<th>agree</th><th>agreement</th><th>disagree</th></tr>",
        ]
    )
    for qa in disagreement["pairwise"]:
        agreement = 100 * qa["agreement_rate"] if qa["agreement_rate"] is not None else None
        parts.append(
            f"<tr><td>{qa['new_labeller']} vs {qa['other_labeller']}</td><td>{qa['n_overlap']}</td>"
            f"<td>{qa['n_new_unsure'] + qa['n_other_unsure']}</td><td>{qa['n_determinate_pairs']}</td>"
            f"<td>{qa['n_agree']}</td><td>{pct(agreement)}</td><td>{qa['n_disagree']}</td></tr>"
        )
    aggregate = disagreement["aggregate"]
    parts.extend(
        [
            "</table></div>",
            "<div class='callout'><b>Aggregate duplicate-pair agreement.</b> "
            f"Across {aggregate['n_pairwise_overlap_rows']} pairwise overlap rows, "
            f"{aggregate['n_determinate_pairs']} determinate comparisons yielded "
            f"{aggregate['n_agree']} agreements and {aggregate['n_disagree']} disagreements "
            f"({pct(100 * aggregate['agreement_rate'])} agreement).</div>",
            "<div class='scroll'><table><caption>Disagreement types across determinate overlaps</caption>"
            "<tr><th>label pair</th><th>count</th></tr>",
        ]
    )
    for label_pair, count in aggregate["disagreement_by_label_pair"].items():
        parts.append(f"<tr><td>{label_pair}</td><td>{count}</td></tr>")
    parts.extend(
        [
            "</table></div>",
            "</details>",
            "<h2>6. Sampling SE audit</h2>",
            "<p class='muted'>The specified 0.015 is the design-time expected SE for overall accuracy, "
            "computed from the OOF expected stratum standard deviations and the planned allocation. "
            "It is not a promise that every realized class-area or accuracy SE will equal 0.015.</p>",
            "<div class='scroll'><table><tr><th>quantity</th><th>value</th></tr>",
            f"<tr><td>specified target SE</td><td>{sampling['specified_target_se']:.5f}</td></tr>",
            f"<tr><td>planned allocation (intact / moderate / severe)</td><td>{sampling['allocated_counts']['intact']} / {sampling['allocated_counts']['moderate']} / {sampling['allocated_counts']['severe']}</td></tr>",
            f"<tr><td>actual draw counts</td><td>{sampling['drawn_counts']['intact']} / {sampling['drawn_counts']['moderate']} / {sampling['drawn_counts']['severe']}</td></tr>",
            f"<tr><td>nominal SE achieved by draw</td><td>{sampling['draw_nominal_design_se']:.5f} (difference {sampling['draw_se_minus_target']:+.5f})</td></tr>",
            f"<tr><td>final determinate counts</td><td>{sampling['usable_determinate_counts']['intact']} / {sampling['usable_determinate_counts']['moderate']} / {sampling['usable_determinate_counts']['severe']}</td></tr>",
            f"<tr><td>nominal SE after unsure exclusions</td><td>{sampling['usable_nominal_design_se_if_expected_S']:.5f}</td></tr>",
            "</table></div>",
            "<div class='note'><b>Interpretation.</b> The original sample draw met its specified SE target: "
            f"{sampling['draw_nominal_design_se']:.5f} versus {sampling['specified_target_se']:.5f}. "
            "The final Olofsson OA SE varies by scenario because it is estimated from the realized labels; "
            "the unsure exclusions reduce usable n but do not invalidate the original draw.</div>",
        ]
    )
    parts.extend(
        [
            "<h2>7. Suggested EFG × reference-condition point budgets</h2>",
            "<p class='muted'>These are incremental plans for new, independent, determinate labels added to the "
            f"current {sum(sum(values.values()) for values in budget_options['starting_usable_counts'].values())}-point analysis sample. "
            "The reporting targets are the nine EFG × reference-condition areas. Because reference condition is "
            "only observed after labelling, points are drawn from EFG × mapped-class cells and allocated to minimise "
            "the combined relative uncertainty of those nine reference-area estimates.</p>",
            "<div class='scroll'><table><caption>Recommended new usable points by EFG × mapped class</caption>"
            "<tr><th>new usable-point budget</th><th>Arid map I / M / S</th><th>Valley map I / M / S</th>"
            "<th>Mesic map I / M / S</th><th>largest projected EFG × reference-area ±95%</th>"
            "<th>relative-variance reduction</th><th>recommended use</th></tr>",
        ]
    )
    for option in budget_options["options"]:
        added = option["added_by_efg_mapped_class"]
        parts.append(
            f"<tr><td>{option['budget_usable_points']}</td>"
            f"<td>+{added['Arid']['intact']} / +{added['Arid']['moderate']} / +{added['Arid']['severe']}</td>"
            f"<td>+{added['Valley']['intact']} / +{added['Valley']['moderate']} / +{added['Valley']['severe']}</td>"
            f"<td>+{added['Mesic']['intact']} / +{added['Mesic']['moderate']} / +{added['Mesic']['severe']}</td>"
            f"<td>{pct(100 * option['largest_expected_relative_moe95'])}</td>"
            f"<td>{pct(option['relative_variance_reduction_percent'])}</td>"
            f"<td>{option['recommendation']}</td></tr>"
        )
    assignment_capacity = " / ".join(
        str(option["assignment_capacity_at_current_exclusion_rate"])
        for option in budget_options["options"]
    )
    parts.extend(
        [
            "</table></div>",
            "<div class='note'><b>Implementation guardrails.</b> Draw the new points independently within the "
            "nine EFG × mapped-class cells and retain their design weights. Duplicate labels remain useful for "
            "interpreter QA, but do not increase the independent estimator n. The current campaign excluded "
            f"{budget_options['current_excluded_points']} of {budget_options['sample_draw_points']} sampled IDs "
            f"({pct(100 * budget_options['current_exclusion_rate'])}) because no independent determinate label "
            f"was available; plan capacity for {assignment_capacity} point assignments for the 100 / 200 / 300 "
            "usable-point targets, respectively, if that rate persists.</div>",
            "<details class='audit'><summary>Show projected relative 95% margins for all EFG × reference-condition areas</summary>"
            "<div class='scroll'><table><tr><th>EFG</th><th>reference condition</th><th>current</th>"
            "<th>after +100</th><th>after +200</th><th>after +300</th></tr>",
        ]
    )
    for efg_name in ("Arid", "Valley", "Mesic"):
        for ref_class in STRATA:
            projected = [
                option["expected_relative_moe95_by_efg_reference"][efg_name][ref_class]
                for option in budget_options["options"]
            ]
            parts.append(
                f"<tr><td>{efg_name}</td><td>{ref_class}</td>"
                f"<td>{pct(100 * budget_options['current_expected_relative_moe95_by_efg_reference'][efg_name][ref_class])}</td>"
                + "".join(f"<td>{pct(100 * value)}</td>" for value in projected)
                + "</tr>"
            )
    parts.extend(
        [
            "</table></div></details>",
            "<div class='callout'><b>Why some mapped cells receive no points.</b> This is a targeted precision "
            "allocation, not an equal-cell allocation: Valley reference-area estimates already have lower relative "
            "uncertainty, so the limited budgets are directed to the higher-uncertainty Arid and Mesic estimates. "
            "If every EFG × mapped-class cell must receive a new measurement, impose a per-cell minimum before "
            "optimising; that will reduce the precision gain for the reference-area targets.</div>",
            "<div class='note'><b>Scope.</b> This update recomputes the design-based class-area estimates, "
            "hard-map PPI comparison, EFG post-stratification, Olofsson map accuracy, and all-label QA. "
            "Continuous-probability PPI and degradation-index analyses are not carried forward because the "
            "added points do not have cached probability vectors.</div>",
            "<div class='foot'>Generated by analysis/23_area_estimation_final_labeller.py · "
            f"version {VERSION} · total mapped domain {fnum(result['area_total_ha'])} ha · "
            "submission validation and previous-result reproduction checks passed.</div>",
        ]
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Final thicket area estimation</title>"
        f"<style>{css}</style></head><body>{''.join(parts)}</body></html>"
    )


def main():
    arp_rows, _ = load_json_labels(ARP_FILE, "ARP")
    svm_rows = load_svm_labels()
    ap_rows, _ = load_json_labels(AP_FILE, "AP")
    mp_rows, mp_doc = load_json_labels(MP_FILE, "MP")
    submission_validation = validate_submission(mp_doc, mp_rows)

    base_by_id = {}
    for code, rows in [("ARP", arp_rows), ("SVM", svm_rows)]:
        for row in rows:
            base_by_id.setdefault(row["id"], {})[code] = row
    base_ids = set(base_by_id)
    ap_added = [row for row in ap_rows if row["id"] not in base_ids and row["label"] != "unsure"]
    previous_used_ids = base_ids | {row["id"] for row in ap_added}
    all_pre_mp_ids = base_ids | {row["id"] for row in ap_rows}

    mp_added = [row for row in mp_rows if row["id"] not in previous_used_ids]
    mp_qa_overlap = [row for row in mp_rows if row["id"] in previous_used_ids]
    mp_new_sample = [row for row in mp_rows if row["id"] not in all_pre_mp_ids]
    mp_resolved_unsure = [
        row
        for row in mp_rows
        if row["id"] in {ap["id"] for ap in ap_rows if ap["label"] == "unsure"}
        and row["id"] not in base_ids
    ]

    with open(os.path.join(RESULTS, "sample_design.json"), encoding="utf-8") as f:
        design = json.load(f)
    with open(PREVIOUS_FILE, encoding="utf-8") as f:
        previous_doc = json.load(f)
    previous_arms = previous_doc["sensitivity_adjudication"]
    qa_pairs = [
        qa_pair(mp_rows, "MP", arp_rows, "ARP"),
        qa_pair(mp_rows, "MP", svm_rows, "SVM"),
        qa_pair(mp_rows, "MP", ap_rows, "AP"),
    ]

    rows_by_arm = {}
    reports = {}
    for adjudicate in ("ARP", "SVM"):
        previous_rows = preferred_base_rows(base_by_id, adjudicate) + [dict(row) for row in ap_added]
        validation = validate_previous_reproduction(
            adjudicate, previous_rows, design, previous_arms[adjudicate]["updated"]
        )
        final_rows = previous_rows + [dict(row) for row in mp_added]
        rows_by_arm[adjudicate] = final_rows
        final_report = final_area_report(adjudicate, final_rows, design, qa_pairs)
        reports[adjudicate] = {
            "previous_result_reproduction": validation,
            "final": final_report,
            "comparison_to_previous": compare_scenarios(
                previous_arms[adjudicate]["updated"], final_report
            ),
        }

    previous_strata = Counter(row["stratum"] for row in rows_by_arm["ARP"] if row["id"] not in {x["id"] for x in mp_added})
    added_strata = Counter(row["stratum"] for row in mp_added)
    draw_ids = set()
    with open(os.path.join(RESULTS, "sample_points.csv"), encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            draw_ids.add(int(row["id"]))
    all_submission_ids = base_ids | {row["id"] for row in ap_rows} | {row["id"] for row in mp_rows}
    remaining_excluded = draw_ids - {row["id"] for row in rows_by_arm["ARP"]}
    if all_submission_ids != draw_ids:
        raise RuntimeError(
            f"Campaign coverage mismatch: submissions cover {len(all_submission_ids)} of {len(draw_ids)} sample IDs"
        )

    efg_final = efg_updates(rows_by_arm, previous_doc)
    efg_area_summary = {
        adjudicate: {
            scenario: efg_area_estimates(efg_final, adjudicate, scenario)
            for scenario in ("A_nothicket_class", "B_nothicket_to_severe")
        }
        for adjudicate in ("ARP", "SVM")
    }
    map_accuracy = map_accuracy_summary(reports)
    all_labels = label_disagreement_summary(
        {"ARP": arp_rows, "SVM": svm_rows, "AP": ap_rows, "MP": mp_rows}
    )
    se_check = sampling_se_check(design, rows_by_arm)

    contribution = {
        "raw_submission_rows": len(mp_rows),
        "previous_submission_overlap_rows": len(mp_rows) - len(mp_new_sample),
        "qa_overlap_rows": len(mp_qa_overlap),
        "new_sample_ids": len(mp_new_sample),
        "resolved_prior_unsure_rows": len(mp_resolved_unsure),
        "usable_new_rows": len(mp_added),
        "previous_unique_points": len(previous_used_ids),
        "final_unique_points": len(previous_used_ids) + len(mp_added),
        "sample_size_increase_percent": 100 * len(mp_added) / len(previous_used_ids),
        "sample_draw_points": len(draw_ids),
        "all_sample_ids_covered": len(all_submission_ids) == len(draw_ids),
        "remaining_excluded_unsure": len(remaining_excluded),
        "remaining_excluded_ids": sorted(remaining_excluded),
        "previous_by_stratum": {stratum: previous_strata[stratum] for stratum in STRATA},
        "usable_added_by_stratum": {stratum: added_strata[stratum] for stratum in STRATA},
        "usable_added_by_label": count_by(mp_added, "label"),
        "treatment": {
            "determinate_overlaps": "QA only; excluded from estimator to avoid double-counting",
            "previously_unseen": "added",
            "prior_unsure_resolved_by_mp": "MP determinate label added",
            "remaining_ap_unsure": "excluded",
        },
    }
    budget_options = efg_reference_budget_options(
        efg_area_summary["ARP"]["B_nothicket_to_severe"],
        contribution["final_unique_points"],
        contribution["sample_draw_points"],
    )

    result = {
        "artifact": "final design-based thicket-condition area estimation",
        "version": VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "area_total_ha": design["area_total_ha"],
        "source_submission": {
            "file": os.path.basename(MP_FILE),
            "labeler": mp_doc.get("labeler"),
            "assignment": mp_doc.get("assignment"),
            "exported": mp_doc.get("exported"),
            "completion": mp_doc.get("completion"),
            "checksum": mp_doc.get("checksum"),
            "validation": submission_validation,
        },
        "preserved_baseline": {
            "json_file": os.path.basename(PREVIOUS_FILE),
            "html_file": "area_estimation_added_labeller_2026-07-21.html",
            "files_overwritten": False,
        },
        "final_labeller_contribution": contribution,
        "qa": {"mp_pairwise": qa_pairs, "all_labels": all_labels},
        "sensitivity_adjudication": reports,
        "efg_updates": efg_final,
        "efg_area_estimates": efg_area_summary,
        "map_accuracy": map_accuracy,
        "label_disagreement": all_labels,
        "sampling_se_check": se_check,
        "efg_reference_budget_options": budget_options,
        "method_notes": [
            "Olofsson et al. stratified estimator retained as the primary estimator.",
            "Original ARP/SVM disagreement adjudication remains a two-arm sensitivity.",
            "MP determinate overlaps are QA duplicates and do not increase independent n.",
            "Four AP-only unsure points receive MP determinate labels and enter the estimator.",
            "Twenty-five remaining AP-only unsure points are excluded.",
            "transformed is remapped to severe; nothicket is reported under two scenarios.",
            "The draw-time target SE is 0.015 for expected overall accuracy; the realized draw achieves 0.01499.",
            "All-label disagreement rates count pairwise overlaps and exclude unsure pairs from agreement denominators.",
        ],
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, allow_nan=False)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(result))

    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_HTML}")
    print(
        f"MP contribution: {len(mp_rows)} submitted; {len(mp_qa_overlap)} QA overlaps; "
        f"{len(mp_resolved_unsure)} prior unsure resolved; {len(mp_added)} usable additions; "
        f"final n={len(previous_used_ids) + len(mp_added)}"
    )
    print(
        f"campaign coverage: {len(all_submission_ids)}/{len(draw_ids)} sampled IDs labelled; "
        f"{len(remaining_excluded)} unresolved unsure excluded"
    )
    for adjudicate in ("ARP", "SVM"):
        comparison = reports[adjudicate]["comparison_to_previous"]["B_nothicket_to_severe"]
        print(f"\n{adjudicate} / scenario B")
        for ref_class, values in comparison["classes"].items():
            print(
                f"  {ref_class:9s}: {values['final_area_ha']:,.0f} ha "
                f"({values['delta_area_ha']:+,.0f}); MoE {values['final_moe95_ha']:,.0f} ha "
                f"({values['moe95_reduction_percent']:+.1f}% reduction)"
            )


if __name__ == "__main__":
    main()
