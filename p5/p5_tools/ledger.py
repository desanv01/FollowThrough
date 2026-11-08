"""Attempt/provenance ledger and report-input projection guard.

The current report-input-0.1 projection has no retry model: each
(evidence_kind, stage, scenario_id, arm, trial) cell may appear once. This module
keeps every attempt in a separate ledger and projects exactly one frozen primary
attempt per cell. It refuses selective replacement of a model outcome.
"""
from __future__ import annotations

import hashlib

INT_CEILING = 9_007_199_254_740_991
ATTEMPT_STATUSES = ("completed", "failed", "denied", "stopped", "unknown")
CLASSIFICATIONS = ("model_outcome", "provider_infrastructure", "sandbox", "unknown")
CLEANUP = ("confirmed_absent", "failed", "unknown")
REPORT_STATUSES = ("completed", "partial", "limit_reached", "provider_error",
                   "infrastructure_error", "invalid")
ELIGIBLE = ("completed", "partial", "limit_reached")
EXCLUDED = ("provider_error", "infrastructure_error", "invalid")
TECHNICAL_REASONS = ("initial_attempt", "technical_provider_failure", "infrastructure_retry")
OUTCOMES = ("functional", "workflow", "evidence", "completion")
RESOURCE_FIELDS = ("model_responses", "tool_actions", "input_tokens", "output_tokens",
                   "elapsed_ms", "estimated_cost_micro_usd", "reserved_micro_usd")


class LedgerError(ValueError):
    """Invalid ledger or unsupported projection."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _int(v, lo, hi, name):
    if not _is_int(v) or not lo <= v <= hi:
        raise LedgerError(f"{name} must be an integer in [{lo}, {hi}]")


def _sha(v, name):
    if not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
        raise LedgerError(f"{name} must be a lowercase SHA-256 hex digest")


def validate_attempt(a):
    required = {
        "attempt_id", "operation_id", "scenario_id", "arm", "trial", "stage",
        "evidence_kind", "status", "classification", "model_outcome",
        "reserved_micro_usd", "estimated_cost_micro_usd", "usage", "cleanup",
        "receipt_sha256",
    }
    if not isinstance(a, dict) or set(a) != required:
        raise LedgerError("attempt must have exactly the ledger fields")
    if not isinstance(a["attempt_id"], str) or not a["attempt_id"]:
        raise LedgerError("attempt_id required")
    if not isinstance(a["operation_id"], str) or not a["operation_id"]:
        raise LedgerError("operation_id required")
    if a["scenario_id"] not in ("R1", "R2", "R3", "C1", "C2", "C3"):
        raise LedgerError("unknown scenario_id")
    if a["arm"] not in ("ordinary", "structured_progress"):
        raise LedgerError("unknown arm")
    _int(a["trial"], 1, INT_CEILING, "trial")
    if a["stage"] not in ("development", "pilot", "formal"):
        raise LedgerError("unknown stage")
    if a["evidence_kind"] not in ("synthetic_development", "real_provider"):
        raise LedgerError("unknown evidence_kind")
    if a["status"] not in ATTEMPT_STATUSES:
        raise LedgerError("unknown attempt status")
    if a["classification"] not in CLASSIFICATIONS:
        raise LedgerError("unknown classification")
    if not isinstance(a["model_outcome"], bool):
        raise LedgerError("model_outcome must be boolean")
    if a["reserved_micro_usd"] is not None:
        _int(a["reserved_micro_usd"], 0, INT_CEILING, "reserved_micro_usd")
    if a["estimated_cost_micro_usd"] is not None:
        _int(a["estimated_cost_micro_usd"], 0, INT_CEILING, "estimated_cost_micro_usd")
    if a["usage"] is not None:
        if not isinstance(a["usage"], dict):
            raise LedgerError("usage must be null or an object")
        for key, value in a["usage"].items():
            if value is not None:
                _int(value, 0, INT_CEILING, f"usage.{key}")
    if a["cleanup"] not in CLEANUP:
        raise LedgerError("cleanup must be confirmed_absent/failed/unknown")
    _sha(a["receipt_sha256"], "receipt_sha256")
    return a


def cell_key(a):
    return (a["evidence_kind"], a["stage"], a["scenario_id"], a["arm"], a["trial"])


def select_primary(attempts, primary_attempt_ids, reasons=None):
    """Return the frozen primary attempt per cell; refuse selective reruns."""
    if not isinstance(attempts, list) or not attempts:
        raise LedgerError("attempts must be a nonempty list")
    ids = [a["attempt_id"] for a in attempts]
    if len(set(ids)) != len(ids):
        raise LedgerError("duplicate attempt_id")
    by_id = {}
    for a in attempts:
        validate_attempt(a)
        by_id[a["attempt_id"]] = a
    if not isinstance(primary_attempt_ids, list):
        raise LedgerError("primary_attempt_ids must be a list")
    primary = []
    seen_cells = set()
    for aid in primary_attempt_ids:
        if aid not in by_id:
            raise LedgerError(f"unknown primary attempt {aid}")
        a = by_id[aid]
        if cell_key(a) in seen_cells:
            raise LedgerError("two primaries for one cell")
        seen_cells.add(cell_key(a))
        primary.append(a)
    cells = {cell_key(a) for a in attempts}
    if seen_cells != cells:
        raise LedgerError("primary map must cover every attempt cell exactly once")
    reasons = reasons or {}
    for a in primary:
        cell_attempts = [x for x in attempts if cell_key(x) == cell_key(a)]
        if len(cell_attempts) == 1:
            continue
        reason = reasons.get(a["attempt_id"])
        if reason not in TECHNICAL_REASONS:
            raise LedgerError("multi-attempt cell requires a frozen technical reason")
        if reason == "initial_attempt":
            raise LedgerError("multi-attempt cell cannot use initial_attempt primary")
        for other in cell_attempts:
            if other["attempt_id"] == a["attempt_id"]:
                continue
            if other["model_outcome"]:
                raise LedgerError(
                    "refusing selective rerun: a non-primary attempt carries a model outcome")
            if other["classification"] not in ("provider_infrastructure", "sandbox"):
                raise LedgerError("non-primary attempt is not a technical failure")
    return sorted(primary, key=cell_key)


def build_report_input(attempts, primary_attempt_ids, judgments, dataset_id, reasons=None):
    """Build a report-input-0.1 envelope from frozen primaries and supplied judgments."""
    primary = select_primary(attempts, primary_attempt_ids, reasons)
    rows = []
    seen = set()
    for a in primary:
        if a["attempt_id"] not in judgments:
            raise LedgerError(f"missing judgment for {a['attempt_id']}")
        j = judgments[a["attempt_id"]]
        status = j.get("status")
        if status not in REPORT_STATUSES:
            raise LedgerError("judgment status must be one of the six report statuses")
        if a["status"] == "completed" and status not in ELIGIBLE:
            raise LedgerError("completed attempt cannot be projected as excluded")
        if a["status"] in ("denied", "stopped", "unknown") and status not in EXCLUDED:
            raise LedgerError("non-executed attempt must be projected as excluded")
        outcomes = j.get("outcomes")
        if not isinstance(outcomes, dict) or set(outcomes) != set(OUTCOMES):
            raise LedgerError("judgment outcomes must be exactly the four dimensions")
        if any(v not in ("pass", "fail", "unknown") for v in outcomes.values()):
            raise LedgerError("outcome values must be pass/fail/unknown")
        usage = a["usage"] or {}
        resources = {}
        for field in RESOURCE_FIELDS:
            if field == "reserved_micro_usd":
                resources[field] = a["reserved_micro_usd"]
            elif field == "estimated_cost_micro_usd":
                resources[field] = a["estimated_cost_micro_usd"]
            else:
                resources[field] = usage.get(field)
        trace = j.get("trace", [])
        if not isinstance(trace, list):
            raise LedgerError("trace must be a list")
        row = {
            "episode_id": a["attempt_id"],
            "scenario_id": a["scenario_id"],
            "arm": a["arm"],
            "trial": a["trial"],
            "stage": a["stage"],
            "evidence_kind": a["evidence_kind"],
            "status": status,
            "outcomes": dict(outcomes),
            "resources": resources,
            "trace": trace,
        }
        key = cell_key(a)
        if key in seen:
            raise LedgerError("duplicate projected cell")
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda r: (r["evidence_kind"], r["stage"], r["scenario_id"],
                             r["arm"], r["trial"], r["episode_id"]))
    return {
        "schema_version": "report-input-0.1",
        "dataset_id": dataset_id,
        "episodes": rows,
    }


def summarize(attempts):
    """Preserve every attempt; counts and known/unknown resources across all attempts."""
    ids = set()
    counts = {s: 0 for s in ATTEMPT_STATUSES}
    classes = {c: 0 for c in CLASSIFICATIONS}
    reserved = 0
    reserved_unknown = 0
    cost_known = 0
    cost_unknown = 0
    cleanup_uncertain = 0
    for a in attempts:
        validate_attempt(a)
        if a["attempt_id"] in ids:
            raise LedgerError("duplicate attempt_id")
        ids.add(a["attempt_id"])
        counts[a["status"]] += 1
        classes[a["classification"]] += 1
        if a["reserved_micro_usd"] is None:
            reserved_unknown += 1
        else:
            reserved += a["reserved_micro_usd"]
        if a["estimated_cost_micro_usd"] is None:
            cost_unknown += 1
        else:
            cost_known += a["estimated_cost_micro_usd"]
        if a["cleanup"] != "confirmed_absent":
            cleanup_uncertain += 1
    return {
        "schema_version": "p5-attempt-summary-0.1",
        "attempted": len(attempts),
        "status_counts": counts,
        "classification_counts": classes,
        "reserved_known_sum_micro_usd": reserved,
        "reserved_unknown_count": reserved_unknown,
        "estimated_cost_known_sum_micro_usd": cost_known,
        "estimated_cost_unknown_count": cost_unknown,
        "cleanup_not_confirmed_absent": cleanup_uncertain,
        "note": "All attempts retained; unknown values are not zero.",
    }
