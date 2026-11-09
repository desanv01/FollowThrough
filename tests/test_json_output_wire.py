"""Synthetic transport checks for JSON-output wiring; no provider access."""

from decimal import Decimal, ROUND_CEILING
import json
import shutil
from pathlib import Path
import tempfile
import unittest
import uuid

from adapters import deepseek
from adapters.budgeted_deepseek import BudgetedDeepSeekClient
from episode.provider import BudgetedModelPort, ModelPortError, _protocol_instruction


def response(content):
    return deepseek.HttpResponse(200, json.dumps({
        "model": "synthetic-json-wire",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": content,
        }}],
    }).encode("utf-8"))


def request(operation="json-wire:1"):
    return {
        "episode_id": "json-wire", "operation_id": operation, "role": "O1",
        "invocation_index": 1,
        "messages": [{"role": "user", "content": "Synthetic role context."}],
        "allowed_actions": ["read", "check", "finish"],
    }


class JsonOutputWireTest(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "tmp"
        scratch.mkdir(exist_ok=True)
        self.root = scratch / ("json-wire-" + uuid.uuid4().hex[:12])
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)

    def temporary_directory(self):
        return tempfile.TemporaryDirectory(dir=self.root)

    def make_client(self, root, transport):
        return BudgetedDeepSeekClient(
            root, campaign_id="synthetic-json-wire", environ={
                "DEEPSEEK_API_KEY": "synthetic-json-wire-key",
            }, transport=transport,
        )

    def test_real_wrapper_json_body_reservation_before_sole_transport(self):
        with self.temporary_directory() as root:
            seen = []

            def transport(body, _key, **_kwargs):
                seen.append(body)
                payload = json.loads(body)
                self.assertEqual(payload["response_format"], {"type": "json_object"})
                self.assertEqual(payload["thinking"], {"type": "enabled"})
                self.assertEqual(body, deepseek._request_body(
                    payload["messages"], None, 64, json_output=True,
                ))
                expected = int((deepseek._token_cost(
                    0, 2 * len(body) + deepseek._PROMPT_OVERHEAD_TOKENS, 64,
                ) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))
                snapshot = client.snapshot()
                self.assertEqual(snapshot["reserved_microdollars"], expected)
                self.assertEqual(len(snapshot["operations"]), 1)
                self.assertEqual(snapshot["operations"][0]["status"], "pending")
                return response('{"action":{"name":"read","filename":"TASK.md"}}')

            client = self.make_client(Path(root), transport)
            port = BudgetedModelPort(client, dispatch=True, max_tokens=64)
            self.assertEqual(port(request())["action"], {
                "name": "read", "filename": "TASK.md",
            })
            self.assertEqual(len(seen), 1)
            self.assertEqual(client.snapshot()["operations"][0]["status"], "success")
            self.assertEqual(client.snapshot()["reserved_microdollars"],
                             port.observations[0]["reservation_micro_usd"])

    def test_default_adapter_and_wrapper_omit_response_format(self):
        bodies = []

        def transport(body, _key, **_kwargs):
            bodies.append(body)
            return response("ordinary plain text")

        messages = [{"role": "user", "content": "Synthetic default request."}]
        deepseek.DeepSeekClient("synthetic-key", transport=transport).complete(
            messages, max_cost_usd="0.10",
        )
        with self.temporary_directory() as root:
            self.make_client(root, transport).complete(
                "default:1", messages, max_cost_usd="0.10", dispatch=True,
            )
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertNotIn("response_format", json.loads(bodies[0]))

    def test_malformed_output_stays_refused_without_retry(self):
        for content in (
            "   ", '```json\n{"action":{}}\n```',
            '{"action":{},"action":{}}', '{"action":{"value":NaN}}',
            '{"action":{"value":1e999}}', '{"action":{},"extra":true}',
        ):
            with self.subTest(content=content), self.temporary_directory() as root:
                calls = []

                def transport(body, _key, **_kwargs):
                    calls.append(body)
                    return response(content)

                client = self.make_client(root, transport)
                port = BudgetedModelPort(client, dispatch=True, max_tokens=64)
                with self.assertRaises(ModelPortError) as raised:
                    port(request())
                self.assertEqual(raised.exception.code, "bridge_invalid_content")
                self.assertEqual(len(calls), 1)

    def test_valid_read_example_for_all_actual_roles(self):
        example = '{"action":{"name":"read","filename":"TASK.md"}}'
        for role in ("O1", "W1", "R1"):
            instruction = _protocol_instruction(role, ["read", "check"])
            self.assertIn(example, instruction)
            self.assertEqual(json.loads(example)["action"]["name"], "read")


if __name__ == "__main__":
    unittest.main()
