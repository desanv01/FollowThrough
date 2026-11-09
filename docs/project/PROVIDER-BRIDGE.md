# Budgeted provider bridge (json-action-0.1)

Status: P3-PROVIDER-BRIDGE/r1 candidate source, offline preparation only. This
module adapts the accepted `BudgetedDeepSeekClient` to the evaluated-role loop's
model-port seam. It does not load credentials, construct a provider client at import,
perform network I/O, retry or use a fallback provider. Live dispatch remains a
separately qualified Main-owned runtime operation.

## API

```python
from episode.provider import BudgetedModelPort

port = BudgetedModelPort(
    client,                      # injected BudgetedDeepSeekClient-compatible object
    dispatch=False,              # must become exactly True before any client call
    max_tokens=2048,             # 1..4096 (adapter maximum)
    timeout_seconds=30,          # 1..30 (adapter maximum); may be shortened
    per_call_cap_usd="0.10",     # Decimal or decimal string, finite and positive
    deadline=None,               # optional absolute monotonic timestamp
    monotonic=None,              # optional clock callable (default time.monotonic)
)
response = port(model_request)   # loop model request -> normalized response
observations = port.observations  # copied host-only observation list
```

`dispatch` is checked with exact identity; any value other than `True` (including
`1` or a truthy object) is refused before a reservation or call. The bridge has no
retry path: one model request causes at most one `client.complete` invocation.

## Wire protocol `json-action-0.1`

The bridge prepends one fixed system instruction to a deep copy of the supplied role
messages. The instruction describes only that role's allowed action schemas, enum
values, field types and shared finding/progress structures from the accepted loop
contract. Supplied role history is retained unchanged; no other role history,
solution, development instruction, hidden evidence or controller-only schedule is
added.

The assistant's `content` must be exactly one JSON object `{"action": <object>}`.
The bridge opts into DeepSeek JSON Output via `json_output=True`, serialized as
`response_format={"type":"json_object"}` by the actual adapter. The protocol
instruction includes a valid read-action JSON example for roles allowing reads.
Other adapter and budgeted-client callers retain `json_output=False` by default,
which omits `response_format`. This follows the
[official JSON Output guide](https://api-docs.deepseek.com/guides/json_mode/).
There is no Markdown-fence stripping, auto-repair, synthetic substitution or
native/tool-call history support. Native tool-call completions are rejected as
unsupported protocol. A parseable action object is passed to the loop unchanged, so
a wrong-role or malformed action remains an observable loop denial. Only an
unparseable envelope is rejected here.

## Reservation and budget

The bridge uses the accepted adapter's pinned internals, deliberately documented as a
version-coupled dependency:

- `_request_body(messages, None, max_tokens, json_output=True)`
- `_PROMPT_OVERHEAD_TOKENS`
- `_token_cost(0, 2 * len(body) + overhead, max_tokens)`

The result is rounded **up** to whole micro-USD with `Decimal`. The bridge refuses
before dispatch when that rounded reservation exceeds the explicit per-call cap, and
passes the rounded reservation (not the whole cap) as `max_cost_usd` to
`client.complete(operation_id, messages, dispatch=True, tools=None, json_output=True, ...)`.
Thus reservation pricing and transport use the same JSON-output serialization. It does not
hardcode prices, change `MODEL`, release ledger reservations or reserve unused cap.
`request.operation_id` is preserved exactly; the wrapper + ledger remain the aggregate
budget boundary.

## Deadline and timeout

When `deadline` is supplied, the bridge reads the injected monotonic clock before
dispatch, computes the remaining time, and floors it to a positive integer timeout
bounded by the configured timeout and the adapter's 30-second maximum. Less than one
second remaining (including an expired deadline) is refused before any call. Any
integer limit is validated excluding bool; deadline/clock values must be finite
numbers. This bounds the adapter timeout; it does **not** claim the bridge can forcibly
interrupt an arbitrary blocking callback. Real request deadlines belong to the future
Main-owned live bridge.

## Response, observations and errors

A successful call returns exactly the loop response keys:

```python
{
  "action": <parseable action object, unchanged>,
  "usage": {"input_tokens": <prompt_tokens or None>,
             "output_tokens": <output_tokens or None>},
  "requested_model": <str>,
  "returned_model": <str>,
}
```

`port.observations` returns a deep copy of host-only records with: operation ID, role,
requested/returned model, usage counts (input/output plus cache-hit, uncached,
reasoning and total when the adapter exposes them), `reservation_micro_usd`,
`estimated_cost_micro_usd` (null when usage is underdetermined) and a fixed
status/error code. Observations never include prompts, assistant content, reasoning,
credentials, exception strings or provider bodies.

Sanitized `ModelPortError` codes:

| Code | Meaning |
| --- | --- |
| `bridge_dispatch_not_authorized` | `dispatch` was not exactly `True`; zero calls |
| `bridge_invalid_config` | Invalid constructor limits/cap/deadline/clock/client; zero calls |
| `bridge_invalid_request` | Request shape/history failed local pre-dispatch validation; zero calls |
| `bridge_deadline_expired` | Less than one second (or negative) remained; zero calls |
| `bridge_clock_error` | Injected clock raised or returned a non-finite non-number; zero calls |
| `bridge_insufficient_cap` | Rounded reservation exceeds the per-call cap; zero calls |
| Provider/adapter code (`http_error`, `transport_timeout`, …) | Preserved fixed adapter code/status/uncertainty; one attempt, no retry |
| `budget_cap_exceeded` and other ledger codes | Preserved fixed aggregate-budget refusal before transport |
| `bridge_internal_error` | Unexpected local/client failure; sanitized, uncertainty reported |
| `bridge_invalid_response` | Completion object lacks required normalized fields after the call |
| `bridge_unsupported_tool_calls` | Native tool-call completion rejected; one attempt |
| `bridge_invalid_content` | Assistant content is not exactly one `{"action": object}` envelope; one billed attempt retained |

Invalid content or unsupported tool calls after a successful billed call retain that
call's usage, reservation and estimated-cost observation and are never retried.
Provider/budget failures are re-raised only after leaving the raw handler, so fixed
exceptions have null `__context__` and `__cause__`; no raw provider text is exposed.

## r2 correction notes (review R1-F1/F2)

- **Deadline recheck (R1-F1):** the injected clock is read only after protocol
  preparation, genuine request serialization, reservation pricing and per-call cap
  validation, immediately before `client.complete`. Remaining time is floored to a
  positive integer timeout, bounded by the configured timeout and the adapter's 30 s
  maximum. A clock that crosses the deadline during preparation produces zero client
  calls and a `bridge_deadline_expired` observation.
- **Strict envelope decoding (R1-F2):** assistant content is decoded with the
  accepted adapter's strict `_json_object` helper, which rejects duplicate object
  keys, `NaN`/`Infinity` constants and non-finite numbers. Ambiguous envelopes such
  as `{"action": {...}, "action": {...}}` are therefore refused as
  `bridge_invalid_content`; the single billed call's usage, reservation and estimate
  observation is retained and nothing is retried. Well-formed action objects with a
  wrong role or malformed fields are still passed through unchanged so the loop can
  record the denial.
- Pinned private adapter helpers now also include `_json_object`; adapter changes to
  any pinned helper require re-review.

## Boundaries and limitations

- Fake transport/client tests only in this packet. The accepted real wrapper is
  exercised only against an owned scratch project with a synthetic key and a fake
  transport; no real credential file, socket, provider or Docker operation is used.
- The loop remains synthetic-only until Main's separately qualified runtime
  integration. This module does not change loop evidence labels or claim real
  provider results, sandbox qualification, formal freeze, acceptance or CI success.
- Pinned internal-helper dependency: a future adapter change to `_request_body`,
  `_PROMPT_OVERHEAD_TOKENS`, `_token_cost`, limits or model settings requires the
  bridge to be re-reviewed; the local envelope is not a provider-billing guarantee.
- A blocking injected callback cannot be forcibly interrupted by this synchronous
  bridge. Real deadlines/retries/aggregate dispatch belong to Main-owned bridges.
- The bridge records one observation per attempt, including pre-dispatch refusals;
  pre-dispatch refusals are not billed calls.

## Minimal verification

From the candidate overlay:

```
python -B -m unittest discover -s tests -p test_provider_bridge.py -v
```

The tests use scripted fake clients/transports, inert messages and one synthetic-key
scratch wrapper project. They cover successful translation, independent operation
histories, dispatch/deadline/cap refusals with zero calls, malformed content with one
call and retained resources, native tool-call rejection, sanitized failures, deadline
timeout flooring, wrapper reserve-before-call plus aggregate refusal, and protocol
context separation.
