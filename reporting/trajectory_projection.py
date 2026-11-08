"""Runtime checkpoint receipts -> trajectory-input-0.1 projection.

Consumes explicitly ordered, supplied independent checkpoint-grade receipts and
produces the accepted trajectory projection.  It does not authenticate receipts
or infer missing checkpoints; a receipt without a semantic grader outcome becomes
an ungraded (null) snapshot, not a pass or fail.

Pending P4.1.6 runtime integration/review; no I/O and no execution on import.
"""

from __future__ import annotations

from reporting import outcomes

SCHEMA_VERSION = "trajectory-input-0.1"
# Trajectory grading provenance is a separate axis from report row provenance:
# protected_container_grading joins synthetic_development/real_provider report
# rows only by episode_id, and the two kinds must never be pooled.
EVIDENCE_KINDS = ("synthetic_development", "protected_container_grading")
VERSIONS = ("v1", "v2")
MAX_SAFE_INT = 9_007_199_254_740_991


class TrajectoryProjectionError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _counter(value):
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > MAX_SAFE_INT:
        raise TrajectoryProjectionError("invalid_counter")
    return value


def _received_grade(receipt, evidence_kind):
    """Return a trajectory grade object, or None when no semantic grade exists.

    Delegates to the accepted outcome rule so candidate/grader failures become
    ``fail``; provider/infrastructure/unknown-origin failures and cleanup-
    unknown receipts remain ungraded. Protected checkpoint receipts are grader-
    origin by construction; an omitted origin defaults to that contract.
    """
    if receipt.get("evidence_kind") != evidence_kind:
        raise TrajectoryProjectionError("mixed_provenance")
    cleanup = receipt.get("cleanup")
    if cleanup != "settled":
        return None
    grade = {
        "status": receipt.get("status"),
        "functional_pass": receipt.get("functional_pass"),
        "cleanup": cleanup,
        "settlement": receipt.get("settlement", "known"),
        "origin": receipt.get("origin") or "grader",
        "errors": receipt.get("errors", []),
    }
    outcome = outcomes.functional_from_grade(grade)
    if outcome not in (outcomes.PASS, outcomes.FAIL):
        return None
    return {
        "candidate_sha256": receipt["candidate_sha256"],
        "requirements_version": receipt["requirements_version"],
        "outcome": outcome,
        "evidence_kind": evidence_kind,
    }


def project_trajectory(
    episode_id,
    evidence_kind,
    checkpoint_receipts,
    *,
    resources_by_checkpoint=None,
    source=None,
):
    """Project ordered checkpoint receipts; no sorting or missing-state guessing.

    ``resources_by_checkpoint`` maps checkpoint_id -> {elapsed_ms,
    model_responses}.  A receipt's checkpoint_id must be present and unique.
    ``source`` binds candidate/version identity for the binding metadata.
    """
    if evidence_kind not in EVIDENCE_KINDS:
        raise TrajectoryProjectionError("invalid_evidence_kind")
    if not isinstance(checkpoint_receipts, list):
        raise TrajectoryProjectionError("invalid_checkpoints")
    resources_by_checkpoint = resources_by_checkpoint or {}
    snapshots = []
    seen = set()
    ordered_ids = []
    for index, receipt in enumerate(checkpoint_receipts, start=1):
        if not isinstance(receipt, dict):
            raise TrajectoryProjectionError("invalid_receipt")
        checkpoint_id = receipt.get("checkpoint_id")
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise TrajectoryProjectionError("missing_checkpoint_id")
        if checkpoint_id in seen:
            raise TrajectoryProjectionError("duplicate_checkpoint")
        seen.add(checkpoint_id)
        ordered_ids.append(checkpoint_id)
        candidate = receipt.get("candidate_sha256")
        version = receipt.get("requirements_version")
        if not isinstance(candidate, str) or not isinstance(version, str):
            raise TrajectoryProjectionError("identity_missing")
        if version not in VERSIONS:
            raise TrajectoryProjectionError("invalid_version")
        if (
            receipt.get("evidence_kind") is not None
            and receipt.get("evidence_kind") != evidence_kind
        ):
            raise TrajectoryProjectionError("mixed_provenance")
        resources = resources_by_checkpoint.get(checkpoint_id, {})
        if not isinstance(resources, dict):
            raise TrajectoryProjectionError("invalid_resources")
        grade = _received_grade(receipt, evidence_kind)
        snapshots.append(
            {
                "checkpoint_id": checkpoint_id,
                "seq": index,
                "candidate_sha256": candidate,
                "requirements_version": version,
                "elapsed_ms": _counter(resources.get("elapsed_ms")),
                "model_responses": _counter(resources.get("model_responses")),
                "grade": grade,
            }
        )
    binding = {
        "episode_id": episode_id,
        "evidence_kind": evidence_kind,
        "checkpoint_count": len(snapshots),
        "checkpoint_ids": ordered_ids,
        "source": dict(source or {}),
        "order": "supplied_order_preserved_no_sorting",
    }
    return (
        {
            "schema_version": SCHEMA_VERSION,
            "episode_id": episode_id,
            "evidence_kind": evidence_kind,
            "snapshots": snapshots,
        },
        binding,
    )


__all__ = [
    "SCHEMA_VERSION", "TrajectoryProjectionError", "project_trajectory",
]
