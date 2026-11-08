"""Focused executable-rule tests for P0.3.2 outcomes/denominators."""

import unittest

from reporting import outcomes


def row(status="completed", **outcomes_values):
    values = {"functional": "pass", "workflow": "pass",
              "evidence": "pass", "completion": "pass"}
    values.update(outcomes_values)
    return {"status": status, "outcomes": values}


class StatusAndStrictTests(unittest.TestCase):
    def test_completed_claim_with_failing_grade_stays_completed(self):
        self.assertEqual(outcomes.status_from_result("completed_claim"), "completed")
        self.assertEqual(outcomes.status_from_result("partial_claim"), "partial")
        self.assertEqual(outcomes.status_from_result("limit_reached"), "limit_reached")
        self.assertEqual(outcomes.status_from_result("provider_error"), "provider_error")
        self.assertEqual(
            outcomes.status_from_result("infrastructure_error"),
            "infrastructure_error",
        )
        self.assertEqual(outcomes.status_from_result("protocol_error"), "invalid")
        self.assertEqual(
            outcomes.status_from_result("cancelled", "candidate"), "partial"
        )
        self.assertEqual(
            outcomes.status_from_result("cancelled", "operator"),
            "infrastructure_error",
        )
        self.assertEqual(
            outcomes.status_from_result("cancelled"), "infrastructure_error"
        )
        self.assertEqual(outcomes.status_from_result("unknown_state"), "invalid")

    def test_strict_joint_outcome_and_denominators(self):
        self.assertEqual(
            outcomes.strict_outcome(
                "completed", {"functional": "pass", "workflow": "pass",
                              "evidence": "pass", "completion": "pass"}
            ),
            "pass",
        )
        self.assertEqual(outcomes.strict_outcome("completed", row()["outcomes"]), "pass")
        self.assertEqual(
            outcomes.strict_outcome(
                "completed",
                {"functional": "fail", "workflow": "pass",
                 "evidence": "pass", "completion": "pass"},
            ),
            "fail",
        )
        self.assertEqual(
            outcomes.strict_outcome(
                "completed",
                {"functional": "unknown", "workflow": "pass",
                 "evidence": "pass", "completion": "pass"},
            ),
            "unknown",
        )
        self.assertEqual(outcomes.strict_outcome("partial", row()["outcomes"]), "fail")
        self.assertEqual(outcomes.strict_outcome("limit_reached", row()["outcomes"]), "fail")
        self.assertEqual(
            outcomes.strict_outcome("provider_error", row()["outcomes"]), "not_scored"
        )
        self.assertEqual(
            outcomes.strict_outcome("invalid", row()["outcomes"]), "not_scored"
        )

        summary = outcomes.denominator_summary(
            [
                row("completed"),
                row("partial", functional="fail"),
                row("infrastructure_error"),
                row("provider_error"),
            ]
        )
        self.assertEqual(summary["attempted"], 4)
        self.assertEqual(summary["eligible"], 2)
        self.assertEqual(summary["excluded"], 2)
        self.assertEqual(
            summary["strict_counts"],
            {"pass": 1, "fail": 1, "unknown": 0},
        )
        self.assertEqual(
            summary["success_fraction"], {"numerator": 1, "denominator": 2}
        )
        self.assertEqual(
            set(outcomes.strict_counts([row("provider_error")])),
            {"pass", "fail", "unknown"},
        )
        self.assertEqual(
            outcomes.strict_counts([row("provider_error", functional="fail")]),
            {"pass": 0, "fail": 0, "unknown": 0},
        )
        self.assertEqual(
            outcomes.strict_outcome("provider_error", {}), "not_scored"
        )

    def test_empty_group_fraction_denominator_zero(self):
        summary = outcomes.denominator_summary([])
        self.assertEqual(summary["success_fraction"], {"numerator": 0, "denominator": 0})


class ComponentPredicateTests(unittest.TestCase):
    def grade(self, **overrides):
        value = {
            "cleanup": "settled",
            "settlement": "known",
            "origin": "grader",
            "status": "passed",
            "functional_pass": True,
            "errors": [],
        }
        value.update(overrides)
        return value

    def test_functional_requires_settled_grader_receipt(self):
        self.assertEqual(outcomes.functional_from_grade(self.grade()), "pass")
        failed = self.grade(
            status="failed",
            functional_pass=False,
            errors=[{"origin": "grader", "detail": "check failure"}],
        )
        self.assertEqual(outcomes.functional_from_grade(failed), "fail")
        candidate_failure = self.grade(
            status="failed",
            functional_pass=False,
            errors=[{"origin": "candidate", "detail": "syntax failure"}],
        )
        self.assertEqual(outcomes.functional_from_grade(candidate_failure), "fail")
        self.assertEqual(outcomes.functional_from_grade(self.grade()), "pass")
        self.assertEqual(
            outcomes.functional_from_grade(self.grade(cleanup="unknown")), "unknown"
        )
        self.assertEqual(
            outcomes.functional_from_grade(self.grade(origin="candidate")), "unknown"
        )
        self.assertEqual(
            outcomes.functional_from_grade(
                self.grade(status="infrastructure_error", functional_pass=None)
            ),
            "unknown",
        )
        self.assertEqual(outcomes.functional_from_grade(None), "unknown")
        self.assertEqual(
            outcomes.functional_from_grade(
                self.grade(
                    status="failed",
                    functional_pass=False,
                    errors=[{"origin": "executor", "detail": "x"}],
                )
            ),
            "unknown",
        )
    def test_completion_is_claim_accuracy_not_grade_copy(self):
        self.assertEqual(
            outcomes.completion_outcome("complete", "completed_claim", "pass", True),
            "pass",
        )
        self.assertEqual(
            outcomes.completion_outcome("complete", "completed_claim", "fail", True),
            "fail",
        )
        self.assertEqual(
            outcomes.completion_outcome("complete", "completed_claim", "unknown", True),
            "unknown",
        )
        self.assertEqual(
            outcomes.completion_outcome("partial", "partial_claim", "fail", True),
            "pass",
        )
        self.assertEqual(
            outcomes.completion_outcome("partial", "limit_reached", "unknown", True),
            "pass",
        )
        self.assertEqual(
            outcomes.completion_outcome("partial", "completed_claim", "pass", True),
            "fail",
        )
        self.assertEqual(
            outcomes.completion_outcome(None, "completed_claim", "pass", True),
            "unknown",
        )
        self.assertEqual(
            outcomes.completion_outcome("complete", "completed_claim", "pass", False),
            "unknown",
        )

    def test_evidence_outcome_requires_binding(self):
        passed = {"status": "passed", "cleanup": "settled",
                  "candidate_sha256": "a" * 64,
                  "requirements_version": "v1"}
        stale = {"status": "passed", "cleanup": "settled",
                 "candidate_sha256": "b" * 64,
                 "requirements_version": "v1"}
        failed = dict(passed, status="failed")
        unknown = dict(passed, cleanup="unknown")
        binding = {"candidate_sha256": "a" * 64,
                   "requirements_version": "v1"}
        self.assertEqual(
            outcomes.evidence_outcome([passed], "settled", **binding), "pass"
        )
        self.assertEqual(
            outcomes.evidence_outcome([failed], "settled", **binding), "fail"
        )
        self.assertEqual(
            outcomes.evidence_outcome([unknown], "settled", **binding),
            "unknown",
        )
        self.assertEqual(
            outcomes.evidence_outcome([passed], "unknown", **binding), "unknown"
        )
        self.assertEqual(
            outcomes.evidence_outcome([stale], "settled", **binding), "unknown"
        )
        self.assertEqual(
            outcomes.evidence_outcome([passed], "settled"), "unknown"
        )
        self.assertEqual(outcomes.evidence_outcome([], "settled", **binding), "unknown")

    def test_workflow_is_only_supplied(self):
        self.assertEqual(outcomes.workflow_outcome(None), "unknown")
        self.assertEqual(outcomes.workflow_outcome("pass"), "pass")
        self.assertEqual(outcomes.workflow_outcome("fail"), "fail")
        self.assertEqual(outcomes.workflow_outcome("unknown"), "unknown")


class AttemptAndRepeatTests(unittest.TestCase):
    def test_attempt_retention_has_no_automatic_retry(self):
        classified = outcomes.classify_attempt(
            {"status": "provider_error", "stop_reason": "model_port_error"}
        )
        self.assertEqual(classified["status"], "provider_error")
        self.assertTrue(classified["excluded"])
        self.assertTrue(classified["no_automatic_retry"])
        self.assertEqual(
            classified["retry_policy"], "pending_freeze_no_automatic_retry"
        )
        self.assertEqual(
            outcomes.exclusion_reason("provider_error"),
            "provider_or_transport_failure",
        )

    def test_unchanged_input_repeat_is_diagnostic_only(self):
        observations = [
            {"candidate_sha256": "a" * 64, "requirements_version": "v1",
             "check_id": "C1", "evidence_id": "e1"},
            {"candidate_sha256": "a" * 64, "requirements_version": "v1",
             "check_id": "C1", "evidence_id": "e2"},
            {"candidate_sha256": "b" * 64, "requirements_version": "v1",
             "check_id": "C1", "evidence_id": "e3"},
            {"candidate_sha256": "a" * 64, "requirements_version": "v2",
             "check_id": "C1", "evidence_id": "e4"},
        ]
        flags = outcomes.unchanged_input_repeats(observations)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["candidate_sha256"], "a" * 64)
        self.assertEqual(flags[0]["requirements_version"], "v1")
        self.assertEqual(flags[0]["count"], 2)
        self.assertEqual(flags[0]["evidence_ids"], ["e1", "e2"])
        self.assertNotIn("penalty", flags[0])


if __name__ == "__main__":
    unittest.main()
