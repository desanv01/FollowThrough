"""Independent recomputation comparison against a reporting output.

Recomputes groups from report-input-0.1 with the P5 analysis implementation and
compares the scoring-relevant fields with a supplied report-output-0.1 document.
Any difference is reported; nothing is silently reconciled.
"""
from __future__ import annotations

from . import analysis

COMPARE_FIELDS = ("attempted", "eligible", "excluded", "status_counts",
                  "component_counts", "strict_counts", "success_fraction")
RESOURCE_FIELDS = analysis.RESOURCE_FIELDS


def _group_key(g):
    return (g.get("evidence_kind"), g.get("stage"), g.get("scenario_id"), g.get("arm"))


def compare(input_obj, report_obj, formal=False):
    recomputed = analysis.aggregate(input_obj, formal=formal)
    if not isinstance(report_obj, dict) or report_obj.get("schema_version") != "report-output-0.1":
        raise analysis.DatasetError("report must be report-output-0.1")
    recorded = {}
    for g in report_obj.get("groups", []):
        recorded[_group_key(g)] = g

    diffs = []
    for group in recomputed["groups"]:
        key = _group_key(group)
        other = recorded.get(key)
        label = "/".join(str(x) for x in key)
        if other is None:
            diffs.append({"group": label, "field": "<group>", "problem": "missing_in_report"})
            continue
        for field in COMPARE_FIELDS:
            if group[field] != other.get(field):
                diffs.append({"group": label, "field": field,
                              "recomputed": group[field], "recorded": other.get(field)})
        their_resources = other.get("resources", {})
        for field in RESOURCE_FIELDS:
            if group["resources"][field] != their_resources.get(field):
                diffs.append({"group": label, "field": f"resources.{field}",
                              "recomputed": group["resources"][field],
                              "recorded": their_resources.get(field)})
    recomputed_keys = {_group_key(g) for g in recomputed["groups"]}
    for key in sorted(set(recorded) - recomputed_keys):
        diffs.append({"group": "/".join(str(x) for x in key), "field": "<group>",
                      "problem": "extra_group_in_report"})

    inventory_diffs = []
    their_inventory = report_obj.get("inventory", {})
    for field in ("attempted", "evidence_counts", "stage_counts"):
        if recomputed["inventory"][field] != their_inventory.get(field):
            inventory_diffs.append({"field": field,
                                    "recomputed": recomputed["inventory"][field],
                                    "recorded": their_inventory.get(field)})

    return {
        "schema_version": "p5-recomputation-0.1",
        "dataset_id": recomputed["dataset_id"],
        "groups_recomputed": len(recomputed["groups"]),
        "groups_recorded": len(recorded),
        "matches": not diffs and not inventory_diffs,
        "group_differences": diffs,
        "inventory_differences": inventory_diffs,
        "evidence_notice": ("Independent recomputation of supplied judgments; differences are "
                            "findings, never silently reconciled. No formal result is asserted."),
    }
