"""P5 campaign design, affordability and bounded run-request planning.

Standard library only. No provider, Docker, credential or network access.
All outputs are labelled proposals/fixtures until reviewed and frozen by P4.
"""
from __future__ import annotations

SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
ARMS = ("ordinary", "structured_progress")
FAMILY = {
    "R1": "retry-processing", "R2": "retry-processing", "R3": "retry-processing",
    "C1": "preview-cache", "C2": "preview-cache", "C3": "preview-cache",
}
INT_CEILING = 9_007_199_254_740_991


class DesignError(ValueError):
    """Invalid design, bounds or affordability input."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _positive_int(v, name, minimum=1):
    if not _is_int(v) or v < minimum:
        raise DesignError(f"{name} must be an integer >= {minimum}")
    return v


def build_design(repetitions=3, scenarios=SCENARIOS, arms=ARMS, rotation=2):
    """Deterministic interleaved design: repetitions are blocks; scenario order rotates."""
    repetitions = _positive_int(repetitions, "repetitions")
    scenarios = tuple(scenarios)
    arms = tuple(arms)
    if not scenarios or any(s not in FAMILY for s in scenarios):
        raise DesignError("scenarios must be a nonempty subset of the six catalog scenarios")
    if any(a not in ARMS for a in arms):
        raise DesignError("arms must be a subset of ordinary/structured_progress")
    if rotation < 1:
        raise DesignError("rotation must be >= 1")

    cells = []
    base = list(scenarios)
    for rep in range(1, repetitions + 1):
        shift = ((rep - 1) * rotation) % len(base)
        order = base[shift:] + base[:shift]
        for index, scenario in enumerate(order):
            pair = list(arms) if (index + rep) % 2 == 0 else list(reversed(arms))
            for arm in pair:
                cells.append({
                    "cell_id": f"{scenario}.{arm}.r{rep}",
                    "scenario_id": scenario,
                    "family": FAMILY[scenario],
                    "arm": arm,
                    "trial": rep,
                })

    keys = [(c["scenario_id"], c["arm"], c["trial"]) for c in cells]
    if len(set(keys)) != len(keys):
        raise DesignError("duplicate design cell")
    expected = len(scenarios) * len(arms) * repetitions
    if len(cells) != expected:
        raise DesignError(f"design count {len(cells)} != expected {expected}")
    for c in cells:
        if not _is_int(c["trial"]) or c["trial"] < 1:
            raise DesignError("trial must be a positive integer")
    return {
        "schema_version": "p5-design-0.1",
        "status": "PROPOSED_NOT_FROZEN",
        "repetitions": repetitions,
        "scenarios": list(scenarios),
        "arms": list(arms),
        "rotation": rotation,
        "cells": cells,
        "count": len(cells),
        "note": "Proposed interleaving only; P4 freeze sets final size/order/settings.",
    }


def affordability(design, pilot):
    """Worst-case reservation/runtime arithmetic from *reviewed* pilot inputs.

    Unknown inputs stay unknown; they are never imputed as zero and no billing
    guarantee or forecast is produced.
    """
    if not isinstance(design, dict) or not _is_int(design.get("count")):
        raise DesignError("design must come from build_design")
    episodes = design["count"]
    required = (
        "episodes", "elapsed_seconds_per_episode", "actual_cost_micro_usd_per_episode",
        "reserved_micro_usd_per_episode", "remaining_balance_micro_usd",
        "max_total_reservation_micro_usd",
    )
    for key in required:
        if key not in pilot:
            raise DesignError(f"pilot input missing {key}")
    if not _is_int(pilot["episodes"]) or pilot["episodes"] < 1:
        raise DesignError("pilot.episodes must be a positive integer")

    def known(v):
        return v is not None

    unknown = []
    units = {}
    for key in required[1:]:
        v = pilot[key]
        if v is not None and (not _is_int(v) or v < 0):
            raise DesignError(f"pilot.{key} must be null or a nonnegative integer")
        if v is None:
            unknown.append(key)

    units["episodes"] = episodes
    if known(pilot["elapsed_seconds_per_episode"]):
        units["worst_case_runtime_seconds"] = episodes * pilot["elapsed_seconds_per_episode"]
    else:
        units["worst_case_runtime_seconds"] = None
    if known(pilot["actual_cost_micro_usd_per_episode"]):
        units["projected_actual_cost_micro_usd"] = episodes * pilot["actual_cost_micro_usd_per_episode"]
    else:
        units["projected_actual_cost_micro_usd"] = None
    if known(pilot["reserved_micro_usd_per_episode"]):
        units["worst_case_reservation_micro_usd"] = episodes * pilot["reserved_micro_usd_per_episode"]
    else:
        units["worst_case_reservation_micro_usd"] = None

    ceilings = []
    if known(pilot["remaining_balance_micro_usd"]):
        ceilings.append(pilot["remaining_balance_micro_usd"])
    if known(pilot["max_total_reservation_micro_usd"]):
        ceilings.append(pilot["max_total_reservation_micro_usd"])
    ceiling = min(ceilings) if ceilings else None
    worst = units["worst_case_reservation_micro_usd"]
    if ceiling is None or worst is None:
        status = "UNKNOWN_INPUT"
        headroom = None
    else:
        headroom = ceiling - worst
        status = "FITS" if headroom >= 0 else "DOES_NOT_FIT"

    return {
        "schema_version": "p5-affordability-0.1",
        "status": status,
        "pilot_input_label": pilot.get("label", "UNLABELLED_INPUT"),
        "observed_inputs": pilot.get("label", "").startswith("REVIEWED"),
        "design_count": episodes,
        "episodes_from_pilot": pilot["episodes"],
        "effective_ceiling_micro_usd": ceiling,
        "worst_case_reservation_micro_usd": worst,
        "headroom_micro_usd": headroom,
        "computed": units,
        "unknown_inputs": unknown,
        "assumptions": [
            "Pilot values are reviewed observations, not guesses; null means unknown.",
            "Worst case reservation uses the reviewed per-episode reservation, not actual cost.",
            "No billing guarantee, price forecast or remaining-balance inference is made.",
            "Campaign size remains a proposal for P4 freeze.",
        ],
    }


def build_run_request(design, stage, bounds, freeze_ids, dataset_id, model_alias="deepseek-flash"):
    """Finite, reviewed run-request draft. Not authorized; P4 freeze + Main sign-off required."""
    if stage not in ("pilot", "formal"):
        raise DesignError("stage must be pilot or formal")
    if not isinstance(freeze_ids, dict) or not freeze_ids.get("freeze_manifest"):
        raise DesignError("freeze_ids.freeze_manifest is required")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise DesignError("dataset_id is required")

    required = (
        "calls_per_episode", "max_output_tokens", "seconds_per_call",
        "reserve_micro_usd_per_call", "max_reservation_micro_usd_per_episode",
        "max_total_reservation_micro_usd", "wall_seconds_total", "concurrency",
    )
    for key in required:
        if key not in bounds:
            raise DesignError(f"bounds missing {key}")
    calls = _positive_int(bounds["calls_per_episode"], "calls_per_episode")
    max_out = _positive_int(bounds["max_output_tokens"], "max_output_tokens")
    seconds = bounds["seconds_per_call"]
    if not _is_int(seconds) or seconds < 1:
        raise DesignError("seconds_per_call must be a positive integer")
    reserve_call = _positive_int(bounds["reserve_micro_usd_per_call"], "reserve_micro_usd_per_call")
    per_episode = _positive_int(bounds["max_reservation_micro_usd_per_episode"],
                                "max_reservation_micro_usd_per_episode")
    total = _positive_int(bounds["max_total_reservation_micro_usd"],
                          "max_total_reservation_micro_usd")
    wall = _positive_int(bounds["wall_seconds_total"], "wall_seconds_total")
    concurrency = _positive_int(bounds["concurrency"], "concurrency")
    if concurrency != 1:
        raise DesignError("P3 operator path is one call at a time; concurrency must be 1")
    if calls * reserve_call > per_episode:
        raise DesignError("calls*reserve_per_call exceeds per-episode maximum reservation")
    if per_episode * design["count"] > total:
        raise DesignError("per-episode reservation * units exceeds total maximum reservation")
    if wall < design["count"] * calls * seconds:
        raise DesignError("wall time is below unit count * calls * per-call timeout")

    units = [{
        "unit_id": c["cell_id"],
        "scenario_id": c["scenario_id"],
        "family": c["family"],
        "arm": c["arm"],
        "trial": c["trial"],
        "stage": stage,
        "evidence_expected": "real_provider",
        "required_receipts": [
            "operation_id", "requested_and_returned_model", "call_count",
            "usage_tokens", "reservation_and_settlement", "cleanup_readback",
            "candidate_sha256_and_requirement_version", "protected_grade_receipt",
        ],
        "authorized": False,
        "status": "DRAFT_FOR_REVIEW",
    } for c in design["cells"]]

    return {
        "schema_version": "p5-run-request-0.1",
        "status": "DRAFT_NOT_AUTHORIZED",
        "stage": stage,
        "evidence_kind": "real_provider",
        "dataset_id": dataset_id,
        "model_alias_proposed": model_alias,
        "design": {"count": design["count"], "cells": design["cells"]},
        "units": units,
        "bounds": {
            "calls_per_episode": calls,
            "max_output_tokens": max_out,
            "seconds_per_call": seconds,
            "reserve_micro_usd_per_call": reserve_call,
            "max_reservation_micro_usd_per_episode": per_episode,
            "max_total_reservation_micro_usd": total,
            "wall_seconds_total": wall,
            "concurrency": concurrency,
        },
        "worst_case_reservation_micro_usd": per_episode * design["count"],
        "freeze_ids": freeze_ids,
        "review": {
            "p4_freeze_required": True,
            "p3_operator_required": True,
            "p5_independent_verifier_required": True,
            "main_authorization_required": True,
        },
        "stop_rules": [
            "Stop on unknown cleanup or unknown settlement.",
            "Stop on reservation refusal or identity mismatch.",
            "No automatic paid retry; a retry needs the frozen attempt policy.",
            "Preserve every denied, failed or stopped attempt.",
        ],
        "note": "Planning artifact only. No provider call is authorized by this file.",
    }
