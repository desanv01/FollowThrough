"""Focused E2E live-check call-site witnesses. Synthetic only; no provider."""

import json
import shutil
import unittest
import uuid
from pathlib import Path

from episode import e2e

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT.parent / "e2e-scratch"


def real_check(request, cleanup="settled"):
    return {
        "schema_version": "check-port-0.1",
        "request_id": request["request_id"],
        "evidence_kind": "real_container_check",
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": "infrastructure_error",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "cleanup": cleanup,
    }


def protected_grade(candidate_sha, candidate_path):
    return {
        "candidate_sha256": candidate_sha,
        "requirements_version": "v1",
        "status": "passed",
        "functional_pass": True,
        "cleanup": "settled",
        "settlement": "known",
        "origin": "grader",
        "evidence_kind": "protected_container_grading",
        "errors": [],
    }


def scripted_port(run_root):
    correct = (ROOT / "references" / "retry-processing" / "correct" /
               "processor.py").read_text(encoding="utf-8")
    return e2e.scripted_r1_model_port(run_root / "package" / "agent", correct)


class E2ELiveCheckTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.run_root = SCRATCH / ("run-" + uuid.uuid4().hex)
        self.operation = {
            "evidence_kind": "real_provider",
            "operation_id": "r6-check-call",
            "run_id": "r6-check-call:run",
            "stage": "development",
        }

    def tearDown(self):
        shutil.rmtree(self.run_root, ignore_errors=True)

    def test_live_real_setup_error_is_infra_and_independent_grade_is_separate(self):
        result = e2e.run_scenario_smoke(
            self.run_root, episode_id="r6-check-call",
            model_port=scripted_port(self.run_root),
            check_port=lambda request: real_check(request),
            grade_fn=protected_grade, live=True, operation=self.operation,
            check_evidence_kind="real_container_check",
        )
        self.assertTrue(result["grade_started"])
        self.assertEqual(result["row"]["status"], "infrastructure_error")
        self.assertEqual(result["result"]["stop_reason"], "check_infrastructure_error")
        self.assertEqual(result["result"]["status"], "infrastructure_error")
        self.assertEqual(result["row"]["outcomes"]["functional"], "pass")
        self.assertEqual(result["row"]["outcomes"]["workflow"], "unknown")
        self.assertEqual(result["row"]["outcomes"]["evidence"], "unknown")
        self.assertEqual(result["row"]["outcomes"]["completion"], "unknown")
        self.assertIn("check", [
            event.get("kind") for event in result["row"]["trace"]
        ])
        self.assertEqual(
            {event.get("role") for event in result["row"]["trace"]},
            {"controller", "O1", "W1"},
        )
        self.assertTrue((self.run_root / "protected-grade.json").is_file())

    def test_live_synthetic_and_synthetic_real_provenance_are_refused(self):
        with self.assertRaises(e2e.E2EError) as live:
            e2e.run_scenario_smoke(
                self.run_root, episode_id="r6-check-refuse-live-synthetic",
                model_port=scripted_port(self.run_root),
                check_port=lambda request: {
                    "schema_version": "check-port-0.1",
                    "request_id": request["request_id"],
                    "evidence_kind": "synthetic_check",
                    "candidate_sha256": request["candidate_sha256"],
                    "requirements_version": request["requirements_version"],
                    "status": "passed", "exit_code": 0,
                    "stdout": "", "stderr": "", "cleanup": "settled",
                },
                grade_fn=protected_grade, live=True, operation=self.operation,
                check_evidence_kind="synthetic_check",
            )
        self.assertEqual(live.exception.code, "live_check_provenance_required")
        with self.assertRaises(e2e.E2EError) as synthetic:
            e2e.run_scenario_smoke(
                self.run_root / "synthetic-real", episode_id="r6-check-refuse-synthetic-real",
                model_port=scripted_port(self.run_root / "synthetic-real"),
                check_port=lambda request: real_check(request),
                grade_fn=protected_grade, live=False,
                check_evidence_kind="real_container_check",
            )
        self.assertEqual(synthetic.exception.code, "synthetic_check_provenance_required")

    def test_unknown_cleanup_skips_grade_and_stays_infrastructure(self):
        result = e2e.run_scenario_smoke(
            self.run_root, episode_id="r6-check-unknown-cleanup",
            model_port=scripted_port(self.run_root),
            check_port=lambda request: real_check(request, cleanup="unknown"),
            grade_fn=protected_grade, live=True,
            check_evidence_kind="real_container_check",
            operation=dict(self.operation, operation_id="r6-check-unknown",
                           run_id="r6-check-unknown:run"),
        )
        self.assertFalse(result["grade_started"])
        self.assertIsNone(result["grade"])
        self.assertEqual(result["row"]["status"], "infrastructure_error")
        self.assertFalse((self.run_root / "protected-grade.json").exists())
        self.assertTrue((self.run_root / "grade-skipped.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
