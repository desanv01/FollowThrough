"""Independent P5 analysis of report-input-0.1 datasets.

Recomputes status dispositions, strict outcomes, component counts, success
fractions and resource aggregates from supplied independent judgments. It never
grades model output, never treats self-report as truth, and never pools
synthetic with provider or pilot with formal rows.
"""
from __future__ import annotations

import re

INT_CEILING = 9_007_199_254_740_991
STATUSES = ("completed", "partial", "limit_reached", "provider_error",
            "infrastructure_error", "invalid")
ELIGIBLE = ("completed", "partial", "limit_reached")
OUTCOMES = ("functional", "workflow", "evidence", "completion")
OUTCOME_VALUES = ("pass", "fail", "unknown")
RESOURCE_FIELDS = ("model_responses", "tool_actions", "input_tokens", "output_tokens",
                   "elapsed_ms", "estimated_cost_micro_usd", "reserved_micro_usd")
STAGES = ("development", "pilot", "formal")
EVIDENCE = ("synthetic_development", "real_provider")
SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
ARMS = ("ordinary", "structured_progress")
TRACE_ROLES = ("O1", "W1", "R1", "controller")
TRACE_KINDS = ("supplied_fixture", "message", "edit", "check", "review",
               "requirement_update", "completion")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
FAMILY = {"R1": "retry-processing", "R2": "retry-processing", "R3": "retry-processing",
          "C1": "preview-cache", "C2": "preview-cache", "C3": "preview-cache"}


class DatasetError(ValueError):
    """Invalid report-input dataset or formal-only violation."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _int_between(v, lo, hi, name):
    if not _is_int(v) or not lo <= v <= hi:
        raise DatasetError(f"{name} must be an integer in [{lo}, {hi}]")
    return v


def _ident(v, name):
    if not isinstance(v, str) or not v or len(v) > 128 or not ID_RE.match(v):
        raise DatasetError(f"{name} must be a nonempty ASCII identifier <=128")
    return v


def strict_outcome(episode):
    """Contract rule: noncompleted eligible -> fail; completed: fail > unknown > pass."""
    status = episode["status"]
    if status not in ELIGIBLE:
        return "not_scored"
    if status != "completed":
        return "fail"
    values = [episode["outcomes"][k] for k in OUTCOMES]
    if "fail" in values:
        return "fail"
    if "unknown" in values:
        return "unknown"
    return "pass"


def _validate_episode(ep, seen_ids, seen_cells):
    expected = {"episode_id", "scenario_id", "arm", "trial", "stage", "evidence_kind",
                "status", "outcomes", "resources", "trace"}
    if not isinstance(ep, dict) or set(ep) != expected:
        raise DatasetError("each episode must have exactly the projection keys")
    _ident(ep["episode_id"], "episode_id")
    if ep["episode_id"] in seen_ids:
        raise DatasetError("duplicate episode_id")
    seen_ids.add(ep["episode_id"])
    if ep["scenario_id"] not in SCENARIOS:
        raise DatasetError("unknown scenario_id")
    if ep["arm"] not in ARMS:
        raise DatasetError("unknown arm")
    _int_between(ep["trial"], 1, INT_CEILING, "trial")
    if ep["stage"] not in STAGES:
        raise DatasetError("unknown stage")
    if ep["evidence_kind"] not in EVIDENCE:
        raise DatasetError("unknown evidence_kind")
    if ep["evidence_kind"] == "synthetic_development" and ep["stage"] != "development":
        raise DatasetError("synthetic_development requires stage=development")
    if ep["status"] not in STATUSES:
        raise DatasetError("unknown status")
    cell = (ep["evidence_kind"], ep["stage"], ep["scenario_id"], ep["arm"], ep["trial"])
    if cell in seen_cells:
        raise DatasetError("duplicate provenance/stage/scenario/arm/trial cell")
    seen_cells.add(cell)
    if not isinstance(ep["outcomes"], dict) or set(ep["outcomes"]) != set(OUTCOMES):
        raise DatasetError("outcomes must be exactly functional/workflow/evidence/completion")
    for key, value in ep["outcomes"].items():
        if value not in OUTCOME_VALUES:
            raise DatasetError(f"outcome {key} must be pass/fail/unknown")
    if not isinstance(ep["resources"], dict) or set(ep["resources"]) != set(RESOURCE_FIELDS):
        raise DatasetError("resources must have exactly the seven projection fields")
    for key, value in ep["resources"].items():
        if value is not None:
            _int_between(value, 0, INT_CEILING, f"resources.{key}")
    if not isinstance(ep["trace"], list):
        raise DatasetError("trace must be a list")
    last = 0
    for event in ep["trace"]:
        if not isinstance(event, dict) or set(event) != {"seq", "role", "kind", "summary"}:
            raise DatasetError("trace events must have exactly seq/role/kind/summary")
        _int_between(event["seq"], 1, INT_CEILING, "trace.seq")
        if event["seq"] <= last:
            raise DatasetError("trace seq must be strictly increasing")
        last = event["seq"]
        if event["role"] not in TRACE_ROLES:
            raise DatasetError("unknown trace role")
        if event["kind"] not in TRACE_KINDS:
            raise DatasetError("unknown trace kind")
        if not isinstance(event["summary"], str) or len(event["summary"]) > 4096:
            raise DatasetError("trace summary must be a string <=4096 characters")


def validate_dataset(obj):
    if not isinstance(obj, dict) or set(obj) != {"schema_version", "dataset_id", "episodes"}:
        raise DatasetError("dataset must have exactly schema_version/dataset_id/episodes")
    if obj["schema_version"] != "report-input-0.1":
        raise DatasetError("unsupported schema_version")
    _ident(obj["dataset_id"], "dataset_id")
    if not isinstance(obj["episodes"], list):
        raise DatasetError("episodes must be a list")
    seen_ids, seen_cells = set(), set()
    for ep in obj["episodes"]:
        _validate_episode(ep, seen_ids, seen_cells)
    return obj


def require_formal(obj):
    """Formal analysis may use only accepted real_provider/formal rows."""
    validate_dataset(obj)
    bad = [ep["episode_id"] for ep in obj["episodes"]
           if not (ep["evidence_kind"] == "real_provider" and ep["stage"] == "formal")]
    if bad:
        raise DatasetError("formal analysis requires real_provider/formal rows only; "
                           f"offending episodes: {bad[:5]}")


def _resource_summary(rows):
    out = {}
    for field in RESOURCE_FIELDS:
        values = [r["resources"][field] for r in rows]
        known = [v for v in values if v is not None]
        unknown = len(values) - len(known)
        complete_total = None if unknown else sum(known)
        out[field] = {
            "known_sum": sum(known),
            "known_count": len(known),
            "unknown_count": unknown,
            "complete_total": complete_total,
        }
    return out


def _group(rows):
    eligible = [r for r in rows if r["status"] in ELIGIBLE]
    excluded = len(rows) - len(eligible)
    status_counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    component_counts = {
        dim: {value: sum(1 for r in eligible if r["outcomes"][dim] == value)
              for value in OUTCOME_VALUES}
        for dim in OUTCOMES
    }
    strict = {"pass": 0, "fail": 0, "unknown": 0}
    for r in eligible:
        strict[strict_outcome(r)] += 1
    return {
        "attempted": len(rows),
        "eligible": len(eligible),
        "excluded": excluded,
        "status_counts": status_counts,
        "component_counts": component_counts,
        "strict_counts": strict,
        "success_fraction": {"numerator": strict["pass"], "denominator": len(eligible)},
        "resources": _resource_summary(rows),
    }


def aggregate(obj, formal=False):
    validate_dataset(obj)
    if formal:
        require_formal(obj)
    groups = {}
    for ep in obj["episodes"]:
        key = (ep["evidence_kind"], ep["stage"], ep["scenario_id"], ep["arm"])
        groups.setdefault(key, []).append(ep)
    out_groups = []
    for key in sorted(groups):
        evidence_kind, stage, scenario_id, arm = key
        rows = sorted(groups[key], key=lambda r: (r["trial"], r["episode_id"]))
        summary = _group(rows)
        summary.update({
            "evidence_kind": evidence_kind,
            "stage": stage,
            "scenario_id": scenario_id,
            "family": FAMILY[scenario_id],
            "arm": arm,
            "cell_id": f"{scenario_id}.{arm}.{stage}",
        })
        out_groups.append(summary)
    episodes = [{
        "episode_id": ep["episode_id"],
        "scenario_id": ep["scenario_id"],
        "arm": ep["arm"],
        "trial": ep["trial"],
        "stage": ep["stage"],
        "evidence_kind": ep["evidence_kind"],
        "status": ep["status"],
        "strict_outcome": strict_outcome(ep),
    } for ep in sorted(obj["episodes"], key=lambda r: (
        r["evidence_kind"], r["stage"], r["scenario_id"], r["arm"],
        r["trial"], r["episode_id"]))]
    return {
        "schema_version": "p5-analysis-0.1",
        "dataset_id": obj["dataset_id"],
        "evidence_notice": (
            "Supplied independent judgments only. Synthetic/development rows are fixture "
            "data, not model results. No pooled headline, significance test or causal claim."
        ),
        "inventory": {
            "attempted": len(obj["episodes"]),
            "evidence_counts": {k: sum(1 for ep in obj["episodes"]
                                       if ep["evidence_kind"] == k) for k in EVIDENCE},
            "stage_counts": {k: sum(1 for ep in obj["episodes"]
                                    if ep["stage"] == k) for k in STAGES},
        },
        "groups": out_groups,
        "episodes": episodes,
        "limitations": [
            "Small authored study; scenario configurations are not independent domains.",
            "Workflow/evidence/completion predicates depend on supplied independent judgments.",
            "Unknown values are not failures and are never imputed as zero.",
            "No statistical significance or model-ranking claim is supported.",
        ],
    }


def compare_arms(analysis):
    """Within-scenario ordinary vs structured rows; never pools across scenarios."""
    by_key = {(g["scenario_id"], g["arm"]): g for g in analysis["groups"]}
    rows = []
    for scenario in sorted({g["scenario_id"] for g in analysis["groups"]}):
        for arm in ARMS:
            g = by_key.get((scenario, arm))
            rows.append({
                "scenario_id": scenario,
                "family": FAMILY[scenario],
                "arm": arm,
                "present": g is not None,
                "eligible": None if g is None else g["eligible"],
                "attempted": None if g is None else g["attempted"],
                "strict_pass": None if g is None else g["strict_counts"]["pass"],
                "strict_fail": None if g is None else g["strict_counts"]["fail"],
                "strict_unknown": None if g is None else g["strict_counts"]["unknown"],
                "success_fraction": None if g is None else g["success_fraction"],
            })
    return {
        "schema_version": "p5-arm-comparison-0.1",
        "evidence_notice": "Descriptive within-scenario comparison; no pooled or causal claim.",
        "rows": rows,
    }


def _fraction(f):
    if f["denominator"] == 0:
        return "N/A (denominator 0)"
    return f'{f["numerator"]}/{f["denominator"]}'


def render_markdown(analysis):
    lines = [
        "# P5 analysis (independent recomputation)",
        "",
        f'**Dataset:** `{analysis["dataset_id"]}`',
        "",
        f'**Evidence notice:** {analysis["evidence_notice"]}',
        "",
        "## Inventory (not a benchmark score)",
        "",
        "| Attempted | synthetic_development | real_provider | development | pilot | formal |",
        "|---|---|---|---|---|---|",
        f'| {analysis["inventory"]["attempted"]} '
        f'| {analysis["inventory"]["evidence_counts"]["synthetic_development"]} '
        f'| {analysis["inventory"]["evidence_counts"]["real_provider"]} '
        f'| {analysis["inventory"]["stage_counts"]["development"]} '
        f'| {analysis["inventory"]["stage_counts"]["pilot"]} '
        f'| {analysis["inventory"]["stage_counts"]["formal"]} |',
        "",
    ]
    if not analysis["groups"]:
        lines += ["No rows: no formal or fixture episode is present in this dataset.", ""]
    for g in analysis["groups"]:
        lines += [
            f'## {g["scenario_id"]} × {g["arm"]} ({g["stage"]}, {g["evidence_kind"]})',
            "",
            f'Attempted {g["attempted"]}; eligible {g["eligible"]}; excluded {g["excluded"]}.',
            "",
            "| completed | partial | limit_reached | provider_error | infrastructure_error | invalid |",
            "|---|---|---|---|---|---|",
            "| " + " | ".join(str(g["status_counts"][s]) for s in STATUSES) + " |",
            "",
            "| strict pass | strict fail | strict unknown | success fraction |",
            "|---|---|---|---|",
            f'| {g["strict_counts"]["pass"]} | {g["strict_counts"]["fail"]} '
            f'| {g["strict_counts"]["unknown"]} | {_fraction(g["success_fraction"])} |',
            "",
            "| component | pass | fail | unknown |",
            "|---|---|---|---|",
        ]
        for dim in OUTCOMES:
            c = g["component_counts"][dim]
            lines.append(f'| {dim} | {c["pass"]} | {c["fail"]} | {c["unknown"]} |')
        lines += ["", "| resource | known_sum | known_count | unknown_count | complete_total |",
                  "|---|---|---|---|---|"]
        for field in RESOURCE_FIELDS:
            r = g["resources"][field]
            total = "null" if r["complete_total"] is None else r["complete_total"]
            lines.append(f'| {field} | {r["known_sum"]} | {r["known_count"]} '
                         f'| {r["unknown_count"]} | {total} |')
        lines.append("")
    lines += ["## Limitations", ""] + [f"- {x}" for x in analysis["limitations"]] + [""]
    return "\n".join(lines)
