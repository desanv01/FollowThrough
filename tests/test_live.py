"""Focused tests for the P3 guarded live composition (offline only)."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout

from adapters.budget import CampaignLedger
from adapters.deepseek import HttpResponse
from episode.live import (
    LiveGateError,
    LivePolicy,
    OperationGuard,
    build_provider,
    initialize_ledger,
    main,
    run_live_r1,
)
from episode.provider import BudgetedModelPort


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
        "model": "synthetic-returned",
        "choices": [{
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

    def __call__(self, run_root, *, episode_id, model_port, check_port, grade_fn):
        self.calls.append(run_root)
        self.episode_ids.append(episode_id)
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
        raise AssertionError("must not run without episode identity")


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
        result, usage = run_live_r1(
            self.root / "run", episode_id="ep-reviewed-1",
            model_port=port, check_port=lambda request: {},
            grade_fn=lambda sha, path: {}, policy=policy, runner=runner,
        )
        self.assertEqual(result["row"]["status"], "completed_claim")
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["reserved_micro_usd"], 1200)
        self.assertEqual(usage["policy"]["max_calls"], 4)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.episode_ids, ["ep-reviewed-1"])

    def test_runner_without_episode_identity_is_refused_before_call(self):
        runner = UnsupportedRunner()
        with self.assertRaises(LiveGateError) as raised:
            run_live_r1(
                self.root / "run", episode_id="ep-reviewed-1",
                model_port=FakePort(), check_port=lambda request: {},
                grade_fn=lambda sha, path: {}, runner=runner,
            )
        self.assertEqual(
            raised.exception.code, "runner_episode_identity_unsupported"
        )
        self.assertEqual(runner.calls, 0)

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
