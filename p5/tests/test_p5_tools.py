"""Focused tests for the P5 offline tooling (stdlib unittest)."""
import copy
import hashlib
import json
import os
import sys
import unittest
import uuid
import zipfile
from pathlib import Path

PREP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PREP))

from p5_tools import analysis, design, ledger, recompute, reproduce, trajectory  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def scratch(name):
    path = PREP / "scratch" / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def attempt(aid, status="completed", classification="model_outcome", model_outcome=True,
            trial=1, reserved=50000, cost=1000, cleanup="confirmed_absent"):
    return {
        "attempt_id": aid,
        "operation_id": "op-" + aid,
        "scenario_id": "R1",
        "arm": "ordinary",
        "trial": trial,
        "stage": "development",
        "evidence_kind": "synthetic_development",
        "status": status,
        "classification": classification,
        "model_outcome": model_outcome,
        "reserved_micro_usd": reserved,
        "estimated_cost_micro_usd": cost,
        "usage": {"model_responses": 2, "tool_actions": 1, "input_tokens": 10,
                  "output_tokens": 5, "elapsed_ms": 1000},
        "cleanup": cleanup,
        "receipt_sha256": "a" * 64,
    }


def judgment(status="completed", functional="pass", workflow="pass", evidence="pass",
             completion="pass"):
    return {"status": status,
            "outcomes": {"functional": functional, "workflow": workflow,
                         "evidence": evidence, "completion": completion},
            "trace": []}


class DesignTests(unittest.TestCase):
    def test_design_is_interleaved_unique_and_complete(self):
        d = design.build_design()
        self.assertEqual(d["count"], 36)
        keys = {(c["scenario_id"], c["arm"], c["trial"]) for c in d["cells"]}
        self.assertEqual(len(keys), 36)
        self.assertEqual({c["family"] for c in d["cells"]},
                         {"retry-processing", "preview-cache"})
        # interleaving: first block starts in catalog order but arms alternate
        first = [(c["scenario_id"], c["arm"]) for c in d["cells"][:4]]
        self.assertEqual(first[0][0], "R1")
        self.assertNotEqual(first[0][1], first[1][1])

    def test_affordability_fits_and_unknown(self):
        d = design.build_design()
        out = design.affordability(d, load("pilot-PROPOSED.json"))
        self.assertEqual(out["status"], "FITS")
        self.assertEqual(out["worst_case_reservation_micro_usd"], 18_000_000)
        self.assertEqual(out["headroom_micro_usd"], 1_000_000)
        unknown = design.affordability(d, {**load("pilot-PROPOSED.json"),
                                           "reserved_micro_usd_per_episode": None})
        self.assertEqual(unknown["status"], "UNKNOWN_INPUT")
        self.assertIn("reserved_micro_usd_per_episode", unknown["unknown_inputs"])

    def test_affordability_does_not_fit(self):
        d = design.build_design()
        out = design.affordability(d, {**load("pilot-PROPOSED.json"),
                                       "remaining_balance_micro_usd": 100})
        self.assertEqual(out["status"], "DOES_NOT_FIT")
        self.assertLess(out["headroom_micro_usd"], 0)

    def test_run_request_is_bounded_and_not_authorized(self):
        req = design.build_run_request(
            design.build_design(), "formal", load("bounds-PROPOSED.json"),
            load("freeze-PROPOSED.json"), "p5-formal-fixture")
        self.assertEqual(req["status"], "DRAFT_NOT_AUTHORIZED")
        self.assertFalse(any(u["authorized"] for u in req["units"]))
        self.assertEqual(req["worst_case_reservation_micro_usd"], 28_800_000)
        with self.assertRaises(design.DesignError):
            design.build_run_request(
                design.build_design(), "formal",
                {**load("bounds-PROPOSED.json"), "max_total_reservation_micro_usd": 1},
                load("freeze-PROPOSED.json"), "x")


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.data = load("analysis-fixture.json")

    def test_statuses_strict_and_fraction(self):
        out = analysis.aggregate(self.data)
        groups = {(g["scenario_id"], g["arm"]): g for g in out["groups"]}
        r1 = groups[("R1", "ordinary")]
        self.assertEqual((r1["attempted"], r1["eligible"], r1["excluded"]), (2, 2, 0))
        self.assertEqual(r1["status_counts"]["completed"], 1)
        self.assertEqual(r1["status_counts"]["partial"], 1)
        self.assertEqual(r1["strict_counts"], {"pass": 1, "fail": 1, "unknown": 0})
        self.assertEqual(r1["success_fraction"], {"numerator": 1, "denominator": 2})
        c1 = groups[("C1", "ordinary")]
        self.assertEqual((c1["attempted"], c1["eligible"], c1["excluded"]), (2, 1, 1))
        self.assertEqual(c1["success_fraction"], {"numerator": 0, "denominator": 1})

    def test_component_and_unknown_resources(self):
        out = analysis.aggregate(self.data)
        r1 = [g for g in out["groups"] if g["scenario_id"] == "R1"][0]
        self.assertEqual(r1["component_counts"]["functional"], {"pass": 2, "fail": 0, "unknown": 0})
        self.assertEqual(r1["resources"]["model_responses"],
                         {"known_sum": 3, "known_count": 2, "unknown_count": 0,
                          "complete_total": 3})
        self.assertEqual(r1["resources"]["tool_actions"],
                         {"known_sum": 3, "known_count": 1, "unknown_count": 1,
                          "complete_total": None})
        c1 = [g for g in out["groups"] if g["scenario_id"] == "C1"][0]
        # excluded rows still contribute to resource aggregates
        self.assertEqual(c1["resources"]["reserved_micro_usd"]["known_count"], 2)

    def test_formal_only_rejects_synthetic_rows(self):
        formal = copy.deepcopy(self.data)
        for ep in formal["episodes"]:
            ep["evidence_kind"] = "real_provider"
            ep["stage"] = "formal"
        self.assertEqual(len(analysis.aggregate(formal, formal=True)["groups"]), 3)
        formal["episodes"][0]["stage"] = "development"
        with self.assertRaises(analysis.DatasetError):
            analysis.aggregate(formal, formal=True)

    def test_compare_arms_no_pooling(self):
        cmp = analysis.compare_arms(analysis.aggregate(self.data))
        self.assertEqual(len(cmp["rows"]), 6)
        c1_structured = [r for r in cmp["rows"]
                         if r["scenario_id"] == "C1" and r["arm"] == "structured_progress"][0]
        c1_ordinary = [r for r in cmp["rows"]
                       if r["scenario_id"] == "C1" and r["arm"] == "ordinary"][0]
        self.assertFalse(c1_structured["present"])
        self.assertTrue(c1_ordinary["present"])

    def test_markdown_and_empty_dataset(self):
        md = analysis.render_markdown(analysis.aggregate(self.data))
        self.assertIn("strict pass", md)
        self.assertIn("N/A", md)  # zero-denominator group is rendered as N/A
        empty = {"schema_version": "report-input-0.1", "dataset_id": "empty-fixture",
                 "episodes": []}
        out = analysis.aggregate(empty)
        self.assertEqual(out["inventory"]["attempted"], 0)
        self.assertIn("No rows", analysis.render_markdown(out))


class LedgerTests(unittest.TestCase):
    def test_summary_retains_all_attempts(self):
        attempts = [attempt("a1"), attempt("a2", status="denied",
                                           classification="provider_infrastructure",
                                           model_outcome=False, cleanup="unknown")]
        out = ledger.summarize(attempts)
        self.assertEqual(out["attempted"], 2)
        self.assertEqual(out["status_counts"]["denied"], 1)
        self.assertEqual(out["cleanup_not_confirmed_absent"], 1)

    def test_primary_projection(self):
        attempts = [attempt("a1")]
        rows = ledger.build_report_input(attempts, ["a1"], {"a1": judgment()}, "proj-fixture")
        self.assertEqual(len(rows["episodes"]), 1)
        self.assertEqual(rows["episodes"][0]["resources"]["reserved_micro_usd"], 50000)

    def test_refuses_selective_rerun(self):
        attempts = [attempt("a1"), attempt("a2", status="denied",
                                           classification="provider_infrastructure",
                                           model_outcome=False)]
        # a1 carries a model outcome and is not primary: refuse
        with self.assertRaises(ledger.LedgerError):
            ledger.select_primary(attempts, ["a2"],
                                  {"a2": "technical_provider_failure"})
        # an explicit technical primary is allowed when no other attempt carries a model outcome
        technical = [
            attempt("t1", status="denied", classification="provider_infrastructure",
                    model_outcome=False),
            attempt("t2", status="denied", classification="provider_infrastructure",
                    model_outcome=False),
        ]
        primary = ledger.select_primary(technical, ["t2"],
                                        {"t2": "technical_provider_failure"})
        self.assertEqual(primary[0]["attempt_id"], "t2")

    def test_missing_primary_coverage_refused(self):
        attempts = [attempt("a1"), attempt("a2", trial=2)]
        with self.assertRaises(ledger.LedgerError):
            ledger.select_primary(attempts, ["a1"])


class ProjectionConsumerTests(unittest.TestCase):
    def _doc(self, eid="e1"):
        return {
            "schema_version": "trajectory-output-0.1",
            "episode_id": eid,
            "evidence_kind": "protected_container_grading",
            "snapshots": [{"checkpoint_id": "c1"}, {"checkpoint_id": "c2"},
                          {"checkpoint_id": "c3"}],
            "segments": [{
                "requirements_version": "v1", "start_seq": 1, "end_seq": 3,
                "snapshot_count": 3, "known_pass": 1, "known_fail": 1, "unknown": 1,
                "first_observed_correct": {"checkpoint_id": "c1", "seq": 1},
                "observed_regressions": [{"from_checkpoint_id": "c1",
                                          "to_checkpoint_id": "c2"}],
            }],
        }

    def test_joins_by_episode_id_and_counts(self):
        episodes = [{"episode_id": "e1", "scenario_id": "R1",
                     "arm": "ordinary", "trial": 1}]
        out = trajectory.from_projection([self._doc()], episodes)
        self.assertTrue(out["available"])
        row = out["episodes"][0]
        self.assertEqual((row["known_pass"], row["known_fail"], row["unknown"]), (1, 1, 1))
        self.assertEqual(row["first_observed_correct_seq"], 1)
        self.assertEqual(row["observed_regressions"], 1)
        self.assertEqual(row["trajectory_evidence_kind"], "protected_container_grading")

    def test_unmapped_and_empty(self):
        episodes = [{"episode_id": "e1", "scenario_id": "R1",
                     "arm": "ordinary", "trial": 1}]
        out = trajectory.from_projection([self._doc("other")], episodes)
        self.assertEqual(out["unmapped_episode_ids"], ["other"])
        self.assertEqual(out["episodes"], [])
        empty = trajectory.from_projection([], episodes)
        self.assertFalse(empty["available"])

    def test_count_partition_refused(self):
        doc = self._doc()
        doc["segments"][0]["snapshot_count"] = 4
        with self.assertRaises(trajectory.TrajectoryError):
            trajectory.from_projection([doc], [{"episode_id": "e1", "scenario_id": "R1",
                                                "arm": "ordinary", "trial": 1}])


class RecomputeTests(unittest.TestCase):
    def _dataset(self):
        return {
            "schema_version": "report-input-0.1",
            "dataset_id": "recompute-fixture",
            "episodes": [
                {"episode_id": "r1-1", "scenario_id": "R1", "arm": "ordinary", "trial": 1,
                 "stage": "development", "evidence_kind": "synthetic_development",
                 "status": "completed",
                 "outcomes": {"functional": "pass", "workflow": "pass",
                              "evidence": "pass", "completion": "pass"},
                 "resources": {k: None for k in analysis.RESOURCE_FIELDS}, "trace": []},
                {"episode_id": "r1-2", "scenario_id": "R1", "arm": "ordinary", "trial": 2,
                 "stage": "development", "evidence_kind": "synthetic_development",
                 "status": "provider_error",
                 "outcomes": {"functional": "unknown", "workflow": "unknown",
                              "evidence": "unknown", "completion": "unknown"},
                 "resources": {k: None for k in analysis.RESOURCE_FIELDS}, "trace": []},
            ],
        }

    def test_agreement_then_detects_difference(self):
        data = self._dataset()
        rec = analysis.aggregate(data)
        report = {"schema_version": "report-output-0.1",
                  "inventory": rec["inventory"], "groups": [dict(g) for g in rec["groups"]]}
        out = recompute.compare(data, report)
        self.assertTrue(out["matches"])
        report["groups"][0]["eligible"] += 1
        bad = recompute.compare(data, report)
        self.assertFalse(bad["matches"])
        self.assertEqual(bad["group_differences"][0]["field"], "eligible")

    def test_rejects_wrong_report_schema(self):
        with self.assertRaises(analysis.DatasetError):
            recompute.compare(self._dataset(), {"schema_version": "report-output-0.0"})


class TrajectoryTests(unittest.TestCase):
    def test_empty_is_unavailable_not_zero(self):
        out = trajectory.build([])
        self.assertFalse(out["available"])
        self.assertIn("report-input-0.1", out["reason"])

    def test_regression_and_repeated_input(self):
        out = trajectory.build(load("trajectory-FIXTURE.json")["observations"])
        self.assertTrue(out["available"])
        row = [r for r in out["checkpoints"] if r["checkpoint_id"] == "new_delivery"][0]
        self.assertEqual(row["first_correct_sequence"], 1)
        self.assertTrue(row["regression_after_first_correct"])
        self.assertTrue(row["repeated_unchanged_check"])
        types = {f["type"] for f in out["flags"]}
        self.assertEqual(types, {"regression", "unchanged_input_repeat"})


class ReproductionTests(unittest.TestCase):
    def _safe_zip(self, root):
        path = root / "safe.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.comment = b"test-comment-0001"
            zf.writestr("Repo-abc/a.txt", "alpha")
            zf.writestr("Repo-abc/sub/b.txt", "beta")
        return path

    def test_inspect_extract_manifest_and_verify(self):
        root = scratch("repro")
        z = self._safe_zip(root)
        info = reproduce.inspect_zip(z, "test-comment-0001")
        self.assertTrue(info["safe"])
        self.assertEqual((info["files"], info["entries"]), (2, 2))
        dest = root / "src"
        out = reproduce.safe_extract(z, dest, "test-comment-0001")
        self.assertEqual(out["extracted"], 2)
        entries = reproduce.manifest_tree(dest)
        self.assertTrue(reproduce.verify_tree(dest, entries)["ok"])
        (dest / "extra.txt").write_text("extra", encoding="utf-8")
        self.assertFalse(reproduce.verify_tree(dest, entries)["ok"])
        (dest / "extra.txt").unlink()
        (dest / "a.txt").write_text("changed", encoding="utf-8")
        self.assertFalse(reproduce.verify_tree(dest, entries)["ok"])

    def test_refuses_traversal_absolute_duplicate_and_symlink(self):
        root = scratch("unsafe")
        path = root / "unsafe.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.comment = b"test-comment-0001"
            zf.writestr("../evil.txt", "x")
            zf.writestr("/abs.txt", "x")
            zf.writestr("C:/drive.txt", "x")
            zf.writestr("Repo-abc/a.txt", "x")
            zf.writestr("Repo-abc/A.txt", "x")
            link = zipfile.ZipInfo("Repo-abc/link")
            link.external_attr = (0o120777 << 16)
            zf.writestr(link, "target")
        info = reproduce.inspect_zip(path, "test-comment-0001")
        self.assertFalse(info["safe"])
        reasons = {r["reason"] for r in info["refusals"]}
        self.assertEqual(reasons, {
            "traversal_or_empty_segment", "absolute_or_drive",
            "duplicate_destination", "symlink"})
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.safe_extract(path, root / "dest", "test-comment-0001")

    def test_comment_mismatch_refused(self):
        root = scratch("comment")
        z = self._safe_zip(root)
        with self.assertRaises(reproduce.ReproductionError):
            reproduce.inspect_zip(z, "wrong-comment")

    def test_exclusion_scan_and_allowlist(self):
        paths = [".coordination/a", "runs/b", ".env.local", "provider-budget.sqlite3",
                 "docs/a.md", "notes/api_key.txt"]
        out = reproduce.exclusion_scan(paths)
        self.assertFalse(out["ok"])
        self.assertEqual(len(out["violations"]), 5)
        allowed = reproduce.exclusion_scan(paths, ["notes/api_key.txt"])
        self.assertEqual(len(allowed["violations"]), 4)

    def test_recursive_subset(self):
        expected = {"a": 1, "b": {"c": 2}}
        actual = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
        self.assertEqual(reproduce.recursive_subset(expected, actual), [])
        self.assertTrue(reproduce.recursive_subset({"a": 2}, actual))


if __name__ == "__main__":
    unittest.main(verbosity=2)
