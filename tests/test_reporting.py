"""Focused tests for the FollowThrough offline reporting projection (v0.1).

All data here is synthetic or clearly marked TEST-ONLY construction.  A
``real_provider``-labeled object created in this file exercises grouping logic
only; it is never actual provider evidence or a published result.

The layout is discovered from this file's location, so the same suite runs from
the worker overlay (which contains a ``reporting`` package) and from an
integrated-shaped root that contains ``reporting/`` and ``tests/`` side by side.
No fixed parent depth or current directory is assumed.
"""

import codecs
import csv
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import reporting.report as reporting_report  # noqa: E402
from reporting.report import (  # noqa: E402
    MAX_EPISODES,
    MAX_INPUT_BYTES,
    MAX_SAFE_INT,
    MAX_TRACE_EVENTS,
    ReportError,
    build_report,
    generate,
    main,
    render_csv,
    render_markdown,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "reporting" / "v0.1"

OUTCOME_KEYS = ("functional", "workflow", "evidence", "completion")
RESOURCE_KEYS = (
    "model_responses",
    "tool_actions",
    "input_tokens",
    "output_tokens",
    "elapsed_ms",
    "estimated_cost_micro_usd",
    "reserved_micro_usd",
)
STATUS_KEYS = {
    "completed",
    "partial",
    "limit_reached",
    "provider_error",
    "infrastructure_error",
    "invalid",
}
GROUP_KEYS = {
    "evidence_kind",
    "stage",
    "scenario_id",
    "arm",
    "attempted",
    "eligible",
    "excluded",
    "status_counts",
    "component_counts",
    "strict_counts",
    "success_fraction",
    "resources",
}
EPISODE_OUT_KEYS = {
    "episode_id",
    "scenario_id",
    "family",
    "arm",
    "trial",
    "stage",
    "evidence_kind",
    "status",
    "outcomes",
    "resources",
    "trace",
    "strict_outcome",
}
CSV_COLUMNS = [
    "episode_id",
    "scenario_id",
    "family",
    "arm",
    "trial",
    "stage",
    "evidence_kind",
    "status",
    "functional",
    "workflow",
    "evidence",
    "completion",
    "strict_outcome",
    "model_responses",
    "tool_actions",
    "input_tokens",
    "output_tokens",
    "elapsed_ms",
    "estimated_cost_micro_usd",
    "reserved_micro_usd",
]


def base_episode(episode_id="ep-1", **overrides):
    """Return one synthetic development episode with all-pass judgments."""
    episode = {
        "episode_id": episode_id,
        "scenario_id": "R1",
        "arm": "ordinary",
        "trial": 1,
        "stage": "development",
        "evidence_kind": "synthetic_development",
        "status": "completed",
        "outcomes": {dimension: "pass" for dimension in OUTCOME_KEYS},
        "resources": {
            "model_responses": 4,
            "tool_actions": 3,
            "input_tokens": 100,
            "output_tokens": 50,
            "elapsed_ms": 1000,
            "estimated_cost_micro_usd": 20,
            "reserved_micro_usd": 100,
        },
        "trace": [
            {
                "seq": 1,
                "role": "controller",
                "kind": "supplied_fixture",
                "summary": "synthetic fixture",
            }
        ],
    }
    for key, value in overrides.items():
        if key == "outcomes":
            episode["outcomes"].update(value)
        elif key == "resources":
            episode["resources"].update(value)
        else:
            episode[key] = value
    return episode


def make_input(*episodes, dataset_id="test-dataset"):
    return {
        "schema_version": "report-input-0.1",
        "dataset_id": dataset_id,
        "episodes": list(episodes),
    }


def fixture(name):
    with open(FIXTURE_DIR / name, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        json.dump(data, handle)
    return path


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def parse_csv(text):
    return list(csv.DictReader(io.StringIO(text, newline="")))


def markdown_literal_text(markdown):
    """Undo Markdown backslash escapes for literal-content assertions.

    This is a test-side readability check only; the installed offline parser
    witness is what verifies actual rendered behavior, including linkifying.
    """
    return re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", markdown)


class ScratchTestCase(unittest.TestCase):
    def setUp(self):
        # Owned default-mode scratch under the discovered root.  Directories are
        # created with normal permissions (not tempfile's 0700 mode) so the
        # suite also works under a Windows workspace sandbox.
        parent = ROOT / "tmp"
        parent.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            candidate = parent / ("p4-report-test-" + os.urandom(6).hex())
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            self.tmp = candidate
            break
        else:  # pragma: no cover - practically unreachable
            raise RuntimeError("could not create test scratch directory")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, args, cwd, root=None):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root or ROOT)  # child-only path
        return subprocess.run(
            [sys.executable, "-B", "-m", "reporting.report"] + list(args),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )


class MainFixtureTests(ScratchTestCase):
    def test_main_fixture_matches_expected_summaries(self):
        data = fixture("input.json")
        expected = fixture("expected.json")
        report = build_report(data)

        self.assertEqual(report["schema_version"], "report-output-0.1")
        self.assertEqual(
            set(report),
            {
                "schema_version",
                "dataset_id",
                "evidence_notice",
                "inventory",
                "groups",
                "episodes",
            },
        )
        self.assertEqual(
            set(report["inventory"]),
            {"attempted", "evidence_counts", "stage_counts"},
        )
        self.assertEqual(report["inventory"], expected["inventory"])
        self.assertEqual(len(report["groups"]), expected["group_count"])
        self.assertEqual(len(report["groups"]), 7)
        self.assertEqual(len(report["episodes"]), 8)

        # Every group is the one fixed synthetic/development partition.
        for group in report["groups"]:
            self.assertEqual(group["evidence_kind"], "synthetic_development")
            self.assertEqual(group["stage"], "development")
            self.assertEqual(set(group), GROUP_KEYS)
            self.assertEqual(set(group["status_counts"]), STATUS_KEYS)
            self.assertEqual(set(group["success_fraction"]), {"numerator", "denominator"})
            self.assertEqual(set(group["resources"]), set(RESOURCE_KEYS))
            for dimension in OUTCOME_KEYS:
                self.assertEqual(
                    set(group["component_counts"][dimension]),
                    {"pass", "fail", "unknown"},
                )
            self.assertEqual(set(group["strict_counts"]), {"pass", "fail", "unknown"})

        expected_groups = {
            (group["scenario_id"], group["arm"]): group
            for group in expected["groups"]
        }
        actual_groups = {
            (group["scenario_id"], group["arm"]): group
            for group in report["groups"]
        }
        self.assertEqual(set(actual_groups), set(expected_groups))
        for key, expected_group in expected_groups.items():
            actual = actual_groups[key]
            for field in ("attempted", "eligible", "excluded"):
                self.assertEqual(actual[field], expected_group[field], (key, field))
            for field in ("strict_counts", "success_fraction"):
                self.assertEqual(actual[field], expected_group[field], (key, field))
            for field, value in expected_group.get("component_counts", {}).items():
                self.assertEqual(
                    actual["component_counts"][field], value, (key, field)
                )
            for field, value in expected_group.get("resources", {}).items():
                self.assertEqual(actual["resources"][field], value, (key, field))

        actual_strict = {
            episode["episode_id"]: episode["strict_outcome"]
            for episode in report["episodes"]
        }
        self.assertEqual(actual_strict, expected["episode_strict_outcomes"])

        # Unknown estimated cost is preserved: R1 has 20 known and one null.
        r1 = actual_groups[("R1", "ordinary")]
        self.assertEqual(
            r1["resources"]["estimated_cost_micro_usd"],
            {
                "known_sum": 20,
                "known_count": 1,
                "unknown_count": 1,
                "complete_total": None,
            },
        )
        # Resources include excluded attempts (C1 infra row still has cost 20).
        c1 = actual_groups[("C1", "structured_progress")]
        self.assertEqual(c1["excluded"], 1)
        self.assertEqual(c1["resources"]["estimated_cost_micro_usd"]["known_sum"], 20)
        self.assertEqual(c1["resources"]["estimated_cost_micro_usd"]["complete_total"], 20)

    def test_main_fixture_markdown_and_csv(self):
        report = build_report(fixture("input.json"))
        markdown = render_markdown(report)
        self.assertIn("SYNTHETIC DEVELOPMENT DATA", markdown)
        self.assertIn("NOT MODEL RESULTS", markdown)
        self.assertIn("synthetic-report-contract-v0.1", markdown)
        self.assertIn("no cross-scenario", markdown)
        self.assertIn("neither is billed cost", markdown)
        literal = markdown_literal_text(markdown)
        for episode in report["episodes"]:
            self.assertIn(episode["episode_id"], literal)
            for event in episode["trace"]:
                self.assertIn(event["summary"], literal)
        self.assertNotIn("C:\\", markdown)
        self.assertNotIn("C:/", markdown)

        rows = parse_csv(render_csv(report))
        self.assertEqual([row["episode_id"] for row in rows], [
            episode["episode_id"] for episode in report["episodes"]
        ])
        self.assertEqual(list(rows[0].keys()), CSV_COLUMNS)
        by_id = {row["episode_id"]: row for row in rows}
        self.assertEqual(by_id["synthetic-r1-success"]["strict_outcome"], "pass")
        self.assertEqual(by_id["synthetic-r2-partial"]["strict_outcome"], "fail")
        self.assertEqual(by_id["synthetic-r3-provider"]["strict_outcome"], "not_scored")
        # Null numeric cells stay empty.
        provider = by_id["synthetic-r3-provider"]
        for column in (
            "input_tokens",
            "output_tokens",
            "estimated_cost_micro_usd",
        ):
            self.assertEqual(provider[column], "")
        self.assertEqual(by_id["synthetic-r1-route-fail"]["estimated_cost_micro_usd"], "")


class SemanticsTests(ScratchTestCase):
    def test_partial_with_completion_pass_is_strict_fail(self):
        report = build_report(
            make_input(
                base_episode(
                    status="partial",
                    outcomes={"functional": "fail", "completion": "pass"},
                )
            )
        )
        episode = report["episodes"][0]
        self.assertEqual(episode["strict_outcome"], "fail")
        group = report["groups"][0]
        self.assertEqual(group["strict_counts"], {"pass": 0, "fail": 1, "unknown": 0})
        self.assertEqual(group["component_counts"]["completion"]["pass"], 1)

    def test_completed_component_fail_is_strict_fail(self):
        report = build_report(
            make_input(base_episode(outcomes={"workflow": "fail"}))
        )
        self.assertEqual(report["episodes"][0]["strict_outcome"], "fail")

    def test_completed_component_unknown_is_strict_unknown(self):
        report = build_report(
            make_input(base_episode(outcomes={"evidence": "unknown"}))
        )
        self.assertEqual(report["episodes"][0]["strict_outcome"], "unknown")

    def test_limit_reached_is_strict_fail(self):
        report = build_report(make_input(base_episode(status="limit_reached")))
        self.assertEqual(report["episodes"][0]["strict_outcome"], "fail")

    def test_excluded_statuses_are_not_scored_but_visible(self):
        episodes = []
        for index, status in enumerate(
            ("provider_error", "infrastructure_error", "invalid"), start=1
        ):
            episodes.append(
                base_episode(
                    episode_id="excluded-%d" % index,
                    trial=index,
                    status=status,
                    outcomes={dimension: "pass" for dimension in OUTCOME_KEYS},
                    resources={"estimated_cost_micro_usd": 20},
                )
            )
        report = build_report(make_input(*episodes))
        group = report["groups"][0]
        self.assertEqual(group["attempted"], 3)
        self.assertEqual(group["eligible"], 0)
        self.assertEqual(group["excluded"], 3)
        self.assertEqual(group["strict_counts"], {"pass": 0, "fail": 0, "unknown": 0})
        self.assertEqual(group["success_fraction"], {"numerator": 0, "denominator": 0})
        self.assertEqual(
            group["status_counts"],
            {
                "completed": 0,
                "partial": 0,
                "limit_reached": 0,
                "provider_error": 1,
                "infrastructure_error": 1,
                "invalid": 1,
            },
        )
        # Excluded rows keep original outcomes and are not counted in components.
        for dimension in OUTCOME_KEYS:
            self.assertEqual(
                group["component_counts"][dimension],
                {"pass": 0, "fail": 0, "unknown": 0},
            )
        for episode in report["episodes"]:
            self.assertEqual(episode["strict_outcome"], "not_scored")
            self.assertEqual(set(episode["outcomes"].values()), {"pass"})

    def test_component_counts_use_only_eligible_rows(self):
        complete = base_episode("eligible", trial=2, outcomes={"functional": "fail"})
        excluded = base_episode(
            "excluded",
            status="provider_error",
            outcomes={dimension: "pass" for dimension in OUTCOME_KEYS},
        )
        report = build_report(make_input(excluded, complete))
        group = report["groups"][0]
        self.assertEqual(group["component_counts"]["functional"]["fail"], 1)
        self.assertEqual(group["component_counts"]["functional"]["pass"], 0)

    def test_all_excluded_group_has_zero_denominator(self):
        report = build_report(
            make_input(base_episode(status="infrastructure_error"))
        )
        markdown = render_markdown(report)
        self.assertIn("0 / 0 eligible (N/A)", markdown)
        self.assertIn("0 / 0", markdown)

    def test_empty_dataset(self):
        report = build_report(make_input())
        self.assertEqual(
            report["inventory"],
            {
                "attempted": 0,
                "evidence_counts": {
                    "synthetic_development": 0,
                    "real_provider": 0,
                },
                "stage_counts": {"development": 0, "pilot": 0, "formal": 0},
            },
        )
        self.assertEqual(report["groups"], [])
        self.assertEqual(report["episodes"], [])
        markdown = render_markdown(report)
        self.assertIn("No episodes were supplied.", markdown)
        self.assertIn("No episode groups were supplied.", markdown)
        self.assertNotIn("SYNTHETIC DEVELOPMENT DATA", markdown)
        rows = parse_csv(render_csv(report))
        self.assertEqual(rows, [])

    def test_resources_complete_total_and_unknown_sum(self):
        first = base_episode("a", resources={"estimated_cost_micro_usd": 20})
        second = base_episode("b", trial=2, resources={"estimated_cost_micro_usd": 30})
        report = build_report(make_input(first, second))
        resource = report["groups"][0]["resources"]["estimated_cost_micro_usd"]
        self.assertEqual(
            resource,
            {
                "known_sum": 50,
                "known_count": 2,
                "unknown_count": 0,
                "complete_total": 50,
            },
        )

    def test_markdown_says_per_success_cost_not_computed(self):
        markdown = render_markdown(build_report(make_input(base_episode())))
        self.assertIn("Per-success cost is not computed", markdown)
        self.assertIn("neither is billed cost", markdown)

    def test_markdown_strict_precedence_matches_calculation(self):
        report = build_report(
            make_input(
                base_episode(
                    outcomes={"workflow": "fail", "evidence": "unknown"}
                )
            )
        )
        self.assertEqual(report["episodes"][0]["strict_outcome"], "fail")
        markdown = render_markdown(report)
        self.assertIn(
            "an eligible non-completed attempt is a strict failure", markdown
        )
        self.assertIn("any failing component is a strict failure", markdown)
        self.assertIn(
            "strict unknown only when at least one component is unknown", markdown
        )
        self.assertIn("Excluded attempts remain `not_scored`", markdown)
        self.assertNotIn(
            "a completed row with any unknown component is strict unknown",
            markdown,
        )


class PartitionAndOrderingTests(ScratchTestCase):
    def test_evidence_and_stage_partitions_do_not_pool(self):
        # TEST-ONLY: real_provider-labeled objects exercise partitioning. They
        # are constructed here and are never published as actual results.
        synthetic = base_episode("syn", trial=1)
        provider_pilot = base_episode(
            "prov-pilot",
            evidence_kind="real_provider",
            stage="pilot",
            trial=1,
        )
        provider_formal = base_episode(
            "prov-formal",
            evidence_kind="real_provider",
            stage="formal",
            trial=1,
        )
        report = build_report(make_input(synthetic, provider_pilot, provider_formal))
        self.assertEqual(len(report["groups"]), 3)
        partitions = [
            (group["evidence_kind"], group["stage"]) for group in report["groups"]
        ]
        self.assertEqual(
            partitions,
            [
                ("real_provider", "formal"),
                ("real_provider", "pilot"),
                ("synthetic_development", "development"),
            ],
        )
        markdown = render_markdown(report)
        self.assertIn("real_provider", markdown)
        self.assertIn("Supplied provenance only", markdown)
        self.assertIn("not verified by the reporter", markdown)
        self.assertEqual(
            report["inventory"]["evidence_counts"],
            {"synthetic_development": 1, "real_provider": 2},
        )
        self.assertEqual(
            report["inventory"]["stage_counts"],
            {"development": 1, "pilot": 1, "formal": 1},
        )

    def test_groups_and_episodes_are_sorted_deterministically(self):
        episodes = [
            base_episode("z", scenario_id="R1", trial=2),
            base_episode("a", scenario_id="R3", trial=1, arm="structured_progress"),
            base_episode("m", scenario_id="C1", trial=1),
            base_episode("b", scenario_id="R1", trial=1),
        ]
        report = build_report(make_input(*reversed(episodes)))
        episode_order = [
            (episode["evidence_kind"], episode["stage"], episode["scenario_id"],
             episode["arm"], episode["trial"], episode["episode_id"])
            for episode in report["episodes"]
        ]
        self.assertEqual(episode_order, sorted(episode_order))
        group_order = [
            (group["evidence_kind"], group["stage"], group["scenario_id"], group["arm"])
            for group in report["groups"]
        ]
        self.assertEqual(group_order, sorted(group_order))
        self.assertEqual(
            [episode["episode_id"] for episode in report["episodes"]],
            ["m", "b", "z", "a"],
        )

    def test_trace_order_is_retained(self):
        trace = [
            {"seq": 2, "role": "O1", "kind": "message", "summary": "second"},
            {"seq": 5, "role": "controller", "kind": "check", "summary": "fifth"},
        ]
        report = build_report(make_input(base_episode(trace=trace)))
        self.assertEqual(
            [event["seq"] for event in report["episodes"][0]["trace"]], [2, 5]
        )


class ValidationTests(ScratchTestCase):
    def assert_code(self, expected_code, data):
        with self.assertRaises(ReportError) as caught:
            build_report(data)
        self.assertEqual(caught.exception.code, expected_code)

    def test_envelope_and_key_validation(self):
        good = make_input()
        cases = []
        cases.append(("invalid_input", "not an object"))
        cases.append(("invalid_input", []))
        cases.append(("invalid_input", {"schema_version": "report-input-0.1"}))
        cases.append(("unsupported_schema", {
            "schema_version": "report-input-0.2",
            "dataset_id": "d",
            "episodes": [],
        }))
        cases.append(("invalid_input", {
            "schema_version": "report-input-0.1",
            "dataset_id": "d",
            "episodes": [],
            "extra": 1,
        }))
        bad_id = dict(good)
        bad_id["dataset_id"] = "bad id"
        cases.append(("invalid_input", bad_id))
        long_id = dict(good)
        long_id["dataset_id"] = "a" * 129
        cases.append(("invalid_input", long_id))
        cases.append(("invalid_input", {
            "schema_version": "report-input-0.1",
            "dataset_id": "",
            "episodes": [],
        }))
        for code, data in cases:
            with self.subTest(code=code, data=str(data)[:40]):
                self.assert_code(code, data)

    def test_episode_field_validation(self):
        cases = {
            "unknown key": ("invalid_input", base_episode(extra="x")),
            "bad scenario": ("invalid_input", base_episode(scenario_id="R4")),
            "bad arm": ("invalid_input", base_episode(arm="baseline")),
            "bad stage": ("invalid_input", base_episode(stage="production")),
            "bad evidence": ("invalid_input", base_episode(evidence_kind="live")),
            "bad status": ("invalid_input", base_episode(status="ok")),
            "bool trial": ("invalid_input", base_episode(trial=True)),
            "zero trial": ("invalid_input", base_episode(trial=0)),
            "float trial": ("invalid_input", base_episode(trial=1.0)),
            "string trial": ("invalid_input", base_episode(trial="1")),
            "bad outcome": ("invalid_input", base_episode(outcomes={"functional": "ok"})),
            "missing outcome": (
                "invalid_input",
                {
                    **base_episode(),
                    "outcomes": {"functional": "pass", "workflow": "pass",
                                 "evidence": "pass"},
                },
            ),
            "bool resource": (
                "invalid_input",
                base_episode(resources={"elapsed_ms": True}),
            ),
            "negative resource": (
                "invalid_input",
                base_episode(resources={"input_tokens": -1}),
            ),
            "float resource": (
                "invalid_input",
                base_episode(resources={"input_tokens": 1.5}),
            ),
            "string resource": (
                "invalid_input",
                base_episode(resources={"input_tokens": "10"}),
            ),
            "resource above max": (
                "invalid_input",
                base_episode(resources={"elapsed_ms": 9007199254740992}),
            ),
            "trace not list": ("invalid_input", base_episode(trace={})),
            "trace unknown key": (
                "invalid_input",
                base_episode(trace=[{"seq": 1, "role": "O1", "kind": "message",
                                     "summary": "x", "extra": 1}]),
            ),
            "bad trace role": (
                "invalid_input",
                base_episode(trace=[{"seq": 1, "role": "O2", "kind": "message",
                                     "summary": "x"}]),
            ),
            "bad trace kind": (
                "invalid_input",
                base_episode(trace=[{"seq": 1, "role": "O1", "kind": "tool",
                                     "summary": "x"}]),
            ),
            "trace summary not string": (
                "invalid_input",
                base_episode(trace=[{"seq": 1, "role": "O1", "kind": "message",
                                     "summary": 5}]),
            ),
            "trace summary too long": (
                "invalid_input",
                base_episode(trace=[{"seq": 1, "role": "O1", "kind": "message",
                                     "summary": "s" * 4097}]),
            ),
            "trace zero seq": (
                "invalid_input",
                base_episode(trace=[{"seq": 0, "role": "O1", "kind": "message",
                                     "summary": "x"}]),
            ),
            "trace repeated seq": (
                "invalid_input",
                base_episode(trace=[
                    {"seq": 1, "role": "O1", "kind": "message", "summary": "a"},
                    {"seq": 1, "role": "O1", "kind": "message", "summary": "b"},
                ]),
            ),
            "trace descending seq": (
                "invalid_input",
                base_episode(trace=[
                    {"seq": 2, "role": "O1", "kind": "message", "summary": "a"},
                    {"seq": 1, "role": "O1", "kind": "message", "summary": "b"},
                ]),
            ),
            "synthetic pilot stage": (
                "invalid_input",
                base_episode(stage="pilot"),
            ),
            "long episode id": ("invalid_input", base_episode("e" * 129)),
            "bad episode id": ("invalid_input", base_episode("bad id")),
            "lone surrogate summary": (
                "invalid_input",
                base_episode(trace=[{"seq": 1, "role": "O1", "kind": "message",
                                     "summary": "\ud800"}]),
            ),
        }
        for label, (code, episode) in cases.items():
            with self.subTest(label):
                self.assert_code(code, make_input(episode))

    def test_duplicate_episode_id(self):
        data = make_input(base_episode("same"), base_episode("same", trial=2))
        self.assert_code("duplicate_episode", data)

    def test_duplicate_cell(self):
        data = make_input(
            base_episode("one"),
            base_episode("two"),  # same provenance/stage/scenario/arm/trial
        )
        self.assert_code("duplicate_cell", data)

    def test_distinct_cells_with_same_ids_allowed(self):
        data = make_input(
            base_episode("one"),
            base_episode("two", evidence_kind="real_provider", stage="pilot"),
        )
        report = build_report(data)
        self.assertEqual(len(report["groups"]), 2)

    def test_episode_and_trace_collection_bounds(self):
        episodes = [base_episode()] * (MAX_EPISODES + 1)
        self.assert_code("input_too_large", make_input(*episodes))
        trace = [{"seq": 1, "role": "O1", "kind": "message", "summary": "x"}] * (
            MAX_TRACE_EVENTS + 1
        )
        self.assert_code(
            "input_too_large", make_input(base_episode(trace=trace))
        )

    def test_trial_and_seq_explicit_numeric_bounds(self):
        huge = 10 ** 5000  # parsed-object equivalent of an over-limit literal
        invalid_values = (True, 0, -1, MAX_SAFE_INT + 1, huge)
        for value in invalid_values:
            label = type(value).__name__
            with self.subTest(field="trial", value=label):
                self.assert_code(
                    "invalid_input", make_input(base_episode(trial=value))
                )
            with self.subTest(field="seq", value=label):
                self.assert_code(
                    "invalid_input",
                    make_input(
                        base_episode(
                            trace=[
                                {
                                    "seq": value,
                                    "role": "O1",
                                    "kind": "message",
                                    "summary": "x",
                                }
                            ]
                        )
                    ),
                )

        report = build_report(
            make_input(
                base_episode(
                    trial=MAX_SAFE_INT,
                    trace=[
                        {
                            "seq": MAX_SAFE_INT,
                            "role": "O1",
                            "kind": "message",
                            "summary": "max accepted",
                        }
                    ],
                )
            )
        )
        self.assertEqual(report["episodes"][0]["trial"], MAX_SAFE_INT)
        self.assertEqual(
            report["episodes"][0]["trace"][0]["seq"], MAX_SAFE_INT
        )
        rendered = render_markdown(report)
        self.assertIn(str(MAX_SAFE_INT), rendered)
        self.assertIn(str(MAX_SAFE_INT), render_csv(report))
        json.dumps(report)  # accepted maximums remain JSON-serializable


class FileAndCliTests(ScratchTestCase):
    def valid_input_file(self):
        return write_json(self.tmp / "input.json", make_input(base_episode()))

    def test_json_duplicate_keys_rejected(self):
        path = write_text(
            self.tmp / "dup.json",
            '{"schema_version": "report-input-0.1", "dataset_id": "d", '
            '"dataset_id": "e", "episodes": []}',
        )
        with self.assertRaises(ReportError) as caught:
            generate(str(path), str(self.tmp / "out"))
        self.assertEqual(caught.exception.code, "duplicate_key")

    def test_json_nan_and_malformed_rejected(self):
        nan_path = write_text(
            self.tmp / "nan.json",
            '{"schema_version": "report-input-0.1", "dataset_id": "d", '
            '"episodes": [], "x": NaN}',
        )
        with self.assertRaises(ReportError) as caught:
            generate(str(nan_path), str(self.tmp / "out"))
        self.assertEqual(caught.exception.code, "invalid_input")
        bad_path = write_text(self.tmp / "bad.json", '{"schema_version":')
        with self.assertRaises(ReportError) as caught:
            generate(str(bad_path), str(self.tmp / "out2"))
        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertFalse((self.tmp / "out").exists())
        self.assertFalse((self.tmp / "out2").exists())

    def test_utf8_bom_accepted(self):
        payload = json.dumps(make_input(base_episode())).encode("utf-8")
        path = self.tmp / "bom.json"
        path.write_bytes(codecs.BOM_UTF8 + payload)
        generate(str(path), str(self.tmp / "out"))
        self.assertTrue((self.tmp / "out" / "report.json").is_file())

    def test_invalid_utf8_rejected(self):
        path = self.tmp / "latin1.json"
        path.write_bytes(b'{"schema_version": "report-input-0.1", "dataset_id": "caf\xe9"}')
        with self.assertRaises(ReportError) as caught:
            generate(str(path), str(self.tmp / "out"))
        self.assertEqual(caught.exception.code, "invalid_input")

    def test_oversized_input_rejected_before_parse(self):
        path = self.tmp / "big.json"
        with open(path, "wb") as handle:
            handle.write(b'{"schema_version": "report-input-0.1"')
            handle.truncate(MAX_INPUT_BYTES + 1)
        with self.assertRaises(ReportError) as caught:
            generate(str(path), str(self.tmp / "out"))
        self.assertEqual(caught.exception.code, "input_too_large")
        self.assertFalse((self.tmp / "out").exists())

    def test_missing_input_and_parent(self):
        with self.assertRaises(ReportError) as caught:
            generate(str(self.tmp / "absent.json"), str(self.tmp / "out"))
        self.assertEqual(caught.exception.code, "io_error")
        valid = self.valid_input_file()
        with self.assertRaises(ReportError) as caught:
            generate(str(valid), str(self.tmp / "missing-parent" / "out"))
        self.assertEqual(caught.exception.code, "io_error")

    def test_existing_output_refused_and_untouched(self):
        valid = self.valid_input_file()
        existing = self.tmp / "existing"
        existing.mkdir()
        sentinel = existing / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        proc = self.run_cli(
            ["--input", str(valid), "--output-dir", str(existing)],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("output_exists", proc.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(sorted(path.name for path in existing.iterdir()), ["sentinel.txt"])

    def _make_directory_link(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt":
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return True
        return False

    def test_symlink_or_junction_input_and_output_parent_refused(self):
        valid = self.valid_input_file()
        target = self.tmp / "real"
        target.mkdir()
        (target / "input.json").write_bytes(valid.read_bytes())
        link = self.tmp / "link"
        if not self._make_directory_link(target, link):
            self.skipTest("symlinks and junctions are unavailable on this host")
        with self.assertRaises(ReportError) as caught:
            generate(str(valid), str(link / "out"))
        self.assertEqual(caught.exception.code, "unsafe_path")

        with self.assertRaises(ReportError) as caught:
            generate(str(link / "input.json"), str(self.tmp / "out2"))
        self.assertEqual(caught.exception.code, "unsafe_path")

        # The reparse ancestor is fixed; a missing descendant still refuses at
        # the ancestor rather than being probed through.
        with self.assertRaises(ReportError) as caught:
            generate(str(link / "missing.json"), str(self.tmp / "out3"))
        self.assertEqual(caught.exception.code, "unsafe_path")

    def test_guard_rejects_reparse_ancestor_before_descendant_probes(self):
        guard_root = self.tmp / "guard-root"
        guard_root.mkdir()
        link = guard_root / "link"
        link.mkdir()
        input_path = link / "input.json"
        output_path = link / "out"
        real_lstat = os.lstat
        real_stat = os.stat
        lstat_probes = []
        stat_probes = []

        def guarded_lstat(path):
            target = Path(path)
            if target == link:
                return SimpleNamespace(
                    st_mode=stat.S_IFLNK, st_file_attributes=0
                )
            if link in target.parents:
                lstat_probes.append(str(target))
                raise AssertionError("descendant lstat probe: %s" % target)
            return real_lstat(path)

        def guarded_stat(path, *args, **kwargs):
            target = Path(path)
            if target == link or link in target.parents:
                stat_probes.append(str(target))
                raise AssertionError("descendant stat probe: %s" % target)
            return real_stat(path, *args, **kwargs)

        def exercise():
            with mock.patch.object(
                reporting_report.os, "lstat", side_effect=guarded_lstat
            ), mock.patch.object(
                reporting_report.os, "stat", side_effect=guarded_stat
            ):
                calls = (
                    (
                        "input",
                        lambda: reporting_report._load_input(str(input_path)),
                    ),
                    (
                        "output",
                        lambda: reporting_report._prepare_output(
                            str(output_path), ""
                        ),
                    ),
                    (
                        "generate",
                        lambda: generate(str(input_path), str(output_path)),
                    ),
                )
                for label, call in calls:
                    with self.subTest(label):
                        with self.assertRaises(ReportError) as caught:
                            call()
                        self.assertEqual(caught.exception.code, "unsafe_path")

        # No target exists yet.
        exercise()
        self.assertEqual(lstat_probes, [])
        self.assertEqual(stat_probes, [])
        # Create descendants of the fixed reparse ancestor; classification and
        # probe order must not change.
        (link / "input.json").write_text("{}", encoding="utf-8")
        (link / "out").mkdir()
        exercise()
        self.assertEqual(lstat_probes, [])
        self.assertEqual(stat_probes, [])

    def _numeric_cli_case(self, field, literal, tag):
        text = json.dumps(make_input(base_episode()))
        key = '"trial": 1' if field == "trial" else '"seq": 1'
        self.assertIn(key, text)
        text = text.replace(key, '"%s": %s' % (field, literal), 1)
        path = write_text(self.tmp / ("num-%s.json" % tag), text)
        out = self.tmp / ("num-out-%s" % tag)
        proc = self.run_cli(
            ["--input", str(path), "--output-dir", str(out)], cwd=self.tmp
        )
        return proc, out

    def test_trial_and_seq_numeric_bounds_through_cli(self):
        cases = (
            (str(MAX_SAFE_INT), "max", True),
            (str(MAX_SAFE_INT + 1), "max-plus-one", False),
            ("true", "bool", False),
            ("0", "zero", False),
            ("-1", "negative", False),
            ("1" + "0" * 4300, "huge", False),
        )
        for field in ("trial", "seq"):
            for literal, name, accepted in cases:
                tag = "%s-%s" % (field, name)
                with self.subTest(tag):
                    proc, out = self._numeric_cli_case(field, literal, tag)
                    if accepted:
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        data = json.loads(
                            (out / "report.json").read_text(encoding="utf-8")
                        )
                        if field == "trial":
                            self.assertEqual(
                                data["episodes"][0]["trial"], MAX_SAFE_INT
                            )
                        else:
                            self.assertEqual(
                                data["episodes"][0]["trace"][0]["seq"],
                                MAX_SAFE_INT,
                            )
                    else:
                        self.assertEqual(proc.returncode, 1, proc.stderr)
                        self.assertIn("invalid_input", proc.stderr)
                        self.assertNotIn("Traceback", proc.stderr)
                        self.assertLessEqual(len(proc.stderr), 600)
                        self.assertFalse(out.exists())
                        if name == "huge":
                            self.assertNotIn("1" + "0" * 100, proc.stderr)

    def test_huge_json_integer_literal_is_bounded_in_process(self):
        text = json.dumps(make_input(base_episode())).replace(
            '"trial": 1', '"trial": ' + "1" + "0" * 4300, 1
        )
        path = write_text(self.tmp / "huge.json", text)
        out = self.tmp / "huge-out"
        with self.assertRaises(ReportError) as caught:
            generate(str(path), str(out))
        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertIn("unsupported numeric value", str(caught.exception))
        self.assertNotIn("1" + "0" * 100, str(caught.exception))
        self.assertFalse(out.exists())

    def test_publication_failure_retains_complete_stage(self):
        valid = self.valid_input_file()
        final = self.tmp / "published"
        with mock.patch.object(
            reporting_report.os, "rename", side_effect=OSError("simulated")
        ):
            with self.assertRaises(ReportError) as caught:
                generate(str(valid), str(final))
        self.assertEqual(caught.exception.code, "io_error")
        self.assertIn("staged files retained", str(caught.exception))
        self.assertFalse(final.exists())
        stage = self.tmp / ".published.p4-stage"
        self.assertTrue(stage.is_dir())
        self.assertEqual(
            sorted(path.name for path in stage.iterdir()),
            ["episodes.csv", "report.json", "report.md"],
        )
        report = json.loads((stage / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], "report-output-0.1")

    def test_transient_windows_rename_denial_is_retried(self):
        valid = self.valid_input_file()
        final = self.tmp / "published"
        real_rename = os.rename
        calls = {"count": 0}

        def flaky_rename(source, destination):
            calls["count"] += 1
            if calls["count"] == 1:
                error = PermissionError(13, "Access denied")
                error.winerror = 5
                raise error
            return real_rename(source, destination)

        with mock.patch.object(
            reporting_report.os, "rename", side_effect=flaky_rename
        ), mock.patch.object(reporting_report.os, "name", "nt"):
            generate(str(valid), str(final))
        self.assertEqual(calls["count"], 2)
        self.assertTrue((final / "report.json").is_file())
        self.assertFalse((self.tmp / ".published.p4-stage").exists())

    def test_stale_stage_refused_and_preserved(self):
        valid = self.valid_input_file()
        final = self.tmp / "published"
        stage = self.tmp / ".published.p4-stage"
        stage.mkdir()
        marker = stage / "keep.txt"
        marker.write_text("stale", encoding="utf-8")
        with self.assertRaises(ReportError) as caught:
            generate(str(valid), str(final))
        self.assertEqual(caught.exception.code, "io_error")
        self.assertFalse(final.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "stale")

    def test_cli_from_unrelated_cwd_with_child_only_pythonpath(self):
        valid = self.valid_input_file()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        out = self.tmp / "out"
        proc = self.run_cli(
            ["--input", str(valid), "--output-dir", str(out)], cwd=elsewhere
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        for name in ("report.json", "episodes.csv", "report.md"):
            self.assertTrue((out / name).is_file(), name)
        data = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(data["dataset_id"], "test-dataset")
        self.assertEqual(len(data["episodes"]), 1)

    def test_cli_integrated_shaped_layout(self):
        integrated = self.tmp / "integrated-root"
        integrated.mkdir()
        shutil.copytree(ROOT / "reporting", integrated / "reporting")
        valid = self.valid_input_file()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        out = self.tmp / "out"
        proc = self.run_cli(
            ["--input", str(valid), "--output-dir", str(out)],
            cwd=elsewhere,
            root=integrated,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((out / "report.md").is_file())

    def test_import_performs_no_io_and_no_bytecode(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-B", "-c", "import reporting; import reporting.report"],
            cwd=str(empty),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(list(empty.iterdir()), [])

    def test_cli_error_is_nonzero_and_does_not_echo_content(self):
        secret = "SECRET-TRACE-TEXT"
        bad = base_episode(
            trace=[{"seq": 1, "role": "O1", "kind": "message",
                    "summary": secret + "s" * 4096}]
        )
        path = write_json(self.tmp / "bad.json", make_input(bad))
        proc = self.run_cli(
            ["--input", str(path), "--output-dir", str(self.tmp / "out")],
            cwd=self.tmp,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid_input", proc.stderr)
        self.assertNotIn(secret, proc.stderr)
        self.assertLessEqual(len(proc.stderr), 600)

    def test_main_in_process_returns_zero_and_prints_bounded_message(self):
        valid = self.valid_input_file()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--input", str(valid), "--output-dir", str(self.tmp / "out")])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("report.json", stdout.getvalue())


class DeterminismAndEscapingTests(ScratchTestCase):
    def test_outputs_are_byte_deterministic(self):
        data = make_input(
            base_episode("a", outcomes={"evidence": "unknown"}),
            base_episode("b", trial=2, status="partial"),
        )
        first = build_report(data)
        second = build_report(json.loads(json.dumps(data)))
        self.assertEqual(first, second)
        self.assertEqual(render_markdown(first), render_markdown(second))
        self.assertEqual(render_csv(first), render_csv(second))

        valid = write_json(self.tmp / "input.json", data)
        generate(str(valid), str(self.tmp / "one"))
        generate(str(valid), str(self.tmp / "two"))
        for name in ("report.json", "episodes.csv", "report.md"):
            self.assertEqual(
                (self.tmp / "one" / name).read_bytes(),
                (self.tmp / "two" / name).read_bytes(),
                name,
            )

    def test_markdown_escapes_table_and_html_content(self):
        summary = 'a|b\n<script>alert("x")</script>&end'
        report = build_report(
            make_input(
                base_episode(
                    trace=[{"seq": 1, "role": "O1", "kind": "message",
                            "summary": summary}]
                )
            )
        )
        markdown = render_markdown(report)
        self.assertIn("a\\|b", markdown)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertIn("&amp;end", markdown)
        self.assertIn("<br>", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("a|b", markdown)

    def test_markdown_image_link_and_inline_formatting_stay_literal(self):
        summary = (
            "![fixture-alt](https://example.invalid/offline.png)\n"
            "[fixture-link](https://example.invalid/offline) "
            "**bold** `code` ~~strike~~"
        )
        report = build_report(
            make_input(
                base_episode(
                    trace=[
                        {
                            "seq": 1,
                            "role": "O1",
                            "kind": "message",
                            "summary": summary,
                        }
                    ]
                )
            )
        )
        markdown = render_markdown(report)
        self.assertNotIn("![fixture-alt](", markdown)
        self.assertNotIn("[fixture-link](", markdown)
        self.assertNotIn("**bold**", markdown)
        self.assertNotIn("`code`", markdown)
        self.assertNotIn("~~strike~~", markdown)
        self.assertNotIn("<img", markdown)
        self.assertNotIn("<a href", markdown)
        self.assertNotIn("https://example.invalid/offline.png", markdown)
        self.assertNotIn("[fixture-link](", markdown)
        self.assertIn(
            "\\!\\[fixture-alt\\]\\(https\\:\\/\\/example\\.invalid\\/offline\\.png\\)",
            markdown,
        )
        self.assertIn(
            "\\[fixture-link\\]\\(https\\:\\/\\/example\\.invalid\\/offline\\)",
            markdown,
        )
        self.assertIn("\\*\\*bold\\*\\*", markdown)
        self.assertIn("\\`code\\`", markdown)
        self.assertIn("\\~\\~strike\\~\\~", markdown)
        self.assertIn("<br>", markdown)
        # Readable data is preserved: removing the Markdown escapes reproduces
        # the exact supplied image/link and URL text.
        literal = markdown_literal_text(markdown)
        self.assertIn(
            "![fixture-alt](https://example.invalid/offline.png)", literal
        )
        self.assertIn(
            "[fixture-link](https://example.invalid/offline)", literal
        )

    def test_markdown_url_and_email_surfaces_cannot_autolink(self):
        summary = (
            "plain https://example.invalid/trace "
            "email fixture@example.invalid "
            "domain example.com and www.example.com/path "
            "netloc //localhost/path"
        )
        report = build_report(
            make_input(
                base_episode(
                    trace=[
                        {
                            "seq": 1,
                            "role": "O1",
                            "kind": "message",
                            "summary": summary,
                        }
                    ]
                )
            )
        )
        markdown = render_markdown(report)
        for raw in (
            "https://example.invalid/trace",
            "fixture@example.invalid",
            "example.com",
            "www.example.com/path",
            "//localhost/path",
        ):
            self.assertNotIn(raw, markdown)
        self.assertIn(
            "https\\:\\/\\/example\\.invalid\\/trace", markdown
        )
        self.assertIn("fixture\\@example\\.invalid", markdown)
        self.assertIn("example\\.com", markdown)
        self.assertIn("\\/\\/localhost\\/path", markdown)
        # Backslash escapes render away; the supplied text remains readable.
        self.assertIn(summary, markdown_literal_text(markdown))

    def test_csv_escapes_formula_leaders_and_nulls(self):
        episode = base_episode("-1", resources={"estimated_cost_micro_usd": None})
        report = build_report(make_input(episode))
        rows = parse_csv(render_csv(report))
        self.assertEqual(rows[0]["episode_id"], "'-1")
        self.assertEqual(rows[0]["estimated_cost_micro_usd"], "")
        self.assertEqual(rows[0]["trial"], "1")
        # Worker-owned helper covers leaders not reachable by the identifier
        # grammar (so the exported output behavior is explicit).
        for leader in ("=", "+", "-", "@", "\t", "\r"):
            self.assertEqual(
                reporting_report._csv_text(leader + "cmd"), "'" + leader + "cmd"
            )

    def test_csv_null_numeric_cells_empty_and_trial_numeric(self):
        episode = base_episode(
            resources={
                "model_responses": None,
                "tool_actions": None,
                "input_tokens": None,
                "output_tokens": None,
                "elapsed_ms": None,
                "estimated_cost_micro_usd": None,
                "reserved_micro_usd": None,
            }
        )
        rows = parse_csv(render_csv(build_report(make_input(episode))))
        for name in RESOURCE_KEYS:
            self.assertEqual(rows[0][name], "", name)


if __name__ == "__main__":
    unittest.main()
