"""Guarded R1 live composition for the accepted provider, ledger and loop.

This is the P3-owned paid entry. It composes, without reimplementing them:

- the accepted credential loader and durable ``BudgetedDeepSeekClient`` ledger guard;
- the P3 ``BudgetedModelPort`` JSON-action bridge, frozen dispatch deadline and
  per-call cap;
- an operation guard for the authorized R1 envelope (16 calls, 2048 output tokens,
  25 s per call, US$0.05 reserved per call, US$0.80 operation reservation, 900 s
  wall including cleanup);
- an injected P2 check port and host grade function, and an injected P4 episode
  runner (default: ``episode.e2e.run_r1_smoke``).

Nothing here launches an executor or provider on import. Real dispatch requires
both explicit authorization flags, an existing canonical ledger, and all executor
component paths; otherwise it refuses before any credential or provider work. The
canonical ledger is never created implicitly: only the explicit reviewed
``initialize-ledger`` action may initialize an absent canonical path, with the
fixed campaign ID and US$20 ceiling.

No retries, parallel model calls, alternate ledgers, raw provider output or secrets
are exposed. Failures are printed as fixed codes only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from adapters.budget import BudgetError, CampaignLedger
from adapters.budgeted_deepseek import BudgetedDeepSeekClient, BudgetedDeepSeekError
from adapters.credentials import load_deepseek_key
from episode.provider import BudgetedModelPort, ModelPortError


LEDGER_RELATIVE = Path(".coordination") / "provider-budget.sqlite3"
MAX_CALLS = 16
MAX_OUTPUT_TOKENS = 2048
MAX_CALL_SECONDS = 25
MAX_CALL_COST_USD = Decimal("0.05")
MAX_OPERATION_COST_USD = Decimal("0.80")
MAX_WALL_SECONDS = 900.0
CAP_USD = "20"
MICRO = Decimal("0.000001")
_EPISODE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class LiveGateError(Exception):
    """Fixed live-gate refusal; no provider or credential detail."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LivePolicy:
    max_calls: int = MAX_CALLS
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    max_call_seconds: int = MAX_CALL_SECONDS
    max_call_cost_usd: Decimal = MAX_CALL_COST_USD
    max_operation_cost_usd: Decimal = MAX_OPERATION_COST_USD
    max_wall_seconds: float = MAX_WALL_SECONDS
    cap_usd: str = CAP_USD


def _ceil_micro(amount):
    return int(
        (Decimal(amount) / MICRO).to_integral_value(rounding=ROUND_CEILING)
    )


def _absolute(path):
    try:
        value = os.fspath(path)
    except TypeError:
        raise LiveGateError("invalid_path") from None
    if not value or "\x00" in value or not os.path.isabs(value):
        raise LiveGateError("invalid_path")
    return Path(os.path.abspath(value))


def check_ledger_path(project_root):
    project = _absolute(project_root)
    canonical = project / LEDGER_RELATIVE
    if canonical != Path(os.path.abspath(os.fspath(canonical))):
        raise LiveGateError("invalid_ledger_path")
    return canonical


class OperationGuard:
    """Bound one R1 operation over an already budget-guarded model port.

    The accepted wrapper owns the canonical reservation. This guard only counts
    attempts and reads the port's sanitized observations; it never reserves,
    releases or invents a ledger balance.
    """

    def __init__(self, model_port, *, policy=None, monotonic=None, operation=None):
        self._port = model_port
        self.policy = policy or LivePolicy()
        self._clock = time.monotonic if monotonic is None else monotonic
        self._started = self._clock()
        self._seen = 0
        self.calls = 0
        self.reserved_micro_usd = 0
        self.estimated_micro_usd = 0
        self._estimated_known = 0
        self._operation = operation if isinstance(operation, dict) else None
        self._per_call_micro = _ceil_micro(self.policy.max_call_cost_usd)
        self._operation_micro = _ceil_micro(self.policy.max_operation_cost_usd)
        self.requested_models = []
        self.returned_models = []

    @property
    def elapsed_seconds(self):
        return self._clock() - self._started

    def _account(self):
        observations = getattr(self._port, "observations", None)
        if type(observations) is not list:
            return
        while self._seen < len(observations):
            observation = observations[self._seen]
            self._seen += 1
            if type(observation) is not dict:
                continue
            reservation = observation.get("reservation_micro_usd")
            estimate = observation.get("estimated_cost_micro_usd")
            if type(reservation) is int:
                self.reserved_micro_usd += reservation
            if type(estimate) is int:
                self.estimated_micro_usd += estimate
                self._estimated_known += 1
            requested = observation.get("requested_model")
            returned = observation.get("returned_model")
            if type(requested) is str and requested not in self.requested_models:
                self.requested_models.append(requested)
            if type(returned) is str and returned not in self.returned_models:
                self.returned_models.append(returned)
        if self._operation is not None:
            self._operation["reserved_micro_usd"] = self.reserved_micro_usd
            self._operation["estimated_cost_micro_usd"] = (
                self.estimated_micro_usd if self._estimated_known else None
            )
            self._operation["call_count"] = self.calls
            self._operation["elapsed_ms"] = int(
                round(self.elapsed_seconds * 1000)
            )

    def __call__(self, request):
        if self.elapsed_seconds > self.policy.max_wall_seconds:
            raise LiveGateError("wall_time_exceeded")
        if self.calls >= self.policy.max_calls:
            raise LiveGateError("call_limit_exceeded")
        if (
            self.reserved_micro_usd + self._per_call_micro
            > self._operation_micro
        ):
            raise LiveGateError("operation_cost_exceeded")
        self.calls += 1
        try:
            response = self._port(request)
        finally:
            self._account()
        usage = response.get("usage") if type(response) is dict else None
        output_tokens = usage.get("output_tokens") if type(usage) is dict else None
        if type(output_tokens) is int and output_tokens > self.policy.max_output_tokens:
            raise LiveGateError("output_token_exceeded")
        return response

    def summary(self):
        return {
            "calls": self.calls,
            "reserved_micro_usd": self.reserved_micro_usd,
            "estimated_micro_usd": self.estimated_micro_usd,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "requested_models": list(self.requested_models),
            "returned_models": list(self.returned_models),
            "policy": {
                "max_calls": self.policy.max_calls,
                "max_output_tokens": self.policy.max_output_tokens,
                "max_call_seconds": self.policy.max_call_seconds,
                "max_call_cost_usd": str(self.policy.max_call_cost_usd),
                "max_operation_cost_usd": str(self.policy.max_operation_cost_usd),
                "max_wall_seconds": self.policy.max_wall_seconds,
                "cap_usd": self.policy.cap_usd,
            },
        }


class CampaignLease:
    """Exclusive P2-authored Docker lock, shared with P3.

    The status file ``leases/docker.json`` is a handoff record, not the lock. The
    lock is the atomically-created ``lease/P2-AUTHORED-001/ACTIVE`` directory.
    P3 preserves the parent owner record and releases only its own settled lock.
    """

    def __init__(self, campaign_root, *, operation_id, run_id):
        self.campaign = _absolute(campaign_root)
        self.operation_id = operation_id
        self.run_id = run_id
        self.lease_root = self.campaign / "lease" / "P2-AUTHORED-001"
        self.active = self.lease_root / "ACTIVE"

    def acquire(self):
        owner_path = self.lease_root / "owner.json"
        try:
            parent = json.loads(owner_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise LiveGateError("lease_unavailable") from None
        if parent.get("owner") != "P2":
            raise LiveGateError("lease_unavailable")
        release_path = self.campaign / "leases" / "docker.json"
        try:
            release = (
                json.loads(release_path.read_text(encoding="utf-8-sig"))
                if release_path.is_file() else None
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise LiveGateError("lease_unavailable") from None
        if release is not None:
            if (
                release.get("status") != "RELEASED_AFTER_SETTLEMENT"
                or release.get("unknown_settlement") is not False
                or release.get("active_docker_operations") != 0
            ):
                raise LiveGateError("lease_unavailable")
        elif (
            parent.get("status") != "RELEASED_AFTER_SETTLEMENT"
            or parent.get("unknown_settlement") is not False
        ):
            raise LiveGateError("lease_unavailable")
        try:
            self.active.mkdir()
        except FileExistsError:
            raise LiveGateError("lease_unavailable") from None
        record = {
            "owner": "P3",
            "operation_id": self.operation_id,
            "run_id": self.run_id,
            "acquired_local": _now(),
            "source_commit": os.environ.get("P3_SOURCE_COMMIT"),
        }
        try:
            (self.active / "owner.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            readback = json.loads(
                (self.active / "owner.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            shutil.rmtree(self.active, ignore_errors=True)
            raise LiveGateError("lease_unavailable") from None
        if (
            readback.get("owner") != "P3"
            or readback.get("operation_id") != self.operation_id
            or readback.get("run_id") != self.run_id
        ):
            shutil.rmtree(self.active, ignore_errors=True)
            raise LiveGateError("lease_unavailable")
        return self

    def release(self, *, settled):
        try:
            record = json.loads(
                (self.active / "owner.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise LiveGateError("lease_release_refused") from None
        if (
            record.get("owner") != "P3"
            or record.get("operation_id") != self.operation_id
            or record.get("run_id") != self.run_id
        ):
            raise LiveGateError("lease_release_refused")
        if settled:
            shutil.rmtree(self.active)
            return True
        return False


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_int(value):
    return value if type(value) is int and value >= 0 else None


def _resources(row, usage):
    resources = row.get("resources") if isinstance(row, dict) else None
    resources = resources if isinstance(resources, dict) else {}
    return {
        "model_responses": _safe_int(resources.get("model_responses")),
        "tool_actions": _safe_int(resources.get("tool_actions")),
        "input_tokens": _safe_int(resources.get("input_tokens")),
        "output_tokens": _safe_int(resources.get("output_tokens")),
        "cache_hit_input_tokens": None,
        "uncached_input_tokens": None,
        "reasoning_tokens": None,
        "elapsed_ms": _safe_int(resources.get("elapsed_ms")),
    }


def _ledger_scope(operation, snapshot):
    entries = snapshot.get("operations") if isinstance(snapshot, dict) else []
    prefix = str(operation.get("episode_id", "")) + ":call:"
    scoped = [
        item for item in entries
        if isinstance(item, dict)
        and isinstance(item.get("operation_id"), str)
        and item["operation_id"].startswith(prefix)
    ]
    if not scoped and isinstance(snapshot, dict):
        # Rehearsal/fixture ledgers may use arbitrary operation IDs.
        scoped = [item for item in entries if isinstance(item, dict)]
    return scoped


def build_attempt_receipt(operation, *, result=None, usage=None,
                          ledger_snapshot=None, check_receipts=None,
                          stop_status=None, run_root=None, campaign_id=None):
    """Build one exact attempt-receipt-0.1 document from trusted observations."""
    result = result if isinstance(result, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    row = result.get("row") if isinstance(result.get("row"), dict) else {}
    grade = result.get("grade") if isinstance(result.get("grade"), dict) else {}
    binding = result.get("binding") if isinstance(result.get("binding"), dict) else {}
    snapshot = ledger_snapshot if isinstance(ledger_snapshot, dict) else {}
    entries = _ledger_scope(operation, snapshot)
    operation_ids = [
        item["operation_id"] for item in entries
        if isinstance(item.get("operation_id"), str)
    ]
    reserved = sum(
        item.get("amount_microdollars", 0) for item in entries
        if type(item.get("amount_microdollars")) is int
    ) if entries else 0
    estimates = [
        item.get("estimated_cost_microdollars") for item in entries
        if type(item.get("estimated_cost_microdollars")) is int
    ]
    statuses = {item.get("status") for item in entries}
    if not entries:
        settlement = "unknown"
    elif statuses & {"pending", "uncertain"}:
        settlement = "uncertain"
    else:
        settlement = "settled"
    candidate = (
        result.get("candidate_sha256")
        or binding.get("candidate_sha256")
        or grade.get("candidate_sha256")
    )
    requirements = (
        operation.get("requirements_version")
        or binding.get("requirements_version")
        or grade.get("requirements_version")
    )
    resources = _resources(row, usage)
    returned = None
    returned_models = usage.get("returned_models") if isinstance(usage, dict) else None
    if isinstance(returned_models, list) and returned_models:
        returned = returned_models[0] if isinstance(returned_models[0], str) else None
    elif isinstance(grade.get("returned_model"), str):
        returned = grade["returned_model"]
    requested = "deepseek-flash"
    requested_models = usage.get("requested_models") if isinstance(usage, dict) else None
    if isinstance(requested_models, list) and requested_models and isinstance(
        requested_models[0], str
    ):
        requested = requested_models[0]
    checks = check_receipts if isinstance(check_receipts, list) else []
    checks_settled = bool(checks) and all(
        isinstance(item, dict) and item.get("cleanup") == "settled"
        for item in checks
    )
    grade_settled = grade.get("cleanup") == "settled"
    cleanup_settled = checks_settled and grade_settled
    cleanup_readback = "settled" if cleanup_settled else "unknown"
    absent = None
    for observation in [*checks, grade]:
        if not isinstance(observation, dict):
            continue
        value = observation.get("container_or_run_absent")
        if isinstance(value, bool):
            absent = value if absent is None else (absent and value)
    attempted = bool(operation_ids) or bool(usage.get("calls"))
    if result and grade:
        classification = "model"
    elif isinstance(stop_status, str) and any(
        token in stop_status.lower()
        for token in ("provider", "transport", "timeout", "http", "rate")
    ):
        classification = "provider"
    else:
        classification = "infrastructure"
    unknown = (not cleanup_settled) or not bool(grade) or bool(stop_status)
    dims = None
    outcomes = row.get("outcomes") if isinstance(row.get("outcomes"), dict) else None
    if (
        isinstance(outcomes, dict)
        and set(outcomes) == {"functional", "workflow", "evidence", "completion"}
        and all(value in ("pass", "fail", "unknown") for value in outcomes.values())
    ):
        dims = {key: outcomes[key] for key in outcomes}
    grade_kind = grade.get("evidence_kind")
    if grade_kind not in ("protected_container_grading", "synthetic_development", None):
        grade_kind = None
    trace_ref = None
    if run_root is not None:
        root = Path(run_root)
        for name in ("progress-events.jsonl", "report.md", "episode-binding.json"):
            if (root / name).is_file():
                trace_ref = name
                break
    receipt_id = None
    if run_root is not None and (Path(run_root) / "protected-grade.json").is_file():
        receipt_id = "protected-grade.json"
    unknown_fields = [
        name for name, value in (
            ("returned_model", returned),
            ("candidate_sha256", candidate),
            ("requirements_version", requirements),
            ("cleanup", cleanup_readback if cleanup_settled else None),
        ) if value is None
    ]
    known = {}
    unknown_counts = {}
    for key in ("model_responses", "tool_actions", "input_tokens", "output_tokens"):
        if resources.get(key) is None:
            unknown_counts[key] = 1
        else:
            known[key] = 1
    for key in ("cache_hit_input_tokens", "uncached_input_tokens", "reasoning_tokens"):
        unknown_counts[key] = 1
    elapsed_seconds = None
    if isinstance(usage.get("elapsed_seconds"), (int, float)):
        elapsed_seconds = usage["elapsed_seconds"]
    elif resources.get("elapsed_ms") is not None:
        elapsed_seconds = resources["elapsed_ms"] / 1000.0
    return {
        "schema_version": "attempt-receipt-0.1",
        "attempt_id": operation["operation_id"],
        "run_id": operation["run_id"],
        "campaign_id": campaign_id or operation.get("campaign_id") or "followthrough-local-r1",
        "episode_id": operation["episode_id"],
        "stage": operation["stage"],
        "scenario_id": operation["scenario_id"],
        "arm": operation["arm"],
        "trial": operation["trial"],
        "dataset_id": operation["dataset_id"],
        "requested_model": requested,
        "returned_model": returned,
        "candidate_sha256": candidate if isinstance(candidate, str) else None,
        "requirements_version": requirements if requirements in ("v1", "v2") else None,
        "dispatch": {
            "attempted": attempted,
            "stop_status": stop_status if isinstance(stop_status, str) else None,
            "classification": classification if attempted else None,
            "outcome_unknown": bool(unknown),
        },
        "bounds": {
            "max_calls": MAX_CALLS,
            "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
            "max_call_seconds": MAX_CALL_SECONDS,
            "max_call_reservation_micro_usd": _ceil_micro(MAX_CALL_COST_USD),
            "max_operation_reservation_micro_usd": _ceil_micro(MAX_OPERATION_COST_USD),
            "max_wall_seconds": int(MAX_WALL_SECONDS),
            "call_count_used": _safe_int(usage.get("calls")) or 0,
            "wall_seconds_used": elapsed_seconds,
        },
        "budget": {
            "operation_ids": operation_ids,
            "reserved_micro_usd": reserved if entries else None,
            "estimated_cost_micro_usd": sum(estimates) if estimates else None,
            "settlement": settlement,
            "remaining_balance_micro_usd": _safe_int(
                snapshot.get("remaining_microdollars")
            ) if isinstance(snapshot, dict) else None,
        },
        "usage": {
            "model_responses": resources["model_responses"],
            "tool_actions": resources["tool_actions"],
            "input_tokens": resources["input_tokens"],
            "output_tokens": resources["output_tokens"],
            "cache_hit_input_tokens": None,
            "uncached_input_tokens": None,
            "reasoning_tokens": None,
            "known_counts": known,
            "unknown_counts": unknown_counts,
            "elapsed_seconds": elapsed_seconds,
        },
        "cleanup": {
            "readback": cleanup_readback,
            "source": "p2_check_and_grade",
            "container_or_run_absent": absent,
        },
        "grade": {
            "receipt_id": receipt_id,
            "evidence_kind": grade_kind,
            "outcome_dimensions": dims,
            "functional_pass": grade.get("functional_pass")
            if isinstance(grade.get("functional_pass"), bool) else None,
        },
        "trace_ref": trace_ref,
        "retained_unknowns": unknown_fields,
    }


def write_attempt_receipt(run_root, campaign_root, receipt):
    """Validate and atomically write the run and sanitized campaign copies."""
    from p5.p5_tools.receipts import validate_receipt

    validation = validate_receipt(receipt)
    if not validation.get("valid"):
        raise LiveGateError("attempt_receipt_invalid")
    run_root = Path(run_root)
    campaign_root = Path(campaign_root)
    run_root.mkdir(parents=True, exist_ok=True)
    for destination in (
        run_root / "attempt-receipt.json",
        campaign_root / "artifacts" / "attempts" / receipt["attempt_id"]
        / "attempt-receipt.json",
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(
            dir=str(destination.parent),
            prefix="." + destination.name + ".",
            suffix=".tmp",
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(receipt, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, destination)
    return validation



def initialize_ledger(project_root, campaign_id):
    """Reviewed first-use initialization; never replaces an existing ledger."""
    project = _absolute(project_root)
    canonical = check_ledger_path(project)
    if canonical.exists():
        raise LiveGateError("canonical_ledger_exists_refused")
    if (
        type(campaign_id) is not str
        or not campaign_id
        or len(campaign_id) > 128
    ):
        raise LiveGateError("invalid_campaign_id")
    CampaignLedger(str(project), campaign_id=campaign_id, cap_usd=CAP_USD)
    return canonical


def authorize(project_root, run_root, *, reviewed_live, runtime_authorized,
              ledger_path=None):
    project = _absolute(project_root)
    run = _absolute(run_root)
    if not reviewed_live:
        raise LiveGateError("live_dispatch_not_reviewed")
    if not runtime_authorized:
        raise LiveGateError("runtime_not_authorized")
    canonical = check_ledger_path(project)
    if ledger_path is not None and _absolute(ledger_path) != canonical:
        raise LiveGateError("alternate_ledger_refused")
    if not canonical.is_file():
        raise LiveGateError("canonical_ledger_missing_refused")
    if run.exists():
        raise LiveGateError("run_root_exists_refused")
    if not run.parent.is_dir():
        raise LiveGateError("run_parent_missing")
    return project, run, canonical


def build_provider(project_root, campaign_id, *, transport=None, environ=None):
    """Compose accepted loader + budgeted provider without dispatching."""
    canonical = check_ledger_path(project_root)
    if not canonical.is_file():
        raise LiveGateError("canonical_ledger_missing_refused")
    key = load_deepseek_key(str(project_root), environ=environ)
    if not key:
        raise LiveGateError("credential_missing")
    return BudgetedDeepSeekClient(
        str(project_root), campaign_id=campaign_id, cap_usd=CAP_USD,
        environ=environ, transport=transport,
    )


def build_check_port(executable, docker, output_root, *, timeout=150, runner=None):
    """Lazy P2 seam: actual reviewed check bridge, never copied here."""
    from episode.check_bridge import LiveCheckBridge

    return LiveCheckBridge(
        executable, docker, output_root, timeout=timeout, runner=runner,
    )


def build_grade_fn(docker, shim_dir, *, scenario_id="R1",
                   requirements_version="v1", checkpoint_id=None,
                   run_id=None, evidence_kind="protected_container_grading"):
    """Lazy P2 seam: qualified Retry or Cache grade for one candidate."""
    family = "retry" if scenario_id in ("R1", "R2", "R3") else "cache"
    token = re.sub(r"[^A-Za-z0-9_.-]", "-", str(run_id or "p3-run"))[:64]

    def grade_fn(candidate_sha256, candidate_path):
        if family == "cache":
            from episode.protected_cache import cache_session, grade_cache

            session = cache_session(docker, candidate_path, shim_dir, token)
            return grade_cache(
                session, candidate_sha256, evidence_kind=evidence_kind,
                requirements_version=requirements_version,
                checkpoint_id=checkpoint_id,
            )
        from episode.protected_session import DockerSession, grade_session

        session = DockerSession(docker, candidate_path, shim_dir, token)
        return grade_session(
            session, candidate_sha256, evidence_kind=evidence_kind,
            checkpoint_id=checkpoint_id,
            requirements_version=requirements_version,
        )

    return grade_fn


def _valid_episode_id(value):
    if type(value) is not str or not _EPISODE_ID.fullmatch(value):
        raise LiveGateError("invalid_episode_id")
    return value


_OPERATION_STAGES = ("development", "pilot", "formal")
_SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
_ARMS = ("ordinary", "structured_progress")


def _family_for_scenario(scenario):
    return "retry" if scenario in ("R1", "R2", "R3") else "cache"


G4_STATUSES = ("READY", "PASS", "APPROVED", "AUTHORIZED")
G4_OPERATION_KEYS = (
    "episode_id", "operation_id", "run_id", "stage", "scenario_id", "arm",
    "trial", "dataset_id",
)
G4_BOUND_KEYS = (
    "max_calls", "max_output_tokens_per_call", "max_call_seconds",
    "max_call_reservation_micro_usd", "max_operation_reservation_micro_usd",
    "max_wall_seconds",
)


def _git_head(project_root):
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LiveGateError("source_unverified") from None
    value = completed.stdout.decode("ascii", "replace").strip().lower()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise LiveGateError("source_unverified")
    return value


def _git_tree(project_root):
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD^{tree}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LiveGateError("source_unverified") from None
    value = completed.stdout.decode("ascii", "replace").strip().lower()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise LiveGateError("source_unverified")
    return value


def _file_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        raise LiveGateError("component_unreadable") from None


def verify_g4_gate(gate_path, project_root, operation):
    """Fail closed unless a reviewed G4 matches source, operation and bounds."""
    try:
        gate = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise LiveGateError("g4_gate_unreadable") from None
    if not isinstance(gate, dict):
        raise LiveGateError("g4_gate_unreadable")
    if gate.get("gate_id") != "G4-R1-READY" or gate.get("status") not in G4_STATUSES:
        raise LiveGateError("g4_not_ready")
    source = gate.get("source")
    source_commit = None
    if isinstance(source, dict):
        source_commit = source.get("commit") or source.get("integration_commit")
    source_commit = source_commit or gate.get("integration_commit")
    if isinstance(gate.get("integration"), dict):
        source_commit = source_commit or gate["integration"].get("commit")
    if not isinstance(source_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40}", source_commit.lower()
    ):
        raise LiveGateError("g4_source_missing")
    if _git_head(project_root) != source_commit.lower():
        raise LiveGateError("g4_source_mismatch")
    source_tree = source.get("tree") if isinstance(source, dict) else None
    if source_tree is not None:
        if not isinstance(source_tree, str) or not re.fullmatch(
            r"[0-9a-f]{40}", source_tree.lower()
        ) or _git_tree(project_root) != source_tree.lower():
            raise LiveGateError("g4_tree_mismatch")
    gated_operation = gate.get("operation")
    if not isinstance(gated_operation, dict):
        gated_operation = gate.get("operation_identity")
    if not isinstance(gated_operation, dict) or set(
        G4_OPERATION_KEYS
    ) - set(gated_operation):
        raise LiveGateError("g4_identity_missing")
    for key in G4_OPERATION_KEYS:
        if gated_operation.get(key) != operation.get(key):
            raise LiveGateError("g4_identity_mismatch")
    bounds = gate.get("limits") if isinstance(gate.get("limits"), dict) else gate.get("bounds")
    if not isinstance(bounds, dict):
        raise LiveGateError("g4_bounds_missing")
    expected = {
        "max_calls": MAX_CALLS,
        "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
        "max_call_seconds": MAX_CALL_SECONDS,
        "max_call_reservation_micro_usd": _ceil_micro(MAX_CALL_COST_USD),
        "max_operation_reservation_micro_usd": _ceil_micro(MAX_OPERATION_COST_USD),
        "max_wall_seconds": int(MAX_WALL_SECONDS),
    }
    for key in G4_BOUND_KEYS:
        if key not in bounds or bounds[key] != expected[key]:
            raise LiveGateError("g4_bounds_mismatch")
    budget = gate.get("budget")
    if not isinstance(budget, dict) or "campaign_id" not in budget or "cap_microdollars" not in budget:
        raise LiveGateError("g4_allowance_missing")
    if budget.get("campaign_id") != operation.get("campaign_id"):
        raise LiveGateError("g4_campaign_mismatch")
    if budget.get("cap_microdollars") != int(CAP_USD) * 1_000_000:
        raise LiveGateError("g4_allowance_mismatch")
    return gate


def _valid_operation(value, episode_id):
    """Validate the host-authorized live operation identity before dispatch."""
    if type(value) is not dict:
        raise LiveGateError("operation_identity_required")
    if value.get("evidence_kind") != "real_provider":
        raise LiveGateError("live_operation_provenance_required")
    for key in ("operation_id", "run_id"):
        candidate = value.get(key)
        if type(candidate) is not str or not _EPISODE_ID.fullmatch(candidate):
            raise LiveGateError("operation_identity_required")
    stage = value.get("stage")
    if stage not in _OPERATION_STAGES:
        raise LiveGateError("operation_stage_required")
    scenario = value.get("scenario_id", "R1")
    arm = value.get("arm", "ordinary")
    if scenario not in _SCENARIOS:
        raise LiveGateError("invalid_scenario")
    if arm not in _ARMS:
        raise LiveGateError("invalid_arm")
    family = _family_for_scenario(scenario)
    expected_version = "v1" if family == "retry" else "v2"
    requirements_version = value.get("requirements_version", expected_version)
    if requirements_version != expected_version:
        raise LiveGateError("operation_version_not_qualified")
    checkpoint_id = value.get("checkpoint_id")
    if checkpoint_id is not None and (
        type(checkpoint_id) is not str
        or not _EPISODE_ID.fullmatch(checkpoint_id)
    ):
        raise LiveGateError("invalid_checkpoint_id")
    trial = value.get("trial", 1)
    if type(trial) is not int or trial < 1:
        raise LiveGateError("invalid_trial")
    dataset_id = value.get("dataset_id", "ds-end-to-end-r1")
    if type(dataset_id) is not str or not dataset_id:
        raise LiveGateError("invalid_dataset_id")
    operation = dict(value)
    operation.update({
        "episode_id": episode_id,
        "scenario_id": scenario,
        "arm": arm,
        "family": family,
        "requirements_version": requirements_version,
        "checkpoint_id": checkpoint_id,
        "trial": trial,
        "dataset_id": dataset_id,
        "reserved_micro_usd": None,
        "estimated_cost_micro_usd": None,
    })
    return operation


def _runner_parameters(runner):
    try:
        parameters = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        return None, False
    return parameters, any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _runner_supports_contract(runner):
    parameters, accepts_kwargs = _runner_parameters(runner)
    if parameters is None:
        return False
    names = {parameter.name for parameter in parameters}
    required = {"episode_id", "live", "operation"}
    return accepts_kwargs or required.issubset(names)


def _runner_supports_names(runner, names):
    parameters, accepts_kwargs = _runner_parameters(runner)
    if parameters is None:
        return False
    available = {parameter.name for parameter in parameters}
    return accepts_kwargs or set(names).issubset(available)


def run_live_r1(run_root, *, episode_id, model_port, check_port, grade_fn,
                operation=None, live=None, policy=None, runner=None,
                monotonic=None, campaign_root=None, ledger_snapshot=None):
    """Run one operation through the injected P4 runner and P2 ports."""
    episode_id = _valid_episode_id(episode_id)
    if live is None:
        live = operation is not None
    if live:
        operation = _valid_operation(operation, episode_id)
    else:
        operation = dict(operation) if isinstance(operation, dict) else {}
        operation.setdefault("episode_id", episode_id)
    guard = OperationGuard(
        model_port, policy=policy, monotonic=monotonic, operation=operation,
    )
    check_receipts = []
    if callable(check_port):
        def capture_check(request):
            receipt = check_port(request)
            check_receipts.append(receipt)
            return receipt
        runner_check_port = capture_check
    else:
        runner_check_port = check_port
    scenario = operation.get("scenario_id", "R1")
    arm = operation.get("arm", "ordinary")
    if runner is None:
        from episode import e2e

        if scenario != "R1" or arm != "ordinary":
            runner = getattr(e2e, "run_scenario_smoke", None)
            if runner is None:
                raise LiveGateError("scenario_arm_runner_unsupported")
        else:
            runner = e2e.run_r1_smoke
    if not _runner_supports_contract(runner):
        raise LiveGateError("runner_operation_contract_unsupported")
    runner_kwargs = {
        "episode_id": episode_id,
        "live": live,
        "operation": operation,
        "model_port": guard,
        "check_port": runner_check_port,
        "grade_fn": grade_fn,
        "trial": operation.get("trial", 1),
        "dataset_id": operation.get("dataset_id", "ds-end-to-end-r1"),
    }
    if scenario != "R1" or arm != "ordinary":
        if not _runner_supports_names(runner, ("scenario_id", "arm")):
            raise LiveGateError("scenario_arm_runner_unsupported")
        runner_kwargs["scenario_id"] = scenario
        runner_kwargs["arm"] = arm
    if operation.get("stage") is not None:
        runner_kwargs["stage"] = operation["stage"]

    def snapshot():
        if callable(ledger_snapshot):
            try:
                return ledger_snapshot() or {}
            except Exception:
                return {}
        return ledger_snapshot if isinstance(ledger_snapshot, dict) else {}

    try:
        result = runner(run_root, **runner_kwargs)
    except Exception as error:
        if campaign_root is not None:
            try:
                receipt = build_attempt_receipt(
                    operation, usage=guard.summary(), ledger_snapshot=snapshot(),
                    check_receipts=check_receipts,
                    stop_status=getattr(error, "code", "operation_failed"),
                    run_root=run_root,
                )
                write_attempt_receipt(run_root, campaign_root, receipt)
            except Exception:
                pass
        raise
    usage = guard.summary()
    grade = result.get("grade") if isinstance(result, dict) else None
    cleanup_settled = bool(check_receipts) and all(
        isinstance(item, dict) and item.get("cleanup") == "settled"
        for item in check_receipts
    ) and isinstance(grade, dict) and grade.get("cleanup") == "settled"
    usage["cleanup_settled"] = cleanup_settled
    if campaign_root is not None:
        receipt = build_attempt_receipt(
            operation, result=result, usage=usage,
            ledger_snapshot=snapshot(), check_receipts=check_receipts,
            run_root=run_root,
        )
        validation = write_attempt_receipt(run_root, campaign_root, receipt)
        usage["attempt_receipt"] = "attempt-receipt.json"
        usage["attempt_receipt_valid"] = bool(validation.get("valid"))
    if guard.elapsed_seconds > guard.policy.max_wall_seconds:
        raise LiveGateError("wall_time_exceeded")
    return result, usage


def _require_components(args):
    missing = [
        name for name, value in (
            ("check_executable", args.check_executable),
            ("docker", args.docker),
            ("shim_dir", args.shim_dir),
        ) if not value
    ]
    if missing:
        raise LiveGateError("integration_components_missing")
    paths = (
        ("check_executable", args.check_executable, "file"),
        ("docker", args.docker, "file"),
        ("shim_dir", args.shim_dir, "dir"),
    )
    for name, value, kind in paths:
        try:
            path = Path(os.path.abspath(os.fspath(value)))
        except TypeError:
            raise LiveGateError("integration_components_invalid") from None
        if (kind == "file" and not path.is_file()) or (
            kind == "dir" and not path.is_dir()
        ):
            raise LiveGateError("integration_components_invalid")


def _verify_launch_roots(project_root, source_root):
    """Keep trusted project storage separate from the reviewed source checkout."""
    project = _absolute(project_root)
    source = _absolute(source_root) if source_root else project
    if not source.is_dir():
        raise LiveGateError("source_root_missing")
    if source != project:
        cwd = Path(os.path.abspath(os.getcwd()))
        if cwd != source:
            raise LiveGateError("source_cwd_mismatch")
        module = Path(__file__).resolve()
        try:
            module.relative_to(source)
        except ValueError:
            raise LiveGateError("source_module_mismatch") from None
        if (source / ".coordination" / "provider-budget.sqlite3").exists():
            raise LiveGateError("source_contains_ledger_refused")
        if (source / ".env.local").exists():
            raise LiveGateError("source_contains_credential_refused")
    return project, source


def _verify_components(args, source_root, scenario_id):
    _require_components(args)
    if args.source_root:
        shim = Path(os.path.abspath(os.fspath(args.shim_dir)))
        expected = (Path(source_root) / "protected").resolve()
        if shim.resolve() != expected:
            raise LiveGateError("integration_components_invalid")
        filename = "cache_shim.py" if scenario_id in ("C1", "C2", "C3") else "retry_shim.py"
        if not (shim / filename).is_file():
            raise LiveGateError("integration_components_invalid")
    if args.check_executable_sha256:
        if _file_sha256(args.check_executable) != args.check_executable_sha256.lower():
            raise LiveGateError("check_executable_identity_mismatch")
    if args.docker_sha256:
        if _file_sha256(args.docker) != args.docker_sha256.lower():
            raise LiveGateError("docker_identity_mismatch")
    if args.image_digest:
        from episode.protected_session import IMAGE

        if args.image_digest != IMAGE:
            raise LiveGateError("image_identity_mismatch")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Guarded R1 live entry.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--source-root", default=None)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--reviewed-live", action="store_true")
    parser.add_argument("--runtime-authorized", action="store_true")
    parser.add_argument("--initialize-ledger", action="store_true")
    parser.add_argument("--ledger", "--ledger-path", dest="ledger", default=None)
    parser.add_argument("--check-executable", default=None)
    parser.add_argument("--docker", default=None)
    parser.add_argument("--shim-dir", default=None)
    parser.add_argument("--check-output-root", default=None)
    parser.add_argument("--check-executable-sha256", default=None)
    parser.add_argument("--docker-sha256", default=None)
    parser.add_argument("--image-digest", default=None)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--operation-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--stage", default=None)
    parser.add_argument("--scenario-id", default="R1")
    parser.add_argument("--arm", default="ordinary")
    parser.add_argument("--trial", type=int, default=1)
    parser.add_argument("--dataset-id", default="ds-end-to-end-r1")
    parser.add_argument("--requirements-version", default=None)
    parser.add_argument("--checkpoint-id", default=None)
    parser.add_argument("--campaign-root", default=None)
    parser.add_argument("--g4-gate", default=None)
    args = parser.parse_args(argv)
    try:
        if args.initialize_ledger:
            if not args.reviewed_live:
                raise LiveGateError("live_dispatch_not_reviewed")
            if not args.runtime_authorized:
                raise LiveGateError("runtime_not_authorized")
            project = _absolute(args.project_root)
            if args.source_root and _absolute(args.source_root) != project:
                raise LiveGateError("ledger_initialization_root_mismatch")
            if not (project / ".env.local").is_file():
                raise LiveGateError("ledger_initialization_original_project_required")
            canonical = initialize_ledger(project, args.campaign_id)
            print(json.dumps({
                "status": "canonical_ledger_initialized",
                "cap_usd": CAP_USD,
                "ledger": str(canonical),
            }, sort_keys=True))
            return 0
        project, run, canonical = authorize(
            args.project_root, args.run_root,
            reviewed_live=args.reviewed_live,
            runtime_authorized=args.runtime_authorized,
            ledger_path=args.ledger,
        )
        project, source = _verify_launch_roots(project, args.source_root)
        _require_components(args)
        _valid_episode_id(args.episode_id)
        operation = _valid_operation({
            "evidence_kind": "real_provider",
            "operation_id": args.operation_id,
            "run_id": args.run_id,
            "stage": args.stage,
            "scenario_id": args.scenario_id,
            "arm": args.arm,
            "trial": args.trial,
            "dataset_id": args.dataset_id,
            "requirements_version": args.requirements_version,
            "checkpoint_id": args.checkpoint_id,
            "campaign_id": args.campaign_id,
        }, args.episode_id)
        _verify_components(args, source, operation["scenario_id"])
        campaign = (
            _absolute(args.campaign_root) if args.campaign_root
            else project / ".coordination" / "local-phase-run" / "r1"
        )
        if not campaign.is_dir():
            raise LiveGateError("campaign_root_missing")
        if not args.g4_gate:
            raise LiveGateError("g4_gate_required")
        verify_g4_gate(args.g4_gate, source, operation)
        client = build_provider(project, args.campaign_id)
        lease = CampaignLease(
            campaign, operation_id=operation["operation_id"],
            run_id=operation["run_id"],
        )
        if args.preflight_only:
            if lease.active.exists():
                raise LiveGateError("lease_unavailable")
            print(json.dumps({
                "status": "PREFLIGHT_OK",
                "source_commit": _git_head(source),
                "source_tree": _git_tree(source),
                "source_root": str(source),
                "project_root": str(project),
                "campaign_root": str(campaign),
                "canonical_ledger": str(canonical),
                "operation_id": operation["operation_id"],
                "run_id": operation["run_id"],
                "scenario_id": operation["scenario_id"],
                "arm": operation["arm"],
                "preflight_only": True,
                "provider_calls": 0,
            }, sort_keys=True))
            return 0
        policy = LivePolicy()
        deadline = time.monotonic() + policy.max_wall_seconds
        model_port = BudgetedModelPort(
            client,
            dispatch=True,
            max_tokens=policy.max_output_tokens,
            timeout_seconds=policy.max_call_seconds,
            per_call_cap_usd=str(policy.max_call_cost_usd),
            deadline=deadline,
        )
        check_port = build_check_port(
            args.check_executable, args.docker,
            (Path(args.check_output_root) if args.check_output_root
             else run / "checks"),
        )
        grade_fn = build_grade_fn(
            args.docker, args.shim_dir,
            scenario_id=operation["scenario_id"],
            requirements_version=operation["requirements_version"],
            checkpoint_id=operation["checkpoint_id"],
            run_id=operation["run_id"],
        )
        lease.acquire()
        result, usage = run_live_r1(
            run, episode_id=args.episode_id, operation=operation, live=True,
            model_port=model_port, check_port=check_port, grade_fn=grade_fn,
            policy=policy, campaign_root=campaign,
            ledger_snapshot=client.snapshot,
        )
        settled = usage.get("cleanup_settled") is True
        lease.release(settled=settled)
        if not settled:
            raise LiveGateError("cleanup_unknown")
        row = result.get("row") if isinstance(result, dict) else None
        receipt_path = Path(run) / "attempt-receipt.json"
        receipt_sha = None
        if receipt_path.is_file():
            receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        print(json.dumps({
            "status": row.get("status") if isinstance(row, dict) else None,
            "outcomes": row.get("outcomes") if isinstance(row, dict) else None,
            "episode_id": args.episode_id,
            "operation_id": operation["operation_id"],
            "run_id": operation["run_id"],
            "stage": operation["stage"],
            "requirements_version": operation["requirements_version"],
            "usage": usage,
            "attempt_receipt": "attempt-receipt.json",
            "attempt_receipt_sha256": receipt_sha,
            "canonical_ledger": str(canonical),
            "ledger_snapshot": client.snapshot(),
        }, sort_keys=True))
        return 0
    except Exception as error:
        code = getattr(error, "code", None)
        print(
            "live refused: " + (code if type(code) is str else "live_setup_error"),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
