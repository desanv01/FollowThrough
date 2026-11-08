"""Validate P3 attempt-receipt-0.1 evidence before P5 ledger/projection use.

The validator checks schema, identity, reservation/cleanup/grade completeness and
rejects secret-like or host-path content. It never executes a provider call and
never treats a receipt as a result; unknown fields remain unknown.
"""
from __future__ import annotations

import hashlib
import json
import re

SCHEMA = "attempt-receipt-0.1"
VALIDATION_SCHEMA = "p5-receipt-validation-0.1"
STAGES = ("development", "pilot", "formal", "r1")
SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
ARMS = ("ordinary", "structured_progress")
CLASSIFICATIONS = ("model", "provider", "infrastructure", "operator", "unknown", None)
SETTLEMENTS = ("settled", "uncertain", "unknown")
CLEANUP = ("settled", "unknown", "failed")
GRADE_KINDS = ("protected_container_grading", "synthetic_development", None)
OUTCOMES = ("functional", "workflow", "evidence", "completion")
OUTCOME_VALUES = ("pass", "fail", "unknown")
ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
HOST_RE = re.compile(r"(^[A-Za-z]:[\\/]|^/|(^|[\\/])\.coordination([\\/]|$)|\x00)")
FORBIDDEN_KEYS = ("credential", "secret", "api_key", "apikey", "password", "prompt",
                  "raw", "stdout", "stderr", "authorization", "api-key")


class ReceiptError(ValueError):
    """Unreadable or structurally impossible receipt."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _is_int(v):
    return type(v) is int


def _id(value, name, errors):
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        errors.append(f"{name}:invalid_identifier")
    return value


def _walk_keys(obj, errors, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            if any(token in lowered for token in FORBIDDEN_KEYS):
                errors.append(f"{path}/{key}:forbidden_secret_or_raw_key")
            _walk_keys(value, errors, path + "/" + str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _walk_keys(value, errors, f"{path}[{i}]")


def _walk_host_paths(obj, errors, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            _walk_host_paths(value, errors, path + "/" + str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _walk_host_paths(value, errors, f"{path}[{i}]")
    elif isinstance(obj, str) and HOST_RE.search(obj):
        errors.append(f"{path}:absolute_or_private_path")


def _dict(obj, name, keys, errors):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        errors.append(f"{name}:exact_keys_required")
        return {}
    return obj


def _check(receipt):
    errors = []
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt_not_object")
    top = ("schema_version", "attempt_id", "run_id", "campaign_id", "episode_id", "stage",
           "scenario_id", "arm", "trial", "dataset_id", "requested_model",
           "returned_model", "candidate_sha256", "requirements_version", "dispatch",
           "bounds", "budget", "usage", "cleanup", "grade", "trace_ref",
           "retained_unknowns")
    if set(receipt) != set(top):
        errors.append("receipt:exact_keys_required")
    if receipt.get("schema_version") != SCHEMA:
        errors.append("schema_version:unsupported")
    for key in ("attempt_id", "campaign_id", "episode_id", "dataset_id"):
        _id(receipt.get(key), key, errors)
    run_id = receipt.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
        errors.append("run_id:invalid")
    if receipt.get("stage") not in STAGES:
        errors.append("stage:invalid")
    if receipt.get("scenario_id") not in SCENARIOS:
        errors.append("scenario_id:invalid")
    if receipt.get("arm") not in ARMS:
        errors.append("arm:invalid")
    if not _is_int(receipt.get("trial")) or receipt["trial"] < 1:
        errors.append("trial:invalid")
    if not isinstance(receipt.get("requested_model"), str) or not receipt["requested_model"]:
        errors.append("requested_model:invalid")
    if receipt.get("returned_model") is not None and not isinstance(receipt["returned_model"], str):
        errors.append("returned_model:invalid")
    candidate = receipt.get("candidate_sha256")
    if candidate is not None and (not isinstance(candidate, str) or SHA_RE.fullmatch(candidate) is None):
        errors.append("candidate_sha256:invalid")
    if receipt.get("requirements_version") not in ("v1", "v2", None):
        errors.append("requirements_version:invalid")

    dispatch = _dict(receipt.get("dispatch"), "dispatch",
                     ("attempted", "stop_status", "classification", "outcome_unknown"), errors)
    if not isinstance(dispatch.get("attempted"), bool):
        errors.append("dispatch.attempted:invalid")
    if dispatch.get("stop_status") is not None and not isinstance(dispatch["stop_status"], str):
        errors.append("dispatch.stop_status:invalid")
    if dispatch.get("classification") not in CLASSIFICATIONS:
        errors.append("dispatch.classification:invalid")
    if not isinstance(dispatch.get("outcome_unknown"), bool):
        errors.append("dispatch.outcome_unknown:invalid")

    bounds = _dict(receipt.get("bounds"), "bounds",
                   ("max_calls", "max_output_tokens_per_call", "max_call_seconds",
                    "max_call_reservation_micro_usd", "max_operation_reservation_micro_usd",
                    "max_wall_seconds", "call_count_used", "wall_seconds_used"), errors)
    for key in ("max_calls", "max_output_tokens_per_call", "max_call_seconds",
                "max_call_reservation_micro_usd", "max_operation_reservation_micro_usd",
                "max_wall_seconds", "call_count_used"):
        if not _is_int(bounds.get(key)) or bounds.get(key, -1) < 0:
            errors.append(f"bounds.{key}:invalid")
    if bounds.get("wall_seconds_used") is not None and not isinstance(bounds["wall_seconds_used"], (int, float)):
        errors.append("bounds.wall_seconds_used:invalid")

    budget = _dict(receipt.get("budget"), "budget",
                   ("operation_ids", "reserved_micro_usd", "estimated_cost_micro_usd",
                    "settlement", "remaining_balance_micro_usd"), errors)
    ops = budget.get("operation_ids")
    if not isinstance(ops, list) or any(not isinstance(x, str) or not x for x in ops):
        errors.append("budget.operation_ids:invalid")
    else:
        if len(set(ops)) != len(ops):
            errors.append("budget.operation_ids:duplicate")
    for key in ("reserved_micro_usd", "estimated_cost_micro_usd", "remaining_balance_micro_usd"):
        if budget.get(key) is not None and (not _is_int(budget[key]) or budget[key] < 0):
            errors.append(f"budget.{key}:invalid")
    if budget.get("settlement") not in SETTLEMENTS:
        errors.append("budget.settlement:invalid")

    usage = _dict(receipt.get("usage"), "usage",
                  ("model_responses", "tool_actions", "input_tokens", "output_tokens",
                   "cache_hit_input_tokens", "uncached_input_tokens", "reasoning_tokens",
                   "known_counts", "unknown_counts", "elapsed_seconds"), errors)
    for key in ("model_responses", "tool_actions", "input_tokens", "output_tokens",
                "cache_hit_input_tokens", "uncached_input_tokens", "reasoning_tokens"):
        if usage.get(key) is not None and (not _is_int(usage[key]) or usage[key] < 0):
            errors.append(f"usage.{key}:invalid")
    for key in ("known_counts", "unknown_counts"):
        if not isinstance(usage.get(key), dict) or any(
                not isinstance(k, str) or k not in (
                    "model_responses", "tool_actions", "input_tokens", "output_tokens",
                    "cache_hit_input_tokens", "uncached_input_tokens", "reasoning_tokens")
                or not _is_int(v) or v < 0 for k, v in usage[key].items()):
            errors.append(f"usage.{key}:invalid")
    if usage.get("elapsed_seconds") is not None and not isinstance(usage["elapsed_seconds"], (int, float)):
        errors.append("usage.elapsed_seconds:invalid")

    cleanup = _dict(receipt.get("cleanup"), "cleanup",
                    ("readback", "source", "container_or_run_absent"), errors)
    if cleanup.get("readback") not in CLEANUP:
        errors.append("cleanup.readback:invalid")
    if not isinstance(cleanup.get("source"), str):
        errors.append("cleanup.source:invalid")
    elif not cleanup["source"].strip():
        errors.append("cleanup.source:empty")
    if cleanup.get("container_or_run_absent") not in (True, False, None):
        errors.append("cleanup.container_or_run_absent:invalid")

    grade = _dict(receipt.get("grade"), "grade",
                  ("receipt_id", "evidence_kind", "outcome_dimensions", "functional_pass"), errors)
    if grade.get("receipt_id") is not None and not isinstance(grade["receipt_id"], str):
        errors.append("grade.receipt_id:invalid")
    if grade.get("evidence_kind") not in GRADE_KINDS:
        errors.append("grade.evidence_kind:invalid")
    dims = grade.get("outcome_dimensions")
    if dims is not None:
        if not isinstance(dims, dict) or set(dims) != set(OUTCOMES) or any(
                v not in OUTCOME_VALUES for v in dims.values()):
            errors.append("grade.outcome_dimensions:invalid")
    if grade.get("functional_pass") not in (True, False, None):
        errors.append("grade.functional_pass:invalid")
    if dims is not None and isinstance(grade.get("functional_pass"), bool):
        expected = "pass" if grade["functional_pass"] else "fail"
        if dims.get("functional") != expected:
            errors.append("grade.functional_consistency:inconsistent")

    trace_ref = receipt.get("trace_ref")
    if trace_ref is not None and (not isinstance(trace_ref, str) or HOST_RE.search(trace_ref)):
        errors.append("trace_ref:invalid_or_absolute")
    unknowns = receipt.get("retained_unknowns")
    if not isinstance(unknowns, list) or any(not isinstance(x, str) for x in unknowns):
        errors.append("retained_unknowns:invalid")

    _walk_keys(receipt, errors)
    _walk_host_paths(receipt, errors)

    binding_ok = (
        isinstance(candidate, str) and receipt.get("requirements_version") in ("v1", "v2")
        and isinstance(dispatch, dict) and dispatch.get("classification") is not None
    )
    ledger_ready = (
        not errors and binding_ok and bool(ops) and isinstance(run_id, str) and bool(run_id.strip())
        and cleanup.get("readback") == "settled"
        and budget.get("settlement") == "settled"
    )
    report_ready = (
        ledger_ready and dims is not None
        and grade.get("evidence_kind") in ("protected_container_grading", "synthetic_development")
        and isinstance(grade.get("functional_pass"), bool)
    )
    return {
        "schema_version": VALIDATION_SCHEMA,
        "valid": not errors,
        "errors": errors,
        "attempt_id": receipt.get("attempt_id"),
        "episode_id": receipt.get("episode_id"),
        "binding_ok": binding_ok,
        "ledger_ready": ledger_ready,
        "report_ready": report_ready,
        "retained": True,
        "receipt_digest": hashlib.sha256(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def validate_receipt(receipt):
    """Validate without short-circuiting; returns a bounded result dictionary."""
    return _check(receipt)
