"""Focused tests for the P3 guarded live composition (offline only)."""

from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from adapters.budget import CampaignLedger
from adapters.deepseek import HttpResponse
from episode import e2e
from episode.live import (
    CampaignLease,
    LiveGateError,
    LivePolicy,
    OperationGuard,
    build_attempt_receipt,
    build_grade_fn,
    build_provider,
    initialize_ledger,
    main,
    run_live_r1,
    verify_g4_gate,
    write_attempt_receipt,
)
from episode.provider import BudgetedModelPort
from p5.p5_tools.receipts import validate_receipt


WORKTREE = Path(__file__).resolve().parents[1]
SCRATCH = WORKTREE / "tmp" / "live-tests"
KEY = "synthetic-live-key-GGGG7777"
SHA = "a" * 64
RUN_ID = "run:live:1"


def request(operation_id="live:op:1"):
    return {
        "episode_id": "ep",
        "operation_id": operation_id,
        "role": "O1",
        "invocation_index": 1,
        "messages": [
            {"role": "system", "content": "role context"},
            {"role": "user", "content": "observation"},
        ],
        "allowed_actions": ["read", "check", "finish"],
    }


def completion_payload():
    return {
        "model": "synthetic-returned",        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps({"action": {"name": "read", "filename": "TASK.md"}}),
            },
        }],
        "usage": {
            "prompt_tokens": 5,
            "prompt_cache_hit_tokens": 1,
            "prompt_cache_miss_tokens": 4,
            "completion_tokens": 2,
            "total_tokens": 7,
        },
    }


def write_lease_release(campaign, *, status="RELEASED_AFTER_SETTLEMENT",
                        unknown=False, active=0):
    path = campaign / "leases" / "docker.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": status, "unknown_settlement": unknown,
        "active_docker_operations": active,
    }), encoding="utf-8")


def synthetic_check(request):    return {
        "schema_version": "check-port-0.1",
        "request_id": request["request_id"],
        "evidence_kind": "synthetic_check",
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": "passed",
        "exit_code": 0,
        "stdout": "synthetic contract fixture\n",
        "stderr": "",
        "cleanup": "settled",
    }


def synthetic_grade(candidate_sha256, candidate_path):
    return {
        "candidate_sha256": candidate_sha256,
        "requirements_version": "v1",
        "status": "passed",
        "functional_pass": True,
        "cleanup": "settled",
        "settlement": "known",
        "origin": "grader",
        "errors": [],
    }


def protected_grade(candidate_sha256, candidate_path):
    return {
        "candidate_sha256": candidate_sha256,
        "requirements_version": "v1",
        "evidence_kind": "protected_container_grading",
        "status": "passed",
        "functional_pass": True,
        "cleanup": "settled",
        "settlement": "known",
        "origin": "grader",
        "errors": [],
    }


def scenario_port(scenario_id):
    filename = "preview.py" if scenario_id in ("C1", "C2", "C3") else "processor.py"
    version = "v2" if scenario_id in ("C1", "C2", "C3") else "v1"
    sequence = [
        {"name": "delegate", "target": "W1", "task": "Inspect the candidate.",
         "claimed_source_sha256": "0" * 64,
         "claimed_requirements_version": version},
        {"name": "read", "filename": filename},
        {"name": "check"},
        {"name": "report", "summary": "Synthetic composition report.",
         "evidence_ids": ["synthetic-check"]},
        {"name": "finish", "claimed_status": "partial",
         "summary": "Synthetic composition stopped after a safe partial.",
         "remaining": ["real protected grading"], "evidence_ids": []},
    ]

    def port(request):
        action = sequence.pop(0)
        return {
            "action": action,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "requested_model": "stub-requested",
            "returned_model": "stub-returned",
        }

    return port


def scenario_grade(scenario_id):
    version = "v2" if scenario_id in ("C1", "C2", "C3") else "v1"

    def grade(candidate_sha256, candidate_path):
        return {
            "candidate_sha256": candidate_sha256,
            "requirements_version": version,
            "evidence_kind": "synthetic_development",
            "status": "passed",
            "functional_pass": True,
            "cleanup": "settled",
            "settlement": "known",
            "origin": "grader",
            "errors": [],
        }

    return grade


def scripted_port(run_root):
    correct = (WORKTREE / "references" / "retry-processing" / "correct" /
               "processor.py").read_text(encoding="utf-8")
    return e2e.scripted_r1_model_port(
        Path(run_root) / "package" / "agent", correct,
    )


class FakePort:
    def __init__(self, *, output_tokens=2, reservation_micro=1000, error=None):
        self.observations = []
        self.output_tokens = output_tokens
        self.reservation_micro = reservation_micro
        self.error = error
        self.requests = []

    def __call__(self, value):
        self.requests.append(value)
        self.observations.append({
            "reservation_micro_usd": self.reservation_micro,
            "estimated_cost_micro_usd": 1,
            "status": "ok",
        })
        if self.error is not None:
            raise self.error
        return {
            "action": {"name": "read", "filename": "TASK.md"},
            "usage": {"input_tokens": 5, "output_tokens": self.output_tokens},
            "requested_model": "deepseek-flash",
            "returned_model": "synthetic-returned",
        }


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.episode_ids = []
        self.live_values = []
        self.operations = []
        self.scenarios = []
        self.arms = []

    def __call__(self, run_root, *, episode_id, live, operation, model_port,
                 check_port, grade_fn, trial=1, dataset_id="ds-end-to-end-r1",
                 stage=None, scenario_id=None, arm=None):
        self.calls.append(run_root)
        self.episode_ids.append(episode_id)
        self.live_values.append(live)
        self.operations.append(operation)
        self.scenarios.append(scenario_id)
        self.arms.append(arm)
        response = model_port(request())
        return {
            "row": {"status": "completed_claim", "outcomes": {"candidate": "ok"}},
            "response": response,
        }


class UnsupportedRunner:
    def __init__(self):
        self.calls = 0

    def __call__(self, run_root, *, model_port, check_port, grade_fn):
        self.calls += 1
        raise AssertionError("must not run without full runner contract")


class LegacyRunner:
    """Supports live/operation but not scenario/arm parameters."""

    def __init__(self):
        self.calls = 0

    def __call__(self, run_root, *, episode_id, live, operation, model_port,
                 check_port, grade_fn, trial=1, dataset_id="ds-end-to-end-r1"):
        self.calls += 1
        raise AssertionError("must not run without scenario/arm parameters")


class LiveCompositionTest(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.root = SCRATCH / ("live-" + uuid.uuid4().hex[:12])
        os.makedirs(self.root)
        self.addCleanup(shutil.rmtree, self.root, True)

    def fixture_project(self, *, key=True, ledger=True):
        project = self.root / "project"
        project.mkdir()
        if key:
            (project / ".env.local").write_bytes(
                ("DEEPSEEK_API_KEY=" + KEY + "\n").encode("ascii")
            )
        if ledger:
            CampaignLedger(str(project), campaign_id="fixture-campaign", cap_usd="20")
        return project

    def fixture_project_at(self, name, *, ledger=True):
        project = self.root / name
        project.mkdir()
        (project / ".env.local").write_bytes(
            ("DEEPSEEK_API_KEY=" + KEY + "\n").encode("ascii")
        )
        if ledger:
            CampaignLedger(str(project), campaign_id="fixture-campaign", cap_usd="20")
        return project

    def _preflight_args(self, project, campaign, run_parent, check, docker, g4, source):
        return [
            "--project-root", str(project),
            "--source-root", str(source),
            "--run-root", str(run_parent / "run"),
            "--episode-id", "r1-preflight",
            "--campaign-id", "fixture-campaign",
            "--operation-id", "r1-preflight",
            "--run-id", "r1-preflight:run",
            "--stage", "development", "--scenario-id", "R1",
            "--arm", "ordinary", "--trial", "1",
            "--dataset-id", "ds-end-to-end-r1",
            "--requirements-version", "v1",
            "--campaign-root", str(campaign),
            "--ledger-path", str(project / ".coordination" / "provider-budget.sqlite3"),
            "--g4-gate", str(g4),
            "--reviewed-live", "--runtime-authorized",
            "--check-executable", str(check),
            "--docker", str(docker),
            "--shim-dir", str(source / "protected"),
            "--check-executable-sha256",
            hashlib.sha256(check.read_bytes()).hexdigest(),
            "--docker-sha256", hashlib.sha256(docker.read_bytes()).hexdigest(),
            "--image-digest", __import__(
                "episode.protected_session", fromlist=["IMAGE"]
            ).IMAGE,
            "--preflight-only",
        ]

    def test_two_root_launch_preflight_binds_source_and_canonical_storage(self):
        source = WORKTREE
        source_commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        source_tree = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        check = self.root / "check.exe"
        docker = self.root / "docker.exe"
        check.write_bytes(b"fixture-check")
        docker.write_bytes(b"fixture-docker")
        campaign = self.root / "campaign"
        lease = campaign / "lease" / "P2-AUTHORED-001"
        lease.mkdir(parents=True)
        (lease / "owner.json").write_text('{"owner": "P2"}\n', encoding="utf-8")
        gate = {
            "gate_id": "G4-R1-READY", "status": "READY",
            "source": {"commit": source_commit, "tree": source_tree},
            "operation": {
                "episode_id": "r1-preflight", "operation_id": "r1-preflight",
                "run_id": "r1-preflight:run", "stage": "development",
                "scenario_id": "R1", "arm": "ordinary", "trial": 1,
                "dataset_id": "ds-end-to-end-r1",
            },
            "limits": {
                "max_calls": 16, "max_output_tokens_per_call": 2048,
                "max_call_seconds": 25, "max_call_reservation_micro_usd": 50000,
                "max_operation_reservation_micro_usd": 800000,
                "max_wall_seconds": 900,
            },
            "budget": {"campaign_id": "fixture-campaign",
                       "cap_microdollars": 20_000_000},
        }
        gate_path = self.root / "g4.json"
        gate_path.write_text(json.dumps(gate), encoding="utf-8")
        observed = []
        for name in ("project-a", "project-b"):
            project = self.fixture_project_at(name)
            parent = self.root / (name + "-runparent")
            parent.mkdir()
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(self._preflight_args(
                    project, campaign, parent, check, docker, gate_path, source,
                ))
            self.assertEqual(code, 0, err.getvalue())
            payload = json.loads(out.getvalue().strip())
            self.assertEqual(payload["status"], "PREFLIGHT_OK")
            self.assertEqual(payload["source_commit"], source_commit)
            self.assertEqual(payload["source_tree"], source_tree)
            self.assertEqual(payload["canonical_ledger"],
                             str(project / ".coordination" / "provider-budget.sqlite3"))
            observed.append(payload["canonical_ledger"])
        self.assertNotEqual(observed[0], observed[1])
        # A wrong source root fails closed before any provider construction.
        wrong = self.root / "wrong-source"
        wrong.mkdir()
        project = self.fixture_project_at("project-wrong")
        parent = self.root / "wrong-runparent"
        parent.mkdir()
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(self._preflight_args(
                project, campaign, parent, check, docker, gate_path, wrong,
            ))
        self.assertEqual(code, 2)
        self.assertIn("live refused:", err.getvalue())

    def test_operation_guard_call_and_operation_limits(self):
        port = FakePort(reservation_micro=1000)
        policy = LivePolicy(max_calls=2)
        guard = OperationGuard(port, policy=policy, monotonic=lambda: 0.0)
        guard(request("op:1"))
        guard(request("op:2"))
        self.assertEqual(guard.calls, 2)
        self.assertEqual(guard.reserved_micro_usd, 2000)
        with self.assertRaises(LiveGateError) as raised:
            guard(request("op:3"))
        self.assertEqual(raised.exception.code, "call_limit_exceeded")

        tight = OperationGuard(
            FakePort(reservation_micro=50_000),
            policy=LivePolicy(max_calls=16, max_operation_cost_usd=0.08),
            monotonic=lambda: 0.0,
        )
        tight(request("op:1"))
        with self.assertRaises(LiveGateError) as raised:
            tight(request("op:2"))
        self.assertEqual(raised.exception.code, "operation_cost_exceeded")

    def test_operation_guard_wall_and_output_token_limits(self):
        state = {"now": 0.0}
        wall = OperationGuard(
            FakePort(), policy=LivePolicy(), monotonic=lambda: state["now"],
        )
        state["now"] = 901.0
        with self.assertRaises(LiveGateError) as raised:
            wall(request())
        self.assertEqual(raised.exception.code, "wall_time_exceeded")

        tokens = OperationGuard(FakePort(output_tokens=2049), policy=LivePolicy())
        with self.assertRaises(LiveGateError) as raised:
            tokens(request())
        self.assertEqual(raised.exception.code, "output_token_exceeded")

    def test_real_wrapper_and_fixture_ledger_compose_with_fake_transport(self):
        project = self.fixture_project()
        transports = []

        def transport(body, api_key, *, timeout_seconds, max_response_bytes):
            transports.append((body, timeout_seconds, max_response_bytes))
            self.assertEqual(api_key, KEY)
            return HttpResponse(200, json.dumps(completion_payload()).encode("utf-8"))

        client = build_provider(
            project, "fixture-campaign", transport=transport,
            environ={},
        )
        port = BudgetedModelPort(
            client, dispatch=True, max_tokens=64, timeout_seconds=25,
            per_call_cap_usd="0.05",
        )
        response = port(request())
        self.assertEqual(response["action"], {"name": "read", "filename": "TASK.md"})
        self.assertEqual(len(transports), 1)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["operations"][0]["status"], "success")
        self.assertGreater(snapshot["reserved_microdollars"], 0)
        self.assertEqual(snapshot["operations"][0]["operation_id"], "live:op:1")
        self.assertIn("live:op:1", json.dumps(snapshot))

    def test_injected_runner_receives_guarded_port_and_reports_usage(self):
        port = FakePort(reservation_micro=1200)
        policy = LivePolicy(max_calls=4)
        runner = FakeRunner()
        operation = {
            "evidence_kind": "real_provider",
            "operation_id": "op:r1:1",
            "run_id": "run:r1:1",
            "stage": "development",
        }
        result, usage = run_live_r1(
            self.root / "run", episode_id="ep-reviewed-1",
            operation=operation, live=True,
            model_port=port, check_port=lambda request: {},
            grade_fn=lambda sha, path: {}, policy=policy, runner=runner,
        )
        self.assertEqual(result["row"]["status"], "completed_claim")
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["reserved_micro_usd"], 1200)
        self.assertEqual(usage["policy"]["max_calls"], 4)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.episode_ids, ["ep-reviewed-1"])
        self.assertEqual(runner.live_values, [True])
        forwarded = runner.operations[0]
        self.assertEqual(forwarded["episode_id"], "ep-reviewed-1")
        self.assertEqual(forwarded["operation_id"], "op:r1:1")
        self.assertEqual(forwarded["run_id"], "run:r1:1")
        self.assertEqual(forwarded["stage"], "development")
        self.assertEqual(forwarded["reserved_micro_usd"], 1200)
        self.assertEqual(forwarded["estimated_cost_micro_usd"], 1)
        self.assertEqual(forwarded["call_count"], 1)

    def test_runner_without_full_contract_is_refused_before_call(self):
        runner = UnsupportedRunner()
        with self.assertRaises(LiveGateError) as raised:
            run_live_r1(
                self.root / "run", episode_id="ep-reviewed-1",
                model_port=FakePort(), check_port=lambda request: {},
                grade_fn=lambda sha, path: {}, runner=runner,
            )
        self.assertEqual(
            raised.exception.code, "runner_operation_contract_unsupported"
        )
        self.assertEqual(runner.calls, 0)

    def test_live_operation_identity_is_validated_before_call(self):
        port = FakePort()
        for operation in (
            None,
            {"evidence_kind": "synthetic_development",
             "operation_id": "op:1", "run_id": "run:1",
             "stage": "development"},
            {"evidence_kind": "real_provider", "run_id": "run:1",
             "stage": "development"},
            {"evidence_kind": "real_provider", "operation_id": "op:1",
             "run_id": "run:1", "stage": "unknown"},
            {"evidence_kind": "real_provider", "operation_id": "op:1",
             "run_id": "run:1", "stage": "development",
             "scenario_id": "X1", "arm": "ordinary"},
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(LiveGateError):
                    run_live_r1(
                        self.root / "run", episode_id="ep-1",
                        operation=operation, live=True, model_port=port,
                        check_port=lambda request: {},
                        grade_fn=lambda sha, path: {}, runner=FakeRunner(),
                    )
        self.assertEqual(port.requests, [])

    def test_unqualified_cache_version_is_refused_before_dispatch(self):
        port = FakePort()
        operation = {
            "evidence_kind": "real_provider",
            "operation_id": "op:c2:v1",
            "run_id": "run:c2:v1",
            "stage": "pilot",
            "scenario_id": "C2",
            "arm": "ordinary",
            "requirements_version": "v1",
        }
        with self.assertRaises(LiveGateError) as raised:
            run_live_r1(
                self.root / "unqualified-cache", episode_id="ep-c2-v1",
                operation=operation, live=True, model_port=port,
                check_port=synthetic_check, grade_fn=protected_grade,
            )
        self.assertEqual(raised.exception.code, "operation_version_not_qualified")
        self.assertEqual(port.requests, [])

    def test_scenario_arm_forwarded_to_supporting_runner(self):
        runner = FakeRunner()
        operation = {
            "evidence_kind": "real_provider",
            "operation_id": "op:c2:1",
            "run_id": "run:c2:1",
            "stage": "pilot",
            "scenario_id": "C2",
            "arm": "structured_progress",
        }
        run_live_r1(
            self.root / "scenario-run", episode_id="ep-c2",
            operation=operation, live=True, model_port=FakePort(),
            check_port=lambda request: {},
            grade_fn=lambda sha, path: {}, runner=runner,
        )
        self.assertEqual(runner.scenarios, ["C2"])
        self.assertEqual(runner.arms, ["structured_progress"])

    def test_default_integrated_runner_routes_non_r1_structured_arm(self):
        operation = {
            "evidence_kind": "synthetic_development",
            "operation_id": "op:c2:integrated",
            "run_id": "run:c2:integrated",
            "stage": "development",
            "scenario_id": "C2",
            "arm": "structured_progress",
            "trial": 3,
            "dataset_id": "ds-c2-contract",
        }
        result, usage = run_live_r1(
            self.root / "integrated-c2", episode_id="ep-c2-integrated",
            operation=operation, live=False,
            model_port=scenario_port("C2"),
            check_port=synthetic_check, grade_fn=scenario_grade("C2"),
        )
        self.assertEqual(result["row"]["scenario_id"], "C2")
        self.assertEqual(result["row"]["arm"], "structured_progress")
        self.assertEqual(result["row"]["trial"], 3)
        self.assertEqual(result["row"]["stage"], "development")
        self.assertEqual(result["row"]["evidence_kind"], "synthetic_development")
        self.assertEqual(result["binding"]["operation_id"], "op:c2:integrated")
        self.assertEqual(result["binding"]["run_id"], "run:c2:integrated")
        self.assertGreaterEqual(usage["calls"], 5)

    def test_injected_legacy_runner_without_scenario_parameters_is_refused(self):
        runner = LegacyRunner()
        operation = {
            "evidence_kind": "synthetic_development",
            "operation_id": "op:c2:legacy",
            "run_id": "run:c2:legacy",
            "stage": "development",
            "scenario_id": "C2",
            "arm": "ordinary",
        }
        port = FakePort()
        with self.assertRaises(LiveGateError) as raised:
            run_live_r1(
                self.root / "legacy-scenario", episode_id="ep-c2-legacy",
                operation=operation, live=False, model_port=port,
                check_port=synthetic_check, grade_fn=synthetic_grade,
                runner=runner,
            )
        self.assertEqual(
            raised.exception.code, "scenario_arm_runner_unsupported"
        )
        self.assertEqual(port.requests, [])
        self.assertEqual(runner.calls, 0)

    def test_default_runner_selects_generic_scenario_runner_when_available(self):
        runner = FakeRunner()
        operation = {
            "evidence_kind": "real_provider",
            "operation_id": "op:c2:default",
            "run_id": "run:c2:default",
            "stage": "pilot",
            "scenario_id": "C2",
            "arm": "structured_progress",
        }
        with mock.patch.object(
            e2e, "run_scenario_smoke", runner, create=True,
        ):
            run_live_r1(
                self.root / "generic-default", episode_id="ep-c2-default",
                operation=operation, live=True, model_port=FakePort(),
                check_port=lambda request: {},
                grade_fn=lambda sha, path: {},
            )
        self.assertEqual(runner.scenarios, ["C2"])
        self.assertEqual(runner.arms, ["structured_progress"])

    def test_live_mode_forwards_operation_and_preserves_report_identity(self):
        """Contract-only regression: synthetic fixtures, scratch deleted, no evidence."""
        run_root = self.root / "live-contract"
        operation = {
            "evidence_kind": "real_provider",
            "operation_id": "op:live:1",
            "run_id": "run:live:1",
            "stage": "development",
            "requirements_version": "v1",
        }
        result, usage = run_live_r1(
            run_root, episode_id="ep-live-contract",
            operation=operation, live=True,
            model_port=scripted_port(run_root),
            check_port=synthetic_check, grade_fn=protected_grade,
        )
        self.assertEqual(result["episode_id"], "ep-live-contract")
        self.assertEqual(result["row"]["episode_id"], "ep-live-contract")
        self.assertEqual(result["row"]["evidence_kind"], "real_provider")
        self.assertEqual(result["row"]["stage"], "development")
        self.assertEqual(result["binding"]["operation_id"], "op:live:1")
        self.assertEqual(result["binding"]["run_id"], "run:live:1")
        self.assertEqual(
            result["report"]["episodes"][0]["episode_id"], "ep-live-contract"
        )
        self.assertEqual(usage["calls"], 9)
        self.assertTrue((run_root / "report.json").is_file())
        self.assertTrue((run_root / "episode-binding.json").is_file())

        with self.assertRaises(e2e.E2EError):
            run_live_r1(
                self.root / "live-contract-synthetic-grade",
                episode_id="ep-live-synthetic",
                operation={**operation, "operation_id": "op:live:2",
                           "run_id": "run:live:2"},
                live=True, model_port=scripted_port(
                    self.root / "live-contract-synthetic-grade"
                ), check_port=synthetic_check, grade_fn=synthetic_grade,
            )

    def test_fake_provider_rehearsal_with_isolated_ledger_and_lock(self):
        project = self.fixture_project()
        transports = []

        def transport(body, api_key, *, timeout_seconds, max_response_bytes):
            transports.append(api_key)
            return HttpResponse(200, json.dumps(completion_payload()).encode("utf-8"))

        client = build_provider(
            project, "fixture-campaign", transport=transport, environ={},
        )
        port = BudgetedModelPort(
            client, dispatch=True, max_tokens=64, timeout_seconds=25,
            per_call_cap_usd="0.05",
        )
        campaign = self.root / "rehearsal-campaign"
        lease_root = campaign / "lease" / "P2-AUTHORED-001"
        lease_root.mkdir(parents=True)
        (lease_root / "owner.json").write_text(
            '{"owner": "P2", "status": "RELEASED_AFTER_SETTLEMENT", "unknown_settlement": false}\n',
            encoding="utf-8",
        )
        write_lease_release(campaign)
        operation = {
            "campaign_id": "fixture-campaign",
            "evidence_kind": "real_provider",
            "episode_id": "ep-rehearsal",
            "operation_id": "ep-rehearsal",
            "run_id": "ep-rehearsal:run",
            "stage": "development",
            "scenario_id": "R1",
            "arm": "ordinary",
            "trial": 1,
            "dataset_id": "ds-end-to-end-r1",
            "requirements_version": "v1",
            "checkpoint_id": None,
        }

        class RehearsalRunner:
            def __call__(self, run_root, *, episode_id, live, operation,
                         model_port, check_port, grade_fn, trial=1,
                         dataset_id="ds-end-to-end-r1", stage=None, **kwargs):
                response = model_port(request("ep-rehearsal:call:1"))
                self.response = response
                check = check_port({
                    "request_id": "rehearsal-check",
                    "candidate_sha256": SHA,
                    "requirements_version": "v1",
                })
                grade = grade_fn(SHA, run_root)
                return {
                    "candidate_sha256": SHA,
                    "binding": {
                        "candidate_sha256": SHA, "requirements_version": "v1",
                        "operation_id": operation["operation_id"],
                        "run_id": operation["run_id"],
                    },
                    "row": {
                        "status": "completed",
                        "resources": {"model_responses": 8, "tool_actions": 8,
                                      "input_tokens": 40, "output_tokens": 10,
                                      "elapsed_ms": 1000},
                        "outcomes": {"functional": "pass", "workflow": "unknown",
                                     "evidence": "pass", "completion": "pass"},
                    },
                    "grade": grade,
                }

        runner = RehearsalRunner()
        lease = CampaignLease(
            campaign, operation_id="ep-rehearsal", run_id="ep-rehearsal:run",
        ).acquire()
        try:
            result, usage = run_live_r1(
                self.root / "rehearsal-run", episode_id="ep-rehearsal",
                operation=operation, live=True, model_port=port,
                check_port=synthetic_check, grade_fn=protected_grade,
                runner=runner, campaign_root=campaign,
                ledger_snapshot=client.snapshot,
            )
            self.assertTrue(usage["cleanup_settled"])
            self.assertTrue(usage["attempt_receipt_valid"])
            self.assertEqual(len(transports), 1)
            receipt = json.loads(
                (self.root / "rehearsal-run" / "attempt-receipt.json")
                .read_text(encoding="utf-8")
            )
            validation = validate_receipt(receipt)
            self.assertTrue(validation["valid"], validation["errors"])
            self.assertTrue(validation["ledger_ready"])
            self.assertGreater(client.snapshot()["reserved_microdollars"], 0)
            lease.release(settled=True)
        finally:
            if (lease_root / "ACTIVE").exists():
                lease.release(settled=True)
    def _receipt_operation(self):
        return {
            "campaign_id": "fixture-campaign",
            "episode_id": "ep-receipt",
            "operation_id": "op-receipt",
            "run_id": "run-receipt",
            "stage": "development",
            "scenario_id": "R1",
            "arm": "ordinary",
            "trial": 1,
            "dataset_id": "ds-end-to-end-r1",
            "requirements_version": "v1",
            "checkpoint_id": None,
        }

    def test_attempt_receipt_success_is_structurally_and_ledger_ready(self):
        run_root = self.root / "receipt-success"
        run_root.mkdir()
        (run_root / "report.md").write_text("sanitized\n", encoding="utf-8")
        (run_root / "protected-grade.json").write_text(
            '{"status":"passed"}\n', encoding="utf-8"
        )
        result = {
            "candidate_sha256": SHA,
            "binding": {"candidate_sha256": SHA, "requirements_version": "v1",
                        "operation_id": "op-receipt", "run_id": "run-receipt"},
            "row": {
                "status": "completed",
                "resources": {"model_responses": 3, "tool_actions": 5,
                              "input_tokens": 120, "output_tokens": 40,
                              "elapsed_ms": 2500},
                "outcomes": {"functional": "pass", "workflow": "unknown",
                             "evidence": "pass", "completion": "pass"},
            },
            "grade": {"candidate_sha256": SHA, "requirements_version": "v1",
                      "evidence_kind": "protected_container_grading",
                      "functional_pass": True, "cleanup": "settled"},
        }
        snapshot = {
            "remaining_microdollars": 19_900_000,
            "operations": [{
                "operation_id": "ep-receipt:call:1",
                "amount_microdollars": 50_000,
                "estimated_cost_microdollars": 100,
                "status": "success",
            }],
        }
        receipt = build_attempt_receipt(
            self._receipt_operation(), result=result,
            usage={"calls": 3, "elapsed_seconds": 2.5},
            ledger_snapshot=snapshot,
            check_receipts=[{"cleanup": "settled"}], run_root=run_root,
        )
        validation = validate_receipt(receipt)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertTrue(validation["ledger_ready"])
        self.assertTrue(validation["report_ready"])
        self.assertIsNone(receipt["cleanup"]["container_or_run_absent"])
        self.assertEqual(receipt["budget"]["operation_ids"],
                         ["ep-receipt:call:1"])
        write_attempt_receipt(run_root, self.root, receipt)
        self.assertTrue((run_root / "attempt-receipt.json").is_file())
        self.assertTrue((self.root / "artifacts" / "attempts" / "op-receipt"
                         / "attempt-receipt.json").is_file())

    def test_attempt_receipt_unknown_infrastructure_is_retained(self):
        run_root = self.root / "receipt-unknown"
        receipt = build_attempt_receipt(
            self._receipt_operation(), usage={"calls": 1},
            ledger_snapshot={
                "remaining_microdollars": 19_950_000,
                "operations": [{
                    "operation_id": "ep-receipt:call:1",
                    "amount_microdollars": 50_000,
                    "estimated_cost_microdollars": None,
                    "status": "uncertain",
                }],
            },
            check_receipts=[], stop_status="provider_timeout",
            run_root=run_root,
        )
        validation = validate_receipt(receipt)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertFalse(validation["ledger_ready"])
        self.assertFalse(validation["report_ready"])
        self.assertTrue(receipt["dispatch"]["outcome_unknown"])
        self.assertEqual(receipt["dispatch"]["classification"], "provider")
        self.assertEqual(receipt["cleanup"]["readback"], "unknown")
        self.assertIsNone(receipt["cleanup"]["container_or_run_absent"])
        self.assertEqual(receipt["budget"]["settlement"], "uncertain")
        write_attempt_receipt(run_root, self.root, receipt)
        self.assertTrue((run_root / "attempt-receipt.json").is_file())

    def test_campaign_lease_is_exclusive_and_released_only_when_settled(self):
        campaign = self.root / "campaign"
        lease_root = campaign / "lease" / "P2-AUTHORED-001"
        lease_root.mkdir(parents=True)
        (lease_root / "owner.json").write_text(
            '{"owner": "P2", "status": "RELEASED_AFTER_SETTLEMENT", "unknown_settlement": false}\n',
            encoding="utf-8",
        )
        write_lease_release(campaign)
        lease = CampaignLease(
            campaign, operation_id="op-lease", run_id="run-lease",
        ).acquire()
        self.assertTrue((lease_root / "ACTIVE" / "owner.json").is_file())
        with self.assertRaises(LiveGateError) as raised:
            CampaignLease(
                campaign, operation_id="op-other", run_id="run-other",
            ).acquire()
        self.assertEqual(raised.exception.code, "lease_unavailable")
        self.assertFalse(lease.release(settled=False))
        self.assertTrue((lease_root / "ACTIVE").is_dir())
        self.assertTrue(lease.release(settled=True))
        self.assertFalse((lease_root / "ACTIVE").exists())
        write_lease_release(campaign, unknown=True)
        with self.assertRaises(LiveGateError) as raised:
            CampaignLease(
                campaign, operation_id="op-unknown", run_id="run-unknown",
            ).acquire()
        self.assertEqual(raised.exception.code, "lease_unavailable")

    def test_initialize_ledger_refuses_clone_project_root(self):
        clone = self.root / "clone-project"
        clone.mkdir()
        err = io.StringIO()
        with redirect_stderr(err):
            code = main([
                "--project-root", str(clone),
                "--source-root", str(WORKTREE),
                "--run-root", str(self.root / "unused-init"),
                "--episode-id", "ep-init",
                "--campaign-id", "fixture-campaign",
                "--initialize-ledger", "--reviewed-live", "--runtime-authorized",
            ])
        self.assertEqual(code, 2)
        self.assertFalse(
            (clone / ".coordination" / "provider-budget.sqlite3").exists()
        )
        self.assertIn("ledger_initialization", err.getvalue())

    def test_grade_fn_forwards_version_checkpoint_and_cache_shim(self):
        from episode import protected_cache, protected_session

        with mock.patch.object(
            protected_session, "DockerSession", return_value=object()
        ), mock.patch.object(
            protected_session, "grade_session", return_value={}
        ) as retry_grade:
            retry = build_grade_fn(
                "docker", self.root, scenario_id="R1",
                requirements_version="v1", checkpoint_id="cp-1",
                run_id="run:retry",
            )
            retry(SHA, self.root)
        self.assertEqual(retry_grade.call_args.kwargs["requirements_version"], "v1")
        self.assertEqual(retry_grade.call_args.kwargs["checkpoint_id"], "cp-1")

        with mock.patch.object(
            protected_cache, "cache_session", return_value=object()
        ), mock.patch.object(
            protected_cache, "grade_cache", return_value={}
        ) as cache_grade:
            cache = build_grade_fn(
                "docker", self.root, scenario_id="C2",
                requirements_version="v2", checkpoint_id=None,
                run_id="run:cache",
            )
            cache(SHA, self.root)
        self.assertEqual(cache_grade.call_args.kwargs["requirements_version"], "v2")
        self.assertTrue(cache_grade.called)

    def test_g4_gate_matches_source_identity_bounds_and_allowance(self):
        operation = {
            "campaign_id": "fixture-campaign",
            "episode_id": "ep-g4", "operation_id": "op-g4",
            "run_id": "run-g4", "stage": "development",
            "scenario_id": "R1", "arm": "ordinary", "trial": 1,
            "dataset_id": "ds-end-to-end-r1",
        }
        source_commit = "a" * 40
        gate = {
            "gate_id": "G4-R1-READY", "status": "READY",
            "source": {"commit": source_commit},
            "operation": {key: operation[key] for key in (
                "episode_id", "operation_id", "run_id", "stage",
                "scenario_id", "arm", "trial", "dataset_id")},
            "limits": {
                "max_calls": 16, "max_output_tokens_per_call": 2048,
                "max_call_seconds": 25, "max_call_reservation_micro_usd": 50000,
                "max_operation_reservation_micro_usd": 800000,
                "max_wall_seconds": 900,
            },
            "budget": {"campaign_id": "fixture-campaign",
                       "cap_microdollars": 20_000_000},
        }
        path = self.root / "g4.json"
        path.write_text(json.dumps(gate), encoding="utf-8")
        with mock.patch("episode.live._git_head", return_value=source_commit):
            self.assertEqual(verify_g4_gate(path, self.root, operation), gate)
            mismatched = dict(operation)
            mismatched["run_id"] = "run-other"
            with self.assertRaises(LiveGateError) as raised:
                verify_g4_gate(path, self.root, mismatched)
        self.assertEqual(raised.exception.code, "g4_identity_mismatch")
        gate["status"] = "BLOCKED"
        path.write_text(json.dumps(gate), encoding="utf-8")
        with self.assertRaises(LiveGateError) as raised:
            verify_g4_gate(path, self.root, operation)
        self.assertEqual(raised.exception.code, "g4_not_ready")

    def test_main_requires_operation_identity_before_provider(self):
        project = self.fixture_project()
        check = self.root / "check.exe"
        check.write_bytes(b"")
        docker = self.root / "docker.exe"
        docker.write_bytes(b"")
        shim = self.root / "shim"
        shim.mkdir()
        run_root = self.root / "cli-run"
        base = [
            "--project-root", str(project),
            "--run-root", str(run_root),
            "--episode-id", "ep-cli",
            "--campaign-id", "fixture-campaign",
            "--reviewed-live", "--runtime-authorized",
            "--check-executable", str(check),
            "--docker", str(docker),
            "--shim-dir", str(shim),
        ]
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(base), 2)
        self.assertIn("operation_identity_required", err.getvalue())
        self.assertFalse(run_root.exists())
        self.assertEqual(
            CampaignLedger(
                str(project), campaign_id="fixture-campaign", cap_usd="20"
            ).snapshot()["operations"],
            [],
        )

    def test_main_refuses_default_and_missing_components_before_provider(self):
        project = self.fixture_project()
        run_root = self.root / "fresh-run"
        base = [
            "--project-root", str(project),
            "--run-root", str(run_root),
            "--episode-id", "ep-1",
            "--campaign-id", "fixture-campaign",
        ]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(base), 2)
        self.assertIn("live_dispatch_not_reviewed", err.getvalue())

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(
                main(base + ["--reviewed-live", "--runtime-authorized"]), 2
            )
        self.assertIn("integration_components_missing", err.getvalue())
        self.assertFalse(run_root.exists(), "refusal created the run root")
        self.assertEqual(
            CampaignLedger(
                str(project), campaign_id="fixture-campaign", cap_usd="20"
            ).snapshot()["operations"],
            [],
        )

        invalid = io.StringIO()
        with redirect_stderr(invalid):
            self.assertEqual(
                main(base + [
                    "--reviewed-live", "--runtime-authorized",
                    "--check-executable", str(self.root / "missing-check"),
                    "--docker", str(self.root / "missing-docker"),
                    "--shim-dir", str(self.root / "missing-shim"),
                ]), 2,
            )
        self.assertIn("integration_components_invalid", invalid.getvalue())
        self.assertFalse(run_root.exists(), "invalid components created the run root")
        self.assertEqual(
            CampaignLedger(
                str(project), campaign_id="fixture-campaign", cap_usd="20"
            ).snapshot()["operations"],
            [],
        )

    def test_first_use_initialization_is_explicit_and_never_replaces(self):
        project = self.root / "first-use"
        project.mkdir()
        canonical = initialize_ledger(project, "fixture-campaign")
        self.assertTrue(canonical.is_file())
        snapshot = CampaignLedger(
            str(project), campaign_id="fixture-campaign", cap_usd="20"
        ).snapshot()
        self.assertEqual(snapshot["cap_usd"], "20.000000")
        self.assertEqual(snapshot["operations"], [])
        with self.assertRaises(LiveGateError) as raised:
            initialize_ledger(project, "fixture-campaign")
        self.assertEqual(
            raised.exception.code, "canonical_ledger_exists_refused"
        )

    def test_main_initialize_ledger_requires_authorization(self):
        project = self.root / "cli-init"
        project.mkdir()
        (project / ".env.local").write_bytes(
            ("DEEPSEEK_API_KEY=" + KEY + "\n").encode("ascii")
        )
        base = [
            "--project-root", str(project),
            "--run-root", str(self.root / "unused-run"),
            "--episode-id", "ep-1",
            "--campaign-id", "fixture-campaign",
            "--initialize-ledger",
        ]
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(base), 2)
        self.assertIn("live_dispatch_not_reviewed", err.getvalue())
        self.assertFalse(
            (project / ".coordination" / "provider-budget.sqlite3").exists()
        )
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(
                main(base + ["--reviewed-live", "--runtime-authorized"]), 0
            )
        self.assertIn("canonical_ledger_initialized", out.getvalue())
        self.assertTrue(
            (project / ".coordination" / "provider-budget.sqlite3").is_file()
        )


if __name__ == "__main__":
    unittest.main()

