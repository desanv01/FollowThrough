"""Six-scenario/two-arm synthetic composition tests for the P4 e2e seam."""

import json
import shutil
import unittest
import uuid
from pathlib import Path

from episode import e2e
from reporting import runtime_projection
from reporting.report import build_report

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT.parent / "six-scenario-scratch"
SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
ARMS = ("ordinary", "structured_progress")
VERSION = {"R1": "v1", "R2": "v1", "R3": "v1", "C1": "v2",
           "C2": "v2", "C3": "v2"}


def check_port(request):
    return {
        "schema_version": "check-port-0.1",
        "request_id": request["request_id"],
        "evidence_kind": "synthetic_check",
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": "passed",
        "exit_code": 0,
        "stdout": "authored public checks passed\n",
        "stderr": "",
        "cleanup": "settled",
    }


def partial_port(scenario_id):
    filename = "preview.py" if scenario_id in ("C1", "C2", "C3") else "processor.py"
    sequence = [
        {"name": "delegate", "target": "W1", "task": "Inspect the candidate.",
         "claimed_source_sha256": "0" * 64,
         "claimed_requirements_version": VERSION[scenario_id]},
        {"name": "read", "filename": filename},
        {"name": "check"},
        {"name": "report", "summary": "Synthetic composition report.",
         "evidence_ids": ["synthetic-check"]},
        {"name": "finish", "claimed_status": "partial",
         "summary": "Synthetic composition stopped after a safe partial.",
         "remaining": ["real protected grading"], "evidence_ids": []},
    ]

    def port(request):
        if not sequence:
            raise AssertionError("scripted model port exhausted")
        action = sequence.pop(0)
        return {
            "action": action,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "requested_model": "stub-requested",
            "returned_model": "stub-returned",
        }

    return port


def grade_fn(scenario_id):
    def grade(candidate_sha, candidate_path):
        return {
            "candidate_sha256": candidate_sha,
            "requirements_version": VERSION[scenario_id],
            "evidence_kind": "synthetic_development",
            "status": "passed",
            "functional_pass": True,
            "cleanup": "settled",
            "settlement": "known",
            "origin": "grader",
            "errors": [],
        }

    return grade


class SixScenarioTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.root = SCRATCH / ("cells-" + uuid.uuid4().hex)
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_all_six_scenarios_and_both_arms(self):
        rows = []
        observed = set()
        for scenario_id in SCENARIOS:
            for arm in ARMS:
                run_root = self.root / (scenario_id + "-" + arm)
                result = e2e.run_scenario_smoke(
                    run_root,
                    episode_id="smoke-" + scenario_id + "-" + arm,
                    scenario_id=scenario_id,
                    arm=arm,
                    check_port=check_port,
                    model_port=partial_port(scenario_id),
                    grade_fn=grade_fn(scenario_id),
                    evidence_kind="synthetic_development",
                    stage="development",
                )
                self.assertIsNone(result["envelope"].get("error"))
                self.assertTrue(result["binding"]["binding_match"])
                self.assertEqual(result["row"]["scenario_id"], scenario_id)
                self.assertEqual(result["row"]["arm"], arm)
                self.assertEqual(result["row"]["stage"], "development")
                self.assertEqual(result["row"]["evidence_kind"],
                                 "synthetic_development")
                self.assertEqual(result["row"]["status"], "partial")
                self.assertEqual(result["row"]["outcomes"]["functional"], "pass")
                self.assertEqual(result["row"]["outcomes"]["evidence"], "pass")
                self.assertEqual(result["row"]["outcomes"]["workflow"], "unknown")
                self.assertEqual(result["row"]["outcomes"]["completion"], "pass")
                self.assertEqual(result["result"]["scenario_id"], scenario_id)
                self.assertEqual(result["result"]["arm"], arm)
                observed.add((scenario_id, arm))
                if scenario_id in ("C2", "C3"):
                    self.assertEqual(result["result"]["current_requirements_version"],
                                     "v2")
                    events = [json.loads(line) for line in
                              (run_root / "episode" / "events.jsonl")
                              .read_text(encoding="utf-8").splitlines()
                              if line.strip()]
                    update = next(e for e in events
                                  if e["type"] == "requirements_update")
                    first_response = next(e for e in events
                                          if e["type"] == "model_response")
                    self.assertLess(update["seq"], first_response["seq"])
                    if scenario_id == "C3":
                        receipt = next(e for e in events
                                       if e["type"] == "supplied_receipt")
                        self.assertGreater(receipt["seq"], first_response["seq"])
                        self.assertEqual(receipt["source_sha256"],
                                         result["candidate_sha256"])
                rows.append(result["row"])
        self.assertEqual(observed, {(s, a) for s in SCENARIOS for a in ARMS})
        report = build_report(runtime_projection.project_report_input(
            "six-scenario-synthetic", rows))
        self.assertEqual(report["inventory"]["attempted"], 12)
        self.assertEqual(len(report["groups"]), 12)
        self.assertEqual(report["inventory"]["stage_counts"]["development"], 12)
        self.assertEqual(report["inventory"]["evidence_counts"]
                         ["synthetic_development"], 12)
        for group in report["groups"]:
            self.assertEqual(group["attempted"], 1)
            self.assertEqual(group["strict_counts"]["fail"], 1)


if __name__ == "__main__":
    unittest.main()
