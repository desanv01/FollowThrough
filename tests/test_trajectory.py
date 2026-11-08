"""Minimal focused tests for P4-TRAJECTORY/r1 pure trajectory summaries.

All records are synthetic test-only constructions.  A ``protected_container_grading``
label in a negative/acceptance test is test data, not real container evidence.
"""

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reporting.trajectory import (  # noqa: E402
    MAX_SNAPSHOTS,
    SCHEMA_INPUT,
    SCHEMA_OUTPUT,
    TrajectoryError,
    summarize_trajectory,
)


def sha(label):
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def snapshot(
    checkpoint_id,
    seq,
    version="v1",
    outcome="pass",
    elapsed_ms=10,
    model_responses=1,
    evidence_kind="synthetic_development",
    candidate_sha256=None,
    graded=True,
):
    candidate = candidate_sha256 or sha(checkpoint_id)
    grade = None
    if graded:
        grade = {
            "candidate_sha256": candidate,
            "requirements_version": version,
            "outcome": outcome,
            "evidence_kind": evidence_kind,
        }
    return {
        "checkpoint_id": checkpoint_id,
        "seq": seq,
        "candidate_sha256": candidate,
        "requirements_version": version,
        "elapsed_ms": elapsed_ms,
        "model_responses": model_responses,
        "grade": grade,
    }


def make_input(*snapshots, evidence_kind="synthetic_development"):
    return {
        "schema_version": SCHEMA_INPUT,
        "episode_id": "episode-1",
        "evidence_kind": evidence_kind,
        "snapshots": list(snapshots),
    }


def first(checkpoint_id, seq, elapsed_ms, model_responses):
    return {
        "checkpoint_id": checkpoint_id,
        "seq": seq,
        "elapsed_ms": elapsed_ms,
        "model_responses": model_responses,
    }


def regression(before, after):
    return {
        "from_checkpoint_id": before["checkpoint_id"],
        "from_candidate_sha256": before["candidate_sha256"],
        "to_checkpoint_id": after["checkpoint_id"],
        "to_candidate_sha256": after["candidate_sha256"],
    }


class TrajectoryTests(unittest.TestCase):
    def assert_code(self, code, data):
        with self.assertRaises(TrajectoryError) as caught:
            summarize_trajectory(data)
        self.assertEqual(caught.exception.code, code)
        self.assertLessEqual(len(str(caught.exception)), 200)

    def test_fail_pass_fail_observes_first_correct_and_confirmed_regression(self):
        rows = [
            snapshot("ckpt-1", 1, outcome="fail", elapsed_ms=10, model_responses=2),
            snapshot("ckpt-2", 2, outcome="pass", elapsed_ms=20, model_responses=3),
            snapshot("ckpt-3", 3, outcome="fail", elapsed_ms=30, model_responses=4),
        ]
        result = summarize_trajectory(make_input(*rows))
        self.assertEqual(
            result,
            {
                "schema_version": SCHEMA_OUTPUT,
                "episode_id": "episode-1",
                "evidence_kind": "synthetic_development",
                "snapshots": rows,
                "segments": [
                    {
                        "requirements_version": "v1",
                        "start_seq": 1,
                        "end_seq": 3,
                        "snapshot_count": 3,
                        "known_pass": 1,
                        "known_fail": 2,
                        "unknown": 0,
                        "first_observed_correct": first("ckpt-2", 2, 20, 3),
                        "observed_regressions": [regression(rows[1], rows[2])],
                    }
                ],
            },
        )

    def test_pass_unknown_fail_has_no_confirmed_regression(self):
        rows = [
            snapshot("ckpt-1", 1, outcome="pass", elapsed_ms=1, model_responses=1),
            snapshot("ckpt-2", 2, outcome="unknown", elapsed_ms=2, model_responses=1),
            snapshot("ckpt-3", 3, outcome="fail", elapsed_ms=3, model_responses=1),
        ]
        result = summarize_trajectory(make_input(*rows))
        self.assertEqual(
            result["segments"],
            [
                {
                    "requirements_version": "v1",
                    "start_seq": 1,
                    "end_seq": 3,
                    "snapshot_count": 3,
                    "known_pass": 1,
                    "known_fail": 1,
                    "unknown": 1,
                    "first_observed_correct": first("ckpt-1", 1, 1, 1),
                    "observed_regressions": [],
                }
            ],
        )

    def test_pinning_a_version_change_does_not_create_a_cross_version_regression(self):
        rows = [
            snapshot("ckpt-1", 1, version="v1", outcome="pass"),
            snapshot("ckpt-2", 2, version="v2", outcome="fail"),
        ]
        result = summarize_trajectory(make_input(*rows))
        self.assertEqual(
            result["segments"],
            [
                {
                    "requirements_version": "v1",
                    "start_seq": 1,
                    "end_seq": 1,
                    "snapshot_count": 1,
                    "known_pass": 1,
                    "known_fail": 0,
                    "unknown": 0,
                    "first_observed_correct": first("ckpt-1", 1, 10, 1),
                    "observed_regressions": [],
                },
                {
                    "requirements_version": "v2",
                    "start_seq": 2,
                    "end_seq": 2,
                    "snapshot_count": 1,
                    "known_pass": 0,
                    "known_fail": 1,
                    "unknown": 0,
                    "first_observed_correct": None,
                    "observed_regressions": [],
                },
            ],
        )

    def test_empty_input_has_empty_segments(self):
        self.assertEqual(
            summarize_trajectory(make_input()),
            {
                "schema_version": SCHEMA_OUTPUT,
                "episode_id": "episode-1",
                "evidence_kind": "synthetic_development",
                "snapshots": [],
                "segments": [],
            },
        )

    def test_all_unknown_including_null_grades_stays_unknown(self):
        rows = [
            snapshot("ckpt-1", 1, graded=False, elapsed_ms=None, model_responses=None),
            snapshot("ckpt-2", 2, outcome="unknown"),
        ]
        result = summarize_trajectory(make_input(*rows))
        self.assertEqual(
            result["segments"],
            [
                {
                    "requirements_version": "v1",
                    "start_seq": 1,
                    "end_seq": 2,
                    "snapshot_count": 2,
                    "known_pass": 0,
                    "known_fail": 0,
                    "unknown": 2,
                    "first_observed_correct": None,
                    "observed_regressions": [],
                }
            ],
        )
        self.assertIsNone(result["snapshots"][0]["grade"])
        self.assertIsNone(result["snapshots"][0]["elapsed_ms"])

    def test_hash_and_version_identity_mismatch_are_refused(self):
        row = snapshot("ckpt-1", 1)
        row["grade"]["candidate_sha256"] = sha("different-candidate")
        self.assert_code("identity_mismatch", make_input(row))

        row = snapshot("ckpt-1", 1)
        row["grade"]["requirements_version"] = "v2"
        self.assert_code("identity_mismatch", make_input(row))

    def test_mixed_provenance_duplicates_and_sequence_are_refused(self):
        row = snapshot("ckpt-1", 1)
        row["grade"]["evidence_kind"] = "protected_container_grading"
        self.assert_code("mixed_provenance", make_input(row))

        duplicate = snapshot("same", 2)
        self.assert_code(
            "duplicate_checkpoint",
            make_input(snapshot("same", 1), duplicate),
        )
        self.assert_code(
            "invalid_sequence",
            make_input(snapshot("ckpt-1", 2), snapshot("ckpt-2", 1)),
        )

    def test_schema_and_snapshot_bound_are_refused(self):
        self.assert_code(
            "unsupported_schema",
            {
                "schema_version": "trajectory-input-0.2",
                "episode_id": "episode-1",
                "evidence_kind": "synthetic_development",
                "snapshots": [],
            },
        )
        self.assert_code(
            "input_too_large",
            make_input(*([snapshot("ckpt-1", 1)] * (MAX_SNAPSHOTS + 1))),
        )


if __name__ == "__main__":
    unittest.main()
