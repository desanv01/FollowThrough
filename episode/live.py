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
from decimal import Decimal, ROUND_CEILING
import inspect
import json
import os
from pathlib import Path
import re
import sys
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

    def __init__(self, model_port, *, policy=None, monotonic=None):
        self._port = model_port
        self.policy = policy or LivePolicy()
        self._clock = time.monotonic if monotonic is None else monotonic
        self._started = self._clock()
        self._seen = 0
        self.calls = 0
        self.reserved_micro_usd = 0
        self.estimated_micro_usd = 0
        self._per_call_micro = _ceil_micro(self.policy.max_call_cost_usd)
        self._operation_micro = _ceil_micro(self.policy.max_operation_cost_usd)

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


def build_grade_fn(docker, shim_dir, *, evidence_kind="protected_container_grading"):
    """Lazy P2 seam: qualified container session grade for one candidate."""
    def grade_fn(candidate_sha256, candidate_path):
        from episode.protected_session import DockerSession, grade_session

        run_id = "r1-" + str(candidate_sha256)[:24]
        session = DockerSession(docker, candidate_path, shim_dir, run_id)
        return grade_session(
            session, candidate_sha256, evidence_kind=evidence_kind,
        )

    return grade_fn


def _valid_episode_id(value):
    if type(value) is not str or not _EPISODE_ID.fullmatch(value):
        raise LiveGateError("invalid_episode_id")
    return value


def _runner_accepts_episode_id(runner):
    try:
        parameters = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "episode_id"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def run_live_r1(run_root, *, episode_id, model_port, check_port, grade_fn,
                policy=None, runner=None, monotonic=None):
    """Run one R1 operation through the injected P4 runner and P2 ports.

    The reviewed episode ID is threaded to the P4 runner. The current accepted
    ``e2e.run_r1_smoke`` does not accept an episode ID; dispatch refuses with
    ``runner_episode_identity_unsupported`` until the P4 seam preserves it, rather
    than silently recording a smoke identity.
    """
    episode_id = _valid_episode_id(episode_id)
    guard = OperationGuard(model_port, policy=policy, monotonic=monotonic)
    if runner is None:
        from episode import e2e

        runner = e2e.run_r1_smoke
    if not _runner_accepts_episode_id(runner):
        raise LiveGateError("runner_episode_identity_unsupported")
    result = runner(
        run_root, episode_id=episode_id, model_port=guard,
        check_port=check_port, grade_fn=grade_fn,
    )
    if guard.elapsed_seconds > guard.policy.max_wall_seconds:
        raise LiveGateError("wall_time_exceeded")
    return result, guard.summary()


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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Guarded R1 live entry.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--reviewed-live", action="store_true")
    parser.add_argument("--runtime-authorized", action="store_true")
    parser.add_argument("--initialize-ledger", action="store_true")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--check-executable", default=None)
    parser.add_argument("--docker", default=None)
    parser.add_argument("--shim-dir", default=None)
    parser.add_argument("--check-output-root", default=None)
    args = parser.parse_args(argv)
    try:
        if args.initialize_ledger:
            if not args.reviewed_live:
                raise LiveGateError("live_dispatch_not_reviewed")
            if not args.runtime_authorized:
                raise LiveGateError("runtime_not_authorized")
            canonical = initialize_ledger(args.project_root, args.campaign_id)
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
        _require_components(args)
        _valid_episode_id(args.episode_id)
        client = build_provider(project, args.campaign_id)
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
        grade_fn = build_grade_fn(args.docker, args.shim_dir)
        result, usage = run_live_r1(
            run, episode_id=args.episode_id, model_port=model_port,
            check_port=check_port, grade_fn=grade_fn, policy=policy,
        )
        row = result.get("row") if isinstance(result, dict) else None
        print(json.dumps({
            "status": row.get("status") if isinstance(row, dict) else None,
            "outcomes": row.get("outcomes") if isinstance(row, dict) else None,
            "usage": usage,
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
