"""Focused tests for the P4 R1 episode-identity seam. Synthetic only."""

import hashlib
import inspect
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from episode import e2e

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT.parent / "e2e-scratch"
A = "a" * 64
B = "b" * 64


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


def grade_fn(candidate_sha, candidate_path):
    return {
        "candidate_sha256": candidate_sha,
        "requirements_version": "v1",
        "status": "passed",
        "functional_pass": True,
        "cleanup": "settled",
        "settlement": "known",
        "origin": "grader",
        "errors": [],
    }


def protected_grade_fn(candidate_sha, candidate_path):
    value = grade_fn(candidate_sha, candidate_path)
    value["evidence_kind"] = "protected_container_grading"
    return value


def scratch_run():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = SCRATCH / ("run-" + uuid.uuid4().hex)
    return path


class E2EIdentityTests(unittest.TestCase):
    def setUp(self):
        self.run_root = scratch_run()

    def tearDown(self):
        shutil.rmtree(self.run_root, ignore_errors=True)

    def test_reviewed_episode_id_is_preserved(self):
        result = e2e.run_r1_smoke(
            self.run_root,
            episode_id="reviewed-r1-01",
            check_port=check_port,
            grade_fn=grade_fn,
        )
        self.assertEqual(result["episode_id"], "reviewed-r1-01")
        self.assertEqual(result["result"]["episode_id"], "reviewed-r1-01")
        self.assertEqual(result["row"]["episode_id"], "reviewed-r1-01")
        self.assertTrue(result["binding"]["binding_match"])
        self.assertEqual(result["binding"]["episode_id"], "reviewed-r1-01")
        self.assertIsNone(result["binding"]["run_id"])
        self.assertEqual(result["report"]["episodes"][0]["scenario_id"], "R1")
        self.assertEqual(result["row"]["outcomes"]["functional"], "pass")
        self.assertEqual(result["row"]["outcomes"]["workflow"], "unknown")
        for name in ("report-input.json", "report.json", "report.md",
                     "protected-grade.json", "episode-binding.json",
                     "progress-events.jsonl", "progress.json", "progress.txt",
                     "progress-meta.json"):
            self.assertTrue((self.run_root / name).is_file(), name)
        self.assertEqual(result["progress"]["state"], "completed_claim")
        self.assertEqual(result["progress"]["counts"]["model_responses"], 9)
        meta = json.loads((self.run_root / "progress-meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "completed_claim")
        progress_text = (self.run_root / "progress.txt").read_text(encoding="utf-8")
        self.assertIn("completed_claim", progress_text)
        self.assertIn("estimate=unknown", progress_text)
        self.assertNotIn("prompt", progress_text.lower())
        raw = (self.run_root / "progress-events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("stdout", raw)
        self.assertNotIn("stderr", raw)

    def test_protected_grade_is_labelled_real_provider(self):
        result = e2e.run_r1_smoke(
            self.run_root,
            episode_id="reviewed-r1-protected",
            check_port=check_port,
            grade_fn=protected_grade_fn,
        )
        self.assertEqual(result["row"]["evidence_kind"], "real_provider")
        self.assertEqual(result["row"]["stage"], "development")
        self.assertEqual(result["row"]["outcomes"]["functional"], "pass")
        self.assertEqual(result["row"]["outcomes"]["evidence"], "pass")

    def test_runner_accepts_episode_id_for_p3(self):
        parameters = inspect.signature(e2e.run_r1_smoke).parameters
        self.assertIn("episode_id", parameters)
        self.assertEqual(parameters["episode_id"].kind,
                         inspect.Parameter.KEYWORD_ONLY)

    def test_invalid_identity_and_missing_grade_are_refused(self):
        with self.assertRaises(e2e.E2EError):
            e2e.run_r1_smoke(self.run_root, episode_id="bad id",
                             grade_fn=grade_fn)
        with self.assertRaises(e2e.E2EError):
            e2e.run_r1_smoke(self.run_root, episode_id="valid-id")

    def test_mismatched_grade_excludes_the_row(self):
        def wrong_grade(candidate_sha, candidate_path):
            value = grade_fn(candidate_sha, candidate_path)
            value["candidate_sha256"] = B
            return value

        result = e2e.run_r1_smoke(
            self.run_root,
            episode_id="reviewed-r1-mismatch",
            check_port=check_port,
            grade_fn=wrong_grade,
        )
        self.assertFalse(result["binding"]["binding_match"])
        self.assertEqual(result["row"]["status"], "invalid")
        self.assertEqual(set(result["row"]["outcomes"].values()), {"unknown"})
        self.assertEqual(result["report"]["episodes"][0]["strict_outcome"],
                         "not_scored")

    def test_different_episode_ids_do_not_collide(self):
        self.run_root.mkdir(parents=True, exist_ok=True)
        first = e2e.run_r1_smoke(
            self.run_root / "one", episode_id="episode-one",
            check_port=check_port, grade_fn=grade_fn,
        )
        second = e2e.run_r1_smoke(
            self.run_root / "two", episode_id="episode-two",
            check_port=check_port, grade_fn=grade_fn,
        )
        self.assertNotEqual(first["row"]["episode_id"],
                            second["row"]["episode_id"])
        self.assertEqual(first["row"]["episode_id"], "episode-one")
        self.assertEqual(second["row"]["episode_id"], "episode-two")


if __name__ == "__main__":
    unittest.main()
