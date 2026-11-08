"""Focused tests for the P4.6 read-only HTML viewer."""

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from reporting.report import build_report
from reporting.viewer import (
    ViewerError,
    build_viewer_data,
    render_html,
    write_html,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "reporting" / "v0.1" / "input.json"


def fixture_report():
    return build_report(json.loads(FIXTURE.read_text(encoding="utf-8")))


def malicious_report():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["episodes"][0]["trace"][0]["summary"] = (
        '<script>alert("x")</script>\x1b[31m\nhttps://example.invalid/raw'
    )
    return build_report(data)


class ViewerTests(unittest.TestCase):
    def test_fixture_projection_and_comparison(self):
        report = fixture_report()
        view = build_viewer_data(report)
        html = render_html(view)
        self.assertIn("SYNTHETIC DEVELOPMENT DATA", html)
        self.assertIn("synthetic-report-contract-v0.1", html)
        for episode in report["episodes"]:
            self.assertIn(episode["episode_id"], html)
        self.assertIn("Within-scenario arm comparison", html)
        self.assertIn("No cross-scenario pooled headline", html)
        self.assertIn("EXCLUDED", html)
        self.assertIn("UNKNOWN", html)
        self.assertIn("Functional", html)
        self.assertIn("Workflow", html)
        self.assertIn("Evidence", html)
        self.assertIn("Completion", html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("href=", html.lower())
        self.assertNotIn("src=", html.lower())

    def test_untrusted_text_is_literal_and_control_free(self):
        html = render_html(build_viewer_data(malicious_report()))
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;alert", html)
        self.assertNotIn("\x1b", html)
        self.assertNotIn("\nhttps://example.invalid/raw", html)
        self.assertIn("https://example.invalid/raw", html)

    def test_all_unknown_cost_remains_unknown(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for episode in data["episodes"]:
            episode["resources"]["estimated_cost_micro_usd"] = None
            episode["resources"]["reserved_micro_usd"] = None
        html = render_html(build_viewer_data(build_report(data)))
        self.assertNotIn("$0.000000", html)
        self.assertIn("Estimated cost (estimate)</dt><dd>unknown", html)
        self.assertIn("Reserved (authorisation)</dt><dd>unknown", html)

    def test_optional_trajectory_and_binding_are_rendered(self):
        trajectory = {
            "schema_version": "trajectory-output-0.1",
            "episode_id": "episode-1",
            "evidence_kind": "synthetic_development",
            "snapshots": [],
            "segments": [
                {
                    "requirements_version": "v1",
                    "start_seq": 1,
                    "end_seq": 2,
                    "snapshot_count": 2,
                    "known_pass": 1,
                    "known_fail": 1,
                    "unknown": 0,
                    "first_observed_correct": {
                        "checkpoint_id": "ckpt-1", "seq": 1,
                        "elapsed_ms": 1, "model_responses": 1,
                    },
                    "observed_regressions": [],
                }
            ],
        }
        binding = {
            "episode_id": "episode-1", "run_id": "run-1",
            "candidate_sha256": "a" * 64, "requirements_version": "v1",
            "binding_match": True,
        }
        html = render_html(
            build_viewer_data(fixture_report(), trajectory=trajectory,
                              binding=binding)
        )
        self.assertIn("Checkpoint trajectory", html)
        self.assertIn("ckpt-1", html)
        self.assertIn("Source / run binding", html)
        self.assertIn("a" * 64, html)

    def test_deterministic_and_portable_write(self):
        view = build_viewer_data(fixture_report())
        first = render_html(view)
        self.assertEqual(first, render_html(view))
        scratch = ROOT / "tmp" / ("viewer-test-" + os.urandom(6).hex())
        scratch.mkdir(parents=True)
        try:
            output = scratch / "report.html"
            write_html(str(output), first)
            self.assertEqual(output.read_text(encoding="utf-8"), first)
            self.assertTrue(output.is_file())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_invalid_input_and_output_parent(self):
        with self.assertRaises(ViewerError):
            build_viewer_data({"schema_version": "other"})
        with self.assertRaises(ViewerError):
            write_html("definitely-missing-parent/report.html", "<html></html>")


if __name__ == "__main__":
    unittest.main()
