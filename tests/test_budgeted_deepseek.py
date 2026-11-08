"""Offline tests for the guarded credential + durable budget composition.

Every transport is an injected fake; no test can reach the network or the real
project credential file.  Synthetic keys and fixture text never appear in
assertion messages.
"""

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import traceback
import unittest
from unittest.mock import patch
import uuid

from adapters import budget as budget_module
from adapters import credentials, deepseek
from adapters.budget import BudgetError
from adapters.budgeted_deepseek import BudgetedDeepSeekClient, BudgetedDeepSeekError


OVERLAY_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = OVERLAY_ROOT / "tmp"
CAMPAIGN = "synthetic-campaign"
KEY = "synthetic-wrapper-key-DDDD3333"
MESSAGES = [{"role": "user", "content": "offline synthetic fixture request"}]
RESPONSE_MARKER = "offline synthetic fixture reply"
TOOLS = [
    {
        "type": "function",
        "function": {"name": "read_fixture", "parameters": {"type": "object"}},
    }
]
USAGE = {
    "prompt_tokens": 10,
    "prompt_cache_hit_tokens": 4,
    "completion_tokens": 9,
    "total_tokens": 19,
    "prompt_tokens_details": {"cached_tokens": 4},
    "completion_tokens_details": {"reasoning_tokens": 8},
}
ESTIMATED = Decimal("0.000012624")
RAW_MARKER = "synthetic-raw-marker-FFFF5555"
INCOMPATIBLE_TRIGGER = (
    "CREATE TRIGGER incompatible_operation_policy"
    " BEFORE INSERT ON operations BEGIN SELECT RAISE(IGNORE); END;"
)


def response(*, usage=USAGE, message=None):
    payload = {
        "model": "deepseek-returned-fixture",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": message or {
                "role": "assistant",
                "content": RESPONSE_MARKER,
                "reasoning_content": "synthetic internal reasoning",
            },
        }],
    }
    if usage is not None:
        payload["usage"] = usage
    return deepseek.HttpResponse(200, json.dumps(payload).encode("utf-8"))


class FakeTransport:
    def __init__(self, *, result=None, failure=None):
        self.result = result
        self.failure = failure
        self.calls = []

    def __call__(self, body, api_key, *, timeout_seconds, max_response_bytes):
        self.calls.append({
            "body": body,
            "key": api_key,
            "timeout_seconds": timeout_seconds,
            "max_response_bytes": max_response_bytes,
        })
        if self.failure is not None:
            raise self.failure
        return self.result


class BudgetedDeepSeekClientTest(unittest.TestCase):
    def setUp(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = SCRATCH_ROOT / ("budgeted-" + uuid.uuid4().hex[:12])
        os.makedirs(self.root, exist_ok=False)
        self.addCleanup(shutil.rmtree, self.root, True)

    def make_project(self, name):
        project = self.root / name
        project.mkdir(parents=True, exist_ok=True)
        (project / ".env.local").write_bytes(
            f"DEEPSEEK_API_KEY={KEY}\n".encode("ascii")
        )
        return project

    def make_client(self, name="project", *, cap="20", transport=None):
        project = self.make_project(name)
        client = BudgetedDeepSeekClient(
            project,
            campaign_id=CAMPAIGN,
            cap_usd=cap,
            environ={},
            transport=transport,
        )
        return client, project

    def assert_error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(BudgetedDeepSeekError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def assert_budget_error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(BudgetError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def test_success_reserves_the_envelope_and_records_the_estimate(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("success", transport=transport)
        completion = client.complete(
            "op-success", MESSAGES, max_cost_usd="1", dispatch=True
        )
        self.assertEqual(completion.finish_reason, "stop")
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertTrue(call["key"] == KEY, "transport received the wrong synthetic key")
        body = json.loads(call["body"])
        self.assertEqual(body["messages"], MESSAGES)
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(call["timeout_seconds"], 15)
        self.assertEqual(call["max_response_bytes"], 131072)
        self.assertTrue(completion.usage.estimated_cost_usd == ESTIMATED)

        snapshot = client.snapshot()
        self.assertEqual(snapshot["campaign_id"], CAMPAIGN)
        self.assertEqual(snapshot["cap_microdollars"], 20_000_000)
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        self.assertEqual(snapshot["remaining_microdollars"], 19_000_000)
        self.assertEqual(len(snapshot["operations"]), 1)
        entry = snapshot["operations"][0]
        self.assertEqual(entry["operation_id"], "op-success")
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["amount_microdollars"], 1_000_000)
        self.assertEqual(entry["estimated_cost_microdollars"], 13)
        self.assertEqual(entry["estimated_cost_usd"], "0.000013")

    def test_dispatch_must_be_exactly_true(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("dispatch", transport=transport)
        for index, value in enumerate((False, None, 1, "true", 0.0, [])):
            with self.assertRaises(BudgetedDeepSeekError) as raised:
                client.complete(
                    f"op-denied-{index}", MESSAGES, max_cost_usd="1", dispatch=value
                )
            self.assertEqual(raised.exception.code, "dispatch_not_authorized")
            self.assertFalse(raised.exception.outcome_unknown)
        with self.assertRaises(BudgetedDeepSeekError) as default_denied:
            client.complete("op-default", MESSAGES, max_cost_usd="1")
        self.assertEqual(default_denied.exception.code, "dispatch_not_authorized")
        self.assertEqual(len(transport.calls), 0)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 0)
        self.assertEqual(snapshot["operations"], [])

    def test_exhausted_campaign_cap_blocks_before_transport(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("exhausted", cap="1.5", transport=transport)
        client.complete("op-first", MESSAGES, max_cost_usd="1", dispatch=True)
        error = self.assert_budget_error(
            "budget_cap_exceeded", client.complete,
            "op-second", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(error.code, "budget_cap_exceeded")
        self.assertEqual(len(transport.calls), 1)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        self.assertEqual(len(snapshot["operations"]), 1)

    def test_invalid_envelope_never_dispatches(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("invalid-envelope", transport=transport)
        for index, value in enumerate((0.5, "NaN", "0", -1, True, 1, "1_0", None)):
            with self.assertRaises(BudgetError) as raised:
                client.complete(
                    f"op-invalid-{index}", MESSAGES, max_cost_usd=value, dispatch=True
                )
            self.assertEqual(raised.exception.code, "invalid_amount")
        self.assertEqual(len(transport.calls), 0)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 0)
        self.assertEqual(snapshot["operations"], [])

    def test_storage_failure_before_transport_fails_closed(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("storage-corrupt", transport=transport)
        Path(client._ledger.ledger_path).write_bytes(b"garbage instead of sqlite" * 10)
        self.assert_budget_error(
            "budget_storage_corrupt", client.complete,
            "op-corrupt", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(len(transport.calls), 0)

        transport = FakeTransport(result=response())
        client, _ = self.make_client("storage-directory", transport=transport)
        database = Path(client._ledger.ledger_path)
        database.unlink()
        database.mkdir()
        self.assert_budget_error(
            "budget_storage_unavailable", client.complete,
            "op-directory", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(len(transport.calls), 0)

    def test_timeout_records_uncertainty_and_retains_funds(self):
        transport = FakeTransport(failure=TimeoutError("synthetic timeout"))
        client, _ = self.make_client("timeout", transport=transport)
        error = self.assert_error(
            "transport_timeout", client.complete,
            "op-timeout", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertTrue(error.outcome_unknown)
        self.assertEqual(len(transport.calls), 1)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["operations"][0]["status"], "uncertain")
        self.assertIsNone(snapshot["operations"][0]["estimated_cost_microdollars"])
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        self.assert_budget_error(
            "duplicate_operation", client.complete,
            "op-timeout", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(len(transport.calls), 1)

    def test_http_errors_preserve_status_and_outcome_class(self):
        cases = ((503, "uncertain", True), (401, "provider_error", False))
        for status, outcome, unknown in cases:
            with self.subTest(status=status):
                transport = FakeTransport(
                    result=deepseek.HttpResponse(status, b"")
                )
                client, _ = self.make_client(f"http-{status}", transport=transport)
                error = self.assert_error(
                    "http_error", client.complete,
                    f"op-http-{status}", MESSAGES, max_cost_usd="1", dispatch=True,
                )
                self.assertEqual(error.status, status)
                self.assertEqual(error.outcome_unknown, unknown)
                self.assertEqual(len(transport.calls), 1)
                snapshot = client.snapshot()
                self.assertEqual(snapshot["operations"][0]["status"], outcome)
                self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)

    def test_unknown_usage_is_a_success_without_an_estimate(self):
        transport = FakeTransport(result=response(usage=None))
        client, _ = self.make_client("unknown-usage", transport=transport)
        completion = client.complete(
            "op-unknown-usage", MESSAGES, max_cost_usd="1", dispatch=True
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertIsNone(completion.usage.estimated_cost_usd)
        entry = client.snapshot()["operations"][0]
        self.assertEqual(entry["status"], "success")
        self.assertIsNone(entry["estimated_cost_microdollars"])
        self.assertIsNone(entry["estimated_cost_usd"])

    def test_finalization_failure_after_transport_keeps_pending_and_funds(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("finalize", transport=transport)
        with patch.object(
            budget_module.CampaignLedger,
            "finish",
            side_effect=RuntimeError(RAW_MARKER),
        ):
            with self.assertRaises(BudgetedDeepSeekError) as raised:
                client.complete(
                    "op-finalize", MESSAGES, max_cost_usd="1", dispatch=True
                )
        error = raised.exception
        self.assertEqual(error.code, "result_not_recorded")
        self.assertTrue(error.outcome_unknown)
        self.assertIsNone(error.__context__, "finalization failure retained a raw context")
        self.assertIsNone(error.__cause__, "finalization failure retained a raw cause")
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertFalse(
            RAW_MARKER in rendered, "rendered finalization error retained a raw marker"
        )
        self.assertFalse(RAW_MARKER in repr(error))
        self.assertEqual(len(transport.calls), 1)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        self.assertEqual(snapshot["operations"][0]["status"], "pending")
        self.assertEqual(snapshot["operations"][0]["amount_microdollars"], 1_000_000)
        self.assertIsNone(snapshot["operations"][0]["estimated_cost_microdollars"])

    def test_duplicate_operation_ids_never_redispatch(self):
        transport = FakeTransport(result=response())
        client, project = self.make_client("duplicates", transport=transport)
        client.complete("op-dup-success", MESSAGES, max_cost_usd="1", dispatch=True)
        self.assert_budget_error(
            "duplicate_operation", client.complete,
            "op-dup-success", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(len(transport.calls), 1)

        failing = FakeTransport(failure=TimeoutError("synthetic timeout"))
        client_failure, project_failure = self.make_client(
            "duplicate-failure", transport=failing
        )
        self.assert_error(
            "transport_timeout", client_failure.complete,
            "op-dup-failure", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assert_budget_error(
            "duplicate_operation", client_failure.complete,
            "op-dup-failure", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(len(failing.calls), 1)

        restarted_transport = FakeTransport(result=response())
        restarted = BudgetedDeepSeekClient(
            project,
            campaign_id=CAMPAIGN,
            cap_usd="20",
            environ={},
            transport=restarted_transport,
        )
        self.assert_budget_error(
            "duplicate_operation", restarted.complete,
            "op-dup-success", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(len(restarted_transport.calls), 0)
        self.assertEqual(len(restarted.snapshot()["operations"]), 1)

    def test_reopened_pending_reservation_cannot_dispatch(self):
        client, project = self.make_client(
            "pending", transport=FakeTransport(result=response())
        )
        client._ledger.reserve("op-abandoned", "1")
        restarted_transport = FakeTransport(result=response())
        restarted = BudgetedDeepSeekClient(
            project,
            campaign_id=CAMPAIGN,
            cap_usd="20",
            environ={},
            transport=restarted_transport,
        )
        snapshot = restarted.snapshot()
        self.assertEqual(snapshot["operations"][0]["status"], "pending")
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        self.assert_budget_error(
            "duplicate_operation", restarted.complete,
            "op-abandoned", MESSAGES, max_cost_usd="1", dispatch=True,
        )
        self.assertEqual(len(restarted_transport.calls), 0)

    def test_unexpected_local_exception_is_fixed_and_uncertain(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("internal", transport=transport)
        with patch.object(
            client._client, "complete", side_effect=RuntimeError("synthetic local fault")
        ):
            error = self.assert_error(
                "internal_error", client.complete,
                "op-internal", MESSAGES, max_cost_usd="1", dispatch=True,
            )
        self.assertTrue(error.outcome_unknown)
        self.assertEqual(len(transport.calls), 0)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["operations"][0]["status"], "internal_error")
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)

    def test_request_arguments_and_envelope_are_unchanged(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("arguments", transport=transport)
        with patch.object(
            client._client, "complete", wraps=client._client.complete
        ) as adapter_complete:
            client.complete(
                "op-arguments", MESSAGES, max_cost_usd="1", tools=TOOLS,
                max_tokens=32, timeout_seconds=4, max_response_bytes=2048,
                dispatch=True,
            )
        adapter_complete.assert_called_once_with(
            MESSAGES,
            max_cost_usd="1",
            tools=TOOLS,
            max_tokens=32,
            timeout_seconds=4,
            max_response_bytes=2048,
        )
        call = transport.calls[0]
        body = json.loads(call["body"])
        self.assertEqual(body["max_tokens"], 32)
        self.assertEqual(body["tools"], TOOLS)
        self.assertEqual(body["stream"], False)
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(call["timeout_seconds"], 4)
        self.assertEqual(call["max_response_bytes"], 2048)

    def test_constructor_requires_a_valid_synthetic_credential(self):
        project = self.root / "missing-key"
        project.mkdir()
        with self.assertRaises(credentials.CredentialError) as raised:
            BudgetedDeepSeekClient(
                project, campaign_id=CAMPAIGN, environ={}, transport=FakeTransport()
            )
        self.assertEqual(raised.exception.code, "credential_missing")
        self.assertFalse((project / ".coordination").exists())

        project = self.root / "invalid-key"
        project.mkdir()
        (project / ".env.local").write_text(
            "DEEPSEEK_API_KEY=invalid key with spaces\n", encoding="utf-8"
        )
        with self.assertRaises(credentials.CredentialError) as raised:
            BudgetedDeepSeekClient(
                project, campaign_id=CAMPAIGN, environ={}, transport=FakeTransport()
            )
        self.assertEqual(raised.exception.code, "credential_invalid")

    def test_composition_refuses_mismatched_ledger_metadata(self):
        client, project = self.make_client(
            "metadata", transport=FakeTransport(result=response())
        )
        with self.assertRaises(BudgetError) as raised:
            BudgetedDeepSeekClient(
                project, campaign_id="other-campaign", environ={},
                transport=FakeTransport(),
            )
        self.assertEqual(raised.exception.code, "budget_campaign_mismatch")
        with self.assertRaises(BudgetError) as raised:
            BudgetedDeepSeekClient(
                project, campaign_id=CAMPAIGN, cap_usd="19", environ={},
                transport=FakeTransport(),
            )
        self.assertEqual(raised.exception.code, "budget_cap_mismatch")

    def test_synthetic_key_and_payload_never_leak(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("secret", transport=transport)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            completion = client.complete(
                "op-secret", MESSAGES, max_cost_usd="1", dispatch=True
            )
        snapshot = client.snapshot()
        representations = (
            repr(client),
            repr(client._client),
            repr(client._ledger),
            repr(completion),
            repr(snapshot),
            json.dumps(snapshot),
            stdout.getvalue(),
            stderr.getvalue(),
        )
        for text in representations:
            self.assertFalse(KEY in text, "a client representation leaked the synthetic key")
            self.assertFalse(RESPONSE_MARKER in text, "a representation leaked a response body")
        self.assertFalse(MESSAGES[0]["content"] in repr(snapshot))

        database = Path(client._ledger.ledger_path).read_bytes()
        self.assertFalse(KEY.encode("ascii") in database, "SQLite bytes leaked the synthetic key")
        self.assertFalse(RESPONSE_MARKER.encode("ascii") in database, "SQLite bytes leaked a response body")
        self.assertFalse(MESSAGES[0]["content"].encode("ascii") in database, "SQLite bytes leaked a prompt")

        failure_transport = FakeTransport(
            result=deepseek.HttpResponse(401, KEY.encode("ascii") + b" synthetic body")
        )
        failing_client, _ = self.make_client("secret-error", transport=failure_transport)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(BudgetedDeepSeekError) as raised:
                failing_client.complete(
                    "op-secret-error", MESSAGES, max_cost_usd="1", dispatch=True
                )
        self.assertFalse(KEY in repr(raised.exception), "error representation leaked the synthetic key")
        self.assertFalse(KEY in stdout.getvalue() + stderr.getvalue(), "diagnostics leaked the synthetic key")
        self.assertTrue(raised.exception.status == 401)


    # -- r2 correction regressions (F2/F3/F4) -----------------------------

    def test_raw_provider_usage_is_not_reachable_through_error_chaining(self):
        payloads = (
            (
                "completion_truncated",
                {
                    "model": "deepseek-returned-fixture",
                    "choices": [{
                        "index": 0,
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": RESPONSE_MARKER},
                    }],
                    "usage": {"prompt_tokens": 5, "synthetic_marker": RAW_MARKER},
                },
            ),
            (
                "malformed_usage",
                {
                    "model": "deepseek-returned-fixture",
                    "choices": [{
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": RESPONSE_MARKER,
                            "reasoning_content": "synthetic reasoning",
                        },
                    }],
                    "usage": {"prompt_tokens": True, "synthetic_marker": RAW_MARKER},
                },
            ),
        )
        for expected_code, payload in payloads:
            with self.subTest(code=expected_code):
                transport = FakeTransport(
                    result=deepseek.HttpResponse(
                        200, json.dumps(payload).encode("utf-8")
                    )
                )
                client, _ = self.make_client(
                    f"raw-{expected_code}", transport=transport
                )
                with self.assertRaises(BudgetedDeepSeekError) as raised:
                    client.complete(
                        f"op-{expected_code}", MESSAGES,
                        max_cost_usd="1", dispatch=True,
                    )
                error = raised.exception
                self.assertEqual(error.code, expected_code)
                self.assertTrue(error.outcome_unknown)
                self.assertIsNone(
                    error.__context__, "public error retained the raw adapter context"
                )
                self.assertIsNone(
                    error.__cause__, "public error retained a raw cause"
                )
                rendered = "".join(
                    traceback.format_exception(
                        type(error), error, error.__traceback__
                    )
                )
                self.assertFalse(
                    RAW_MARKER in rendered,
                    "rendered public error retained a raw usage marker",
                )
                self.assertFalse(RAW_MARKER in repr(error))
                self.assertEqual(len(transport.calls), 1)
                snapshot = client.snapshot()
                self.assertEqual(snapshot["operations"][0]["status"], "uncertain")
                self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)

    def test_unexpected_local_failure_has_no_raw_chain(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("local-marker", transport=transport)
        with patch.object(
            client._client, "complete", side_effect=RuntimeError(RAW_MARKER)
        ):
            with self.assertRaises(BudgetedDeepSeekError) as raised:
                client.complete(
                    "op-local", MESSAGES, max_cost_usd="1", dispatch=True
                )
        error = raised.exception
        self.assertEqual(error.code, "internal_error")
        self.assertTrue(error.outcome_unknown)
        self.assertIsNone(error.__context__, "internal_error retained a raw context")
        self.assertIsNone(error.__cause__, "internal_error retained a raw cause")
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        self.assertFalse(
            RAW_MARKER in rendered, "rendered internal_error retained a raw marker"
        )
        self.assertFalse(RAW_MARKER in repr(error))
        self.assertEqual(len(transport.calls), 0)
        snapshot = client.snapshot()
        self.assertEqual(snapshot["operations"][0]["status"], "internal_error")
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)

    def test_relative_root_is_bound_against_chdir(self):
        base = self.root / "wrapper-chdir"
        first = base / "first" / "project"
        second = base / "second" / "project"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        for project in (first, second):
            (project / ".env.local").write_bytes(
                f"DEEPSEEK_API_KEY={KEY}\n".encode("ascii")
            )
        transport = FakeTransport(result=response())
        previous = os.getcwd()
        try:
            os.chdir(first.parent)
            client = BudgetedDeepSeekClient(
                "project", campaign_id=CAMPAIGN, environ={}, transport=transport
            )
            os.chdir(second.parent)
            completion = client.complete(
                "op-bound", MESSAGES, max_cost_usd="1", dispatch=True
            )
        finally:
            os.chdir(previous)
        self.assertEqual(completion.finish_reason, "stop")
        self.assertEqual(client.snapshot()["reserved_microdollars"], 1_000_000)
        self.assertFalse(
            (second / ".coordination").exists(),
            "chdir redirected the wrapper ledger",
        )

    def test_ledger_metadata_change_blocks_before_transport(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("wrapper-metadata", transport=transport)
        connection = sqlite3.connect(client._ledger.ledger_path)
        connection.execute(
            "UPDATE ledger_meta SET value = '1000000' WHERE key = 'cap_micro'"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(BudgetError) as raised:
            client.complete(
                "op-meta", MESSAGES, max_cost_usd="1", dispatch=True
            )
        self.assertEqual(raised.exception.code, "budget_cap_mismatch")
        self.assertEqual(len(transport.calls), 0)

    def test_invalid_stored_reservation_blocks_before_transport(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client(
            "wrapper-corrupt", cap="1", transport=transport
        )
        client._ledger.reserve("op-full", "1")
        connection = sqlite3.connect(client._ledger.ledger_path)
        try:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                "UPDATE operations SET amount_micro = 'text'"
                " WHERE operation_id = 'op-full'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(BudgetError) as raised:
            client.complete("op-new", MESSAGES, max_cost_usd="1", dispatch=True)
        self.assertEqual(raised.exception.code, "budget_ledger_corrupt")
        self.assertEqual(len(transport.calls), 0)

    def test_empty_root_is_denied_before_component_construction(self):
        with self.assertRaises(BudgetedDeepSeekError) as raised:
            BudgetedDeepSeekClient(
                "", campaign_id=CAMPAIGN, environ={}, transport=FakeTransport()
            )
        self.assertEqual(raised.exception.code, "invalid_project_root")
        self.assertIsNone(raised.exception.__context__)
        self.assertIsNone(raised.exception.__cause__)


    # -- r3 correction regressions (R2-F1/R2-F2) --------------------------

    def test_incompatible_trigger_blocks_reopen_before_transport(self):
        project = self.make_project("r3-trigger-reopen")
        budget_module.CampaignLedger(project, campaign_id=CAMPAIGN, cap_usd="1")
        connection = sqlite3.connect(
            project / ".coordination" / "provider-budget.sqlite3"
        )
        try:
            connection.execute(INCOMPATIBLE_TRIGGER)
            connection.commit()
        finally:
            connection.close()
        transport = FakeTransport(result=response())
        with self.assertRaises(BudgetError) as raised:
            BudgetedDeepSeekClient(
                project, campaign_id=CAMPAIGN, cap_usd="1", environ={},
                transport=transport,
            )
        self.assertEqual(raised.exception.code, "budget_schema_unsupported")
        self.assertEqual(len(transport.calls), 0)

    def test_active_handle_schema_drift_blocks_reserve_snapshot_finish(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client("r3-trigger-active", transport=transport)
        client._ledger.reserve("op-pending", "0.1")
        connection = sqlite3.connect(client._ledger.ledger_path)
        try:
            connection.execute(INCOMPATIBLE_TRIGGER)
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(BudgetError) as raised:
            client.complete(
                "op-new", MESSAGES, max_cost_usd="0.1", dispatch=True
            )
        self.assertEqual(raised.exception.code, "budget_schema_unsupported")
        self.assertEqual(len(transport.calls), 0)
        with self.assertRaises(BudgetError) as snapshotted:
            client.snapshot()
        self.assertEqual(snapshotted.exception.code, "budget_schema_unsupported")
        with self.assertRaises(BudgetError) as finished:
            client._ledger.finish("op-pending", "success")
        self.assertEqual(finished.exception.code, "budget_schema_unsupported")
        connection = sqlite3.connect(client._ledger.ledger_path)
        try:
            rows = connection.execute(
                "SELECT operation_id, status FROM operations"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [("op-pending", "pending")])

    def test_pending_reservation_seen_by_fresh_connection_at_dispatch(self):
        calls = []
        observed = []
        client = None

        def observing_transport(body, api_key, *, timeout_seconds, max_response_bytes):
            calls.append(True)
            connection = sqlite3.connect(client._ledger.ledger_path)
            try:
                observed.append(connection.execute(
                    "SELECT operation_id, amount_micro, status,"
                    " estimated_cost_micro, typeof(amount_micro)"
                    " FROM operations WHERE operation_id = 'op-visible'"
                ).fetchall())
            finally:
                connection.close()
            return response()

        client, _ = self.make_client("r3-visible", transport=observing_transport)
        completion = client.complete(
            "op-visible", MESSAGES, max_cost_usd="1", dispatch=True
        )
        self.assertEqual(completion.finish_reason, "stop")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            observed,
            [[("op-visible", 1_000_000, "pending", None, "integer")]],
            "transport did not observe the exact committed pending reservation",
        )
        self.assertEqual(
            client.snapshot()["operations"][0]["status"], "success"
        )

    def test_suppressed_insert_blocks_before_transport(self):
        transport = FakeTransport(result=response())
        client, _ = self.make_client(
            "r3-suppressed", cap="1", transport=transport
        )
        connection = sqlite3.connect(client._ledger.ledger_path)
        try:
            connection.execute(INCOMPATIBLE_TRIGGER)
            connection.commit()
        finally:
            connection.close()
        with patch.object(
            budget_module.CampaignLedger,
            "_validate_supported_schema",
            lambda self, connection: None,
        ):
            with self.assertRaises(BudgetError) as raised:
                client.complete(
                    "op-suppressed", MESSAGES, max_cost_usd="1", dispatch=True
                )
        self.assertEqual(
            raised.exception.code, "budget_reservation_not_persisted"
        )
        self.assertEqual(len(transport.calls), 0)
        connection = sqlite3.connect(client._ledger.ledger_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM operations"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0, "suppressed insert retained a row")
        with self.assertRaises(BudgetError) as snapshotted:
            client.snapshot()
        self.assertEqual(snapshotted.exception.code, "budget_schema_unsupported")

    def test_reservation_deleting_schema_variants_are_refused_without_transport(self):
        for variant in ("sqliteXforget", "unique-replace"):
            with self.subTest(variant=variant):
                transport = FakeTransport(result=response())
                client, project = self.make_client(variant, cap="1", transport=transport)
                ledger = client._ledger
                ledger.reserve("op-existing", "0.4")
                ledger.finish("op-existing", "success")
                ledger.reserve("op-pending", "0.2")
                database = Path(ledger.ledger_path)
                connection = sqlite3.connect(database)
                try:
                    if variant == "sqliteXforget":
                        connection.execute(
                            "CREATE TRIGGER sqliteXforget AFTER INSERT ON operations"
                            " BEGIN DELETE FROM operations"
                            " WHERE operation_id <> NEW.operation_id; END;"
                        )
                    else:
                        ddl = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE name = 'operations'"
                        ).fetchone()[0]
                        self.assertIn("status TEXT NOT NULL", ddl)
                        connection.execute("BEGIN")
                        connection.execute("ALTER TABLE operations RENAME TO original_operations")
                        connection.execute(ddl.replace(
                            "status TEXT NOT NULL", "status TEXT NOT NULL UNIQUE ON CONFLICT REPLACE"
                        ))
                        connection.execute("INSERT INTO operations SELECT * FROM original_operations")
                        connection.execute("DROP TABLE original_operations")
                        self.assertIn(
                            ("sqlite_autoindex_operations_2",),
                            connection.execute(
                                "SELECT name FROM sqlite_master WHERE type = 'index'"
                            ).fetchall(),
                        )
                    connection.commit()
                finally:
                    connection.close()

                before = database.read_bytes()
                methods = (
                    (BudgetedDeepSeekClient, (project,), {
                        "campaign_id": CAMPAIGN, "cap_usd": "1",
                        "environ": {}, "transport": transport,
                    }),
                    (client.snapshot, (), {}),
                    (client.complete, ("op-new", MESSAGES), {
                        "max_cost_usd": "0.3", "dispatch": True,
                    }),
                    (ledger.finish, ("op-pending", "success"), {}),
                )
                for method, args, kwargs in methods:
                    with self.assertRaises(BudgetError) as raised:
                        method(*args, **kwargs)
                    self.assertEqual(raised.exception.code, "budget_schema_unsupported")
                    self.assertEqual(database.read_bytes(), before)
                    self.assertEqual(len(transport.calls), 0)
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                try:
                    self.assertEqual(connection.execute(
                        "SELECT operation_id, amount_micro, status FROM operations ORDER BY operation_id"
                    ).fetchall(), [
                        ("op-existing", 400_000, "success"),
                        ("op-pending", 200_000, "pending"),
                    ])
                finally:
                    connection.close()


if __name__ == "__main__":
    unittest.main()
