"""Host-authoritative protected Retry grading.

The candidate runs only in a separate container with the candidate file and a
minimal execution shim. Expected outcomes and scoring live only in this host
process. Candidate-visible observations are data; a malformed or erroring candidate
never becomes a pass. Cache v1/v2 and checkpoint semantics live in
``episode.protected_cache`` and are graded by the same host-authority boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from episode import check_receipt as _unused  # noqa: F401  (package import parity)

PROTECTED_SCHEMA = "protected-grade-receipt-0.1"
REQUEST_VERSION = "protected-grade-request-0.1"
OBSERVATION_SCHEMA = "retry-protected-observations-0.1"
IMAGE = "python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3"
OUTPUT_LIMIT = 65536
DEFAULT_TIMEOUT = 60
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_RUN_RE = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")

# Finite exchange: no expected result appears here.
_OPERATIONS = [
    {"kind": "event", "item": {"tenant": "ledger", "event_id": "open",
                               "units": (1 << 80) + 53}},
    {"kind": "event", "item": {"tenant": "ledger", "event_id": "next", "units": 1}},
    {"kind": "event", "item": {"tenant": "ledger", "event_id": "open",
                               "units": (1 << 80) + 53}},
    {"kind": "event", "item": {"tenant": "ledger", "event_id": "open", "units": 29}},
    {"kind": "event", "item": None},
    {"kind": "batch", "items": [
        {"tenant": "jade", "event_id": "x", "units": 13},
        {"tenant": "jade", "event_id": "malformed", "units": 0},
        {"tenant": "jade", "event_id": "x", "units": 27},
        None,
        {"tenant": "jade", "event_id": "x", "units": 13},
    ]},
    {"kind": "event", "item": {"tenant": "moss", "event_id": "pending", "units": 41},
     "fault": "after_credit"},
    {"kind": "event", "item": {"tenant": "moss", "event_id": "pending", "units": 41}},
]


def _hex(value):
    return value.encode("utf-8", errors="surrogatepass").hex()


def _db(totals, completed):
    return {
        "totals": sorted([[ _hex(tenant), format(units, "x")] for tenant, units in totals.items()]),
        "completed": sorted([
            [_hex(tenant), _hex(event_id), format(units, "x")]
            for (tenant, event_id), units in completed.items()
        ]),
    }


LARGE = (1 << 80) + 53
_EXPECTED = [
    {"status": "applied", "fault": None, "lookup": 1,
     "db": _db({"ledger": LARGE}, {("ledger", "open"): LARGE})},
    {"status": "applied", "fault": None, "lookup": 1,
     "db": _db({"ledger": LARGE + 1},
               {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"status": "duplicate", "fault": None, "lookup": 1,
     "db": _db({"ledger": LARGE + 1},
               {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"status": "conflict", "fault": None, "lookup": 1,
     "db": _db({"ledger": LARGE + 1},
               {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"status": "invalid", "fault": None, "lookup": 0,
     "db": _db({"ledger": LARGE + 1},
               {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"results": ["applied", "invalid", "conflict", "invalid", "duplicate"],
     "fault": None, "lookup": 3,
     "db": _db({"ledger": LARGE + 1, "jade": 13},
               {("ledger", "open"): LARGE, ("ledger", "next"): 1,
                ("jade", "x"): 13})},
    {"status": None, "fault": True, "lookup": 1,
     "db": _db({"ledger": LARGE + 1, "jade": 13},
               {("ledger", "open"): LARGE, ("ledger", "next"): 1,
                ("jade", "x"): 13})},
    {"status": "applied", "fault": None, "lookup": 1,
     "db": _db({"ledger": LARGE + 1, "jade": 13, "moss": 41},
               {("ledger", "open"): LARGE, ("ledger", "next"): 1,
                ("jade", "x"): 13, ("moss", "pending"): 41})},
]

CHECK_IDS = ["new_delivery", "second_delivery", "duplicate", "conflict",
             "malformed", "mixed_batch", "rollback_after_credit",
             "retry_after_fault"]


class ProtectedGradeError(Exception):
    """Fixed host error code; never contains raw candidate output or paths."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass
class GradeRunnerResult:
    stdout: bytes
    cleanup: str
    timed_out: bool = False
    spawn_failed: bool = False
    isolation: dict | None = None
    error: str | None = None


def build_exchange():
    return {"schema_version": "retry-protected-exchange-0.1",
            "operations": json.loads(json.dumps(_OPERATIONS))}


def expected_observations(candidate_sha256):
    """Host-only expected observation document (never mounted to the candidate)."""
    operations = []
    for index, (expected, operation) in enumerate(zip(_EXPECTED, _OPERATIONS)):
        item = {
            "index": index,
            "kind": operation["kind"],
            "fault_propagated": expected["fault"],
            "lookup_count": expected["lookup"],
            "db": expected["db"],
        }
        if "results" in expected:
            item["results"] = list(expected["results"])
        else:
            item["status"] = expected["status"]
        operations.append(item)
    return {"schema_version": OBSERVATION_SCHEMA,
            "candidate_sha256": candidate_sha256,
            "operations": operations}


def _compare(operation, expected, index):
    check = {"id": CHECK_IDS[index], "passed": False,
             "detail": "observation mismatch"}
    if type(operation) is not dict:
        check["detail"] = "observation not an object"
        return check
    if operation.get("candidate_error"):
        check["detail"] = "candidate execution error"
        return check
    if "results" in expected:
        if operation.get("results") != expected["results"]:
            return check
    else:
        if operation.get("status") != expected["status"]:
            return check
    if operation.get("fault_propagated") != expected["fault"]:
        return check
    if operation.get("lookup_count") != expected["lookup"]:
        return check
    if operation.get("db") != expected["db"]:
        return check
    check["passed"] = True
    check["detail"] = "expected observation"
    return check


def adjudicate(candidate_sha256, observations, *, evidence_kind, cleanup="settled"):
    """Pure host adjudication of a candidate observation document.

    This legacy observation path is synthetic/rehearsal only. The protected label
    requires the qualified host session in ``episode.protected_session``; a caller
    cannot label reference-injected expected observations as a protected result.
    """
    if evidence_kind == "protected_container_grading":
        raise ProtectedGradeError("protected_evidence_requires_qualified_runner")
    if evidence_kind != "synthetic_development":
        raise ProtectedGradeError("invalid_evidence_kind")
    receipt = {
        "schema_version": PROTECTED_SCHEMA,
        "task_family": "retry-processing",
        "candidate_filename": "processor.py",
        "candidate_sha256": candidate_sha256,
        "requirements_version": "v1",
        "grade_kind": "final",
        "checkpoint_id": None,
        "evidence_kind": evidence_kind,
        "origin": "grader",
        "status": "infrastructure_error",
        "functional_pass": None,
        "exit_code": None,
        "checks": [],
        "errors": [],
        "stdout": "",
        "stderr": "",
        "cleanup": cleanup,
        "settlement": "unknown" if cleanup == "unknown" else "known",
        "limitations": [],
    }
    if cleanup == "unknown":
        receipt["limitations"].append("cleanup_unknown_forbids_semantic_outcome")
        return receipt
    if type(observations) is not dict:
        receipt["status"] = "infrastructure_error"
        receipt["errors"].append({"origin": "executor", "phase": "observations",
                                  "type": "Malformed", "detail": "not an object"})
        return receipt
    if observations.get("schema_version") != OBSERVATION_SCHEMA:
        receipt["status"] = "infrastructure_error"
        receipt["errors"].append({"origin": "executor", "phase": "observations",
                                  "type": "Malformed", "detail": "schema mismatch"})
        return receipt
    if observations.get("candidate_sha256") != candidate_sha256:
        receipt["status"] = "infrastructure_error"
        receipt["errors"].append({"origin": "executor", "phase": "identity",
                                  "type": "Mismatch", "detail": "candidate hash mismatch"})
        return receipt
    if observations.get("load_error"):
        receipt["status"] = "candidate_error"
        receipt["origin"] = "candidate"
        receipt["errors"].append({"origin": "candidate", "phase": "load",
                                  "type": str(observations["load_error"])[:64],
                                  "detail": "candidate import failed"})
        return receipt
    operations = observations.get("operations")
    if type(operations) is not list or len(operations) != len(_EXPECTED):
        receipt["status"] = "infrastructure_error"
        receipt["errors"].append({"origin": "executor", "phase": "observations",
                                  "type": "Malformed", "detail": "operation count"})
        return receipt
    checks = []
    candidate_error = False
    for index, (operation, expected) in enumerate(zip(operations, _EXPECTED)):
        if type(operation) is dict and operation.get("candidate_error"):
            candidate_error = True
            receipt["errors"].append({"origin": "candidate", "phase": "invoke",
                                      "type": str(operation["candidate_error"])[:64],
                                      "detail": str(operation.get("detail", ""))[:512],
                                      "check_id": CHECK_IDS[index]})
        checks.append(_compare(operation, expected, index))
    receipt["checks"] = checks
    if candidate_error:
        receipt["status"] = "candidate_error"
        receipt["origin"] = "candidate"
        return receipt
    if all(check["passed"] for check in checks):
        receipt["status"] = "passed"
        receipt["functional_pass"] = True
        receipt["origin"] = "grader"
    else:
        receipt["status"] = "failed"
        receipt["functional_pass"] = False
        receipt["origin"] = "grader"
        receipt["errors"].append({"origin": "grader", "phase": "adjudication",
                                  "type": "CheckFailure",
                                  "detail": "one or more checks failed"})
    return receipt


def trajectory_outcome(receipt):
    if receipt.get("status") == "passed" and receipt.get("functional_pass") is True:
        return "pass"
    if (receipt.get("status") == "failed" and receipt.get("functional_pass") is False
            and all(error.get("origin") == "grader" for error in receipt.get("errors", []))):
        return "fail"
    return "unknown"


def _trajectory_projection(receipt):
    return {
        "candidate_sha256": receipt["candidate_sha256"],
        "requirements_version": receipt["requirements_version"],
        "outcome": trajectory_outcome(receipt),
        "evidence_kind": receipt["evidence_kind"],
    }


class DockerCandidateRunner:
    """Real candidate-container runner. Requires qualified Docker access.

    Candidate and shim are mounted read-only; no grader, references, project root,
    credentials or Docker socket. Cleanup is attempted only by exact owned name.
    """

    def __init__(self, docker, image=IMAGE, timeout=DEFAULT_TIMEOUT):
        self.docker = Path(docker)
        self.image = image
        self.timeout = timeout

    def __call__(self, candidate, exchange, shim_dir, run_id):
        if not _RUN_RE.fullmatch(run_id):
            raise ProtectedGradeError("invalid_run_id")
        container = "ft-grade-" + run_id
        argv = [
            str(self.docker), "run", "--rm", "--pull=never", "--name", container,
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=256m", "--cpus=1", "--user=65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--workdir", "/work",
            "--mount", f"type=bind,src={candidate},dst=/candidate/processor.py,readonly",
            "--mount", f"type=bind,src={shim_dir},dst=/opt/shim,readonly",
            "--mount", f"type=bind,src={exchange},dst=/exchange/exchange.json,readonly",
            "--entrypoint", "python", self.image,
            "-I", "-B", "/opt/shim/retry_shim.py",
            "/exchange/exchange.json", "/candidate/processor.py",
        ]
        try:
            completed = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=self.timeout,
                env={k: v for k, v in os.environ.items()
                     if k in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")},
                check=False,
            )
        except subprocess.TimeoutExpired:
            return GradeRunnerResult(b"", "unknown", timed_out=True)
        except OSError:
            return GradeRunnerResult(b"", "not_created", spawn_failed=True,
                                     error="docker_unavailable")
        cleanup = "settled"
        try:
            listing = subprocess.run(
                [str(self.docker), "ps", "-a", "--quiet", "--filter",
                 f"name={container}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=5, check=False,
            )
            if listing.returncode != 0 or listing.stdout.strip():
                cleanup = "unknown"
        except (OSError, subprocess.TimeoutExpired):
            cleanup = "unknown"
        if completed.returncode != 0:
            cleanup = "unknown" if cleanup == "settled" else cleanup
        return GradeRunnerResult(
            completed.stdout[-OUTPUT_LIMIT:], cleanup,
            isolation={
                "network": "none", "read_only_root": True, "cap_drop": "ALL",
                "user": "65534:65534", "mounts": ["candidate", "shim", "exchange"],
                "witness": "runtime_restrictions_declared_not_independently_verified",
            },
            error=None if completed.returncode == 0 else "candidate_container_failed",
        )


def grade_with_runner(
    candidate_path,
    runner,
    *,
    run_id,
    requirements_version="v1",
    checkpoint_id=None,
    evidence_kind="protected_container_grading",
):
    """Deprecated synthetic test harness; protected results require the host session."""
    raise ProtectedGradeError("legacy_observation_runner_cannot_grade_protected")


def _error_receipt(digest, status, origin, detail, cleanup, checkpoint_id,
                   evidence_kind):
    return {
        "schema_version": PROTECTED_SCHEMA,
        "task_family": "retry-processing",
        "candidate_filename": "processor.py",
        "candidate_sha256": digest,
        "requirements_version": "v1",
        "grade_kind": "checkpoint" if checkpoint_id else "final",
        "checkpoint_id": checkpoint_id,
        "evidence_kind": evidence_kind,
        "origin": origin,
        "status": status,
        "functional_pass": None,
        "exit_code": None,
        "checks": [],
        "errors": [{"origin": origin, "phase": "execution", "type": "Error",
                    "detail": detail}],
        "stdout": "",
        "stderr": "",
        "cleanup": cleanup,
        "settlement": "unknown" if cleanup == "unknown" else "known",
        "limitations": ["no_semantic_outcome"],
    }


__all__ = [
    "CHECK_IDS", "DockerCandidateRunner", "GradeRunnerResult", "IMAGE",
    "PROTECTED_SCHEMA", "ProtectedGradeError", "adjudicate", "build_exchange",
    "grade_with_runner", "trajectory_outcome", "_trajectory_projection",
]
