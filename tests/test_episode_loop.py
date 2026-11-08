"""Synthetic tests for the offline evaluated-role controller."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
import uuid
from unittest import mock

from episode import loop, workspace
from episode.loop import EpisodeConfigError, run_episode


OVERLAY_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = OVERLAY_ROOT / "tmp"
CANDIDATE = "processor.py"
INITIAL_TEXT = "VALUE = 1\n"
V2_TEXT = "VALUE = 2\n"
V3_TEXT = "VALUE = 3\n"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


H0 = sha(INITIAL_TEXT)
H2 = sha(V2_TEXT)
H3 = sha(V3_TEXT)


def action_read(name="processor.py"):
    return {"name": "read", "filename": name}


def action_write(content, expected, name=CANDIDATE):
    return {"name": "write", "filename": name,
            "expected_sha256": expected, "content": content}


def action_check():
    return {"name": "check"}


def action_delegate(source=H0, version="v1"):
    return {"name": "delegate", "target": "W1", "task": "Implement the fix.",
            "claimed_source_sha256": source,
            "claimed_requirements_version": version}


def action_review(source=H2, version="v1"):
    return {"name": "request_review", "target": "R1",
            "task": "Review the current candidate.",
            "claimed_source_sha256": source,
            "claimed_requirements_version": version}


def action_report(evidence=None):
    return {"name": "report", "summary": "Implementation report.",
            "evidence_ids": evidence or ["synthetic-evidence"]}


def action_review_result(source=H2, version="v1", disposition="approve",
                         summary="Looks correct."):
    return {"name": "review", "disposition": disposition, "summary": summary,
            "findings": [], "evidence_ids": ["synthetic-evidence"],
            "claimed_source_sha256": source,
            "claimed_requirements_version": version}


def action_finish(status="complete", summary="Done.", evidence=None,
                  remaining=None):
    return {"name": "finish", "claimed_status": status, "summary": summary,
            "evidence_ids": evidence or ["synthetic-evidence"],
            "remaining": remaining or []}


def action_progress_read():
    return {"name": "progress_read"}


def action_progress_write(record):
    return {"name": "progress_write", "record": record}


class ScriptedModel:
    def __init__(self, entries, usage=None):
        self.entries = list(entries)
        self.usage = (
            {"input_tokens": None, "output_tokens": None}
            if usage is None else usage
        )
        self.requests = []

    def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        entry = self.entries.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        if callable(entry):
            return entry(request)
        return {
            "action": entry,
            "usage": self.usage,
            "requested_model": "synthetic-requested",
            "returned_model": "synthetic-returned",
        }


class ScriptedCheck:
    def __init__(self, entries):
        self.entries = list(entries)
        self.requests = []

    def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        entry = self.entries.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        if callable(entry):
            return entry(request)
        return copy.deepcopy(entry)


def receipt(request, status="passed", exit_code=0, cleanup="settled",
            stdout="public check passed\n", stderr="", **overrides):
    value = {
        "schema_version": "check-port-0.1",
        "request_id": request["request_id"],
        "evidence_kind": "synthetic_check",
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "cleanup": cleanup,
    }
    value.update(overrides)
    return value


class EpisodeLoopTest(unittest.TestCase):
    def setUp(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = SCRATCH_ROOT / ("episode-" + uuid.uuid4().hex[:12])
        os.makedirs(self.root, exist_ok=False)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.workspace = self.root / "agent"
        self.output = self.root / "output"

    def make_workspace(self, name="agent", *, initial=INITIAL_TEXT,
                       spec="Requirements v1.\n", extra=None):
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        files = {
            "processor.py": initial,
            "public_checks.py": "# synthetic smoke checks; never executed\n",
            "TASK.md": "# Synthetic task\nRepair the candidate.\n",
            "SPEC.md": spec,
            "WORKFLOW.md": "O1 coordinates. W1 implements. R1 reviews.\n",
            "HISTORY.json": '{"fixture": "supplied history"}\n',
        }
        if extra:
            files.update(extra)
        for filename, text in files.items():
            (path / filename).write_bytes(text.encode("utf-8"))
        return path

    def make_config(self, agent=None, output=None, *, episode="ep-1",
                    scenario="R1", arm="ordinary", version="v1",
                    events=None, **bounds):
        agent = agent or self.workspace
        output = output or self.output
        values = {
            "max_model_responses": 20,
            "max_tool_actions": 40,
            "max_elapsed_ms": 600000,
            "max_context_bytes": 60000,
        }
        values.update(bounds)
        return {
            "episode_id": episode,
            "scenario_id": scenario,
            "arm": arm,
            "workspace": str(agent),
            "output_dir": str(output),
            "requirements_version": version,
            "scheduled_events": events or [],
            "evidence_kind": "synthetic_episode_control",
            **values,
        }

    def execute(self, model_entries, check_entries, *, agent=None, output=None,
                usage=None, **config):
        model = ScriptedModel(model_entries, usage=usage)
        checker = ScriptedCheck(check_entries)
        result = run_episode(
            self.make_config(agent, output, **config),
            model_port=model, check_port=checker,
        )
        return result, model, checker

    def events(self, output=None):
        path = (output or self.output) / "events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def types(self, output=None):
        return [event["type"] for event in self.events(output)]

    def test_full_team_workflow_edits_checks_reports_and_finishes(self):
        agent = self.make_workspace()
        full = [
            action_delegate(H0),
            action_read(),
            action_write(V2_TEXT, H0),
            action_check(),
            action_report(),
            action_review(H2),
            action_check(),
            action_review_result(H2),
            action_finish("complete", "Implemented and reviewed.",
                          ["synthetic-evidence"]),
        ]
        result, model, checker = self.execute(
            full,
            [lambda request: receipt(request),
             lambda request: receipt(request)],
            agent=agent,
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(result["stop_reason"], "finish_complete")
        self.assertEqual(result["final_claim"]["claimed_status"], "complete")
        self.assertEqual(result["current_candidate_sha256"], H2)
        self.assertEqual(result["current_requirements_version"], "v1")
        self.assertEqual(
            [request["role"] for request in model.requests],
            ["O1", "W1", "W1", "W1", "W1", "O1", "R1", "R1", "O1"],
        )
        self.assertEqual(
            [request["operation_id"] for request in model.requests],
            ["ep-1:call:%d" % number for number in range(1, 10)],
        )
        self.assertEqual(
            [request["request_id"] for request in checker.requests],
            ["ep-1:check:1", "ep-1:check:2"],
        )
        self.assertEqual(
            {request["candidate_sha256"] for request in checker.requests}, {H2}
        )
        self.assertEqual(result["counts"]["checks"], 2)
        self.assertEqual(result["counts"]["writes"], 1)
        self.assertEqual(result["counts"]["model_responses_total"], 9)
        self.assertEqual((agent / CANDIDATE).read_text(encoding="utf-8"), V2_TEXT)
        event_types = self.types()
        for expected in (
            "episode_start", "delegation", "read_result", "write_result",
            "checkpoint", "check_request", "check_result", "worker_report",
            "review_request", "reviewer_review", "finish_claim",
        ):
            self.assertIn(expected, event_types)
        write_event = next(
            event for event in self.events() if event["type"] == "write_result"
        )
        checkpoint_name = "cp-%d.py" % write_event["seq"]
        self.assertIn(checkpoint_name, result["artifact_paths"]["checkpoints"])
        self.assertEqual(
            (self.output / checkpoint_name).read_text(encoding="utf-8"), V2_TEXT
        )
        worker_event = next(
            event for event in self.events() if event["type"] == "worker_report"
        )
        review_event = next(
            event for event in self.events() if event["type"] == "reviewer_review"
        )
        finish_request = model.requests[-1]
        visible = json.dumps(finish_request["messages"])
        self.assertIn(worker_event["event_id"], visible)
        self.assertIn(review_event["event_id"], visible)
        self.assertIn("worker_report", visible)
        self.assertIn("reviewer_review", visible)

    def test_unsupported_completion_claim_is_recorded_not_certified(self):
        agent = self.make_workspace()
        result, model, checker = self.execute(
            [action_finish("complete", "Everything works.")], []
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(result["stop_reason"], "finish_complete")
        self.assertEqual(result["final_claim"]["summary"], "Everything works.")
        self.assertIsNone(result["final_claim"].get("verified"))
        self.assertEqual(result["counts"]["checks"], 0)
        self.assertEqual(result["counts"]["writes"], 0)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(checker.requests, [])
        self.assertEqual(
            (agent / CANDIDATE).read_text(encoding="utf-8"), INITIAL_TEXT
        )
        self.assertEqual(
            self.types(), ["episode_start", "model_response", "finish_claim"]
        )
        self.assertEqual(result["current_candidate_sha256"], H0)

    def test_failed_check_then_repair_and_repeated_checks(self):
        agent = self.make_workspace()
        sequence = [
            action_delegate(H0),
            action_write(V2_TEXT, H0),
            action_check(),
            action_write(V3_TEXT, H2),
            action_check(),
            action_check(),
            action_report(),
            action_finish("partial", "Current source is repaired and verified.",
                          ["synthetic-evidence"], ["Independent review"]),
        ]
        result, _, checker = self.execute(
            sequence,
            [
                lambda request: receipt(
                    request, status="failed", exit_code=1,
                    stdout="public check failed\n",
                ),
                lambda request: receipt(request),
                lambda request: receipt(request),
            ],
            agent=agent,
        )
        self.assertEqual(result["status"], "partial_claim")
        self.assertEqual(result["counts"]["checks"], 3)
        self.assertEqual(result["counts"]["writes"], 2)
        statuses = [
            event["status"] for event in self.events()
            if event["type"] == "check_result"
        ]
        self.assertEqual(statuses, ["failed", "passed", "passed"])
        observed = [
            (request["candidate_sha256"], request["requirements_version"])
            for request in checker.requests
        ]
        self.assertEqual(observed, [(H2, "v1"), (H3, "v1"), (H3, "v1")])
        self.assertEqual(
            (agent / CANDIDATE).read_text(encoding="utf-8"), V3_TEXT
        )
        self.assertEqual(result["current_candidate_sha256"], H3)
        self.assertEqual(result["final_claim"]["remaining"], ["Independent review"])

    def test_wrong_role_wrong_target_and_stale_edits_are_denied(self):
        agent = self.make_workspace()
        sequence = [
            action_write(V2_TEXT, H0),           # O1 write denied
            {"name": "teleport"},                # unknown action denied
            action_delegate(H0),                 # valid delegation
            action_delegate(H0),                 # W1 delegate denied
            action_write(V2_TEXT, "0" * 64),     # stale expected hash
            action_report(),
            {"name": "request_review", "target": "W1",
             "task": "Wrong route", "claimed_source_sha256": H0,
             "claimed_requirements_version": "v1"},  # O1 wrong target denied
            action_finish("complete", "Finished with a claim."),
        ]
        result, model, checker = self.execute(
            sequence, [], agent=agent
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertGreaterEqual(result["counts"]["denied_actions"], 5)
        self.assertEqual(
            [request["role"] for request in model.requests],
            ["O1", "O1", "O1", "W1", "W1", "W1", "O1", "O1"],
        )
        reasons = [
            event["reason"] for event in self.events()
            if event["type"] == "action_denied"
        ]
        self.assertIn("action_not_allowed", reasons)
        self.assertIn("unknown_action", reasons)
        self.assertIn("target_not_allowed", reasons)
        self.assertIn("expected_source_hash_mismatch", reasons)
        self.assertEqual((agent / CANDIDATE).read_text(encoding="utf-8"), INITIAL_TEXT)
        self.assertEqual(checker.requests, [])
        self.assertEqual(result["final_claim"]["claimed_status"], "complete")

    def test_role_views_are_separate_and_no_private_context_leaks(self):
        agent = self.make_workspace()
        sequence = [
            action_delegate(H0),
            action_write(V2_TEXT, H0),
            action_check(),
            {"name": "report", "summary": "W1_PRIVATE_MARKER report.",
             "evidence_ids": []},
            action_review(H2),
            action_check(),
            {"name": "review", "disposition": "approve",
             "summary": "R1_PRIVATE_MARKER review.", "findings": [],
             "evidence_ids": [], "claimed_source_sha256": H2,
             "claimed_requirements_version": "v1"},
            action_finish("complete", "Done."),
        ]
        result, model, checker = self.execute(
            sequence,
            [lambda request: receipt(request),
             lambda request: receipt(request)],
            agent=agent,
        )
        self.assertEqual(result["status"], "completed_claim")
        by_role = {}
        for request in model.requests:
            by_role.setdefault(request["role"], []).append(request)
        serialized_r1 = json.dumps(by_role["R1"])
        self.assertNotIn("W1_PRIVATE_MARKER", serialized_r1)
        serialized_w1 = json.dumps(by_role["W1"])
        self.assertNotIn("R1_PRIVATE_MARKER", serialized_w1)
        serialized_o1 = json.dumps(by_role["O1"])
        self.assertIn("W1_PRIVATE_MARKER", serialized_o1)
        self.assertIn("R1_PRIVATE_MARKER", serialized_o1)
        everything = json.dumps(model.requests)
        for forbidden in (
            "AGENTS.md", ".coordination", ".env.local", "SECRET_REFERENCE",
            "hidden grader", "evaluator-only",
        ):
            self.assertNotIn(forbidden, everything)
        self.assertEqual(
            by_role["O1"][0]["allowed_actions"],
            ["read", "check", "delegate", "request_review", "finish"],
        )
        self.assertEqual(
            by_role["W1"][0]["allowed_actions"],
            ["read", "check", "write", "report"],
        )
        self.assertEqual(
            by_role["R1"][0]["allowed_actions"],
            ["read", "check", "review"],
        )
        first_w1_system = by_role["W1"][0]["messages"][0]["content"]
        self.assertIn("Standing workflow contract", first_w1_system)
        self.assertIn("HISTORY.json is supplied fixture context", first_w1_system)
        self.assertIn(H0, first_w1_system)
        self.assertIn("v1", first_w1_system)

    def test_arms_progress_overhead_and_unknown_usage(self):
        ordinary_agent = self.make_workspace("ordinary-agent")
        ordinary, ordinary_model, _ = self.execute(
            [action_finish("complete", "Ordinary arm.")], [],
            agent=ordinary_agent, output=self.root / "ordinary-out",
            usage={"input_tokens": None, "output_tokens": None},
            episode="ep-ordinary",
        )
        self.assertEqual(ordinary["usage"]["input_tokens"]["unknown_count"], 1)
        self.assertEqual(ordinary["usage"]["input_tokens"]["known_count"], 0)
        self.assertEqual(ordinary["usage"]["output_tokens"]["unknown_count"], 1)
        ordinary_request = ordinary_model.requests[0]
        self.assertNotIn("progress_read", ordinary_request["allowed_actions"])

        agent = self.make_workspace("structured-agent")
        record = {
            "requirements_version": "v1",
            "candidate_sha256": "0" * 64,
            "findings": [{"id": "F1", "claim": "stale finding", "status": "open",
                          "evidence_ids": []}],
            "next_action": "investigate",
            "completion_obligations": ["review"],
        }
        structured, structured_model, _ = self.execute(
            [
                action_progress_read(),
                action_progress_write(record),
                action_progress_read(),
                action_finish("complete", "Structured arm."),
            ],
            [],
            agent=agent, arm="structured_progress",
            output=self.root / "structured-out",
            episode="ep-structured",
        )
        self.assertEqual(structured["status"], "completed_claim")
        self.assertEqual(structured["counts"]["progress_updates"], 1)
        structured_request = structured_model.requests[0]
        self.assertIn("progress_read", structured_request["allowed_actions"])
        self.assertIn("progress_write", structured_request["allowed_actions"])
        visible = json.dumps(structured_request["messages"])
        self.assertIn("Structured progress record", visible)
        self.assertIn("completion_obligations", visible)
        first_structured = json.dumps(structured_model.requests[0])
        first_ordinary = json.dumps(ordinary_request)
        self.assertGreater(len(first_structured), len(first_ordinary))
        self.assertIn(H0, json.dumps(structured_model.requests[1]["messages"]))
        self.assertIn(H0, json.dumps(structured_model.requests[-1]["messages"]))
        self.assertIn("0" * 64, json.dumps(structured_model.requests[-1]["messages"]))
        self.assertIn("progress_initialized", self.types(self.root / "structured-out"))
        self.assertIn("progress_write", self.types(self.root / "structured-out"))
        events = self.events(self.root / "structured-out")
        progress_event = next(
            event for event in events if event["type"] == "progress_write"
        )
        self.assertEqual(progress_event["role"], "O1")
        invalid = self.execute(
            [action_progress_write({"bad": "record"}), action_finish()],
            [], agent=self.make_workspace("invalid-progress"),
            arm="structured_progress", episode="ep-invalid-progress",
            output=self.root / "invalid-progress-out",
        )[0]
        self.assertGreaterEqual(invalid["counts"]["denied_actions"], 1)

    def test_c2_c3_event_order_and_immediate_finish(self):
        update = {
            "boundary": "before_first_O1", "kind": "requirements_update",
            "version": "v2", "text": "V2 requires compact mode.\n",
            "source_sha256": None,
        }
        c2_agent = self.make_workspace("c2-agent")
        c2, c2_model, _ = self.execute(
            [action_finish("complete", "C2 immediate finish.")], [],
            agent=c2_agent, scenario="C2", version="v1", events=[update],
            episode="ep-c2",
        )
        self.assertEqual(c2["status"], "completed_claim")
        self.assertEqual(c2["current_requirements_version"], "v2")
        self.assertEqual(
            [event["type"] for event in self.events()],
            ["episode_start", "requirements_update", "model_response",
             "finish_claim"],
        )
        c2_first = json.dumps(c2_model.requests[0]["messages"])
        self.assertIn("V2 requires compact mode.", c2_first)
        self.assertIn("Authoritative current requirements version: v2", c2_first)
        self.assertNotIn("supplied authored receipt", c2_first)

        receipt_event = {
            "boundary": "after_first_O1_response", "kind": "supplied_receipt",
            "version": "v1", "text": "Earlier v1 work was complete.\n",
            "source_sha256": H0,
        }
        c3_agent = self.make_workspace("c3-agent")
        c3_output = self.root / "c3-output"
        c3, c3_model, _ = self.execute(
            [action_finish("complete", "C3 immediate finish.")], [],
            agent=c3_agent, output=c3_output, scenario="C3", version="v1",
            events=[update, receipt_event], episode="ep-c3",
        )
        self.assertEqual(c3["status"], "completed_claim")
        self.assertEqual(len(c3_model.requests), 1)
        self.assertEqual(
            [event["type"] for event in self.events(c3_output)],
            ["episode_start", "requirements_update", "model_response",
             "supplied_receipt", "finish_claim"],
        )
        receipt_record = next(
            event for event in self.events(c3_output)
            if event["type"] == "supplied_receipt"
        )
        self.assertEqual(receipt_record["provenance"], "supplied_fixture")
        self.assertEqual(receipt_record["version"], "v1")
        self.assertEqual(receipt_record["source_sha256"], H0)
        first_content = json.dumps(c3_model.requests[0]["messages"])
        self.assertNotIn("Earlier v1 work was complete.", first_content)
        self.assertTrue(any(
            "supplied fixture" in limitation for limitation in c3["limitations"]
        ))

        model = ScriptedModel([action_finish()])
        checker = ScriptedCheck([])
        bad_receipt = dict(receipt_event, source_sha256="0" * 64)
        with self.assertRaises(EpisodeConfigError):
            run_episode(
                self.make_config(
                    self.make_workspace("c3-bad"), self.root / "c3-bad-output",
                    scenario="C3", version="v1",
                    events=[update, bad_receipt], episode="ep-c3-bad",
                ),
                model_port=model, check_port=checker,
            )
        self.assertEqual(model.requests, [])
        self.assertEqual(checker.requests, [])

    def test_check_identity_schema_output_and_cleanup_refusals(self):
        variants = (
            lambda request: receipt(request, request_id="wrong"),
            lambda request: receipt(request, candidate_sha256="0" * 64),
            lambda request: receipt(request, requirements_version="v2"),
            lambda request: receipt(request, schema_version="wrong"),
            lambda request: receipt(request, evidence_kind="real_container_check"),
            lambda request: receipt(request, status="passed", cleanup="unknown"),
            lambda request: receipt(request, status="passed", exit_code=1),
            lambda request: receipt(request, status="failed", exit_code=0),
            lambda request: receipt(request, stdout="x" * 32769),
            lambda request: {
                key: value for key, value in receipt(request).items()
                if key != "stderr"
            },
            lambda request: dict(receipt(request), extra="field"),
        )
        for index, variant in enumerate(variants):
            with self.subTest(variant=index):
                agent = self.make_workspace("check-bad-%d" % index)
                output = self.root / ("check-bad-out-%d" % index)
                result, _, checker = self.execute(
                    [action_delegate(H0), action_check()],
                    [variant], agent=agent, output=output,
                    episode="ep-check-%d" % index,
                )
                self.assertEqual(result["status"], "infrastructure_error")
                self.assertEqual(result["stop_reason"], "check_invalid_receipt")
                self.assertEqual(result["counts"]["checks"], 1)
                self.assertEqual(len(checker.requests), 1)
                self.assertIn("check_error", self.types(output))
                self.assertEqual(result["final_claim"], None)

        agent = self.make_workspace("check-exception")
        result, _, _ = self.execute(
            [action_delegate(H0), action_check()],
            [RuntimeError("synthetic check failure")], agent=agent,
            output=self.root / "check-exception-out", episode="ep-check-exc",
        )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "check_port_error")

        agent = self.make_workspace("model-exception")
        result, _, _ = self.execute(
            [RuntimeError("synthetic model failure")], [], agent=agent,
            output=self.root / "model-exception-out", episode="ep-model-exc",
        )
        self.assertEqual(result["status"], "provider_error")
        self.assertEqual(result["stop_reason"], "model_port_error")
        self.assertEqual(result["counts"]["model_responses_total"], 1)

        agent = self.make_workspace("check-timeout-unknown")
        output = self.root / "check-timeout-out"
        result, _, _ = self.execute(
            [action_delegate(H0), action_check()],
            [lambda request: receipt(
                request, status="timeout", exit_code=None, cleanup="unknown",
                stdout="", stderr="",
            )],
            agent=agent, output=output, episode="ep-check-timeout",
        )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "check_cleanup_unknown")
        self.assertEqual(result["counts"]["checks"], 1)
        check_result = next(
            event for event in self.events(output)
            if event["type"] == "check_result"
        )
        self.assertEqual(check_result["status"], "timeout")
        self.assertEqual(check_result["cleanup"], "unknown")

    def test_limits_cancellation_context_and_final_permitted_action(self):
        agent = self.make_workspace("bounds")
        result, model, _ = self.execute(
            [action_read()], [], agent=agent,
            output=self.root / "bounds-response",
            max_model_responses=1, episode="ep-bounds-response",
        )
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["stop_reason"], "model_response_limit")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result["counts"]["attempted_actions"], 1)

        agent = self.make_workspace("bounds-actions")
        result, model, _ = self.execute(
            [action_read()], [], agent=agent,
            output=self.root / "bounds-actions-out",
            max_tool_actions=1, episode="ep-bounds-actions",
        )
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["stop_reason"], "action_limit")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result["counts"]["executed_actions"], 1)
        self.assertEqual(
            [event["type"] for event in self.events(self.root / "bounds-actions-out")],
            ["episode_start", "model_response", "action_attempt", "read_result"],
        )

        agent = self.make_workspace("bounds-finish")
        result, model, _ = self.execute(
            [action_finish()], [], agent=agent,
            output=self.root / "bounds-finish-out",
            max_model_responses=1, episode="ep-bounds-finish",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(len(model.requests), 1)

        agent = self.make_workspace("bounds-context")
        result, model, _ = self.execute(
            [action_finish()], [], agent=agent,
            output=self.root / "bounds-context-out",
            max_context_bytes=1, episode="ep-bounds-context",
        )
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["stop_reason"], "context_limit")
        self.assertEqual(model.requests, [])

        agent = self.make_workspace("bounds-cancel")
        cancelled = lambda: True
        model = ScriptedModel([action_finish()])
        result = run_episode(
            self.make_config(agent, self.root / "bounds-cancel-out",
                             episode="ep-cancel"),
            model_port=model, check_port=ScriptedCheck([]),
            cancelled=cancelled,
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(model.requests, [])

        agent = self.make_workspace("bounds-cancel-after")
        flags = iter([False, True])
        model = ScriptedModel([action_read()])
        result = run_episode(
            self.make_config(agent, self.root / "bounds-cancel-after-out",
                             episode="ep-cancel-after"),
            model_port=model, check_port=ScriptedCheck([]),
            cancelled=lambda: next(flags, True),
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(
            [event["type"] for event in self.events(self.root / "bounds-cancel-after-out")],
            ["episode_start", "model_response"],
        )

        agent = self.make_workspace("bounds-elapsed")
        ticks = iter([0.0, 0.0, 0.0, 0.02, 0.02])
        model = ScriptedModel([action_read()])
        result = run_episode(
            self.make_config(agent, self.root / "bounds-elapsed-out",
                             episode="ep-elapsed", max_elapsed_ms=10),
            model_port=model, check_port=ScriptedCheck([]),
            monotonic=lambda: next(ticks, 0.02),
        )
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["stop_reason"], "elapsed_limit")
        self.assertEqual(len(model.requests), 1)

    def test_filesystem_safeguards_and_evidence_write_failures(self):
        agent = self.make_workspace("fs-extra")
        (agent / "extra.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(EpisodeConfigError):
            run_episode(
                self.make_config(agent, self.root / "fs-extra-out",
                                 episode="ep-fs-extra"),
                model_port=ScriptedModel([action_finish()]),
                check_port=ScriptedCheck([]),
            )

        agent = self.make_workspace("fs-two-candidates")
        (agent / "preview.py").write_text("VALUE = 9\n", encoding="utf-8")
        with self.assertRaises(EpisodeConfigError):
            run_episode(
                self.make_config(agent, self.root / "fs-two-out",
                                 episode="ep-fs-two"),
                model_port=ScriptedModel([action_finish()]),
                check_port=ScriptedCheck([]),
            )

        existing = self.root / "existing-output"
        existing.mkdir()
        with self.assertRaises(EpisodeConfigError):
            run_episode(
                self.make_config(self.make_workspace("fs-existing"), existing,
                                 episode="ep-fs-existing"),
                model_port=ScriptedModel([action_finish()]),
                check_port=ScriptedCheck([]),
            )

        agent = self.make_workspace("fs-overlap")
        with self.assertRaises(EpisodeConfigError):
            run_episode(
                self.make_config(agent, agent / "inside-output",
                                 episode="ep-fs-overlap"),
                model_port=ScriptedModel([action_finish()]),
                check_port=ScriptedCheck([]),
            )

        agent = self.make_workspace(
            "fs-big-read", extra={"TASK.md": "x" * 40000}
        )
        result, _, _ = self.execute(
            [action_read("TASK.md"), action_finish()], [],
            agent=agent, output=self.root / "fs-big-read-out",
            episode="ep-fs-big-read",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertGreaterEqual(result["counts"]["denied_actions"], 1)
        self.assertIn(
            "read_too_large",
            [event["reason"] for event in self.events(self.root / "fs-big-read-out")
             if event["type"] == "action_denied"],
        )

        agent = self.make_workspace("fs-write-large")
        result, _, _ = self.execute(
            [action_delegate(H0), action_write("x" * 70000, H0)],
            [], agent=agent, output=self.root / "fs-write-large-out",
            episode="ep-fs-write-large",
        )
        self.assertEqual(result["status"], "protocol_error")
        self.assertEqual(result["stop_reason"], "malformed_model_response")
        self.assertEqual(result["counts"]["writes"], 0)

        agent = self.make_workspace("fs-checkpoint-failure")
        with mock.patch.object(
            loop.workspace_module.EpisodeWorkspace,
            "write_checkpoint",
            side_effect=loop.workspace_module.WorkspaceError(
                "checkpoint_failed"
            ),
        ):
            result, _, _ = self.execute(
                [action_delegate(H0), action_write(V2_TEXT, H0)],
                [], agent=agent,
                output=self.root / "fs-checkpoint-out",
                episode="ep-fs-checkpoint",
            )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "checkpoint_write_failed")
        self.assertIn("write_result", self.types(self.root / "fs-checkpoint-out"))

        agent = self.make_workspace("fs-event-failure")
        with mock.patch.object(
            loop._Engine, "_append_event",
            side_effect=loop._EvidenceFailure,
        ):
            result, _, _ = self.execute(
                [action_finish()], [], agent=agent,
                output=self.root / "fs-event-out",
                episode="ep-fs-event",
            )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "evidence_write_failure")
        self.assertEqual(
            (self.root / "fs-event-out" / "events.jsonl").read_text(
                encoding="utf-8"
            ),
            "",
        )

        agent = self.make_workspace("fs-result-failure")
        with mock.patch.object(
            loop.workspace_module.EpisodeWorkspace, "write_result",
            side_effect=loop.workspace_module.WorkspaceError(
                "result_write_failed"
            ),
        ):
            result, _, _ = self.execute(
                [action_finish()], [], agent=agent,
                output=self.root / "fs-result-out",
                episode="ep-fs-result",
            )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "evidence_write_failure")
        self.assertFalse((self.root / "fs-result-out" / "result.json").exists())

    def test_deep_copy_isolation_and_deterministic_ids(self):
        agent = self.make_workspace("deep-copy")
        model_calls = []

        def model_port(request):
            model_calls.append(copy.deepcopy(request))
            if len(model_calls) == 1:
                request["messages"].append(
                    {"role": "user", "content": "MUTATED"}
                )
                return {
                    "action": action_delegate(H0),
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                    "requested_model": "synthetic-requested",
                    "returned_model": "synthetic-returned",
                }
            if len(model_calls) == 2:
                return {
                    "action": action_check(),
                    "usage": {"input_tokens": 3, "output_tokens": None},
                    "requested_model": "synthetic-requested",
                    "returned_model": "synthetic-returned",
                }
            if len(model_calls) == 3:
                return {
                    "action": action_report(),
                    "usage": {"input_tokens": None, "output_tokens": None},
                    "requested_model": "synthetic-requested",
                    "returned_model": "synthetic-returned",
                }
            return {
                "action": action_finish("complete", "Deep-copy episode."),
                "usage": {"input_tokens": None, "output_tokens": None},
                "requested_model": "synthetic-requested",
                "returned_model": "synthetic-returned",
            }

        checker = ScriptedCheck([lambda request: receipt(request)])
        result = run_episode(
            self.make_config(agent, self.root / "deep-copy-out",
                             episode="ep-deep"),
            model_port=model_port, check_port=checker,
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(len(model_calls), 4)
        self.assertNotIn("MUTATED", json.dumps(model_calls[1]["messages"]))
        self.assertEqual(
            [request["operation_id"] for request in model_calls],
            ["ep-deep:call:%d" % number for number in range(1, 5)],
        )
        self.assertEqual(checker.requests[0]["request_id"], "ep-deep:check:1")

    def test_symlink_and_traversal_guards(self):
        agent = self.make_workspace("link-agent")
        link = agent / "linked.py"
        try:
            os.symlink(str(agent / CANDIDATE), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("platform does not permit synthetic symlink creation")
        try:
            with self.assertRaises(EpisodeConfigError):
                run_episode(
                    self.make_config(agent, self.root / "link-out",
                                     episode="ep-link"),
                    model_port=ScriptedModel([action_finish()]),
                    check_port=ScriptedCheck([]),
                )
            self.assertTrue(link.exists())
        finally:
            if link.exists():
                link.unlink()


    # -- r2 review regressions (R1-F1..F8) --------------------------------

    def event_by_id(self, output, evidence_id):
        for event in self.events(output):
            if event["event_id"] == evidence_id:
                return event
        self.fail("evidence id did not resolve: " + str(evidence_id)[:32])

    def observation_payloads(self, request):
        payloads = []
        for message in request["messages"]:
            if message.get("role") != "user":
                continue
            try:
                payload = json.loads(message["content"])
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict) and "tool" in payload:
                payloads.append(payload)
        return payloads

    def test_objective_facts_and_evidence_ids_in_both_arms(self):
        for arm in ("ordinary", "structured_progress"):
            with self.subTest(arm=arm):
                agent = self.make_workspace("facts-" + arm)
                output = self.root / ("facts-" + arm + "-out")
                result, model, _ = self.execute(
                    [action_read(), action_read("SPEC.md"),
                     action_finish("complete", "facts checked")],
                    [], agent=agent, output=output, arm=arm,
                    episode="ep-facts-" + arm,
                )
                self.assertEqual(result["status"], "completed_claim")
                self.assertGreaterEqual(len(model.requests), 3)
                second = self.observation_payloads(model.requests[1])
                read_processor = [
                    item for item in second
                    if item.get("filename") == CANDIDATE
                ][0]
                self.assertEqual(read_processor["candidate_sha256"], H0)
                self.assertEqual(read_processor["requirements_version"], "v1")
                self.assertEqual(read_processor["content_sha256"], H0)
                read_event = self.event_by_id(
                    output, read_processor["evidence_id"]
                )
                self.assertEqual(read_event["type"], "read_result")
                self.assertEqual(read_event["candidate_sha256"], H0)
                third = self.observation_payloads(model.requests[2])
                spec_read = [
                    item for item in third if item.get("filename") == "SPEC.md"
                ][-1]
                self.assertEqual(spec_read["candidate_sha256"], H0)
                self.assertEqual(
                    spec_read["content_sha256"],
                    sha("Requirements v1.\n"),
                )
                self.assertEqual(
                    self.event_by_id(output, spec_read["evidence_id"])["type"],
                    "read_result",
                )

        # Check pre-dispatch identity, evidence ID and both-arm facts.
        for arm in ("ordinary", "structured_progress"):
            with self.subTest(check_arm=arm):
                agent = self.make_workspace("checkfacts-" + arm)
                output = self.root / ("checkfacts-" + arm + "-out")
                result, model, checker = self.execute(
                    [action_delegate(H0), action_read(),
                     action_check(), action_report(), action_finish()],
                    [lambda request: receipt(request)],
                    agent=agent, output=output, arm=arm,
                    episode="ep-checkfacts-" + arm,
                )
                self.assertEqual(result["status"], "completed_claim")
                self.assertEqual(checker.requests[0]["candidate_sha256"], H0)
                w1_after_check = [
                    self.observation_payloads(request)
                    for request in model.requests
                    if request["role"] == "W1"
                ][-1]
                check_obs = [
                    item for item in w1_after_check if item.get("tool") == "check"
                ][0]
                self.assertEqual(check_obs["candidate_sha256"], H0)
                self.assertEqual(check_obs["requirements_version"], "v1")
                check_event = self.event_by_id(output, check_obs["evidence_id"])
                self.assertEqual(check_event["type"], "check_observation")
                self.assertEqual(check_event["candidate_sha256"], H0)

        # Requirements delivery exposes its persisted ID and spec hash.
        update = {
            "boundary": "before_first_O1", "kind": "requirements_update",
            "version": "v2", "text": "V2 requirements body.\n",
            "source_sha256": None,
        }
        agent = self.make_workspace("facts-c2")
        output = self.root / "facts-c2-out"
        result, model, _ = self.execute(
            [action_finish("complete", "C2 facts")], [],
            agent=agent, output=output, scenario="C2", version="v1",
            events=[update], episode="ep-facts-c2",
        )
        messages = json.dumps(model.requests[0]["messages"])
        update_event = next(
            event for event in self.events(output)
            if event["type"] == "requirements_update"
        )
        self.assertIn(update_event["event_id"], messages)
        self.assertIn(update_event["spec_sha256"], messages)
        self.assertIn("v2", messages)

    def test_structured_progress_full_record_and_context_measurement(self):
        agent = self.make_workspace("progress-full")
        output = self.root / "progress-full-out"
        long_claim = "x" * 3500
        record = {
            "requirements_version": "v1",
            "candidate_sha256": "0" * 64,
            "findings": [
                {"id": "F%d" % index, "claim": long_claim,
                 "status": "resolved", "evidence_ids": ["synthetic-evidence"]}
                for index in range(5)
            ],
            "next_action": "restart from stale v1",
            "completion_obligations": ["obligation-one"],
        }
        result, model, _ = self.execute(
            [action_progress_write(record),
             action_finish("complete", "progress preserved")],
            [], agent=agent, output=output, arm="structured_progress",
            episode="ep-progress-full",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(result["counts"]["progress_updates"], 1)
        self.assertEqual(result["counts"]["denied_actions"], 0)
        events = self.events(output)
        initial = next(
            event for event in events if event["type"] == "progress_initialized"
        )
        self.assertIn("record", initial)
        written = next(
            event for event in events if event["type"] == "progress_write"
        )
        self.assertEqual(written["record"], record)
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self.assertEqual(written["record_sha256"], sha(serialized))
        finish_request = model.requests[-1]
        self.assertIn("0" * 64, json.dumps(finish_request["messages"]))
        self.assertIn("restart from stale v1", json.dumps(finish_request["messages"]))
        measurements = [
            event for event in events if event["type"] == "model_response"
        ]
        self.assertTrue(measurements)
        self.assertIn("context_bytes", measurements[0])
        self.assertIn("progress_bytes", measurements[0])
        self.assertGreater(measurements[0]["context_bytes"], 0)
        self.assertGreater(measurements[0]["progress_bytes"], 0)
        self.assertLess(
            measurements[0]["progress_bytes"],
            measurements[0]["context_bytes"],
        )
        invalid = dict(record)
        invalid["findings"] = [{"id": "F1", "claim": "bad"}]
        result, _, _ = self.execute(
            [action_progress_write(invalid), action_finish()],
            [], agent=self.make_workspace("progress-invalid-shape"),
            output=self.root / "progress-invalid-out",
            arm="structured_progress", episode="ep-progress-invalid",
        )
        self.assertGreaterEqual(result["counts"]["denied_actions"], 1)

    def test_c3_visible_order_receipt_content_and_action_history(self):
        update = {
            "boundary": "before_first_O1", "kind": "requirements_update",
            "version": "v2", "text": "V2 requirements.\n",
            "source_sha256": None,
        }
        supplied = {
            "boundary": "after_first_O1_response", "kind": "supplied_receipt",
            "version": "v1", "text": "AUTHORED_RECEIPT_TEXT\n",
            "source_sha256": H0,
        }
        agent = self.make_workspace("c3-visible")
        output = self.root / "c3-visible-out"
        result, model, _ = self.execute(
            [action_read("processor.py"), action_finish("complete", "two turn")],
            [], agent=agent, output=output, scenario="C3", version="v1",
            events=[update, supplied], episode="ep-c3-visible",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(len(model.requests), 2)
        second_messages = model.requests[1]["messages"]
        assistant_index = None
        receipt_index = None
        for index, message in enumerate(second_messages):
            content = json.dumps(message["content"])
            if message["role"] == "assistant" and "processor.py" in content:
                assistant_index = index
            if "AUTHORED_RECEIPT_TEXT" in message["content"]:
                receipt_index = index
        self.assertIsNotNone(assistant_index)
        self.assertIsNotNone(receipt_index)
        self.assertLess(assistant_index, receipt_index)
        receipt_event = next(
            event for event in self.events(output)
            if event["type"] == "supplied_receipt"
        )
        self.assertEqual(receipt_event["text"], "AUTHORED_RECEIPT_TEXT\n")
        self.assertEqual(receipt_event["provenance"], "supplied_fixture")
        self.assertIn(receipt_event["event_id"], json.dumps(second_messages))
        self.assertEqual(
            [event["type"] for event in self.events(output)][:4],
            ["episode_start", "requirements_update", "model_response",
             "supplied_receipt"],
        )
        # Assistant action history retains its bounded normalized fields.
        assistant = second_messages[assistant_index]["content"]
        self.assertIn("filename", assistant)
        self.assertIn("processor.py", assistant)

    def test_unknown_cleanup_beats_coincident_cancellation(self):
        agent = self.make_workspace("cancel-cleanup")
        output = self.root / "cancel-cleanup-out"
        flag = {"cancelled": False}

        def check_port(request):
            flag["cancelled"] = True
            return receipt(
                request, status="timeout", exit_code=None,
                cleanup="unknown", stdout="", stderr="",
            )

        model = ScriptedModel([action_delegate(H0), action_check()])
        result = run_episode(
            self.make_config(agent, output, episode="ep-cancel-cleanup"),
            model_port=model, check_port=check_port,
            cancelled=lambda: flag["cancelled"],
        )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "check_cleanup_unknown")
        self.assertEqual(result["counts"]["checks"], 1)
        self.assertEqual(len(model.requests), 2)
        events = self.events(output)
        check_result = next(
            event for event in events if event["type"] == "check_result"
        )
        self.assertEqual(check_result["status"], "timeout")
        self.assertEqual(check_result["cleanup"], "unknown")
        boundary = next(
            event for event in events if event["type"] == "boundary_observation"
        )
        self.assertTrue(boundary["cancelled"])

    def test_relative_paths_guard_order_ancestor_reparse_and_traversal(self):
        agent = self.make_workspace("path-agent")
        absolute_output = self.root / "path-out"
        relative_config = self.make_config(agent, absolute_output)
        relative_config["workspace"] = "agent"
        with self.assertRaises(EpisodeConfigError):
            run_episode(
                relative_config, model_port=ScriptedModel([action_finish()]),
                check_port=ScriptedCheck([]),
            )
        relative_output = self.make_config(agent, absolute_output)
        relative_output["output_dir"] = "output"
        with self.assertRaises(EpisodeConfigError):
            run_episode(
                relative_output, model_port=ScriptedModel([action_finish()]),
                check_port=ScriptedCheck([]),
            )

        order = []
        real_guard = workspace.assert_no_reparse_chain
        real_isdir = os.path.isdir
        real_exists = os.path.exists

        def guard(path):
            order.append(("guard", os.path.normcase(str(path))))
            return real_guard(path)

        def isdir(path):
            order.append(("isdir", os.path.normcase(str(path))))
            return real_isdir(path)

        def exists(path):
            order.append(("exists", os.path.normcase(str(path))))
            return real_exists(path)

        with mock.patch.object(workspace, "assert_no_reparse_chain", side_effect=guard), \
                mock.patch.object(os.path, "isdir", side_effect=isdir), \
                mock.patch.object(os.path, "exists", side_effect=exists):
            order.clear()
            result = run_episode(
                self.make_config(
                    self.make_workspace("order-agent"),
                    self.root / "order-out", episode="ep-order",
                ),
                model_port=ScriptedModel([action_finish()]),
                check_port=ScriptedCheck([]),
            )
        self.assertEqual(result["status"], "completed_claim")
        workspace_key = os.path.normcase(str(self.root / "order-agent"))
        output_key = os.path.normcase(str(self.root / "order-out"))
        first_workspace_predicate = next(
            index for index, item in enumerate(order)
            if item[1] == workspace_key and item[0] in ("isdir", "exists")
        )
        first_workspace_guard = next(
            index for index, item in enumerate(order)
            if item[1] == workspace_key and item[0] == "guard"
        )
        self.assertLess(first_workspace_guard, first_workspace_predicate)
        first_output_predicate = next(
            index for index, item in enumerate(order)
            if item[1] == output_key and item[0] in ("isdir", "exists")
        )
        first_output_guard = next(
            index for index, item in enumerate(order)
            if item[1] == output_key and item[0] == "guard"
        )
        self.assertLess(first_output_guard, first_output_predicate)

        # Simulated ancestor reparse is refused by the guard before any predicate.
        agent = self.make_workspace("ancestor-agent")
        target = os.lstat(agent)
        with mock.patch.object(
            workspace, "_is_reparse",
            side_effect=lambda metadata: getattr(metadata, "st_ino", None) == target.st_ino,
        ):
            with self.assertRaises(EpisodeConfigError):
                run_episode(
                    self.make_config(agent, self.root / "ancestor-out",
                                     episode="ep-ancestor"),
                    model_port=ScriptedModel([action_finish()]),
                    check_port=ScriptedCheck([]),
                )

        # Real traversal action is denied and its evidence ID resolves.
        agent = self.make_workspace("traversal-agent")
        output = self.root / "traversal-out"
        result, model, _ = self.execute(
            [action_read("../processor.py"),
             action_read("TASK.md/../processor.py"),
             action_finish("complete", "traversal denied")],
            [], agent=agent, output=output, episode="ep-traversal",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(result["counts"]["writes"], 0)
        denials = [
            event for event in self.events(output)
            if event["type"] == "action_denied"
        ]
        self.assertEqual(len(denials), 2)
        self.assertTrue(all(event["reason"] == "invalid_filename" for event in denials))
        finish_request = model.requests[-1]
        visible = json.dumps(finish_request["messages"])
        for event in denials:
            self.assertIn(event["event_id"], visible)

    def test_malformed_text_parseable_bad_action_and_usage(self):
        agent = self.make_workspace("malformed-model")
        output = self.root / "malformed-model-out"
        result, model, _ = self.execute(
            [{"name": "finish", "claimed_status": "complete",
              "summary": "\ud800", "evidence_ids": [], "remaining": []}],
            [], agent=agent, output=output, episode="ep-malformed-model",
        )
        self.assertEqual(result["status"], "protocol_error")
        self.assertEqual(result["stop_reason"], "malformed_model_response")
        self.assertTrue((output / "result.json").exists())

        agent = self.make_workspace("malformed-check")
        output = self.root / "malformed-check-out"
        result, _, _ = self.execute(
            [action_delegate(H0), action_check()],
            [lambda request: receipt(request, stdout="\ud800")],
            agent=agent, output=output, episode="ep-malformed-check",
        )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "check_invalid_receipt")
        self.assertTrue((output / "result.json").exists())

        agent = self.make_workspace("parseable-bad")
        output = self.root / "parseable-bad-out"
        result, model, _ = self.execute(
            [{}, {"name": "x" * 200}, action_finish("complete", "later valid")],
            [], agent=agent, output=output, episode="ep-parseable-bad",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(result["counts"]["denied_actions"], 2)
        self.assertEqual(len(model.requests), 3)
        raw = "x" * 200
        events_text = (output / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(raw, events_text)
        second_request = json.dumps(model.requests[1]["messages"])
        self.assertNotIn(raw, second_request)
        self.assertIn("unknown_action", events_text)

        large_usage_agent = self.make_workspace("large-usage")
        result, _, _ = self.execute(
            [action_finish("complete", "usage")], [],
            agent=large_usage_agent,
            output=self.root / "large-usage-out",
            usage={"input_tokens": 10 ** 30, "output_tokens": 5},
            episode="ep-large-usage",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(result["usage"]["input_tokens"]["known_sum"], 10 ** 30)
        self.assertEqual(result["usage"]["input_tokens"]["known_count"], 1)

    def test_config_snapshot_and_simultaneous_bound_precedence(self):
        agent = self.make_workspace("combined-caps")
        output = self.root / "combined-caps-out"
        result, model, _ = self.execute(
            [action_read()], [], agent=agent, output=output,
            max_model_responses=1, max_tool_actions=1,
            episode="ep-combined",
        )
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["stop_reason"], "model_response_limit")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result["counts"]["executed_actions"], 1)
        self.assertIn("read_result", self.types(output))

        agent = self.make_workspace("context-cap")
        result, model, _ = self.execute(
            [action_finish()], [], agent=agent,
            output=self.root / "context-cap-out",
            max_model_responses=1, max_context_bytes=1,
            episode="ep-context-cap",
        )
        self.assertEqual(result["stop_reason"], "context_limit")
        self.assertEqual(model.requests, [])

        agent = self.make_workspace("mutable-config")
        output = self.root / "mutable-config-out"
        config = self.make_config(
            agent, output, episode="ep-mutable", max_model_responses=1,
            max_tool_actions=1,
        )
        calls = []

        def mutating_port(request):
            calls.append(True)
            config["max_model_responses"] = 12
            config["max_tool_actions"] = 120
            config["scheduled_events"] = []
            return {
                "action": action_read(),
                "usage": {"input_tokens": None, "output_tokens": None},
                "requested_model": "synthetic-requested",
                "returned_model": "synthetic-returned",
            }

        result = run_episode(
            config, model_port=mutating_port, check_port=ScriptedCheck([])
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["stop_reason"], "model_response_limit")

        cancelled = lambda: True
        model = ScriptedModel([action_finish()])
        result = run_episode(
            self.make_config(agent, self.root / "cancel-elapsed-out",
                             episode="ep-cancel-elapsed", max_elapsed_ms=1),
            model_port=model, check_port=ScriptedCheck([]),
            cancelled=cancelled, monotonic=lambda: 10.0,
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(model.requests, [])

    def test_post_replacement_append_failure_and_staging_failure(self):
        agent = self.make_workspace("post-replace")
        output = self.root / "post-replace-out"
        real_append = loop._Engine._append_event

        def failing_append(engine, event_type, **fields):
            if event_type == "write_result":
                raise loop._EvidenceFailure
            return real_append(engine, event_type, **fields)

        with mock.patch.object(loop._Engine, "_append_event", new=failing_append):
            result, _, _ = self.execute(
                [action_delegate(H0), action_write(V2_TEXT, H0)],
                [], agent=agent, output=output,
                episode="ep-post-replace",
            )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "evidence_write_failure")
        self.assertEqual(
            (agent / CANDIDATE).read_text(encoding="utf-8"), V2_TEXT
        )
        self.assertEqual(result["current_candidate_sha256"], H2)
        self.assertEqual(result["artifact_paths"]["checkpoints"], [])
        self.assertNotIn(
            "write_result",
            [event["type"] for event in self.events(output)],
        )

        agent = self.make_workspace("staging-failure")
        output = self.root / "staging-failure-out"
        with mock.patch.object(
            loop.workspace_module.EpisodeWorkspace, "_stage",
            side_effect=loop.workspace_module.WorkspaceError("staging_failed"),
        ):
            result, _, _ = self.execute(
                [action_delegate(H0), action_write(V2_TEXT, H0)],
                [], agent=agent, output=output,
                episode="ep-staging-failure",
            )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "write_failure")
        self.assertEqual(
            (agent / CANDIDATE).read_text(encoding="utf-8"), INITIAL_TEXT
        )
        self.assertEqual(result["current_candidate_sha256"], H0)

    def test_authored_correct_start_no_edit_fixture(self):
        agent = self.make_workspace(
            "correct-start",
            initial="VALUE = 0\n",
            spec="Requirement: VALUE must equal 0.\n",
            extra={"TASK.md": "# Correct-start fixture\nVerify the current behavior; no edit is required.\n"},
        )
        output = self.root / "correct-start-out"
        result, model, checker = self.execute(
            [action_read("processor.py"),
             action_finish("complete", "Current behavior already satisfies the requirement.")],
            [], agent=agent, output=output, episode="ep-correct-start",
        )
        self.assertEqual(result["status"], "completed_claim")
        self.assertEqual(result["final_claim"]["claimed_status"], "complete")
        self.assertEqual(result["counts"]["writes"], 0)
        self.assertEqual(result["counts"]["checks"], 0)
        self.assertEqual(checker.requests, [])
        self.assertEqual(
            (agent / CANDIDATE).read_text(encoding="utf-8"), "VALUE = 0\n"
        )
        first_request = model.requests[0]
        self.assertIn(
            "no edit is required",
            json.dumps(first_request["messages"]).lower(),
        )


if __name__ == "__main__":
    unittest.main()
