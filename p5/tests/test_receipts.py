"""Focused tests for P3 attempt-receipt validation."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from p5_tools import receipts  # noqa: E402


def valid_receipt():
    return {
        "schema_version": "attempt-receipt-0.1",
        "attempt_id": "att-001",
        "run_id": "run-001",
        "campaign_id": "followthrough-local-r1",
        "episode_id": "ep-r1-ord-1",
        "stage": "pilot",
        "scenario_id": "R1",
        "arm": "ordinary",
        "trial": 1,
        "dataset_id": "p5-pilot-proposed",
        "requested_model": "deepseek-flash",
        "returned_model": "deepseek-flash-returned",
        "candidate_sha256": "a" * 64,
        "requirements_version": "v1",
        "dispatch": {"attempted": True, "stop_status": "completed",
                     "classification": "model", "outcome_unknown": False},
        "bounds": {"max_calls": 16, "max_output_tokens_per_call": 2048,
                   "max_call_seconds": 25, "max_call_reservation_micro_usd": 50000,
                   "max_operation_reservation_micro_usd": 800000, "max_wall_seconds": 900,
                   "call_count_used": 9, "wall_seconds_used": 12.5},
        "budget": {"operation_ids": ["op-1"], "reserved_micro_usd": 50000,
                   "estimated_cost_micro_usd": 1200, "settlement": "settled",
                   "remaining_balance_micro_usd": 19000000},
        "usage": {"model_responses": 9, "tool_actions": 8, "input_tokens": 100,
                  "output_tokens": 50, "cache_hit_input_tokens": None,
                  "uncached_input_tokens": 100, "reasoning_tokens": None,
                  "known_counts": {"input_tokens": 1}, "unknown_counts": {"cache_hit_input_tokens": 1},
                  "elapsed_seconds": 12.5},
        "cleanup": {"readback": "settled", "source": "p2-receipt",
                    "container_or_run_absent": True},
        "grade": {"receipt_id": "grade-1", "evidence_kind": "protected_container_grading",
                  "outcome_dimensions": {"functional": "pass", "workflow": "unknown",
                                         "evidence": "pass", "completion": "pass"},
                  "functional_pass": True},
        "trace_ref": "traces/att-001.jsonl",
        "retained_unknowns": ["cache_hit_input_tokens", "reasoning_tokens"],
    }


class ReceiptTests(unittest.TestCase):
    def test_valid_receipt_is_ledger_and_report_ready(self):
        out = receipts.validate_receipt(valid_receipt())
        self.assertTrue(out["valid"], out["errors"])
        self.assertTrue(out["ledger_ready"])
        self.assertTrue(out["report_ready"])
        self.assertEqual(len(out["receipt_digest"]), 64)

    def test_uncertain_settlement_preserves_but_is_not_ready(self):
        r = valid_receipt()
        r["budget"]["settlement"] = "uncertain"
        out = receipts.validate_receipt(r)
        self.assertTrue(out["valid"])
        self.assertFalse(out["ledger_ready"])
        self.assertFalse(out["report_ready"])

    def test_unknown_cleanup_blocks_readiness(self):
        r = valid_receipt()
        r["cleanup"]["readback"] = "unknown"
        out = receipts.validate_receipt(r)
        self.assertTrue(out["valid"])
        self.assertFalse(out["ledger_ready"])

    def test_missing_operation_ids_and_duplicates(self):
        r = valid_receipt()
        r["budget"]["operation_ids"] = []
        self.assertFalse(receipts.validate_receipt(r)["ledger_ready"])
        r["budget"]["operation_ids"] = ["op-1", "op-1"]
        out = receipts.validate_receipt(r)
        self.assertTrue(any("duplicate" in e for e in out["errors"]))

    def test_secret_and_raw_fields_rejected(self):
        r = valid_receipt()
        r["trace_ref"] = "C:\\Users\\x\\trace.jsonl"
        out = receipts.validate_receipt(r)
        self.assertFalse(out["valid"])
        self.assertTrue(any("absolute" in e for e in out["errors"]))
        r = valid_receipt()
        r["grade"]["raw_prompt"] = "x"
        out = receipts.validate_receipt(r)
        self.assertFalse(out["valid"])
        self.assertTrue(any("forbidden" in e for e in out["errors"]))

    def test_bool_trial_and_missing_grade_dimensions(self):
        r = valid_receipt()
        r["trial"] = True
        self.assertFalse(receipts.validate_receipt(r)["valid"])
        r = valid_receipt()
        r["grade"]["outcome_dimensions"] = None
        out = receipts.validate_receipt(r)
        self.assertTrue(out["valid"])
        self.assertFalse(out["report_ready"])
        r = valid_receipt()
        r["grade"]["outcome_dimensions"]["functional"] = "maybe"
        self.assertFalse(receipts.validate_receipt(r)["valid"])

    def test_development_stage_and_missing_run_id(self):
        r = valid_receipt()
        r["stage"] = "development"
        self.assertTrue(receipts.validate_receipt(r)["valid"])
        r = valid_receipt()
        r.pop("run_id")
        out = receipts.validate_receipt(r)
        self.assertFalse(out["valid"])
        self.assertFalse(out["ledger_ready"])

    def test_functional_consistency(self):
        r = valid_receipt()
        r["grade"]["functional_pass"] = True
        r["grade"]["outcome_dimensions"]["functional"] = "fail"
        out = receipts.validate_receipt(r)
        self.assertFalse(out["valid"])
        self.assertTrue(any("functional_consistency" in e for e in out["errors"]))

    def test_synthetic_receipt_is_report_ready_but_label_preserved(self):
        r = valid_receipt()
        r["grade"]["evidence_kind"] = "synthetic_development"
        out = receipts.validate_receipt(r)
        self.assertTrue(out["report_ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
