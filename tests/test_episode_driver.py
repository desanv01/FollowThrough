"""Focused tests for the offline scenario-to-episode driver."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4


OVERLAY = Path(__file__).resolve().parents[1]
SCRATCH = OVERLAY / "tmp" / "tests"
sys.path.insert(0, str(OVERLAY))

from episode import driver as driver_module  # noqa: E402


LIMITS = {
    "max_model_responses": 6,
    "max_tool_actions": 6,
    "max_elapsed_ms": 30000,
    "max_context_bytes": 60000,
}


def partial_finish():
    return {
        "action": {
            "name": "finish",
            "claimed_status": "partial",
            "summary": "Synthetic wiring smoke partial claim; not a functional pass.",
            "evidence_ids": [],
            "remaining": ["No task evaluation was performed."],
        },
        "usage": {"input_tokens": None, "output_tokens": None},
        "requested_model": None,
        "returned_model": None,
    }


class RefuseCheck:
    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        raise RuntimeError("synthetic check port refuses unexpected use")


class EpisodeDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.run_root = SCRATCH / f"driver-{uuid4().hex}"
        cls.run_root.mkdir()
        cls.addClassCleanup(shutil.rmtree, cls.run_root, ignore_errors=True)

    def new_run(self, name):
        return self.run_root / f"{name}-{uuid4().hex}"

    def run_driver(self, scenario_id, run_root, model_port, episode_id=None, arm="ordinary"):
        return driver_module.run_scenario(
            OVERLAY,
            scenario_id,
            run_root=run_root,
            episode_id=episode_id or f"smoke-{scenario_id.lower()}",
            arm=arm,
            limits=dict(LIMITS),
            model_port=model_port,
            check_port=RefuseCheck(),
        )

    def test_all_six_partial_episode_envelopes(self):
        for scenario_id in ("R1", "R2", "R3", "C1", "C2", "C3"):
            calls = {"model": 0}
            check = RefuseCheck()

            def model(request, calls=calls):
                calls["model"] += 1
                self.assertEqual(request["role"], "O1")
                return partial_finish()

            run_root = self.new_run(f"all-{scenario_id}")
            envelope = driver_module.run_scenario(
                OVERLAY,
                scenario_id,
                run_root=run_root,
                episode_id=f"smoke-{scenario_id.lower()}",
                arm="ordinary",
                limits=dict(LIMITS),
                model_port=model,
                check_port=check,
            )
            self.assertEqual(set(envelope), driver_module.ENVELOPE_KEYS)
            self.assertEqual(envelope["schema_version"], "episode-driver-0.1")
            self.assertEqual(envelope["evidence_kind"], "synthetic_episode_control")
            self.assertEqual(envelope["status"], "episode_returned")
            self.assertEqual(envelope["error"], None)
            result = envelope["result"]
            self.assertEqual(result["schema_version"], "episode-local-0.1")
            self.assertEqual(result["status"], "partial_claim")
            self.assertEqual(result["scenario_id"], scenario_id)
            self.assertEqual(result["evidence_kind"], "synthetic_episode_control")
            self.assertEqual(calls["model"], 1)
            self.assertEqual(check.calls, 0)
            paths = envelope["paths"]
            self.assertEqual(Path(paths["package"]), run_root / "package")
            self.assertEqual(Path(paths["episode"]), run_root / "episode")
            self.assertTrue((run_root / "driver.json").is_file())
            self.assertTrue((run_root / "episode" / "result.json").is_file())
            self.assertTrue((run_root / "episode" / "events.jsonl").is_file())
            self.assertFalse((run_root / "package" / "agent" / "driver.json").exists())
            self.assertEqual(
                {path.name for path in (run_root / "package" / "agent").iterdir()},
                {"processor.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"}
                if scenario_id in ("R1", "R2", "R3")
                else {"preview.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"},
            )
            stored_result = json.loads(
                (run_root / "episode" / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored_result, result)

    def test_c3_model_response_precedes_supplied_receipt(self):
        run_root = self.new_run("c3-order")
        envelope = self.run_driver("C3", run_root, lambda request: partial_finish())
        self.assertEqual(envelope["status"], "episode_returned")
        events = [
            json.loads(line)
            for line in (run_root / "episode" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        response_seq = [event["seq"] for event in events if event["type"] == "model_response"]
        receipt_seq = [event["seq"] for event in events if event["type"] == "supplied_receipt"]
        self.assertEqual(len(response_seq), 1)
        self.assertEqual(len(receipt_seq), 1)
        self.assertLess(response_seq[0], receipt_seq[0])
        self.assertEqual(
            [event["type"] for event in events if event["type"] == "supplied_receipt"],
            ["supplied_receipt"],
        )
        receipt = next(event for event in events if event["type"] == "supplied_receipt")
        candidate_hash = hashlib.sha256(
            (run_root / "package" / "agent" / "preview.py").read_bytes()
        ).hexdigest()
        self.assertEqual(receipt["source_sha256"], candidate_hash)
        self.assertEqual(envelope["result"]["current_requirements_version"], "v2")

    def test_c2_c3_initial_agent_bytes_match(self):
        c2_root = self.new_run("c2-equal")
        c3_root = self.new_run("c3-equal")
        self.run_driver("C2", c2_root, lambda request: partial_finish())
        self.run_driver("C3", c3_root, lambda request: partial_finish())
        c2_agent = c2_root / "package" / "agent"
        c3_agent = c3_root / "package" / "agent"
        self.assertEqual(
            {path.name: path.read_bytes() for path in c2_agent.iterdir()},
            {path.name: path.read_bytes() for path in c3_agent.iterdir()},
        )

    def test_r1_role_handoff_with_scripted_ports(self):
        run_root = self.new_run("r1-handoff")
        calls = []

        def model(request):
            calls.append((request["role"], request["operation_id"]))
            if request["role"] == "O1" and len(calls) == 1:
                candidate_hash = hashlib.sha256(
                    (run_root / "package" / "agent" / "processor.py").read_bytes()
                ).hexdigest()
                return {
                    "action": {
                        "name": "delegate",
                        "target": "W1",
                        "task": "Inspect the supplied candidate against the visible requirements.",
                        "claimed_source_sha256": candidate_hash,
                        "claimed_requirements_version": "v1",
                    },
                    "usage": {"input_tokens": None, "output_tokens": None},
                    "requested_model": None,
                    "returned_model": None,
                }
            if request["role"] == "W1":
                return {
                    "action": {
                        "name": "report",
                        "summary": "Synthetic W1 report for wiring only.",
                        "evidence_ids": [],
                    },
                    "usage": {"input_tokens": None, "output_tokens": None},
                    "requested_model": None,
                    "returned_model": None,
                }
            return partial_finish()

        check = RefuseCheck()
        envelope = driver_module.run_scenario(
            OVERLAY,
            "R1",
            run_root=run_root,
            episode_id="handoff-r1",
            arm="ordinary",
            limits=dict(LIMITS),
            model_port=model,
            check_port=check,
        )
        self.assertEqual(envelope["status"], "episode_returned")
        result = envelope["result"]
        self.assertEqual(result["status"], "partial_claim")
        self.assertEqual([role for role, _ in calls], ["O1", "W1", "O1"])
        self.assertEqual(check.calls, 0)
        events = [
            json.loads(line)
            for line in (run_root / "episode" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        types = [event["type"] for event in events]
        self.assertIn("delegation", types)
        self.assertIn("worker_report", types)
        self.assertEqual(result["counts"]["model_responses_by_role"]["O1"], 2)
        self.assertEqual(result["counts"]["model_responses_by_role"]["W1"], 1)

    def test_preexisting_run_root_is_refused_without_modification(self):
        run_root = self.new_run("existing-root")
        run_root.mkdir()
        marker = run_root / "keep.txt"
        marker.write_bytes(b"existing evidence\n")
        envelope = self.run_driver("R1", run_root, lambda request: partial_finish())
        self.assertEqual(envelope["status"], "preparation_error")
        self.assertEqual(envelope["error"], "run_root_exists")
        self.assertIsNone(envelope["result"])
        self.assertIsNone(envelope["paths"]["package"])
        self.assertEqual(marker.read_bytes(), b"existing evidence\n")
        self.assertEqual({path.name for path in run_root.iterdir()}, {"keep.txt"})

    def test_errors_before_ownership_create_nothing(self):
        run_root = self.new_run("invalid-scenario")
        envelope = self.run_driver("RX", run_root, lambda request: partial_finish())
        self.assertEqual(envelope["status"], "preparation_error")
        self.assertEqual(envelope["error"], "invalid_scenario")
        self.assertFalse(run_root.exists())

    def test_separate_root_loaded_asset_tree_refused_before_mkdir(self):
        fixture_root = self.run_root / f"fixture-{uuid4().hex}"
        fixture_root.mkdir()
        loaded_package_root = Path(driver_module.__file__).resolve().parents[1] / "scenarios"
        target = loaded_package_root / "visible" / f"driver-guard-{uuid4().hex}"
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        calls = []
        original_mkdir = os.mkdir

        def spy(path, *args, **kwargs):
            calls.append(str(path))
            return original_mkdir(path, *args, **kwargs)

        driver_module.os.mkdir = spy
        try:
            envelope = driver_module.run_scenario(
                fixture_root,
                "R1",
                run_root=target,
                episode_id="guard-check",
                arm="ordinary",
                limits=dict(LIMITS),
                model_port=lambda request: partial_finish(),
                check_port=RefuseCheck(),
            )
        finally:
            driver_module.os.mkdir = original_mkdir
        self.assertEqual(envelope["status"], "preparation_error")
        self.assertEqual(envelope["error"], "run_root_protected")
        self.assertEqual(calls, [])
        self.assertFalse(target.exists())
        self.assertFalse((target / "driver.json").exists())

    def test_import_does_not_probe_paths(self):
        cwd = self.run_root / f"import-cwd-{uuid4().hex}"
        cwd.mkdir()
        code = (
            "import pathlib\n"
            "def boom(*args, **kwargs):\n"
            "    raise AssertionError('path probe during import')\n"
            "pathlib.Path.resolve = boom\n"
            "import episode.driver\n"
            "print('driver-import-ok')\n"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(OVERLAY)
        invocation = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=str(cwd), env=environment, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        self.assertIn("driver-import-ok", invocation.stdout)

    def test_cli_synthetic_smoke_and_required_flag(self):
        cwd = self.run_root / "cli-cwd"
        cwd.mkdir()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(OVERLAY)
        common = [
            sys.executable,
            "-B",
            "-m",
            "episode.driver",
            "--project-root",
            str(OVERLAY),
            "--scenario",
            "R1",
            "--episode-id",
            "cli-smoke",
            "--arm",
            "ordinary",
        ]
        run_root = self.new_run("cli-smoke")
        invocation = subprocess.run(
            common + ["--run-root", str(run_root), "--synthetic-smoke"],
            cwd=str(cwd),
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        payload = json.loads(invocation.stdout)
        self.assertIn("synthetic wiring smoke", payload["notice"].lower())
        self.assertEqual(payload["driver"]["status"], "episode_returned")
        self.assertEqual(payload["driver"]["result"]["status"], "partial_claim")
        self.assertEqual(
            payload["driver"]["result"]["evidence_kind"], "synthetic_episode_control"
        )
        self.assertTrue((run_root / "driver.json").is_file())

        refused_root = self.new_run("cli-refused")
        refused = subprocess.run(
            common + ["--run-root", str(refused_root)],
            cwd=str(cwd),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertFalse(refused_root.exists())


if __name__ == "__main__":
    unittest.main()
