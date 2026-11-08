"""Pure checkpoint trajectory summaries for explicitly supplied grade records.

``summarize_trajectory(data)`` validates a parsed trajectory-input projection and
returns deterministic segments.  It performs no I/O, executes nothing and does
not authenticate the caller: the grade objects are supplied trusted-grader
records, not model claims or public-check receipts.

Contract: P4-TRAJECTORY/r1.  This is a trajectory subcomponent; it adds no
headline score, causal conclusion, check penalty or change to the report-input
projection.  Importing this module performs no I/O.
"""

import re

SCHEMA_INPUT = "trajectory-input-0.1"
SCHEMA_OUTPUT = "trajectory-output-0.1"
EVIDENCE_KINDS = ("synthetic_development", "protected_container_grading")
REQUIREMENTS_VERSIONS = ("v1", "v2")
OUTCOMES = ("pass", "fail", "unknown")
MAX_SNAPSHOTS = 1000
MAX_SAFE_INT = 9_007_199_254_740_991

TOP_LEVEL_KEYS = ("schema_version", "episode_id", "evidence_kind", "snapshots")
SNAPSHOT_KEYS = (
    "checkpoint_id",
    "seq",
    "candidate_sha256",
    "requirements_version",
    "elapsed_ms",
    "model_responses",
    "grade",
)
GRADE_KEYS = (
    "candidate_sha256",
    "requirements_version",
    "outcome",
    "evidence_kind",
)

_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class TrajectoryError(ValueError):
    """Bounded trajectory-input error with a stable ``code`` attribute.

    Codes: unsupported_schema, invalid_input, duplicate_checkpoint,
    invalid_sequence, identity_mismatch, mixed_provenance, input_too_large.
    Messages are fixed and never echo supplied identifiers, hashes or grades.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise TrajectoryError(code, message)


def _check_object(value, keys, code="invalid_input"):
    if not isinstance(value, dict) or set(value.keys()) != set(keys):
        _fail(code, "trajectory input does not match the contract")
    return value


def _check_enum(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        _fail("invalid_input", "trajectory input does not match the contract")
    return value


def _check_identifier(value):
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail("invalid_input", "trajectory input does not match the contract")
    return value


def _check_sha256(value):
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail("invalid_input", "trajectory input does not match the contract")
    return value


def _check_text(value):
    if not isinstance(value, str) or not value:
        _fail("invalid_input", "trajectory input does not match the contract")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("invalid_input", "trajectory input does not match the contract")
    return value


def _check_header_id(value):
    # The packet fixes checkpoint-ID uniqueness but not a narrower character
    # grammar; a nonempty, UTF-8-encodable string is retained as supplied.
    return _check_text(value)


def _check_counter(value):
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > MAX_SAFE_INT:
        _fail("invalid_input", "trajectory input does not match the contract")
    return value


def _check_seq(value):
    if type(value) is not int or value <= 0:
        _fail("invalid_sequence", "seq values must be strictly increasing positive integers")
    return value


def _check_grade(raw, snapshot, evidence_kind):
    if raw is None:
        return None
    _check_object(raw, GRADE_KEYS)
    candidate_sha256 = _check_sha256(raw["candidate_sha256"])
    requirements_version = _check_enum(
        raw["requirements_version"], REQUIREMENTS_VERSIONS
    )
    outcome = _check_enum(raw["outcome"], OUTCOMES)
    grade_evidence_kind = _check_enum(raw["evidence_kind"], EVIDENCE_KINDS)
    if grade_evidence_kind != evidence_kind:
        _fail(
            "mixed_provenance",
            "grade evidence_kind does not match the envelope",
        )
    if (
        candidate_sha256 != snapshot["candidate_sha256"]
        or requirements_version != snapshot["requirements_version"]
    ):
        _fail(
            "identity_mismatch",
            "grade identity/version does not match its snapshot",
        )
    return {
        "candidate_sha256": candidate_sha256,
        "requirements_version": requirements_version,
        "outcome": outcome,
        "evidence_kind": grade_evidence_kind,
    }


def _validate(data):
    if not isinstance(data, dict):
        _fail("invalid_input", "trajectory input does not match the contract")
    if "schema_version" in data:
        version = data["schema_version"]
        if not isinstance(version, str):
            _fail("invalid_input", "trajectory input does not match the contract")
        if version != SCHEMA_INPUT:
            _fail(
                "unsupported_schema",
                "schema_version is not trajectory-input-0.1",
            )
    _check_object(data, TOP_LEVEL_KEYS)
    episode_id = _check_identifier(data["episode_id"])
    evidence_kind = _check_enum(data["evidence_kind"], EVIDENCE_KINDS)
    raw_snapshots = data["snapshots"]
    if not isinstance(raw_snapshots, list):
        _fail("invalid_input", "trajectory input does not match the contract")
    if len(raw_snapshots) > MAX_SNAPSHOTS:
        _fail("input_too_large", "more than 1000 trajectory snapshots")

    snapshots = []
    seen_checkpoints = set()
    previous_seq = None
    for raw in raw_snapshots:
        _check_object(raw, SNAPSHOT_KEYS)
        checkpoint_id = _check_header_id(raw["checkpoint_id"])
        seq = _check_seq(raw["seq"])
        if checkpoint_id in seen_checkpoints:
            _fail("duplicate_checkpoint", "duplicate checkpoint_id")
        if previous_seq is not None and seq <= previous_seq:
            _fail(
                "invalid_sequence",
                "seq values must be strictly increasing positive integers",
            )
        seen_checkpoints.add(checkpoint_id)
        previous_seq = seq

        candidate_sha256 = _check_sha256(raw["candidate_sha256"])
        requirements_version = _check_enum(
            raw["requirements_version"], REQUIREMENTS_VERSIONS
        )
        snapshot = {
            "checkpoint_id": checkpoint_id,
            "seq": seq,
            "candidate_sha256": candidate_sha256,
            "requirements_version": requirements_version,
            "elapsed_ms": _check_counter(raw["elapsed_ms"]),
            "model_responses": _check_counter(raw["model_responses"]),
            "grade": None,
        }
        snapshot["grade"] = _check_grade(
            raw["grade"], snapshot, evidence_kind
        )
        snapshots.append(snapshot)
    return {
        "episode_id": episode_id,
        "evidence_kind": evidence_kind,
        "snapshots": snapshots,
    }


def _segment():
    return {
        "requirements_version": None,
        "start_seq": None,
        "end_seq": None,
        "snapshot_count": 0,
        "known_pass": 0,
        "known_fail": 0,
        "unknown": 0,
        "first_observed_correct": None,
        "observed_regressions": [],
    }


def _summarize(validated):
    segments = []
    current = None
    current_version = None
    previous_outcome = None
    previous_checkpoint_id = None
    previous_candidate_sha256 = None
    for snapshot in validated["snapshots"]:
        version = snapshot["requirements_version"]
        if current is None or version != current_version:
            # A version change ends the previous maximal run; a later recurring
            # version starts another segment rather than bridging.
            current = _segment()
            current["requirements_version"] = version
            current["start_seq"] = snapshot["seq"]
            segments.append(current)
            current_version = version
            previous_outcome = None

        current["end_seq"] = snapshot["seq"]
        current["snapshot_count"] += 1
        grade = snapshot["grade"]
        outcome = grade["outcome"] if grade is not None else None
        if outcome is None or outcome == "unknown":
            current["unknown"] += 1
        elif outcome == "pass":
            current["known_pass"] += 1
            if current["first_observed_correct"] is None:
                current["first_observed_correct"] = {
                    "checkpoint_id": snapshot["checkpoint_id"],
                    "seq": snapshot["seq"],
                    "elapsed_ms": snapshot["elapsed_ms"],
                    "model_responses": snapshot["model_responses"],
                }
        else:
            current["known_fail"] += 1

        # Only two consecutive snapshots in this same segment with grade pass
        # then fail form a confirmed regression.  Unknown/null, version changes
        # and any intervening grade interrupt this inference.
        if previous_outcome == "pass" and outcome == "fail":
            current["observed_regressions"].append(
                {
                    "from_checkpoint_id": previous_checkpoint_id,
                    "from_candidate_sha256": previous_candidate_sha256,
                    "to_checkpoint_id": snapshot["checkpoint_id"],
                    "to_candidate_sha256": snapshot["candidate_sha256"],
                }
            )
        previous_outcome = outcome
        previous_checkpoint_id = snapshot["checkpoint_id"]
        previous_candidate_sha256 = snapshot["candidate_sha256"]

    return {
        "schema_version": SCHEMA_OUTPUT,
        "episode_id": validated["episode_id"],
        "evidence_kind": validated["evidence_kind"],
        "snapshots": validated["snapshots"],
        "segments": segments,
    }


def summarize_trajectory(data):
    """Validate a parsed trajectory-input object and return its summary.

    No I/O and no execution.  Raises TrajectoryError with a stable bounded code
    for invalid input.
    """
    return _summarize(_validate(data))


__all__ = [
    "MAX_SAFE_INT",
    "MAX_SNAPSHOTS",
    "SCHEMA_INPUT",
    "SCHEMA_OUTPUT",
    "TrajectoryError",
    "summarize_trajectory",
]
