"""Focused tests for the P4 runtime report projection."""

import unittest

from reporting import runtime_projection
from reporting.report import build_report

CANDIDATE = "a" * 64


def event(event_type, **fields):
    value = {"type": event_type, "role": "controller", "elapsed_ms": 100,
             "evidence_kind": "synthetic_development"}
    value.update(fields)
    return value


def result(status="completed_claim", claim="complete", candidate=CANDIDATE,
           version="v1", counts=None, usage=None, **extra):
    value = {
        "episode_id": "proj-r1",
        "scenario_id": "R1",
        "arm": "ordinary",
        "status": status,
        "stop_reason": "finish_" + (claim or status),
        "final_claim": (
            {"claimed_status": claim, "summary": "claim", "evidence_ids": [],
             "remaining": []}
            if claim is not None else None
        ),
        "current_candidate_sha256": candidate,
        "current_requirements_version": version,
        "counts": counts or {"model_responses_total": 3,
                             "executed_actions": 2},
        "usage": usage or {
            "input_tokens": {"known_sum": 10, "known_count": 1,
                             "unknown_count": 0},
            "output_tokens": {"known_sum": 5, "known_count": 1,
                              "unknown_count": 0},
        },
    }
    value.update(extra)
    return value


def grade(status="passed", cleanup="settled", candidate=CANDIDATE,
          version="v1"):
    return {
        "candidate_sha256": candidate,
        "requirements_version": version,
        "status": status,
        "functional_pass": status == "passed",
        "cleanup": cleanup,
        "settlement": "known" if cleanup == "settled" else "unknown",
        "origin": "grader",
        "errors": [] if status == "passed" else
        ([{"origin": "grader"}] if status == "failed" else []),
    }


def source(candidate=CANDIDATE, version="v1"):
    return {"candidate_sha256": candidate, "requirements_version": version}


def project(**overrides):
    values = {
        "result": result(),
        "events": [
            event("episode_start"),
            event("model_response", role="O1"),
            event("check_result", role="W1", status="passed",
                  cleanup="settled", candidate_sha256=CANDIDATE,
                  requirements_version="v1"),
            event("finish_claim", role="O1"),
        ],
        "grade": grade(),
        "source": source(),
        "evidence_kind": "synthetic_development",
        "stage": "development",
        "trial": 1,
    }
    values.update(overrides)
    return runtime_projection.project_episode(**values)


class RuntimeProjectionTests(unittest.TestCase):
    def test_ordinary_completed_row_binds_and_keeps_workflow_unknown(self):
        row, binding = project()
        self.assertTrue(binding["binding_match"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["outcomes"]["functional"], "pass")
        self.assertEqual(row["outcomes"]["workflow"], "unknown")
        self.assertEqual(row["outcomes"]["evidence"], "pass")
        self.assertEqual(row["outcomes"]["completion"], "pass")
        self.assertEqual(row["resources"]["input_tokens"], 10)
        self.assertEqual(row["resources"]["output_tokens"], 5)
        self.assertIsNone(row["resources"]["estimated_cost_micro_usd"])
        self.assertIsNone(row["resources"]["reserved_micro_usd"])
        self.assertEqual(row["trace"][0]["kind"], "supplied_fixture")
        self.assertEqual(row["trace"][1]["kind"], "message")
        self.assertNotIn("Raw candidate output", row["trace"][1]["summary"])
        report = build_report(runtime_projection.project_report_input(
            "projection-test", [row]))
        self.assertEqual(report["episodes"][0]["strict_outcome"], "unknown")

    def test_completed_execution_with_failing_grade_stays_completed(self):
        row, _ = project(grade=grade(status="failed"))
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["outcomes"]["functional"], "fail")
        self.assertEqual(row["outcomes"]["completion"], "fail")
        report = build_report(runtime_projection.project_report_input(
            "projection-fail", [row]))
        self.assertEqual(report["episodes"][0]["strict_outcome"], "fail")

    def test_provider_error_is_excluded_not_partial(self):
        row, binding = project(
            result=result(status="provider_error", claim=None),
            grade=grade(status="infrastructure_error", cleanup="unknown"),
        )
        self.assertTrue(binding["binding_match"])
        self.assertEqual(row["status"], "provider_error")
        self.assertEqual(
            row["outcomes"],
            {"functional": "unknown", "workflow": "unknown",
             "evidence": "unknown", "completion": "unknown"},
        )
        report = build_report(runtime_projection.project_report_input(
            "projection-error", [row]))
        self.assertEqual(report["episodes"][0]["strict_outcome"], "not_scored")

    def test_honest_partial_claim_completion_passes_but_strict_fails(self):
        row, _ = project(
            result=result(status="partial_claim", claim="partial"),
            grade=grade(status="failed"),
        )
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["outcomes"]["completion"], "pass")
        report = build_report(runtime_projection.project_report_input(
            "projection-partial", [row]))
        self.assertEqual(report["episodes"][0]["strict_outcome"], "fail")

    def test_identity_mismatch_invalidates_the_row(self):
        row, binding = project(source=source(candidate="b" * 64))
        self.assertFalse(binding["binding_match"])
        self.assertEqual(row["status"], "invalid")
        self.assertEqual(set(row["outcomes"].values()), {"unknown"})
        report = build_report(runtime_projection.project_report_input(
            "projection-mismatch", [row]))
        self.assertEqual(report["episodes"][0]["strict_outcome"], "not_scored")

    def test_unknown_usage_and_operation_amounts_are_not_zero(self):
        row, _ = project(
            result=result(usage={
                "input_tokens": {"known_sum": 0, "known_count": 0,
                                 "unknown_count": 1},
                "output_tokens": {"known_sum": 0, "known_count": 0,
                                  "unknown_count": 1},
            }),
            operation={"estimated_cost_micro_usd": 20,
                       "reserved_micro_usd": 100},
        )
        self.assertIsNone(row["resources"]["input_tokens"])
        self.assertIsNone(row["resources"]["output_tokens"])
        self.assertEqual(row["resources"]["estimated_cost_micro_usd"], 20)
        self.assertEqual(row["resources"]["reserved_micro_usd"], 100)

    def test_stale_check_receipt_keeps_evidence_unknown(self):
        events = [
            event("check_result", role="W1", status="passed",
                  cleanup="settled", candidate_sha256="b" * 64,
                  requirements_version="v1"),
        ]
        row, _ = project(events=events)
        self.assertEqual(row["outcomes"]["evidence"], "unknown")

    def test_candidate_execution_failure_is_functional_fail(self):
        failed = grade(status="failed")
        failed["errors"] = [{"origin": "candidate"}]
        row, _ = project(grade=failed)
        self.assertEqual(row["outcomes"]["functional"], "fail")

    def test_provider_provenance_and_stage_consistency(self):
        with self.assertRaises(runtime_projection.ProjectionError):
            project(evidence_kind="synthetic_development", stage="pilot")
        row, _ = project(evidence_kind="real_provider", stage="development")
        self.assertEqual(row["evidence_kind"], "real_provider")
        row, _ = project(evidence_kind="real_provider", stage="formal")
        self.assertEqual(row["evidence_kind"], "real_provider")
        self.assertEqual(row["stage"], "formal")

    def test_trace_is_bounded_and_sanitized(self):
        events = [event("model_response", type="model_response",
                        action="x" * 40, stdout="secret raw output")]
        row, _ = project(events=events)
        self.assertEqual(row["trace"][0]["seq"], 1)
        self.assertNotIn("secret raw output", row["trace"][0]["summary"])
        self.assertLessEqual(len(row["trace"][0]["summary"]), 256)

    def test_malformed_event_is_skipped_not_crashing(self):
        row, _ = project(events=[{"role": "W1", "status": "done"}])
        self.assertEqual(row["trace"], [])
        self.assertEqual(row["outcomes"]["evidence"], "unknown")

    def test_supplied_receipt_is_fixture_provenance(self):
        row, _ = project(events=[
            event("supplied_receipt", role="controller",
                  summary="C3 authored v1 receipt"),
        ])
        self.assertEqual(row["trace"][0]["kind"], "supplied_fixture")
        self.assertEqual(row["trace"][0]["role"], "controller")
        self.assertNotIn("prompt", row["trace"][0]["summary"])

    def test_operation_identity_is_not_episode_identity(self):
        row, binding = project()
        self.assertIsNone(binding["run_id"])
        self.assertIsNone(binding["operation_id"])
        row, binding = project(operation={
            "run_id": "op-run-1",
            "operation_id": "op-0001",
            "estimated_cost_micro_usd": 20,
            "reserved_micro_usd": 100,
        })
        self.assertEqual(binding["run_id"], "op-run-1")
        self.assertEqual(binding["operation_id"], "op-0001")
        self.assertNotEqual(binding["run_id"], binding["episode_id"])

    def test_invalid_identifiers_are_refused_before_row(self):
        for label, values, code in [
            ("scenario", result(scenario_id="ZZ"), "invalid_scenario"),
            ("arm", result(arm="wrong"), "invalid_arm"),
            ("episode", result(episode_id="bad id"), "invalid_episode_id"),
        ]:
            with self.subTest(label=label):
                with self.assertRaises(runtime_projection.ProjectionError):
                    project(result=values)

    def test_elapsed_prefers_cumulative_result_value(self):
        row, _ = project(result=result(elapsed_ms=5000))
        self.assertEqual(row["resources"]["elapsed_ms"], 5000)
        row, _ = project()
        self.assertEqual(row["resources"]["elapsed_ms"], 100)
        row, _ = project(result=result(elapsed_ms=None),
                         operation={"elapsed_ms": 700})
        self.assertEqual(row["resources"]["elapsed_ms"], 700)


if __name__ == "__main__":
    unittest.main()
