"""Focused tests for runtime checkpoint trajectory projection."""

import unittest

from reporting import trajectory_projection
from reporting.trajectory import summarize_trajectory

A = "a" * 64
B = "b" * 64


def receipt(checkpoint_id, candidate, version="v1", status="passed",
            cleanup="settled", evidence="protected_container_grading",
            errors=None):
    return {
        "checkpoint_id": checkpoint_id,
        "candidate_sha256": candidate,
        "requirements_version": version,
        "evidence_kind": evidence,
        "status": status,
        "functional_pass": status == "passed",
        "cleanup": cleanup,
        "origin": "grader",
        "errors": errors if errors is not None else (
            [] if status == "passed" else [{"origin": "grader"}]
        ),
    }


class TrajectoryProjectionTests(unittest.TestCase):
    def test_ordered_checkpoints_form_observed_regression(self):
        data, binding = trajectory_projection.project_trajectory(
            "episode-1",
            "protected_container_grading",
            [
                receipt("c1", A),
                receipt("c2", B, status="failed"),
            ],
            resources_by_checkpoint={
                "c1": {"elapsed_ms": 10, "model_responses": 1},
                "c2": {"elapsed_ms": 20, "model_responses": 2},
            },
            source={"candidate_sha256": B, "requirements_version": "v1"},
        )
        summary = summarize_trajectory(data)
        self.assertEqual(binding["checkpoint_ids"], ["c1", "c2"])
        self.assertEqual(summary["segments"][0]["known_pass"], 1)
        self.assertEqual(summary["segments"][0]["known_fail"], 1)
        self.assertEqual(
            summary["segments"][0]["first_observed_correct"]["checkpoint_id"], "c1"
        )
        self.assertEqual(len(summary["segments"][0]["observed_regressions"]), 1)
        self.assertEqual(
            summary["segments"][0]["observed_regressions"][0]["from_checkpoint_id"],
            "c1",
        )
        self.assertEqual(
            summary["segments"][0]["observed_regressions"][0]["to_checkpoint_id"],
            "c2",
        )
        self.assertEqual(data["schema_version"], "trajectory-input-0.1")

    def test_ungraded_checkpoint_stays_null_unknown(self):
        data, _ = trajectory_projection.project_trajectory(
            "episode-2",
            "protected_container_grading",
            [
                receipt("c1", A),
                receipt("c2", A, cleanup="unknown"),
            ],
        )
        self.assertIsNone(data["snapshots"][1]["grade"])
        summary = summarize_trajectory(data)
        self.assertEqual(summary["segments"][0]["unknown"], 1)
        self.assertEqual(summary["segments"][0]["known_pass"], 1)
        self.assertEqual(summary["segments"][0]["observed_regressions"], [])

    def test_identity_and_provenance_refusals(self):
        with self.assertRaises(trajectory_projection.TrajectoryProjectionError):
            trajectory_projection.project_trajectory(
                "episode-3",
                "synthetic_development",
                [receipt("c1", A, evidence="protected_container_grading")],
            )
        with self.assertRaises(trajectory_projection.TrajectoryProjectionError):
            trajectory_projection.project_trajectory(
                "episode-4",
                "protected_container_grading",
                [receipt("", A)],
            )
        with self.assertRaises(trajectory_projection.TrajectoryProjectionError):
            trajectory_projection.project_trajectory(
                "episode-5",
                "protected_container_grading",
                [receipt("c1", A), receipt("c1", B)],
            )
        with self.assertRaises(trajectory_projection.TrajectoryProjectionError):
            trajectory_projection.project_trajectory(
                "episode-6",
                "protected_container_grading",
                [receipt("c1", A, version="v3")],
            )

    def test_candidate_failure_is_graded_fail_not_null(self):
        data, _ = trajectory_projection.project_trajectory(
            "episode-8",
            "protected_container_grading",
            [receipt("c1", A),
             receipt("c2", B, status="failed",
                     errors=[{"origin": "candidate"}])],
        )
        self.assertEqual(data["snapshots"][1]["grade"]["outcome"], "fail")
        summary = summarize_trajectory(data)
        self.assertEqual(summary["segments"][0]["known_fail"], 1)

    def test_provider_failure_and_unknown_cleanup_stay_ungraded(self):
        data, _ = trajectory_projection.project_trajectory(
            "episode-9",
            "protected_container_grading",
            [receipt("c1", A, status="failed", cleanup="settled",
                     errors=[{"origin": "provider"}]),
             receipt("c2", B, cleanup="unknown")],
        )
        self.assertIsNone(data["snapshots"][0]["grade"])
        self.assertIsNone(data["snapshots"][1]["grade"])

    def test_no_sorting_is_documented_and_order_preserved(self):
        data, binding = trajectory_projection.project_trajectory(
            "episode-7",
            "synthetic_development",
            [receipt("c2", A, evidence="synthetic_development"),
             receipt("c1", B, evidence="synthetic_development")],
        )
        self.assertEqual([row["checkpoint_id"] for row in data["snapshots"]],
                         ["c2", "c1"])
        self.assertEqual(binding["order"], "supplied_order_preserved_no_sorting")


if __name__ == "__main__":
    unittest.main()
