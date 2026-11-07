from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal, localcontext
import io
import json
from threading import Event
import unittest
from unittest.mock import Mock, patch

from adapters import deepseek


KEY = "mock-key-private"
MESSAGES = [{"role": "user", "content": "Read the fixture."}]
TOOLS = [{"type": "function", "function": {"name": "read_fixture", "parameters": {"type": "object"}}}]
BUDGET = "0.01"


def tool_call(call_id, arguments):
    return {"type": "function", "id": call_id, "function": {"name": "read_fixture", "arguments": arguments}}


def response(message=None, *, finish="stop", usage=None, model="deepseek-returned-fixture"):
    payload = {
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "message": message or {
            "role": "assistant", "content": "READY", "reasoning_content": "internal fixture",
        }}],
    }
    if usage is not None:
        payload["usage"] = usage
    return deepseek.HttpResponse(200, json.dumps(payload).encode("utf-8"))


class DeepSeekAdapterTest(unittest.TestCase):
    def assert_error(self, code, method, *args, **kwargs):
        with self.assertRaises(deepseek.DeepSeekError) as raised:
            method(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def test_tool_history_preserves_protocol_fields_and_order(self):
        tool_message = {
            "role": "assistant", "content": None, "reasoning_content": "  原始 thought\r\n",
            "tool_calls": [tool_call("second", '{ "path": "a" }'), tool_call("first", '{"path":"b"}')],
        }
        transport = Mock(side_effect=[response(tool_message, finish="tool_calls"), response()])
        client = deepseek.DeepSeekClient(KEY, transport=transport)
        first = client.complete(MESSAGES, tools=TOOLS, max_cost_usd=BUDGET)
        self.assertEqual(first.assistant_message, tool_message)
        self.assertEqual(first.tool_calls[0].arguments, {"path": "a"})
        self.assertEqual(first.requested_model, "deepseek-flash")
        self.assertEqual(first.returned_model, "deepseek-returned-fixture")
        history = deepseek.append_tool_results(MESSAGES, first.assistant_message, {"first": "B", "second": "A"})
        client.complete(history, tools=TOOLS, max_cost_usd=BUDGET)
        self.assertEqual(len(MESSAGES), 1)
        body, key = transport.call_args.args
        self.assertEqual(key, KEY)
        self.assertEqual(json.loads(body), {
            "model": "deepseek-flash", "max_tokens": 64, "messages": MESSAGES + [
                tool_message,
                {"role": "tool", "tool_call_id": "second", "content": "A"},
                {"role": "tool", "tool_call_id": "first", "content": "B"},
            ],
            "tools": TOOLS, "stream": False, "thinking": {"type": "enabled"}, "reasoning_effort": "high",
        })

    def test_invalid_arguments_can_be_answered_with_a_tool_error(self):
        malformed = ['{"secret":"' + KEY, '[1]', '{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}']
        for raw in malformed:
            with self.subTest(raw=raw):
                message = {
                    "role": "assistant", "content": None, "reasoning_content": "private",
                    "tool_calls": [tool_call("call_1", raw)],
                }
                client = deepseek.DeepSeekClient(KEY, transport=Mock(return_value=response(message, finish="tool_calls")))
                result = client.complete(MESSAGES, max_cost_usd=BUDGET)
                call = result.tool_calls[0]
                self.assertEqual(call.arguments_error, "invalid_tool_arguments")
                self.assertIsNone(call.arguments)
                self.assertNotIn(KEY, repr(result))
                self.assertNotIn("private", repr(result))
                history = deepseek.append_tool_results(MESSAGES, result.assistant_message, {call.id: "invalid arguments"})
                self.assertEqual(history[1]["tool_calls"][0]["function"]["arguments"], raw)

    def test_history_rejects_missing_reasoning_and_mismatched_result_ids(self):
        transport = Mock()
        client = deepseek.DeepSeekClient(KEY, transport=transport)
        message = {"role": "assistant", "tool_calls": [tool_call("call_1", "{}")], "content": None}
        result = {"role": "tool", "tool_call_id": "call_1", "content": "value"}
        self.assert_error("invalid_history", client.complete, MESSAGES + [message, result], max_cost_usd=BUDGET)
        message["reasoning_content"] = "think"
        for history in (MESSAGES + [message], MESSAGES + [result], MESSAGES + [message, result, result]):
            self.assert_error("invalid_history", client.complete, history, max_cost_usd=BUDGET)
        self.assert_error("invalid_tool_results", deepseek.append_tool_results, MESSAGES, message, {"wrong": "value"})
        transport.assert_not_called()

    def test_usage_preserves_raw_and_charges_each_bucket_once(self):
        raw = {
            "prompt_tokens": 10, "prompt_cache_hit_tokens": 4, "completion_tokens": 9, "total_tokens": 19,
            "prompt_tokens_details": {"cached_tokens": 4}, "completion_tokens_details": {"reasoning_tokens": 8},
        }
        client = deepseek.DeepSeekClient(KEY, transport=Mock(return_value=response(usage=raw)))
        with localcontext() as context:
            context.prec = 1
            result = client.complete(MESSAGES, max_cost_usd=BUDGET)
            self.assertEqual(result.usage.estimated_cost_usd, Decimal("0.000012624"))
        self.assertEqual(result.usage.raw, raw)
        self.assertEqual(result.usage.cache_hit_input_tokens, 4)
        self.assertEqual(result.usage.uncached_input_tokens, 6)
        self.assertEqual(result.usage.output_tokens, 9)
        self.assertEqual(result.usage.reasoning_tokens, 8)

    def test_unknown_usage_stays_unknown(self):
        for raw in (None, {}, {"prompt_tokens": 12, "completion_tokens": 7}):
            client = deepseek.DeepSeekClient(KEY, transport=Mock(return_value=response(usage=raw)))
            usage = client.complete(MESSAGES, max_cost_usd=BUDGET).usage
            self.assertEqual(usage.raw, raw)
            self.assertIsNone(usage.cache_hit_input_tokens)
            self.assertIsNone(usage.uncached_input_tokens)
            self.assertIsNone(usage.estimated_cost_usd)

    def test_rejects_inconsistent_or_excess_usage(self):
        malformed = (
            "bad", {"prompt_tokens": True}, {"completion_tokens": -1},
            {"prompt_tokens": 4, "prompt_cache_miss_tokens": 5},
            {"prompt_tokens": 4, "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 1},
            {"prompt_tokens_details": {"cached_tokens": 5}, "prompt_cache_hit_tokens": 2},
            {"total_tokens": 2, "prompt_cache_hit_tokens": 3},
            {"completion_tokens": 2, "completion_tokens_details": {"reasoning_tokens": 3}},
        )
        for raw in malformed:
            client = deepseek.DeepSeekClient(KEY, transport=Mock(return_value=response(usage=raw)))
            error = self.assert_error("malformed_usage", client.complete, MESSAGES, max_cost_usd=BUDGET)
            self.assertIsNotNone(error.reservation_usd)
        for raw in ({"prompt_tokens": 100_000}, {"completion_tokens": 65},
                    {"prompt_tokens": 10, "total_tokens": 75},
                    {"completion_tokens_details": {"reasoning_tokens": 65}}):
            client = deepseek.DeepSeekClient(KEY, transport=Mock(return_value=response(usage=raw)))
            error = self.assert_error("usage_exceeds_reservation", client.complete, MESSAGES, max_cost_usd=BUDGET)
            self.assertEqual(error.raw_usage, raw)

    def test_bad_response_bodies_and_finish_reason_are_structured_errors(self):
        cases = (
            ("invalid_response_json", deepseek.HttpResponse(200, b'{"unfinished":')),
            ("invalid_response_json", deepseek.HttpResponse(200, b'\xff')),
            ("invalid_response_json", deepseek.HttpResponse(200, b'{"x":1,"x":2}')),
            ("malformed_response", deepseek.HttpResponse(200, b'{"choices":[]}')),
            ("completion_truncated", response(finish="length")),
            ("response_truncated", deepseek.HttpResponse(200, b"x" * (deepseek.MAX_RESPONSE_BYTES + 1))),
            ("malformed_response", response({"role": "assistant", "tool_calls": [tool_call("id", "{}")]}, finish="tool_calls")),
        )
        for code, value in cases:
            client = deepseek.DeepSeekClient(KEY, transport=Mock(return_value=value))
            error = self.assert_error(code, client.complete, MESSAGES, max_cost_usd=BUDGET)
            self.assertTrue(error.outcome_unknown)

    def test_invalid_or_unaffordable_requests_do_not_dispatch(self):
        transport = Mock()
        client = deepseek.DeepSeekClient(KEY, transport=transport)
        for value in (0, -1, True, "NaN", "-Infinity", float("inf"), "bad", None):
            self.assert_error("invalid_budget", client.complete, MESSAGES, max_cost_usd=value)
        for key, value, code in (
            ("max_tokens", 0, "invalid_output_limit"), ("max_tokens", 4097, "invalid_output_limit"),
            ("timeout_seconds", 31, "invalid_timeout"), ("timeout_seconds", True, "invalid_timeout"),
            ("max_response_bytes", 0, "invalid_response_limit"),
            ("max_response_bytes", deepseek.MAX_RESPONSE_BYTES + 1, "invalid_response_limit"),
        ):
            self.assert_error(code, client.complete, MESSAGES, max_cost_usd=BUDGET, **{key: value})
        error = self.assert_error("insufficient_budget", client.complete, MESSAGES, max_cost_usd="0.000001")
        self.assertGreater(error.reservation_usd, Decimal("0.000001"))
        self.assert_error("request_too_large", client.complete,
                          [{"role": "user", "content": "x" * deepseek.MAX_REQUEST_BYTES}], max_cost_usd="1")
        self.assert_error("invalid_request", client.complete,
                          [{"role": "user", "content": "x", "extra": {1: "a", "1": "b"}}], max_cost_usd=BUDGET)
        self.assert_error("missing_auth", deepseek.DeepSeekClient(None, transport=transport).complete,
                          MESSAGES, max_cost_usd=BUDGET)
        self.assert_error("invalid_auth", deepseek.DeepSeekClient(KEY + "\n", transport=transport).complete,
                          MESSAGES, max_cost_usd=BUDGET)
        transport.assert_not_called()

    def test_timeout_and_transport_failure_redact_and_do_not_retry(self):
        for code, failure in (("transport_timeout", TimeoutError(KEY)), ("transport_failure", RuntimeError(KEY))):
            transport = Mock(side_effect=failure)
            client = deepseek.DeepSeekClient(KEY, transport=transport)
            error = self.assert_error(code, client.complete, MESSAGES, max_cost_usd=BUDGET,
                                      timeout_seconds=4, max_response_bytes=200)
            transport.assert_called_once()
            self.assertEqual(transport.call_args.kwargs, {"timeout_seconds": 4, "max_response_bytes": 200})
            self.assertNotIn(KEY, repr(error))
            self.assertIsNone(error.__context__)
            self.assertTrue(error.outcome_unknown)
            self.assertIsNotNone(error.reservation_usd)

    def test_http_error_body_is_not_used_in_diagnostics(self):
        client = deepseek.DeepSeekClient(KEY, transport=Mock(return_value=deepseek.HttpResponse(401, KEY.encode())))
        error = self.assert_error("http_error", client.complete, MESSAGES, max_cost_usd=BUDGET)
        self.assertEqual(error.status, 401)
        self.assertFalse(error.outcome_unknown)
        self.assertNotIn(KEY, repr(error))

    def test_gateway_timeout_retains_unknown_outcome_and_reservation(self):
        transport = Mock(return_value=deepseek.HttpResponse(504, b""))
        client = deepseek.DeepSeekClient(KEY, transport=transport)

        error = self.assert_error("http_error", client.complete, MESSAGES, max_cost_usd=BUDGET)

        self.assertEqual(error.status, 504)
        self.assertTrue(error.outcome_unknown)
        self.assertGreater(error.reservation_usd, Decimal(0))
        transport.assert_called_once()

    def test_default_transport_disables_stdlib_debug_with_credentials(self):
        payload = response().body
        fake_socket = Mock()
        head = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(payload)
        fake_socket.makefile.return_value = io.BytesIO(head + payload)

        def connect(connection):
            connection.sock = fake_socket

        connection_class = deepseek.http.client.HTTPSConnection
        with (
            patch.object(connection_class, "connect", connect),
            patch.object(connection_class, "debuglevel", 1),
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            result = deepseek.DeepSeekClient(KEY).complete(MESSAGES, max_cost_usd=BUDGET)

        self.assertEqual(result.finish_reason, "stop")
        authorization = f"Authorization: Bearer {KEY}".encode("ascii")
        self.assertTrue(any(authorization in call.args[0] for call in fake_socket.sendall.call_args_list))
        self.assertFalse(KEY in stdout.getvalue() or KEY in stderr.getvalue(), "HTTP diagnostics exposed the credential")

    @patch.object(deepseek.http.client, "HTTPSConnection")
    def test_default_transport_uses_fixed_tls_host_and_no_redirect(self, connection_factory):
        connection = connection_factory.return_value
        provider_response = connection.getresponse.return_value.__enter__.return_value
        provider_response.status = 302
        error = self.assert_error("http_error", deepseek.DeepSeekClient(KEY).complete,
                                  MESSAGES, max_cost_usd=BUDGET)
        self.assertEqual(error.status, 302)
        connection_factory.assert_called_once_with("api.deepseek.com", timeout=15)
        connection.connect.assert_called_once_with()
        connection.request.assert_called_once()
        self.assertEqual(connection.request.call_args.args, ("POST", "/chat/completions"))
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Authorization"], f"Bearer {KEY}")
        provider_response.read.assert_not_called()
        connection.close.assert_called_once_with()

    @patch.object(deepseek.http.client, "HTTPSConnection")
    def test_default_transport_caps_read_and_rejects_partial_content_length(self, connection_factory):
        provider_response = connection_factory.return_value.getresponse.return_value.__enter__.return_value
        provider_response.status = 200
        provider_response.length = 20
        provider_response.read.return_value = b"{}"
        self.assert_error("response_truncated", deepseek.DeepSeekClient(KEY).complete,
                          MESSAGES, max_cost_usd=BUDGET, max_response_bytes=32)
        provider_response.read.assert_called_once_with(33)

    @patch.object(deepseek.http.client, "HTTPSConnection")
    def test_default_deadline_prevents_post_after_late_connection(self, connection_factory):
        release_connection = Event()
        settled = Event()
        connection = connection_factory.return_value
        connection.connect.side_effect = release_connection.wait
        connection.close.side_effect = settled.set
        try:
            error = self.assert_error("transport_timeout", deepseek.DeepSeekClient(KEY).complete,
                                      MESSAGES, max_cost_usd=BUDGET, timeout_seconds=1)
            self.assertTrue(error.outcome_unknown)
        finally:
            release_connection.set()
        self.assertTrue(settled.wait(2))
        connection.connect.assert_called_once_with()
        connection.request.assert_not_called()

    def test_cli_gates_credentials_and_dispatch(self):
        transport, env = Mock(), Mock()
        with redirect_stderr(io.StringIO()):
            for argv in ([], ["--max-cost-usd", BUDGET], ["--live"], ["--live", "--max-cost-usd", "NaN"]):
                self.assertEqual(deepseek.main(argv, environ=env, transport=transport), 2)
            with self.assertRaises(SystemExit) as short_flag:
                deepseek.main(["--liv", "--max-cost-usd", BUDGET], environ=env, transport=transport)
            self.assertEqual(short_flag.exception.code, 2)
        env.get.assert_not_called()
        transport.assert_not_called()
        with redirect_stderr(io.StringIO()) as output:
            self.assertEqual(deepseek.main(["--live", "--max-cost-usd", BUDGET], environ={}, transport=transport), 1)
        self.assertEqual(json.loads(output.getvalue())["error"], "missing_auth")
        transport.assert_not_called()

    def test_fake_cli_metadata_does_not_print_credential_or_content(self):
        transport = Mock(return_value=response(model=f"fake-{KEY}"))
        with redirect_stdout(io.StringIO()) as output:
            code = deepseek.main(["--live", "--max-cost-usd", BUDGET],
                                environ={"DEEPSEEK_API_KEY": KEY}, transport=transport)
        self.assertEqual(code, 0)
        transport.assert_called_once()
        self.assertNotIn(KEY, output.getvalue())
        self.assertNotIn("internal fixture", output.getvalue())
        self.assertNotIn("READY", output.getvalue())
        self.assertIsNone(json.loads(output.getvalue())["estimated_usage_cost_usd"])


if __name__ == "__main__":
    unittest.main()
