"""Runtime result/grade -> report-input-0.1 projection.

This module binds an episode result to the exact source, requirements version,
run and independent grade before producing a report row.  It does not execute
anything, call a provider or authenticate receipts.  Missing identity makes the
row ineligible (invalid) rather than guess success; execution disposition is
kept separate from claim accuracy and independent grade.

Proposed for P4.1.1/P4.1.5; pending local integration, independent review and
Main acceptance.  Importing performs no I/O.
"""

from __future__ import annotations

import hashlib
import json
import re

from reporting import outcomes

SCHEMA_VERSION = "report-input-0.1"
BINDING_SCHEMA = "episode-binding-0.1"
EVIDENCE_KINDS = ("synthetic_development", "real_provider")
STAGES = ("development", "pilot", "formal")
SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
ARMS = ("ordinary", "structured_progress")
TRACE_ROLES = ("O1", "W1", "R1", "controller")
TRACE_KINDS = ("supplied_fixture", "message", "edit", "check", "review",
               "requirement_update", "completion")
MAX_SAFE_INT = 9_007_199_254_740_991
MAX_TRACE = 1000
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class ProjectionError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _sha(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _safe_int(value, *, allow_none=True):
    if value is None and allow_none:
        return None
    if type(value) is not int or value < 0 or value > MAX_SAFE_INT:
        raise ProjectionError("invalid_resource")
    return value


def _text(value, limit=256):
    text = "" if value is None else str(value)
    out = []
    for character in text:
        code = ord(character)
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            out.append(" ")
        else:
            out.append(character)
    return "".join(out)[:limit]


def _usage_sum(result, key):
    usage = result.get("usage", {}).get(key, {})
    if not isinstance(usage, dict) or usage.get("known_count", 0) <= 0:
        return None
    return _safe_int(usage.get("known_sum"))


def _identifier(value, code):
    if (
        not isinstance(value, str)
        or _IDENTIFIER_RE.fullmatch(value) is None
    ):
        raise ProjectionError(code)
    return value


def _check_receipts(events):
    receipts = []
    for event in events:
        if isinstance(event, dict) and event.get("type") == "check_result":
            receipts.append(
                {
                    "status": event.get("status"),
                    "cleanup": event.get("cleanup"),
                    "candidate_sha256": event.get("candidate_sha256"),
                    "requirements_version": event.get("requirements_version"),
                }
            )
    return receipts


_EVENT_KIND = {
    "model_response": "message",
    "read_result": "message",
    "write_result": "edit",
    "check_result": "check",
    "check_error": "check",
    "reviewer_review": "review",
    "requirements_update": "requirement_update",
    "finish_claim": "completion",
    "episode_start": "supplied_fixture",
    "supplied_receipt": "supplied_fixture",
}
_EVENT_ROLE = {"O1": "O1", "W1": "W1", "R1": "R1", "host": "controller",
               "controller": "controller"}


def _trace_from_events(events):
    """Bounded safe display trace: type/role/status labels, never raw content."""
    trace = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            continue
        role = _EVENT_ROLE.get(event.get("role"), "controller")
        kind = _EVENT_KIND.get(event_type, "message")
        detail = event_type
        if event.get("status") is not None:
            detail += " status=" + _text(event.get("status"), 32)
        if event.get("action") is not None:
            detail += " action=" + _text(event.get("action"), 32)
        trace.append(
            {
                "seq": len(trace) + 1,
                "role": role,
                "kind": kind,
                "summary": _text(detail, 256),
            }
        )
        if len(trace) >= MAX_TRACE:
            break
    return trace


def _claim_status(result):
    final_claim = result.get("final_claim")
    if not isinstance(final_claim, dict):
        return None
    return final_claim.get("claimed_status")


def _binding(result, grade, *, source, operation=None):
    result_candidate = result.get("current_candidate_sha256")
    result_version = result.get("current_requirements_version")
    grade_candidate = grade.get("candidate_sha256") if isinstance(grade, dict) else None
    grade_version = grade.get("requirements_version") if isinstance(grade, dict) else None
    source_candidate = source.get("candidate_sha256")
    source_version = source.get("requirements_version")
    bound = (
        isinstance(result_candidate, str)
        and isinstance(result_version, str)
        and isinstance(grade_candidate, str)
        and isinstance(grade_version, str)
        and isinstance(source_candidate, str)
        and isinstance(source_version, str)
        and result_candidate == grade_candidate == source_candidate
        and result_version == grade_version == source_version
    )
    operation = operation if isinstance(operation, dict) else {}
    run_id = operation.get("run_id") or result.get("run_id")
    operation_id = operation.get("operation_id") or result.get("operation_id")
    return {
        "schema_version": BINDING_SCHEMA,
        "episode_id": result.get("episode_id"),
        "run_id": run_id if isinstance(run_id, str) else None,
        "operation_id": operation_id if isinstance(operation_id, str) else None,
        "candidate_sha256": source_candidate,
        "current_candidate_sha256": result_candidate,
        "grade_candidate_sha256": grade_candidate,
        "requirements_version": source_version,
        "current_requirements_version": result_version,
        "grade_requirements_version": grade_version,
        "binding_match": bool(bound),
    }


def project_episode(
    result,
    events,
    grade,
    *,
    source,
    evidence_kind,
    stage,
    trial=1,
    workflow_judgment=None,
    operation=None,
):
    """Build one exact report-input row and its binding record.

    ``source`` binds candidate SHA-256 + requirements version for the actual
    source tree.  ``operation`` may provide ``estimated_cost_micro_usd`` and
    ``reserved_micro_usd``; absent amounts stay null.  No raw event content is
    copied into the display trace.
    """
    if not isinstance(result, dict):
        raise ProjectionError("invalid_result")
    if evidence_kind not in EVIDENCE_KINDS or stage not in STAGES:
        raise ProjectionError("invalid_provenance")
    if evidence_kind == "synthetic_development" and stage != "development":
        raise ProjectionError("invalid_provenance")
    if not isinstance(events, list):
        raise ProjectionError("invalid_events")
    episode_id = _identifier(result.get("episode_id"), "invalid_episode_id")
    scenario_id = result.get("scenario_id")
    if scenario_id not in SCENARIOS:
        raise ProjectionError("invalid_scenario")
    arm = result.get("arm")
    if arm not in ARMS:
        raise ProjectionError("invalid_arm")
    if source is None:
        source = {}
    binding = _binding(result, grade, source=source, operation=operation)
    status = outcomes.status_from_result(
        result.get("status"),
        result.get("cancel_origin") or result.get("origin"),
    )
    if not binding["binding_match"]:
        status = "invalid"

    functional = (
        outcomes.functional_from_grade(grade)
        if binding["binding_match"]
        else outcomes.UNKNOWN
    )
    workflow = outcomes.workflow_outcome(workflow_judgment)
    evidence = outcomes.evidence_outcome(
        _check_receipts(events),
        grade.get("cleanup") if isinstance(grade, dict) else None,
        candidate_sha256=source.get("candidate_sha256"),
        requirements_version=source.get("requirements_version"),
    )
    completion = outcomes.completion_outcome(
        _claim_status(result),
        result.get("status"),
        functional,
        binding["binding_match"],
    )
    if status == "invalid":
        functional = workflow = evidence = completion = outcomes.UNKNOWN

    counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    if result.get("elapsed_ms") is not None:
        elapsed_ms = _safe_int(result.get("elapsed_ms"))
    elif isinstance(operation, dict) and operation.get("elapsed_ms") is not None:
        elapsed_ms = _safe_int(operation.get("elapsed_ms"))
    elif events:
        elapsed_ms = _safe_int(events[-1].get("elapsed_ms"))
    else:
        elapsed_ms = None
    resources = {
        "model_responses": _safe_int(counts.get("model_responses_total")),
        "tool_actions": _safe_int(counts.get("executed_actions")),
        "input_tokens": _usage_sum(result, "input_tokens"),
        "output_tokens": _usage_sum(result, "output_tokens"),
        "elapsed_ms": elapsed_ms,
        "estimated_cost_micro_usd": (
            _safe_int(operation.get("estimated_cost_micro_usd"))
            if isinstance(operation, dict) else None
        ),
        "reserved_micro_usd": (
            _safe_int(operation.get("reserved_micro_usd"))
            if isinstance(operation, dict) else None
        ),
    }
    row = {
        "episode_id": episode_id,
        "scenario_id": scenario_id,
        "arm": arm,
        "trial": _safe_int(trial, allow_none=False),
        "stage": stage,
        "evidence_kind": evidence_kind,
        "status": status,
        "outcomes": {
            "functional": functional,
            "workflow": workflow,
            "evidence": evidence,
            "completion": completion,
        },
        "resources": resources,
        "trace": _trace_from_events(events),
    }
    return row, binding


def project_unscored_episode(result, events, *, source, evidence_kind, stage,
                             trial=1, operation=None, reason):
    """Project an episode with no protected grade (e.g. unknown cleanup).

    The row is excluded as ``infrastructure_error`` with all dimensions unknown;
    it never invents a grade or converts an infrastructure stop into a score.
    """
    if not isinstance(result, dict):
        raise ProjectionError("invalid_result")
    if evidence_kind not in EVIDENCE_KINDS or stage not in STAGES:
        raise ProjectionError("invalid_provenance")
    if evidence_kind == "synthetic_development" and stage != "development":
        raise ProjectionError("invalid_provenance")
    if not isinstance(events, list):
        raise ProjectionError("invalid_events")
    episode_id = _identifier(result.get("episode_id"), "invalid_episode_id")
    scenario_id = result.get("scenario_id")
    if scenario_id not in SCENARIOS:
        raise ProjectionError("invalid_scenario")
    arm = result.get("arm")
    if arm not in ARMS:
        raise ProjectionError("invalid_arm")
    source = source if isinstance(source, dict) else {}
    operation = operation if isinstance(operation, dict) else {}
    counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    if result.get("elapsed_ms") is not None:
        elapsed_ms = _safe_int(result.get("elapsed_ms"))
    elif operation.get("elapsed_ms") is not None:
        elapsed_ms = _safe_int(operation.get("elapsed_ms"))
    elif events:
        elapsed_ms = _safe_int(events[-1].get("elapsed_ms"))
    else:
        elapsed_ms = None
    binding = {
        "schema_version": BINDING_SCHEMA,
        "episode_id": result.get("episode_id"),
        "run_id": operation.get("run_id") if isinstance(operation.get("run_id"), str) else None,
        "operation_id": operation.get("operation_id")
        if isinstance(operation.get("operation_id"), str) else None,
        "candidate_sha256": source.get("candidate_sha256"),
        "current_candidate_sha256": result.get("current_candidate_sha256"),
        "requirements_version": source.get("requirements_version"),
        "current_requirements_version": result.get("current_requirements_version"),
        "grade_candidate_sha256": None,
        "grade_requirements_version": None,
        "binding_match": False,
        "grade_skipped_reason": _text(reason, 128),
    }
    row = {
        "episode_id": episode_id,
        "scenario_id": scenario_id,
        "arm": arm,
        "trial": _safe_int(trial, allow_none=False),
        "stage": stage,
        "evidence_kind": evidence_kind,
        "status": "infrastructure_error",
        "outcomes": {
            "functional": outcomes.UNKNOWN,
            "workflow": outcomes.UNKNOWN,
            "evidence": outcomes.UNKNOWN,
            "completion": outcomes.UNKNOWN,
        },
        "resources": {
            "model_responses": _safe_int(counts.get("model_responses_total")),
            "tool_actions": _safe_int(counts.get("executed_actions")),
            "input_tokens": _usage_sum(result, "input_tokens"),
            "output_tokens": _usage_sum(result, "output_tokens"),
            "elapsed_ms": elapsed_ms,
            "estimated_cost_micro_usd": _safe_int(operation.get("estimated_cost_micro_usd")),
            "reserved_micro_usd": _safe_int(operation.get("reserved_micro_usd")),
        },
        "trace": _trace_from_events(events),
    }
    return row, binding


def project_report_input(dataset_id, episodes):
    """Wrap projected rows in the exact report-input-0.1 envelope."""
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "episodes": list(episodes),
    }


def row_digest(row):
    return _sha(json.dumps(row, ensure_ascii=False, sort_keys=True))


__all__ = [
    "BINDING_SCHEMA", "ProjectionError", "project_episode",
    "project_report_input", "project_unscored_episode", "row_digest",
]
