"""Focused tests for the P4.5 offline live-progress receipt renderer."""

import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from reporting import progress

ROOT = Path(__file__).resolve().parent.parent


def meta(**overrides):
    value = {
        "episode_id": "prog-r1",
        "run_id": "run-1",
        "scenario_id": "R1",
        "arm": "ordinary",
        "stage": "development",
        "evidence_kind": "synthetic_development",
        "source_sha256": "a" * 64,
        "requirements_version": "v1",
        "events_path": "events.jsonl",
        "result_path": "result.json",
        "report_path": "report.json",
    }
    value.update(overrides)
    return value


class ProgressTests(unittest.TestCase):
    def test_counts_role_elapsed_and_unknown_money(self):
        events = [
            {"type": "episode_start", "role": "controller", "elapsed_ms": 0},
            {"type": "model_response", "role": "O1", "elapsed_ms": 10},
            {"type": "action_attempt", "role": "O1", "elapsed_ms": 20},
            {"type": "write_result", "role": "W1", "elapsed_ms": 30},
            {"type": "check_result", "role": "W1", "status": "passed",
             "elapsed_ms": 40},
        ]
        state = progress.summarize_progress(
            meta(), events, {"status": "completed_claim", "cleanup": "settled"}
        )
        self.assertEqual(state["counts"]["model_responses"], 1)
        self.assertEqual(state["counts"]["tool_actions"], 1)
        self.assertEqual(state["counts"]["writes"], 1)
        self.assertEqual(state["counts"]["checks"], 1)
        self.assertEqual(state["role"], "W1")
        self.assertEqual(state["elapsed_ms"], 40)
        self.assertIsNone(state["estimated_cost_micro_usd"])
        self.assertIsNone(state["reserved_micro_usd"])
        self.assertEqual(state["cleanup"], "settled")
        line = progress.render_progress(state)
        self.assertIn("[completed_claim]", line)
        self.assertIn("estimate=unknown", line)
        self.assertIn("reserved=unknown", line)
        self.assertNotIn("\x1b", line)

    def test_early_stop_and_unknown_cleanup_remain_distinct(self):
        state = progress.summarize_progress(
            meta(), [{"type": "model_response", "role": "O1", "elapsed_ms": 5}],
            {"status": "provider_error", "cleanup": "unknown"},
        )
        self.assertEqual(state["state"], "provider_error")
        self.assertEqual(state["cleanup"], "unknown")
        self.assertEqual(state["counts"]["model_responses"], 1)
        self.assertEqual(state["elapsed_ms"], 5)
        self.assertIn("provider_error", progress.render_progress(state))

    def test_empty_events_and_sanitized_ids(self):
        state = progress.summarize_progress(
            meta(episode_id="bad\x1b[31m\nid"), [], None
        )
        self.assertEqual(state["state"], "no_events")
        self.assertEqual(state["event_count"], 0)
        self.assertNotIn("\x1b", state["episode_id"])
        self.assertNotIn("\n", state["episode_id"])
        self.assertEqual(
            progress.render_progress(state).count("estimate=unknown"), 1
        )

    def test_cli_json_from_explicit_files(self):
        scratch = ROOT / "tmp" / ("progress-test-" + os.urandom(6).hex())
        scratch.mkdir(parents=True)
        try:
            events_path = scratch / "events.jsonl"
            result_path = scratch / "result.json"
            events_path.write_text(
                json.dumps({"type": "model_response", "role": "O1",
                            "elapsed_ms": 5}) + "\n",
                encoding="utf-8",
            )
            result_path.write_text(
                json.dumps({"status": "partial_claim", "cleanup": "settled"}),
                encoding="utf-8",
            )
            out = io.StringIO()
            with redirect_stdout(out):
                code = progress.main(
                    ["--events", str(events_path), "--result", str(result_path),
                     "--json"]
                )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["counts"]["model_responses"], 1)
        self.assertEqual(data["state"], "partial_claim")


if __name__ == "__main__":
    unittest.main()
