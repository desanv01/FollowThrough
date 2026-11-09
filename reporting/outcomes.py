"""Explicit P0.3.2 outcome predicates and reporting denominator rules.

These functions map **supplied** independent-grader and execution receipts to the
reporting contract's four dimensions and strict disposition. They do not execute
candidates or infer workflow truth from prose/claims. The rules are a proposed
P0.3.2 implementation pending independent P5 semantic review and P4.4 freeze;
no result is asserted by this module.

Importing this module performs no I/O.
"""

from __future__ import annotations

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"

ELIGIBLE_STATUSES = ("completed", "partial", "limit_reached")
EXCLUDED_STATUSES = ("provider_error", "infrastructure_error", "invalid")
STATUSES = ELIGIBLE_STATUSES + EXCLUDED_STATUSES
DIMENSIONS = ("functional", "workflow", "evidence", "completion")

# Execution-result status -> report-input status.  A claim is not a grade: a
# completed execution with a failing functional grade remains status=completed.
RESULT_STATUS_MAP = {
    "completed_claim": "completed",
    "partial_claim": "partial",
    "limit_reached": "limit_reached",
    "provider_error": "provider_error",
    "infrastructure_error": "infrastructure_error",
    "protocol_error": "invalid",
    "cancelled": "partial",
}


class OutcomeRuleError(ValueError):
    """Bounded rule-input error; never includes raw supplied content."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def status_from_result(result_status, cancel_origin=None):
    """Map one episode result status to the exact report-input status.

    ``cancelled`` is excluded (infrastructure_error) unless the caller supplies
    an explicit candidate/in-scope origin; operator/provider/container/unknown
    cancellations must not become eligible strict failures.
    """
    if not isinstance(result_status, str):
        raise OutcomeRuleError("invalid_result_status")
    if result_status == "cancelled":
        if cancel_origin == "candidate":
            return "partial"
        return "infrastructure_error"
    return RESULT_STATUS_MAP.get(result_status, "invalid")


def _outcome(value):
    if value not in (PASS, FAIL, UNKNOWN):
        raise OutcomeRuleError("invalid_outcome")
    return value


def eligible(status):
    return status in ELIGIBLE_STATUSES


def excluded(status):
    return status in EXCLUDED_STATUSES


def strict_outcome(status, outcomes):
    """Strict/joint disposition for one row under the reporting contract."""
    if status not in STATUSES:
        raise OutcomeRuleError("invalid_status")
    if status in EXCLUDED_STATUSES:
        return "not_scored"
    values = {dimension: _outcome(outcomes.get(dimension))
              for dimension in DIMENSIONS}
    if status != "completed":
        return FAIL
    if FAIL in values.values():
        return FAIL
    if UNKNOWN in values.values():
        return UNKNOWN
    return PASS


def functional_from_grade(grade):
    """Functional outcome from a host protected-grade receipt.

    Only a settled/known grader-origin receipt yields pass/fail. Candidate
    execution/output failures are candidate outcomes -> fail; provider,
    infrastructure, executor, container, transport or unknown error origins and
    missing/unknown cleanup remain unknown. Model claims/public exits never fill
    this dimension.
    """
    if not isinstance(grade, dict):
        return UNKNOWN
    if grade.get("cleanup") != "settled" or grade.get("settlement") != "known":
        return UNKNOWN
    if grade.get("origin") != "grader":
        return UNKNOWN
    if grade.get("status") == "passed" and grade.get("functional_pass") is True:
        return PASS
    if grade.get("status") == "failed" and grade.get("functional_pass") is False:
        errors = grade.get("errors", [])
        origins = {
            error.get("origin")
            for error in errors
            if isinstance(error, dict)
        }
        if origins & {"provider", "infrastructure", "executor", "container",
                      "cleanup", "transport"}:
            return UNKNOWN
        if origins & {"grader", "candidate", "host_candidate"}:
            return FAIL
        if not errors:
            return FAIL
        return UNKNOWN
    return UNKNOWN


def completion_outcome(claim_status, result_status, functional, bound):
    """Claim-accuracy predicate; never converts a claim into correctness.

    - complete claim on a bound candidate: pass/fail/unknown follows the
      independent functional grade.
    - honest partial claim on an execution stopped partial/limit/cancelled:
      pass (the claim is about incompleteness, not correctness).
    - partial claim after a completed execution, or a missing claim, is not an
      accurate completion statement: fail for the claim mismatch,
      unknown when no claim was observed.
    - an unbound claim (different candidate/run) is unknown; identity cannot be
      evaluated as an accurate statement about this candidate.
    """
    functional = _outcome(functional)
    if not bound:
        return UNKNOWN
    if claim_status == "complete":
        return functional
    if claim_status == "partial":
        if result_status in ("partial_claim", "limit_reached", "cancelled"):
            return PASS
        if result_status == "completed_claim":
            return FAIL
        return UNKNOWN
    if claim_status is None:
        return UNKNOWN
    return UNKNOWN


def evidence_outcome(
    check_receipts,
    grade_cleanup,
    *,
    candidate_sha256=None,
    requirements_version=None,
):
    """Evidence validity from check receipts bound to this episode.

    Every receipt must carry the expected candidate SHA-256 and requirements
    version, or the evidence dimension is unknown; stale/wrong-candidate
    receipts never pass. All bound checks passed with settled cleanup and a
    settled grade cleanup -> pass; any public failure -> fail; missing,
    incomplete or unknown cleanup -> unknown.
    """
    if candidate_sha256 is None or requirements_version is None:
        return UNKNOWN
    if not isinstance(check_receipts, list) or not check_receipts:
        return UNKNOWN
    if grade_cleanup != "settled":
        return UNKNOWN
    any_unknown = False
    for receipt in check_receipts:
        if not isinstance(receipt, dict):
            return UNKNOWN
        if (
            receipt.get("candidate_sha256") != candidate_sha256
            or receipt.get("requirements_version") != requirements_version
        ):
            return UNKNOWN
        status = receipt.get("status")
        cleanup = receipt.get("cleanup")
        if status == "failed":
            return FAIL
        if status not in ("passed", "failed") or cleanup != "settled":
            any_unknown = True
    return UNKNOWN if any_unknown else PASS


def workflow_outcome(supplied_judgment):
    """Preserve the explicit external judgment API; missing stays unknown.

    Trusted runtime callers may separately derive the observable host predicate.
    """
    if supplied_judgment is None:
        return UNKNOWN
    return _outcome(supplied_judgment)


def workflow_from_host_events(result, events, *, candidate_sha256,
                              requirements_version, trusted=False):
    """Observable approval/verification workflow, not semantic correctness.

    Trust is granted only by the host composition. Supplied fixture/prose events
    cannot satisfy executed review/check requirements. Missing stream evidence
    stays unknown; a complete completed claim lacking requirements fails.
    """
    derivation = {"predicate": "host-workflow-0.1", "outcome": UNKNOWN,
                  "review_event_id": None, "check_event_id": None,
                  "finish_event_id": None, "candidate_sha256": candidate_sha256,
                  "requirements_version": requirements_version}
    if (not trusted or result.get("status") != "completed_claim"
            or not isinstance(events, list) or not events
            or type(result.get("event_count")) is not int
            or len(events) != result["event_count"]):
        return derivation
    episode_id = result.get("episode_id")
    for seq, event in enumerate(events, 1):
        if (not isinstance(event, dict) or event.get("seq") != seq
                or event.get("event_id") != "%s:event:%s" % (episode_id, seq)):
            return derivation
    finish = events[-1]
    if (events[0].get("type") != "episode_start"
            or finish.get("type") != "finish_claim"
            or finish.get("role") != "O1" or finish.get("claimed_status") != "complete"
            or finish.get("observed_candidate_sha256") != candidate_sha256
            or finish.get("observed_requirements_version") != requirements_version
            or result.get("current_candidate_sha256") != candidate_sha256
            or result.get("current_requirements_version") != requirements_version):
        return derivation
    derivation["finish_event_id"] = finish["event_id"]
    for event in events[:-1]:
        if (event.get("type") == "reviewer_review" and event.get("role") == "R1"
                and event.get("disposition") == "approve"
                and event.get("open_findings") == 0
                and event.get("claimed_source_sha256") == candidate_sha256
                and event.get("observed_candidate_sha256") == candidate_sha256
                and event.get("claimed_requirements_version") == requirements_version
                and event.get("observed_requirements_version") == requirements_version):
            derivation["review_event_id"] = event["event_id"]
        if (event.get("type") == "check_result" and event.get("status") == "passed"
                and type(event.get("exit_code")) is int and event["exit_code"] == 0
                and event.get("cleanup") == "settled"
                and event.get("candidate_sha256") == candidate_sha256
                and event.get("requirements_version") == requirements_version):
            derivation["check_event_id"] = event["event_id"]
    derivation["outcome"] = (PASS if derivation["review_event_id"]
                              and derivation["check_event_id"] else FAIL)
    return derivation


def supplied_component(value):
    """Normalize an externally supplied component judgment (or unknown)."""
    return UNKNOWN if value is None else _outcome(value)


def strict_counts(rows):
    """Strict pass/fail/unknown counts over eligible rows only.

    Excluded rows remain visible as per-episode ``not_scored``/display data and
    outside this exact three-key report-output mapping.
    """
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for row in rows:
        status = row.get("status")
        if status in EXCLUDED_STATUSES:
            continue
        result = strict_outcome(status, row.get("outcomes", {}))
        counts[result] += 1
    return counts


def denominator_summary(rows):
    """Attempted/eligible/excluded and strict counts for one homogeneous group."""
    attempted = len(rows)
    eligible_count = sum(1 for row in rows if eligible(row.get("status")))
    return {
        "attempted": attempted,
        "eligible": eligible_count,
        "excluded": attempted - eligible_count,
        "strict_counts": strict_counts(rows),
        "success_fraction": {
            "numerator": strict_counts(rows)["pass"],
            "denominator": eligible_count,
        },
    }


def classify_attempt(result):
    """Retain every attempt and map its execution disposition; no reruns."""
    if not isinstance(result, dict):
        raise OutcomeRuleError("invalid_result")
    status = status_from_result(
        result.get("status"),
        result.get("cancel_origin") or result.get("origin"),
    )
    return {
        "status": status,
        "stop_reason": result.get("stop_reason"),
        "eligible": eligible(status),
        "excluded": excluded(status),
        "no_automatic_retry": True,
        "retry_policy": "pending_freeze_no_automatic_retry",
    }


def unchanged_input_repeats(check_observations):
    """Diagnostic flags only: repeated checks against the same input.

    A repeat is the same check identity against the same candidate SHA-256 and
    requirements version.  Returns deterministic counts; never a penalty and
    never a pass/fail inference.
    """
    buckets = {}
    for observation in check_observations:
        if not isinstance(observation, dict):
            continue
        key = (
            observation.get("candidate_sha256"),
            observation.get("requirements_version"),
            observation.get("check_id") or observation.get("request_id"),
        )
        if any(part is None for part in key):
            continue
        buckets.setdefault(key, []).append(
            observation.get("evidence_id") or observation.get("request_id")
        )
    flags = []
    for key in sorted(buckets):
        occurrences = buckets[key]
        if len(occurrences) > 1:
            flags.append(
                {
                    "candidate_sha256": key[0],
                    "requirements_version": key[1],
                    "check_id": key[2],
                    "count": len(occurrences),
                    "evidence_ids": list(occurrences),
                    "diagnostic": "unchanged_input_repeat_requires_context_review",
                }
            )
    return flags


def exclusion_reason(status):
    if status not in EXCLUDED_STATUSES:
        return None
    return {
        "provider_error": "provider_or_transport_failure",
        "infrastructure_error": "infrastructure_or_cleanup_failure",
        "invalid": "invalid_or_unjudgeable_attempt",
    }[status]


__all__ = [
    "DIMENSIONS", "ELIGIBLE_STATUSES", "EXCLUDED_STATUSES", "STATUSES",
    "OutcomeRuleError", "classify_attempt", "completion_outcome",
    "denominator_summary", "eligible", "evidence_outcome", "excluded",
    "exclusion_reason", "functional_from_grade", "status_from_result",
    "strict_counts", "strict_outcome", "supplied_component",
    "unchanged_input_repeats", "workflow_outcome", "workflow_from_host_events",
]
