"""One-request DeepSeek Chat Completions client and opt-in readiness probe."""

import argparse
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Context, Decimal, InvalidOperation, localcontext
import http.client
import json
import math
import os
import queue
import socket
import sys
import threading
from typing import Protocol


HOST = "api.deepseek.com"
COMPLETIONS_PATH = "/chat/completions"
MODEL = "deepseek-flash"
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 131_072
MAX_OUTPUT_TOKENS = 4_096
MAX_TIMEOUT_SECONDS = 30

# Published peak Flash rates from docs/project/DEEPSEEK.md.
_CACHE_HIT_RATE = Decimal("0.000000006")
_CACHE_MISS_RATE = Decimal("0.00000030")
_OUTPUT_RATE = Decimal("0.00000120")
_PROMPT_OVERHEAD_TOKENS = 4_096


def _token_cost(hit, miss, output):
    with localcontext(Context(prec=28)):
        return hit * _CACHE_HIT_RATE + miss * _CACHE_MISS_RATE + output * _OUTPUT_RATE


class DeepSeekError(Exception):
    """Machine-readable failure with no provider text or credentials."""

    def __init__(self, code, *, status=None, reservation_usd=None, outcome_unknown=False, raw_usage=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.reservation_usd = reservation_usd
        self.outcome_unknown = outcome_unknown
        self.raw_usage = raw_usage


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = field(repr=False)


class Transport(Protocol):
    def __call__(
        self, body: bytes, api_key: str, *, timeout_seconds: int, max_response_bytes: int
    ) -> HttpResponse: ...


@dataclass(frozen=True)
class ToolCall:
    id: str = field(repr=False)
    name: str = field(repr=False)
    arguments: dict | None = field(repr=False)
    arguments_error: str | None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None
    cache_hit_input_tokens: int | None
    uncached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    raw: dict | None = field(repr=False)

    @property
    def estimated_cost_usd(self) -> Decimal | None:
        hit, miss, output = self.cache_hit_input_tokens, self.uncached_input_tokens, self.output_tokens
        if hit is None or miss is None or output is None:
            return None
        return _token_cost(hit, miss, output)


@dataclass(frozen=True)
class Completion:
    requested_model: str
    returned_model: str = field(repr=False)
    assistant_message: dict = field(repr=False)
    tool_calls: tuple[ToolCall, ...] = field(repr=False)
    finish_reason: str
    usage: Usage
    reservation_usd: Decimal


class _IncompleteResponse(Exception):
    pass


def _https_transport(body, api_key, *, timeout_seconds, max_response_bytes):
    """Use one verified TLS connection with no proxies, redirects or retries."""
    connection = http.client.HTTPSConnection(HOST, timeout=timeout_seconds)
    connection.set_debuglevel(0)
    results = queue.Queue(maxsize=1)
    cancelled = threading.Event()
    active_socket = None

    def exchange():
        nonlocal active_socket
        connection.connect()
        active_socket = connection.sock
        if cancelled.is_set():
            raise TimeoutError
        connection.request(
            "POST", COMPLETIONS_PATH, body=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with connection.getresponse() as response:
            if response.status != 200:
                return HttpResponse(response.status, b"")
            if response.length is not None and response.length > max_response_bytes:
                raise _IncompleteResponse
            payload = response.read(max_response_bytes + 1)
            if len(payload) > max_response_bytes or response.length:
                raise _IncompleteResponse
            return HttpResponse(response.status, payload)

    def run():
        try:
            result = exchange()
        except TimeoutError:
            result = TimeoutError()
        except (_IncompleteResponse, http.client.IncompleteRead):
            result = _IncompleteResponse()
        except Exception:
            result = ConnectionError()
        finally:
            try:
                connection.close()
            except Exception:
                pass
        results.put(result)

    # The socket timeout alone cannot bound DNS or an HTTP peer that trickles bytes.
    threading.Thread(target=run, daemon=True).start()
    try:
        result = results.get(timeout=timeout_seconds)
    except queue.Empty:
        cancelled.set()
        if active_socket is not None:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        result = TimeoutError()
    if isinstance(result, Exception):
        raise result
    return result


def _decimal_budget(value) -> Decimal:
    if type(value) not in (str, int, float, Decimal):
        raise DeepSeekError("invalid_budget")
    try:
        budget = Decimal.from_float(value) if type(value) is float else Decimal(value)
    except (InvalidOperation, ValueError):
        budget = None
    if budget is None or not budget.is_finite() or budget <= 0:
        raise DeepSeekError("invalid_budget")
    return budget


def _positive_limit(value, maximum, code):
    if type(value) is not int or not 1 <= value <= maximum:
        raise DeepSeekError(code)


def _object_without_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError
        obj[key] = value
    return obj


def _invalid_constant(_value):
    raise ValueError


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def _json_object(value):
    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        parsed = json.loads(
            value, object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_invalid_constant, parse_float=_finite_float,
        )
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _tool_calls(message, error_code):
    raw_calls = message.get("tool_calls")
    if raw_calls is None or raw_calls == []:
        return ()
    if not isinstance(raw_calls, list) or not isinstance(message.get("reasoning_content"), str):
        raise DeepSeekError(error_code)

    calls = []
    ids = set()
    for item in raw_calls:
        if not isinstance(item, dict) or item.get("type") != "function":
            raise DeepSeekError(error_code)
        call_id = item.get("id")
        function = item.get("function")
        if not isinstance(call_id, str) or not call_id or call_id in ids or not isinstance(function, dict):
            raise DeepSeekError(error_code)
        name, raw_arguments = function.get("name"), function.get("arguments")
        if not isinstance(name, str) or not name or not isinstance(raw_arguments, str):
            raise DeepSeekError(error_code)
        ids.add(call_id)
        arguments = _json_object(raw_arguments)
        error = "invalid_tool_arguments" if arguments is None else None
        calls.append(ToolCall(call_id, name, arguments, error))
    return tuple(calls)


def _assistant_calls(message, error_code):
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise DeepSeekError(error_code)
    calls = _tool_calls(message, error_code)
    content = message.get("content")
    if not isinstance(content, str) and (content is not None or not calls):
        raise DeepSeekError(error_code)
    reasoning = message.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        raise DeepSeekError(error_code)
    return calls


def _validate_messages(messages):
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
        raise DeepSeekError("invalid_history")
    pending = set()
    for message in messages:
        if not isinstance(message, dict):
            raise DeepSeekError("invalid_history")
        role = message.get("role")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending or not isinstance(message.get("content"), str):
                raise DeepSeekError("invalid_history")
            pending.remove(call_id)
        elif pending:
            raise DeepSeekError("invalid_history")
        elif role == "assistant":
            pending = {call.id for call in _assistant_calls(message, "invalid_history")}
        elif role not in ("system", "user") or not isinstance(message.get("content"), str):
            raise DeepSeekError("invalid_history")
    if pending:
        raise DeepSeekError("invalid_history")


def append_tool_results(messages, assistant_message, results: Mapping[str, str]) -> list[dict]:
    """Replay a returned assistant turn, followed by one string result per call ID."""
    _validate_messages(messages)
    calls = _assistant_calls(assistant_message, "invalid_tool_turn")
    if not calls or not isinstance(results, Mapping) or set(results) != {call.id for call in calls}:
        raise DeepSeekError("invalid_tool_results")
    if any(not isinstance(results[call.id], str) for call in calls):
        raise DeepSeekError("invalid_tool_results")
    return [
        *deepcopy(list(messages)), deepcopy(assistant_message),
        *[{"role": "tool", "tool_call_id": call.id, "content": results[call.id]} for call in calls],
    ]


def _request_body(messages, tools, max_tokens):
    _validate_messages(messages)
    payload = {
        "model": MODEL, "messages": list(messages), "stream": False,
        "thinking": {"type": "enabled"}, "reasoning_effort": "high", "max_tokens": max_tokens,
    }
    if tools is not None:
        if not isinstance(tools, (tuple, list)) or any(
            not isinstance(tool, dict) or tool.get("type") != "function"
            or not isinstance(tool.get("function"), dict) for tool in tools
        ):
            raise DeepSeekError("invalid_tools")
        payload["tools"] = list(tools)
    try:
        body = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        body = None
    if body is None:
        raise DeepSeekError("invalid_request")
    if len(body) > MAX_REQUEST_BYTES:
        raise DeepSeekError("request_too_large")
    if _json_object(body) is None:
        raise DeepSeekError("invalid_request")
    return body


def _usage_count(obj, key):
    value = obj.get(key)
    if value is not None and (type(value) is not int or value < 0):
        raise DeepSeekError("malformed_usage")
    return value


def _normalize_usage(raw):
    if raw is None:
        return Usage(None, None, None, None, None, None, None)
    if not isinstance(raw, dict):
        raise DeepSeekError("malformed_usage")
    prompt = _usage_count(raw, "prompt_tokens")
    hit = _usage_count(raw, "prompt_cache_hit_tokens")
    miss = _usage_count(raw, "prompt_cache_miss_tokens")
    output = _usage_count(raw, "completion_tokens")
    total = _usage_count(raw, "total_tokens")
    details = raw.get("prompt_tokens_details")
    if details is not None:
        if not isinstance(details, dict):
            raise DeepSeekError("malformed_usage")
        cached = _usage_count(details, "cached_tokens")
        if hit is not None and cached is not None and cached != hit:
            raise DeepSeekError("malformed_usage")
        if hit is None:
            hit = cached
    details = raw.get("completion_tokens_details")
    reasoning = None
    if details is not None:
        if not isinstance(details, dict):
            raise DeepSeekError("malformed_usage")
        reasoning = _usage_count(details, "reasoning_tokens")

    if prompt is None and hit is not None and miss is not None:
        prompt = hit + miss
    if total is not None:
        if prompt is None and output is not None:
            prompt = total - output
        elif output is None and prompt is not None:
            output = total - prompt
    if prompt is not None:
        if hit is None and miss is not None:
            hit = prompt - miss
        elif miss is None and hit is not None:
            miss = prompt - hit
        elif hit is not None and hit + miss != prompt:
            raise DeepSeekError("malformed_usage")
    if any(value is not None and value < 0 for value in (prompt, hit, miss, output)):
        raise DeepSeekError("malformed_usage")
    if reasoning is not None and output is not None and reasoning > output:
        raise DeepSeekError("malformed_usage")
    if total is not None:
        minimum_prompt = prompt if prompt is not None else (hit or 0) + (miss or 0)
        minimum_output = output if output is not None else reasoning or 0
        minimum = minimum_prompt + minimum_output
        if minimum > total or (prompt is not None and output is not None and minimum != total):
            raise DeepSeekError("malformed_usage")
    return Usage(prompt, hit, miss, output, reasoning, total, raw)


def _parse_completion(payload, prompt_limit, output_limit, reservation):
    model, choices = payload.get("model"), payload.get("choices")
    if not isinstance(model, str) or not model.strip() or not isinstance(choices, list) or len(choices) != 1:
        raise DeepSeekError("malformed_response")
    choice = choices[0]
    if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
        raise DeepSeekError("malformed_response")
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise DeepSeekError("completion_truncated")
    if finish_reason not in ("stop", "tool_calls"):
        raise DeepSeekError("incomplete_completion")
    message = choice.get("message")
    calls = _assistant_calls(message, "malformed_response")
    if bool(calls) != (finish_reason == "tool_calls"):
        raise DeepSeekError("malformed_response")
    usage = _normalize_usage(payload.get("usage"))
    counts_and_limits = (
        (usage.prompt_tokens, prompt_limit), (usage.cache_hit_input_tokens, prompt_limit),
        (usage.uncached_input_tokens, prompt_limit), (usage.output_tokens, output_limit),
        (usage.reasoning_tokens, output_limit),
        (usage.total_tokens, prompt_limit + output_limit),
    )
    if any(count is not None and count > limit for count, limit in counts_and_limits):
        raise DeepSeekError("usage_exceeds_reservation")
    return Completion(MODEL, model, message, calls, finish_reason, usage, reservation)


class DeepSeekClient:
    def __init__(self, api_key: str | None, *, transport: Transport | None = None):
        self._api_key = api_key
        self._transport = _https_transport if transport is None else transport

    def complete(
        self, messages, *, max_cost_usd, tools=None, max_tokens=64,
        timeout_seconds=15, max_response_bytes=MAX_RESPONSE_BYTES,
    ) -> Completion:
        """Make a single call after checking the local peak-price reservation."""
        budget = _decimal_budget(max_cost_usd)
        _positive_limit(max_tokens, MAX_OUTPUT_TOKENS, "invalid_output_limit")
        _positive_limit(timeout_seconds, MAX_TIMEOUT_SECONDS, "invalid_timeout")
        _positive_limit(max_response_bytes, MAX_RESPONSE_BYTES, "invalid_response_limit")
        key = self._api_key
        if key is None or key == "":
            raise DeepSeekError("missing_auth")
        if type(key) is not str or len(key) > 4096 or any(not 33 <= ord(char) <= 126 for char in key):
            raise DeepSeekError("invalid_auth")
        body = _request_body(messages, tools, max_tokens)
        prompt_limit = 2 * len(body) + _PROMPT_OVERHEAD_TOKENS
        reservation = _token_cost(0, prompt_limit, max_tokens)
        if budget < reservation:
            raise DeepSeekError("insufficient_budget", reservation_usd=reservation)

        try:
            response = self._transport(
                body, key, timeout_seconds=timeout_seconds, max_response_bytes=max_response_bytes,
            )
        except TimeoutError:
            error = "transport_timeout"
        except _IncompleteResponse:
            error = "response_truncated"
        except Exception:
            error = "transport_failure"
        else:
            error = None
        if error is not None:
            raise DeepSeekError(error, reservation_usd=reservation, outcome_unknown=True)

        status = None
        outcome_unknown = True
        raw_usage = None
        if not isinstance(response, HttpResponse) or type(response.status) is not int or not 100 <= response.status <= 599:
            error = "invalid_transport_response"
        elif response.status != 200:
            status = response.status
            error = "http_error"
            outcome_unknown = 500 <= status <= 599
        elif type(response.body) is not bytes:
            error = "invalid_transport_response"
        elif len(response.body) > max_response_bytes:
            error = "response_truncated"
        else:
            payload = _json_object(response.body)
            if payload is None:
                error = "invalid_response_json"
            else:
                raw_usage = payload.get("usage")
                try:
                    return _parse_completion(payload, prompt_limit, max_tokens, reservation)
                except DeepSeekError as failure:
                    error = failure.code
        raise DeepSeekError(
            error, status=status, reservation_usd=reservation, outcome_unknown=outcome_unknown, raw_usage=raw_usage,
        )


class _ReadinessParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "invalid_arguments\n")


def main(argv=None, *, environ=None, transport=None):
    parser = _ReadinessParser(
        description="Send one bounded official DeepSeek readiness request.", allow_abbrev=False,
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-cost-usd")
    args = parser.parse_args(argv)
    if not args.live or args.max_cost_usd is None:
        print("live_and_budget_required", file=sys.stderr)
        return 2
    try:
        budget = _decimal_budget(args.max_cost_usd)
    except DeepSeekError as failure:
        print(failure.code, file=sys.stderr)
        return 2

    env = os.environ if environ is None else environ
    key = env.get("DEEPSEEK_API_KEY")
    try:
        result = DeepSeekClient(key, transport=transport).complete(
            [{"role": "user", "content": "Reply with READY."}], max_cost_usd=budget,
        )
    except DeepSeekError as failure:
        error = {"error": failure.code, "outcome_unknown": failure.outcome_unknown}
        if failure.status is not None:
            error["http_status"] = failure.status
        if failure.reservation_usd is not None:
            error["reservation_usd"] = str(failure.reservation_usd)
        print(json.dumps(error), file=sys.stderr)
        return 1
    estimated_cost = result.usage.estimated_cost_usd
    print(json.dumps({
        "requested_model": result.requested_model,
        "returned_model": result.returned_model.replace(key, "[REDACTED]"),
        "finish_reason": result.finish_reason,
        "reservation_usd": str(result.reservation_usd),
        "estimated_usage_cost_usd": str(estimated_cost) if estimated_cost is not None else None,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
