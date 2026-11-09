"""Budgeted official-DeepSeek model-port bridge for the offline evaluated-role loop.

The bridge adapts the accepted :class:`adapters.budgeted_deepseek.BudgetedDeepSeekClient`
to the loop's normalized model-port seam with a frozen content-JSON wire protocol
(``json-action-0.1``).  It performs no I/O at import, loads no credentials, constructs
no provider client and offers no retry or fallback provider.  Tests use fake
transports/clients only; live dispatch belongs to a separately qualified Main runtime.
"""

from __future__ import annotations

import copy
from decimal import Context, Decimal, InvalidOperation, ROUND_CEILING, localcontext
import math
import time

from adapters.budget import BudgetError
from adapters.budgeted_deepseek import BudgetedDeepSeekError
from adapters.deepseek import (
    DeepSeekError,
    MAX_OUTPUT_TOKENS,
    MAX_RESPONSE_BYTES,
    MAX_TIMEOUT_SECONDS,
    _PROMPT_OVERHEAD_TOKENS,
    _json_object,
    _request_body,
    _token_cost,
)
from .loop import (
    ACTION_SCHEMAS,
    FINISH_STATUSES,
    MAX_CONTENT_BYTES,
    MAX_EVIDENCE_ID_CHARS,
    MAX_EVIDENCE_IDS,
    MAX_FINDINGS,
    MAX_REMAINING,
    MAX_TEXT_CHARS,
    PROGRESS_KEYS,
    REVIEW_DISPOSITIONS,
    REVIEW_FINDING_KEYS,
    ROLES,
)


PROTOCOL = "json-action-0.1"
MICRODOLLARS_PER_USD = 1_000_000
_REQUEST_KEYS = (
    "episode_id", "operation_id", "role", "invocation_index",
    "messages", "allowed_actions",
)
_ROLE_TARGETS = {
    "delegate": "W1",
    "request_review": "R1",
}
_FIELD_TYPES = {
    "filename": "string (one exact allowlisted workspace name)",
    "target": "exact string",
    "task": "string <=%d chars" % MAX_TEXT_CHARS,
    "summary": "string <=%d chars" % MAX_TEXT_CHARS,
    "evidence_ids": "array of <=%d strings, each <=%d chars"
                    % (MAX_EVIDENCE_IDS, MAX_EVIDENCE_ID_CHARS),
    "findings": "array of <=%d finding objects" % MAX_FINDINGS,
    "remaining": "array of <=%d strings, each <=%d chars"
                 % (MAX_REMAINING, MAX_TEXT_CHARS),
    "claimed_source_sha256": "lowercase 64-hex string",
    "claimed_requirements_version": 'string "v1" or "v2"',
    "expected_sha256": "lowercase 64-hex string",
    "content": "string <=%d UTF-8 bytes" % MAX_CONTENT_BYTES,
    "disposition": "one of " + ", ".join(REVIEW_DISPOSITIONS),
    "claimed_status": "one of " + ", ".join(FINISH_STATUSES),
    "record": "progress-record object",
}


class ModelPortError(Exception):
    """Sanitized bridge failure with a fixed code and no raw cause/response body."""

    def __init__(self, code, *, status=None, outcome_unknown=False, reservation_usd=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.outcome_unknown = outcome_unknown
        self.reservation_usd = reservation_usd


def _integer_limit(value, maximum, code):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ModelPortError(code)
    return value


def _cap_decimal(value):
    if isinstance(value, Decimal):
        cap = value
    elif type(value) is str:
        if len(value) > 64:
            raise ModelPortError("bridge_invalid_config")
        try:
            cap = Decimal(value)
        except (InvalidOperation, ValueError):
            raise ModelPortError("bridge_invalid_config") from None
    else:
        raise ModelPortError("bridge_invalid_config")
    if not cap.is_finite() or cap <= 0:
        raise ModelPortError("bridge_invalid_config")
    return cap


def _deadline_value(value):
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ModelPortError("bridge_invalid_config")
    if not math.isfinite(value):
        raise ModelPortError("bridge_invalid_config")
    return value


def _ceil_microdollars(amount):
    with localcontext(Context(prec=40, rounding=ROUND_CEILING)):
        micro = int(amount.scaleb(6).to_integral_value(rounding=ROUND_CEILING))
    return micro


def _micro_usd(micro):
    with localcontext(Context(prec=40)):
        return Decimal(micro).scaleb(-6)


def _estimate_micro(usage):
    try:
        cost = usage.estimated_cost_usd
    except Exception:
        return None
    if cost is None:
        return None
    try:
        return _ceil_microdollars(cost)
    except (ArithmeticError, ValueError):
        return None


def _schema_text(name):
    fields = sorted(ACTION_SCHEMAS[name] - {"name"})
    parts = []
    for field in fields:
        parts.append("%s: %s" % (field, _FIELD_TYPES.get(field, "bounded value")))
    if name == "delegate" or name == "request_review":
        parts = ["target: exact string " + _ROLE_TARGETS[name]] + [
            part for part in parts if not part.startswith("target:")
        ]
    return "{name: %r%s}" % (name, "".join(", " + part for part in parts))


def _protocol_instruction(role, allowed_actions):
    lines = [
        "Wire protocol: %s." % PROTOCOL,
        "You are role %s in an offline evaluated-role episode." % role,
        'Reply with exactly one JSON object and nothing else: {"action": <object>}.',
        "Do not use Markdown fences, prose, multiple objects, native tool calls or "
        "provider tool-call fields. The controller's tool observations arrive as user "
        "messages; answer them with a single action object.",
        "Allowed actions for this role:",
    ]
    for name in allowed_actions:
        lines.append("- " + _schema_text(name))
    if "read" in allowed_actions:
        lines.append(
            'Valid JSON format example (a read action, not a task decision): '
            '{"action":{"name":"read","filename":"TASK.md"}}'
        )
    lines.extend([
        "Shared structures:",
        '- findings: array <=%d of {"id": string <=%d, "claim": string <=%d, '
        '"status": "open"|"resolved", "evidence_ids": array <=%d of string <=%d}.'
        % (MAX_FINDINGS, MAX_EVIDENCE_ID_CHARS, MAX_TEXT_CHARS,
           MAX_EVIDENCE_IDS, MAX_EVIDENCE_ID_CHARS),
        '- evidence_ids: array <=%d of string <=%d.'
        % (MAX_EVIDENCE_IDS, MAX_EVIDENCE_ID_CHARS),
    ])
    if "progress_write" in allowed_actions:
        lines.append(
            '- progress record keys: %s; requirements_version "v1"|"v2", '
            "candidate_sha256 lowercase 64-hex, findings use the shared finding "
            "shape, next_action string <=%d, completion_obligations array <=%d of "
            "string <=%d."
            % ("{" + ", ".join(sorted(PROGRESS_KEYS)) + "}", MAX_TEXT_CHARS,
               MAX_REMAINING, MAX_TEXT_CHARS)
        )
    lines.append("Do not invent actions or fields outside this instruction.")
    return "\n".join(lines)


class BudgetedModelPort:
    """Callable model port over one injected budgeted client, with observations."""

    def __init__(
        self,
        client,
        *,
        dispatch=False,
        max_tokens=2048,
        timeout_seconds=30,
        per_call_cap_usd="0.10",
        deadline=None,
        monotonic=None,
    ):
        if not callable(getattr(client, "complete", None)):
            raise ModelPortError("bridge_invalid_config")
        self.client = client
        self.dispatch = dispatch
        self.max_tokens = _integer_limit(
            max_tokens, MAX_OUTPUT_TOKENS, "bridge_invalid_config"
        )
        self.timeout_seconds = _integer_limit(
            timeout_seconds, MAX_TIMEOUT_SECONDS, "bridge_invalid_config"
        )
        self.per_call_cap_usd = _cap_decimal(per_call_cap_usd)
        self.deadline = _deadline_value(deadline)
        if monotonic is not None and not callable(monotonic):
            raise ModelPortError("bridge_invalid_config")
        self._monotonic = time.monotonic if monotonic is None else monotonic
        self._observations = []

    @property
    def observations(self):
        """Copied host-only observations; no prompts, content, keys or raw errors."""
        return copy.deepcopy(self._observations)

    def _observe(self, request, *, status, reservation_micro_usd=None,
                 requested_model=None, returned_model=None, usage=None,
                 estimated_cost_micro_usd=None):
        record = {
            "operation_id": request.get("operation_id")
            if isinstance(request, dict) else None,
            "role": request.get("role") if isinstance(request, dict) else None,
            "requested_model": requested_model,
            "returned_model": returned_model,
            "usage": {
                "input_tokens": None,
                "output_tokens": None,
                "cache_hit_input_tokens": None,
                "uncached_input_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
            },
            "reservation_micro_usd": reservation_micro_usd,
            "estimated_cost_micro_usd": estimated_cost_micro_usd,
            "status": status,
        }
        if usage is not None:
            for key, attribute in (
                ("input_tokens", "prompt_tokens"),
                ("output_tokens", "output_tokens"),
                ("cache_hit_input_tokens", "cache_hit_input_tokens"),
                ("uncached_input_tokens", "uncached_input_tokens"),
                ("reasoning_tokens", "reasoning_tokens"),
                ("total_tokens", "total_tokens"),
            ):
                value = getattr(usage, attribute, None)
                record["usage"][key] = value if type(value) is int else None
        self._observations.append(record)

    def _validate_request(self, request):
        if type(request) is not dict:
            raise ModelPortError("bridge_invalid_request")
        for key in _REQUEST_KEYS:
            if key not in request:
                raise ModelPortError("bridge_invalid_request")
        if request["role"] not in ROLES:
            raise ModelPortError("bridge_invalid_request")
        if type(request["operation_id"]) is not str or not request["operation_id"]:
            raise ModelPortError("bridge_invalid_request")
        if type(request["messages"]) is not list or not request["messages"]:
            raise ModelPortError("bridge_invalid_request")
        allowed = request["allowed_actions"]
        if type(allowed) is not list or any(
            type(name) is not str or name not in ACTION_SCHEMAS for name in allowed
        ):
            raise ModelPortError("bridge_invalid_request")
        for message in request["messages"]:
            if (
                type(message) is not dict
                or message.get("role") not in ("system", "user", "assistant")
                or type(message.get("content")) is not str
            ):
                raise ModelPortError("bridge_invalid_request")
        return request

    def _effective_timeout(self):
        configured = self.timeout_seconds
        if self.deadline is None:
            return configured, None
        try:
            now = self._monotonic()
        except Exception:
            return None, "bridge_clock_error"
        if type(now) not in (int, float) or not math.isfinite(now):
            return None, "bridge_clock_error"
        remaining = self.deadline - now
        if remaining < 1:
            return None, "bridge_deadline_expired"
        timeout = min(configured, int(math.floor(remaining)), MAX_TIMEOUT_SECONDS)
        return timeout, None

    def __call__(self, request):
        if self.dispatch is not True:
            self._observe(request, status="bridge_dispatch_not_authorized")
            raise ModelPortError("bridge_dispatch_not_authorized")
        self._validate_request(request)

        instruction = _protocol_instruction(
            request["role"], request["allowed_actions"]
        )
        messages = [{"role": "system", "content": instruction}]
        messages.extend(copy.deepcopy(request["messages"]))

        body = None
        local_error = None
        try:
            body = _request_body(messages, None, self.max_tokens, json_output=True)
        except DeepSeekError:
            local_error = "bridge_invalid_request"
        except Exception:
            local_error = "bridge_invalid_request"
        if local_error is not None:
            self._observe(request, status=local_error)
            raise ModelPortError(local_error)

        prompt_limit = 2 * len(body) + _PROMPT_OVERHEAD_TOKENS
        reservation = _token_cost(0, prompt_limit, self.max_tokens)
        reservation_micro = max(1, _ceil_microdollars(reservation))
        reservation_usd = _micro_usd(reservation_micro)
        if reservation_usd > self.per_call_cap_usd:
            self._observe(
                request, status="bridge_insufficient_cap",
                reservation_micro_usd=reservation_micro,
            )
            raise ModelPortError(
                "bridge_insufficient_cap", reservation_usd=reservation_usd
            )

        # Recheck the clock after protocol preparation, genuine serialization,
        # pricing and cap validation, immediately before the client call.  A clock
        # that crossed the deadline during preparation must produce zero calls.
        timeout, timeout_error = self._effective_timeout()
        if timeout_error is not None:
            self._observe(
                request, status=timeout_error,
                reservation_micro_usd=reservation_micro,
            )
            raise ModelPortError(timeout_error)

        completion = None
        failure = None
        try:
            completion = self.client.complete(
                request["operation_id"],
                messages,
                max_cost_usd=reservation_usd,
                tools=None,
                max_tokens=self.max_tokens,
                timeout_seconds=timeout,
                max_response_bytes=MAX_RESPONSE_BYTES,
                dispatch=True,
                json_output=True,
            )
        except (BudgetedDeepSeekError, DeepSeekError, BudgetError) as raised:
            failure = (
                getattr(raised, "code", "bridge_internal_error"),
                getattr(raised, "status", None),
                bool(getattr(raised, "outcome_unknown", False)),
                getattr(raised, "reservation_usd", None),
            )
        except Exception:
            failure = ("bridge_internal_error", None, True, None)
        if failure is not None:
            code, status, unknown, failed_reservation = failure
            self._observe(
                request, status=code, reservation_micro_usd=reservation_micro,
            )
            raise ModelPortError(
                code, status=status, outcome_unknown=unknown,
                reservation_usd=(
                    failed_reservation
                    if failed_reservation is not None else reservation_usd
                ),
            )

        usage = getattr(completion, "usage", None)
        assistant = getattr(completion, "assistant_message", None)
        tool_calls = getattr(completion, "tool_calls", None)
        requested_model = getattr(completion, "requested_model", None)
        returned_model = getattr(completion, "returned_model", None)
        invalid = (
            type(assistant) is not dict
            or type(requested_model) is not str
            or type(returned_model) is not str
            or usage is None
            or type(getattr(usage, "prompt_tokens", 0)) not in (int, type(None))
            or type(getattr(usage, "output_tokens", 0)) not in (int, type(None))
        )
        if invalid:
            self._observe(
                request, status="bridge_invalid_response",
                reservation_micro_usd=reservation_micro,
                requested_model=(requested_model if type(requested_model) is str else None),
                returned_model=(returned_model if type(returned_model) is str else None),
                usage=usage,
            )
            raise ModelPortError(
                "bridge_invalid_response", outcome_unknown=True,
                reservation_usd=reservation_usd,
            )
        estimated_micro = _estimate_micro(usage)
        if tool_calls or assistant.get("tool_calls"):
            self._observe(
                request, status="bridge_unsupported_tool_calls",
                reservation_micro_usd=reservation_micro,
                requested_model=requested_model, returned_model=returned_model,
                usage=usage, estimated_cost_micro_usd=estimated_micro,
            )
            raise ModelPortError(
                "bridge_unsupported_tool_calls", outcome_unknown=True,
                reservation_usd=reservation_usd,
            )
        content = assistant.get("content")
        action = None
        content_error = None
        if type(content) is not str:
            content_error = "bridge_invalid_content"
        else:
            # Strict accepted-adapter decoder: duplicate keys, non-finite
            # constants and non-JSON numbers are refused rather than last-wins.
            envelope = _json_object(content)
            if (
                envelope is None
                or set(envelope) != {"action"}
                or type(envelope.get("action")) is not dict
            ):
                content_error = "bridge_invalid_content"
            else:
                action = envelope["action"]
        if content_error is not None:
            self._observe(
                request, status=content_error,
                reservation_micro_usd=reservation_micro,
                requested_model=requested_model, returned_model=returned_model,
                usage=usage, estimated_cost_micro_usd=estimated_micro,
            )
            raise ModelPortError(
                content_error, outcome_unknown=True,
                reservation_usd=reservation_usd,
            )
        self._observe(
            request, status="ok",
            reservation_micro_usd=reservation_micro,
            requested_model=requested_model, returned_model=returned_model,
            usage=usage, estimated_cost_micro_usd=estimated_micro,
        )
        return {
            "action": copy.deepcopy(action),
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
            "requested_model": requested_model,
            "returned_model": returned_model,
        }

    def __repr__(self):
        return (
            "BudgetedModelPort("
            f"dispatch={self.dispatch!r}, max_tokens={self.max_tokens}, "
            f"timeout_seconds={self.timeout_seconds}, protocol={PROTOCOL!r})"
        )


__all__ = ["BudgetedModelPort", "ModelPortError", "PROTOCOL"]
