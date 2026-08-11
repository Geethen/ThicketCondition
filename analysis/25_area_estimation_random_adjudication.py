"""Recompute final area estimates with reproducible random duplicate selection.

Every sampled location contributes at most one reference label. For every
location with two determinate labels, one submitted source label is selected
uniformly at random using NumPy's PCG64 generator with a fixed seed. This rule
applies to all ARP, SVM, AP and MP duplicates, whether the labels agree or
conflict. ``unsure`` labels are not eligible for selection; locations with no
determinate label are excluded. Every draw is written to the output JSON.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
VERSION = "2026-08-06"
ARM = "random"
SEED = 0
SOURCE_ORDER = ("ARP", "SVM", "AP", "MP")
OUT_JSON = RESULTS / f"area_estimation_random_adjudication_{VERSION}.json"


def load_final_module():
    path = HERE / "23_area_estimation_final_labeller.py"
    spec = importlib.util.spec_from_file_location("final_area_estimation", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def select_random_rows(rows_by_source: dict[str, list[dict]]):
    """Select one determinate source label per sampled location."""
    by_id: dict[int, list[dict]] = {}
    for source in SOURCE_ORDER:
        for row in rows_by_source[source]:
            by_id.setdefault(row["id"], []).append(row)

    rng = np.random.Generator(np.random.PCG64(SEED))
    selected_rows, draws, excluded = [], [], []
    for point_id in sorted(by_id):
        determinate = sorted(
            (row for row in by_id[point_id] if row["label"] != "unsure"),
            key=lambda row: SOURCE_ORDER.index(row["labeler"]),
        )
        if not determinate:
            excluded.append(point_id)
            continue

        if len(determinate) == 1:
            chosen = determinate[0]
        else:
            chosen = determinate[int(rng.integers(0, len(determinate)))]
            draws.append(
                {
                    "id": point_id,
                    "stratum": chosen["stratum"],
                    "candidate_labels": [
                        {"source": row["labeler"], "label": row["label"]}
                        for row in determinate
                    ],
                    "selected_source": chosen["labeler"],
                    "selected_label": chosen["label"],
                    "labels_conflict": len({row["label"] for row in determinate}) > 1,
                }
            )
        selected_rows.append(dict(chosen))

    return selected_rows, draws, excluded, by_id


def efg_result_for_rows(module, rows):
    """Calculate the EFG x reference-condition estimates required by Figure 1b."""
    with open(RESULTS / "stratum_areas_efg.json", encoding="utf-8") as handle:
        areas_m2 = json.load(handle)["area_m2"]
    areas_ha = {int(key): value / 1e4 for key, value in areas_m2.items()}
    with open(RESULTS / "existing_tagged_efg.json", encoding="utf-8") as handle:
        tag_rows = json.load(handle)["existing"]
    tags = {int(row["id"]): row for row in tag_rows}

    final = {}
    for mode, scenario in (("class", "A_nothicket_class"), ("severe", "B_nothicket_to_severe")):
        final[scenario] = module.efg_mod.run_scenario(rows, tags, areas_ha, mode)
    return {ARM: {"final": final}}


def main():
    module = load_final_module()
    arp_rows, _ = module.load_json_labels(module.ARP_FILE, "ARP")
    svm_rows = module.load_svm_labels()
    ap_rows, _ = module.load_json_labels(module.AP_FILE, "AP")
    mp_rows, mp_doc = module.load_json_labels(module.MP_FILE, "MP")
    submission_validation = module.validate_submission(mp_doc, mp_rows)
    rows_by_source = {"ARP": arp_rows, "SVM": svm_rows, "AP": ap_rows, "MP": mp_rows}

    final_rows, draws, excluded_ids, labels_by_id = select_random_rows(rows_by_source)
    if any(row["label"] == "unsure" for row in final_rows):
        raise RuntimeError("An unsure label entered the random-selection estimator")
    if len({row["id"] for row in final_rows}) != len(final_rows):
        raise RuntimeError("Duplicate point IDs entered the random-selection estimator")

    with open(RESULTS / "sample_design.json", encoding="utf-8") as handle:
        design = json.load(handle)
    with open(RESULTS / "sample_points.csv", encoding="utf-8-sig") as handle:
        drawn_ids = {int(row["id"]) for row in csv.DictReader(handle)}
    if set(labels_by_id) != drawn_ids:
        raise RuntimeError(
            f"Submission coverage mismatch: {len(labels_by_id)} labelled IDs and {len(drawn_ids)} sampled IDs"
        )
    if sorted(drawn_ids - {row["id"] for row in final_rows}) != excluded_ids:
        raise RuntimeError("Estimator exclusions do not match locations with no determinate label")
    if any(len(labels_by_id[point_id]) != 1 for point_id in excluded_ids):
        raise RuntimeError("Expected every no-determinate location to have a single unsure label")

    qa_pairs = [
        module.qa_pair(rows_by_source[a], a, rows_by_source[b], b)
        for i, a in enumerate(SOURCE_ORDER)
        for b in SOURCE_ORDER[i + 1 :]
    ]
    final_report = module.final_area_report(ARM, final_rows, design, qa_pairs)
    final_report["inputs"]["adjudication"] = (
        "For every location with two determinate labels, select one submitted source label "
        f"independently with equal probability using NumPy PCG64 seed {SEED}; unsure labels "
        "are ineligible and locations with no determinate label are excluded."
    )
    final_report["inputs"]["random_selection"] = {
        "generator": "NumPy PCG64",
        "seed": SEED,
        "n_duplicate_determinate_locations": len(draws),
        "n_conflicting_duplicate_locations": sum(draw["labels_conflict"] for draw in draws),
        "draws": draws,
    }
    reports = {ARM: {"final": final_report}}
    efg_final = efg_result_for_rows(module, final_rows)
    efg_area_estimates = {
        ARM: {
            scenario: module.efg_area_estimates(efg_final, ARM, scenario)
            for scenario in ("A_nothicket_class", "B_nothicket_to_severe")
        }
    }

    result = {
        "artifact": "design-based thicket-condition area estimation with random duplicate selection",
        "version": VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "area_total_ha": design["area_total_ha"],
        "random_label_selection": {
            "method": "independent equal-probability source-label selection for every location with two determinate labels",
            "generator": "NumPy PCG64",
            "seed": SEED,
            "n_duplicate_determinate_locations": len(draws),
            "n_conflicting_duplicate_locations": sum(draw["labels_conflict"] for draw in draws),
            "draws": draws,
        },
        "reference_label_handling": {
            "one_label_per_point": True,
            "duplicate_determinate_labels": "select one submitted source label at random",
            "unsure_labels": "ineligible for random selection",
            "no_determinate_label": "excluded",
            "no_determinate_label_n": len(excluded_ids),
            "no_determinate_label_ids": excluded_ids,
            "n_final_unique_points": len(final_rows),
            "nothicket_treatment": "reported as a class (scenario A) or grouped with severe (scenario B)",
        },
        "source_submission": {
            "file": Path(module.MP_FILE).name,
            "labeler": mp_doc.get("labeler"),
            "assignment": mp_doc.get("assignment"),
            "completion": mp_doc.get("completion"),
            "checksum": mp_doc.get("checksum"),
            "validation": submission_validation,
        },
        "sampling": {
            "sampled_locations": len(drawn_ids),
            "all_label_submissions": sum(len(rows) for rows in rows_by_source.values()),
            "duplicate_locations": sum(len(rows) > 1 for rows in labels_by_id.values()),
            "final_unique_points": len(final_rows),
            "excluded_no_determinate_label": len(excluded_ids),
        },
        "sensitivity_adjudication": reports,
        "efg_area_estimates": efg_area_estimates,
        "map_accuracy": module.map_accuracy_summary(reports),
        "label_disagreement": module.label_disagreement_summary(rows_by_source),
        "method_notes": [
            "Every determinate duplicate location is resolved by reproducible equal-probability random source-label selection, not by map accuracy.",
            "unsure labels are ineligible; locations with no determinate label are excluded.",
            "The Olofsson stratified estimator is used for area and accuracy estimates.",
            "transformed is remapped to severe; no thicket is retained in scenario A and grouped with severe in scenario B.",
        ],
    }
    with open(OUT_JSON, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)

    print(f"wrote {OUT_JSON}")
    print(
        f"randomised duplicate locations={len(draws)}; conflicting duplicate locations="
        f"{sum(draw['labels_conflict'] for draw in draws)}"
    )
    print(f"final n={len(final_rows)}; excluded no-determinate locations={len(excluded_ids)}")


if __name__ == "__main__":
    main()
