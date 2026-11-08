"""Fake-transport tests for the budgeted model-port bridge (no network/provider)."""

from __future__ import annotations

import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

from adapters import deepseek
from adapters.budget import BudgetError
from adapters.budgeted_deepseek import BudgetedDeepSeekClient
from episode import provider as provider_module
from episode.provider import BudgetedModelPort, ModelPortError, PROTOCOL


OVERLAY = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = OVERLAY / "tmp"
KEY = "synthetic-bridge-key-DDDD4444"


def usage():
    return deepseek.Usage(
        prompt_tokens=10,
        cache_hit_input_tokens=2,
        uncached_input_tokens=8,
        output_tokens=5,
        reasoning_tokens=1,
        total_tokens=15,
        raw={"synthetic": "raw-provider-usage"},
    )


def completion(action, *, tool_calls=(), content=None, usage_value=None):
    message = {"role": "assistant", "reasoning_content": "synthetic reasoning"}
    if content is not None:
        message["content"] = content
    elif tool_calls:
        message["content"] = None
        message["tool_calls"] = [
            {"type": "function", "id": call.id, "function": {
                "name": call.name, "arguments": "{}",
            }} for call in tool_calls
        ]
    else:
        message["content"] = json.dumps({"action": action})
    return deepseek.Completion(
        requested_model=deepseek.MODEL,
        returned_model="synthetic-returned-model",
        assistant_message=message,
        tool_calls=tuple(tool_calls),
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=usage_value or usage(),
        reservation_usd=Decimal("0.001"),
    )


def request(operation_id="ep:call:1", *, role="O1", messages=None,
            allowed_actions=None):
    return {
        "episode_id": "ep",
        "operation_id": operation_id,
        "role": role,
        "invocation_index": 1,
        "messages": messages if messages is not None else [
            {"role": "system", "content": "Standing role context."},
            {"role": "user", "content": '{"tool": "read", "evidence_id": "ep:event:1"}'},
        ],
        "allowed_actions": allowed_actions if allowed_actions is not None else [
            "read", "check", "finish",
        ],
    }


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def complete(self, operation_id, messages, *, max_cost_usd, tools,
                 max_tokens, timeout_seconds, max_response_bytes, dispatch):
        self.calls.append({
            "operation_id": operation_id,
            "messages": copy.deepcopy(messages),
            "max_cost_usd": max_cost_usd,
            "tools": tools,
            "max_tokens": max_tokens,
            "timeout_seconds": timeout_seconds,
            "max_response_bytes": max_response_bytes,
            "dispatch": dispatch,
        })
        if self.error is not None:
            raise self.error
        return self.response


class ProviderBridgeTest(unittest.TestCase):
    def setUp(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = SCRATCH_ROOT / ("bridge-" + uuid.uuid4().hex[:12])
        os.makedirs(self.root, exist_ok=False)
        self.addCleanup(shutil.rmtree, self.root, True)

    def assert_bridge_error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(ModelPortError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def test_successful_role_request_json_translation_and_observation(self):
        fake = FakeClient(completion({"name": "read", "filename": "TASK.md"}))
        port = BudgetedModelPort(fake, dispatch=True)
        supplied = request()
        original = copy.deepcopy(supplied)
        result = port(supplied)
        self.assertEqual(result["action"], {"name": "read", "filename": "TASK.md"})
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 5})
        self.assertEqual(result["requested_model"], deepseek.MODEL)
        self.assertEqual(result["returned_model"], "synthetic-returned-model")
        self.assertEqual(supplied, original, "bridge mutated the loop request")
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["operation_id"], "ep:call:1")
        self.assertIsNone(call["tools"])
        self.assertIs(call["dispatch"], True)
        self.assertEqual(call["max_tokens"], 2048)
        self.assertEqual(call["timeout_seconds"], 30)
        self.assertGreater(call["max_cost_usd"], Decimal("0"))
        self.assertLessEqual(call["max_cost_usd"], Decimal("0.10"))
        self.assertEqual(call["messages"][0]["role"], "system")
        self.assertIn(PROTOCOL, call["messages"][0]["content"])
        self.assertIn("Allowed actions for this role", call["messages"][0]["content"])
        self.assertIn("'read'", call["messages"][0]["content"])
        self.assertNotIn("'write'", call["messages"][0]["content"])
        self.assertNotIn("'review'", call["messages"][0]["content"])
        self.assertEqual(
            call["messages"][1:],
            [{"role": "system", "content": "Standing role context."},
             {"role": "user", "content": '{"tool": "read", "evidence_id": "ep:event:1"}'}],
            "bridge did not preserve role history unchanged",
        )
        observations = port.observations
        self.assertEqual(len(observations), 1)
        observation = observations[0]
        self.assertEqual(observation["status"], "ok")
        self.assertEqual(observation["operation_id"], "ep:call:1")
        self.assertEqual(observation["role"], "O1")
        self.assertEqual(observation["usage"]["input_tokens"], 10)
        self.assertEqual(observation["usage"]["output_tokens"], 5)
        self.assertEqual(observation["usage"]["cache_hit_input_tokens"], 2)
        self.assertEqual(observation["usage"]["uncached_input_tokens"], 8)
        self.assertEqual(observation["usage"]["reasoning_tokens"], 1)
        self.assertGreater(observation["reservation_micro_usd"], 0)
        self.assertGreater(observation["estimated_cost_micro_usd"], 0)
        observations.append({"injected": True})
        self.assertEqual(len(port.observations), 1, "observations were not copied")

    def test_two_operation_ids_preserve_independent_histories(self):
        first = FakeClient(completion({"name": "read", "filename": "TASK.md"}))
        second = FakeClient(completion({"name": "check"}))
        port = BudgetedModelPort(first, dispatch=True)
        port(request("ep:call:1", messages=[
            {"role": "user", "content": "first history only"},
        ]))
        other = BudgetedModelPort(second, dispatch=True)
        other(request("ep:call:2", messages=[
            {"role": "user", "content": "second history only"},
        ]))
        self.assertEqual(first.calls[0]["operation_id"], "ep:call:1")
        self.assertEqual(second.calls[0]["operation_id"], "ep:call:2")
        first_text = json.dumps(first.calls[0]["messages"])
        second_text = json.dumps(second.calls[0]["messages"])
        self.assertIn("first history only", first_text)
        self.assertNotIn("second history only", first_text)
        self.assertIn("second history only", second_text)
        self.assertNotIn("first history only", second_text)

    def test_pre_dispatch_refusals_make_zero_calls(self):
        fake = FakeClient(completion({"name": "read", "filename": "TASK.md"}))
        port = BudgetedModelPort(fake)
        self.assert_bridge_error(
            "bridge_dispatch_not_authorized", port, request()
        )
        self.assertEqual(fake.calls, [])
        self.assertEqual(port.observations[0]["status"], "bridge_dispatch_not_authorized")
        truthy = BudgetedModelPort(fake, dispatch=1)
        self.assert_bridge_error("bridge_dispatch_not_authorized", truthy, request())
        self.assertEqual(fake.calls, [])

        expired = BudgetedModelPort(
            fake, dispatch=True, deadline=10.0, monotonic=lambda: 10.5
        )
        self.assert_bridge_error("bridge_deadline_expired", expired, request())
        self.assertEqual(fake.calls, [])

        capped = BudgetedModelPort(
            fake, dispatch=True, per_call_cap_usd="0.0000001"
        )
        self.assert_bridge_error("bridge_insufficient_cap", capped, request())
        self.assertEqual(fake.calls, [])

        for kwargs in (
            {"max_tokens": True},
            {"max_tokens": 0},
            {"max_tokens": 4097},
            {"timeout_seconds": False},
            {"timeout_seconds": 0},
            {"timeout_seconds": 31},
            {"per_call_cap_usd": 0.1},
            {"per_call_cap_usd": "NaN"},
            {"per_call_cap_usd": "-1"},
            {"deadline": "later"},
            {"deadline": float("inf")},
            {"monotonic": 7},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ModelPortError):
                    BudgetedModelPort(fake, **kwargs)

    def test_malformed_content_makes_one_call_and_keeps_observation(self):
        cases = (
            "not json",
            '```json\n{"action": {}}\n```',
            '{"action": []}',
            '{"action": {}, "extra": 1}',
            '{"action": {}}{"action": {}}',
            '{"wrapper": {"action": {}}}',
        )
        for index, content in enumerate(cases):
            with self.subTest(case=index):
                fake = FakeClient(
                    completion(None, content=content, usage_value=usage())
                )
                port = BudgetedModelPort(fake, dispatch=True)
                self.assert_bridge_error("bridge_invalid_content", port, request())
                self.assertEqual(len(fake.calls), 1, "bridge retried a billed call")
                observation = port.observations[0]
                self.assertEqual(observation["status"], "bridge_invalid_content")
                self.assertEqual(observation["usage"]["input_tokens"], 10)
                self.assertGreater(observation["reservation_micro_usd"], 0)
                self.assertGreater(observation["estimated_cost_micro_usd"], 0)

        call = deepseek.ToolCall("call-1", "read_fixture", {}, None)
        tool_client = FakeClient(
            completion(None, tool_calls=(call,), usage_value=usage())
        )
        tool_port = BudgetedModelPort(tool_client, dispatch=True)
        self.assert_bridge_error(
            "bridge_unsupported_tool_calls", tool_port, request()
        )
        self.assertEqual(len(tool_client.calls), 1)
        self.assertEqual(
            tool_port.observations[0]["status"], "bridge_unsupported_tool_calls"
        )

    def test_sanitized_provider_budget_and_internal_failures(self):
        provider = FakeClient(
            error=deepseek.DeepSeekError(
                "http_error", status=503, outcome_unknown=True,
                reservation_usd=Decimal("0.001"),
            )
        )
        port = BudgetedModelPort(provider, dispatch=True)
        error = self.assert_bridge_error("http_error", port, request())
        self.assertEqual(error.status, 503)
        self.assertTrue(error.outcome_unknown)
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertEqual(provider.calls.__len__(), 1)
        self.assertEqual(port.observations[0]["status"], "http_error")

        internal = FakeClient(error=RuntimeError("synthetic-secret-marker"))
        internal_port = BudgetedModelPort(internal, dispatch=True)
        error = self.assert_bridge_error(
            "bridge_internal_error", internal_port, request()
        )
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertNotIn("synthetic-secret-marker", repr(error))
        self.assertNotIn("synthetic-secret-marker", repr(internal_port.observations))
        self.assertTrue(error.outcome_unknown)

        budget = FakeClient(error=BudgetError("budget_cap_exceeded"))
        budget_port = BudgetedModelPort(budget, dispatch=True)
        error = self.assert_bridge_error(
            "budget_cap_exceeded", budget_port, request()
        )
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertEqual(len(budget.calls), 1)

    def test_deadline_floors_positive_timeout(self):
        fake = FakeClient(completion({"name": "read", "filename": "TASK.md"}))
        port = BudgetedModelPort(
            fake, dispatch=True, deadline=10.0, monotonic=lambda: 7.9
        )
        port(request())
        self.assertEqual(fake.calls[0]["timeout_seconds"], 2)
        self.assertEqual(fake.calls[0]["max_response_bytes"], 131072)

        short = FakeClient(completion({"name": "read", "filename": "TASK.md"}))
        one_second = BudgetedModelPort(
            short, dispatch=True, deadline=10.0, monotonic=lambda: 9.0
        )
        one_second(request())
        self.assertEqual(short.calls[0]["timeout_seconds"], 1)

    def test_wrapper_fake_transport_composition_and_aggregate_refusal(self):
        project = self.root / "fake-project"
        project.mkdir()
        (project / ".env.local").write_bytes(
            ("DEEPSEEK_API_KEY=" + KEY + "\n").encode("ascii")
        )
        ledger_path = project / ".coordination" / "provider-budget.sqlite3"
        observed = {"calls": 0, "pending": [], "hashes": []}
        payload = {
            "model": "deepseek-returned-fixture",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "action": {"name": "read", "filename": "TASK.md"}
                    }),
                },
            }],
            "usage": {
                "prompt_tokens": 5,
                "prompt_cache_hit_tokens": 1,
                "prompt_cache_miss_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 7,
            },
        }

        def transport(body, api_key, *, timeout_seconds, max_response_bytes):
            observed["calls"] += 1
            connection = sqlite3.connect(ledger_path)
            try:
                rows = connection.execute(
                    "SELECT operation_id, amount_micro, status FROM operations"
                    " ORDER BY created_at, operation_id"
                ).fetchall()
            finally:
                connection.close()
            observed["pending"].append([tuple(row) for row in rows])
            self.assertTrue(api_key == KEY, "wrong synthetic key reached transport")
            self.assertIn(b"json-action-0.1", body)
            return deepseek.HttpResponse(200, json.dumps(payload).encode("utf-8"))

        wrapper = BudgetedDeepSeekClient(
            project, campaign_id="bridge-campaign", cap_usd="0.01",
            environ={}, transport=transport,
        )
        port = BudgetedModelPort(wrapper, dispatch=True, max_tokens=64)
        result = port(request("bridge:call:1"))
        self.assertEqual(result["action"]["name"], "read")
        self.assertEqual(observed["calls"], 1)
        self.assertEqual(len(observed["pending"]), 1)
        self.assertEqual(observed["pending"][0][0][2], "pending")
        snapshot = wrapper.snapshot()
        self.assertEqual(snapshot["operations"][0]["status"], "success")
        self.assertEqual(
            snapshot["reserved_microdollars"],
            port.observations[0]["reservation_micro_usd"],
        )

        # Aggregate refusal: a ledger cap exactly equal to one computed envelope
        # admits the first reservation and refuses the second before transport.
        probe = BudgetedModelPort(
            FakeClient(completion({"name": "read", "filename": "TASK.md"})),
            dispatch=True, max_tokens=64,
        )
        probe(request("probe:call:1"))
        envelope_micro = probe.observations[0]["reservation_micro_usd"]
        cap = str(Decimal(envelope_micro).scaleb(-6))
        project_two = self.root / "aggregate-project"
        project_two.mkdir()
        (project_two / ".env.local").write_bytes(
            ("DEEPSEEK_API_KEY=" + KEY + "\n").encode("ascii")
        )
        aggregate_calls = []

        def aggregate_transport(body, api_key, *, timeout_seconds, max_response_bytes):
            aggregate_calls.append(True)
            return deepseek.HttpResponse(200, json.dumps(payload).encode("utf-8"))

        aggregate_wrapper = BudgetedDeepSeekClient(
            project_two, campaign_id="bridge-campaign", cap_usd=cap,
            environ={}, transport=aggregate_transport,
        )
        aggregate_port = BudgetedModelPort(
            aggregate_wrapper, dispatch=True, max_tokens=64
        )
        aggregate_port(request("aggregate:call:1"))
        self.assertEqual(len(aggregate_calls), 1)
        self.assert_bridge_error(
            "budget_cap_exceeded", aggregate_port, request("aggregate:call:2")
        )
        self.assertEqual(len(aggregate_calls), 1, "refused call reached transport")
        self.assertEqual(
            aggregate_wrapper.snapshot()["reserved_microdollars"], envelope_micro
        )

    def test_request_validation_and_protocol_context_separation(self):
        fake = FakeClient(completion({"name": "read", "filename": "TASK.md"}))
        port = BudgetedModelPort(fake, dispatch=True)
        with self.assertRaises(ModelPortError):
            port({"episode_id": "ep"})
        self.assertEqual(fake.calls, [])
        bad_role = request(role="X1")
        self.assert_bridge_error("bridge_invalid_request", port, bad_role)
        self.assertEqual(fake.calls, [])
        port(request(allowed_actions=["read"]))
        instruction = fake.calls[0]["messages"][0]["content"]
        for forbidden in ("AGENTS.md", ".coordination", ".env.local",
                          "hidden grader", "other role transcript"):
            self.assertNotIn(forbidden, instruction)
        self.assertIn("read", instruction)
        self.assertNotIn("'write'", instruction)
        self.assertNotIn("'delegate'", instruction)


    def test_deadline_rechecked_after_genuine_preparation(self):
        state = {"now": 0.0}
        real_serializer = provider_module._request_body

        def crossing_serializer(messages, tools, max_tokens):
            body = real_serializer(messages, tools, max_tokens)
            state["now"] = 1.2
            return body

        fake = FakeClient(completion({"name": "read", "filename": "TASK.md"}))
        with mock.patch.object(
            provider_module, "_request_body", side_effect=crossing_serializer
        ):
            port = BudgetedModelPort(
                fake, dispatch=True, deadline=1.1,
                monotonic=lambda: state["now"],
            )
            self.assert_bridge_error(
                "bridge_deadline_expired", port, request()
            )
        self.assertEqual(fake.calls, [], "client called after deadline crossing")
        observation = port.observations[0]
        self.assertEqual(observation["status"], "bridge_deadline_expired")
        self.assertGreater(observation["reservation_micro_usd"], 0)

    def test_strict_envelope_rejects_duplicate_keys_and_nonjson_constants(self):
        cases = (
            ('{"action":{"name":"check"},"action":{"name":"finish",'
             '"claimed_status":"complete","summary":"x",'
             '"evidence_ids":[],"remaining":[]}}', "duplicate_top_level"),
            ('{"action":{"name":"check","name":"finish"}}', "duplicate_inner"),
            ('{"action":{"name":"check","extra":NaN}}', "nan_constant"),
            ('{"action":{"name":"check","extra":Infinity}}', "infinity_constant"),
        )
        for content, label in cases:
            with self.subTest(label=label):
                fake = FakeClient(
                    completion(None, content=content, usage_value=usage())
                )
                port = BudgetedModelPort(fake, dispatch=True)
                self.assert_bridge_error(
                    "bridge_invalid_content", port, request()
                )
                self.assertEqual(len(fake.calls), 1)
                observation = port.observations[0]
                self.assertEqual(
                    observation["status"], "bridge_invalid_content"
                )
                self.assertEqual(observation["usage"]["input_tokens"], 10)
                self.assertGreater(observation["reservation_micro_usd"], 0)

    def test_wellformed_wrong_role_and_missing_field_actions_pass_through(self):
        actions = (
            {"name": "write", "filename": "processor.py"},
            {"name": "read"},
            {"name": "finish", "claimed_status": "not-an-enum",
             "summary": "x", "evidence_ids": [], "remaining": []},
        )
        for action in actions:
            with self.subTest(name=action.get("name")):
                fake = FakeClient(completion(action))
                port = BudgetedModelPort(fake, dispatch=True)
                result = port(request())
                self.assertEqual(result["action"], action)
                self.assertEqual(port.observations[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
