# Offline evaluated-role episode loop

Status: P3-ROLE-LOOP/r1 candidate implementation. This is offline synthetic episode
control behind injected scripted model and check seams. It is not a live provider
bridge, container runner, hidden grader or formal benchmark result.

## API

```python
from episode.loop import run_episode

result = run_episode(
    config,
    model_port=model_port,     # request -> normalized response
    check_port=check_port,     # CHECK-PORT-v0.1 request -> receipt
    cancelled=None,            # optional () -> bool
    monotonic=None,            # optional () -> seconds (time.monotonic)
)
```

`config` fields are fixed and validated exactly:

- `episode_id`: ASCII `[A-Za-z0-9._:-]`, 1..64 characters.
- `scenario_id`: `R1`, `R2`, `R3`, `C1`, `C2`, `C3`.
- `arm`: `ordinary` or `structured_progress`.
- `workspace`: absolute existing flat agent directory.
- `output_dir`: absolute new directory whose parent exists, outside the workspace.
- `requirements_version`: `v1` or `v2` (initial authoritative version).
- `scheduled_events`: exact `{boundary,kind,version,text,source_sha256}` entries.
- `max_model_responses` (1..60), `max_tool_actions` (1..120),
  `max_elapsed_ms` (1..600000), `max_context_bytes` (1..60000) — integers, no bool.
- `evidence_kind`: exactly `synthetic_episode_control`.

Scenario/event rules are enforced before either port is called:

| Scenario | Initial version | Scheduled events |
| --- | --- | --- |
| R1/R2/R3 | v1 | none |
| C1 | v2 | none |
| C2 | v1 | one `requirements_update` before first O1 |
| C3 | v1 | the C2 update plus one `supplied_receipt` after first O1 response |

A C3 receipt whose `source_sha256` does not equal the initial candidate hash is
rejected with `EpisodeConfigError` before any model or check call. The authored
receipt is fixture context, never an executed check or review.

## Model seam (explicitly not the DeepSeek wire format)

`model_port(request) -> response`; `request` exact keys:

- `episode_id`
- `operation_id` = `episode_id:call:<1-based team call counter>`
- `role` (`O1`/`W1`/`R1`), `invocation_index`
- `messages`: permitted role history, system/user/assistant content only
- `allowed_actions`: frozen role/tool matrix

`response` exact keys: `action`, `usage`, `requested_model`, `returned_model`.
`usage` is `{input_tokens, output_tokens}` with nullable nonnegative integers; known
and unknown observations are aggregated separately and never converted to complete
zero totals. Responses above 65,536 UTF-8 bytes, missing/extra keys, invalid
types/usage or non-object actions terminate `protocol_error`. Parseable but invalid
actions become bounded tool-error observations and count as attempted actions.

Both ports receive deep copies; a port cannot mutate controller histories, state or
config. This sequential kernel cannot forcibly interrupt a blocking callback: real
request/check deadlines belong to future Main-owned adapters and bridges. No adapter,
budget ledger, socket or credential is imported or touched.

## Action matrix (frozen for this revision)

| Action | O1 | W1 | R1 | Structured only |
| --- | --- | --- | --- | --- |
| `read` | yes | yes | yes | |
| `write` | no | yes | no | |
| `check` | yes | yes | yes | |
| `delegate` (`target` exactly W1) | yes | no | no | |
| `request_review` (`target` exactly R1) | yes | no | no | |
| `report` | no | yes | no | |
| `review` | no | no | yes | |
| `finish` | yes | no | no | |
| `progress_read` | yes | yes | yes | yes |
| `progress_write` | yes | no | no | yes |

Wrong-role, wrong-target, malformed and stale-hash attempts are recorded as denied
attempts and returned as fixed tool errors to the same active role. The controller
never reroutes them, auto-delegates, auto-reviews, auto-checks or repairs a claim.
Delegation/review schedule the target's next invocation; W1 report or R1 review is
the only way control returns to O1. Only one role is active at a time.

## Workspace safety

The workspace is a flat allowlist: exactly one of `processor.py`/`preview.py`, plus
`public_checks.py`, `TASK.md`, `SPEC.md`, `WORKFLOW.md`, `HISTORY.json`. Files are
data, including Python source; nothing is imported, executed, evaluated or shelled
out. Per-file and total size bounds apply, path components and entries are checked
for symlinks/reparse points top-down, and every read/edit revalidates names,
regular-file status, size and reparse safety.

Reads return at most 32,768 UTF-8 bytes for the selected file. Candidate writes are
W1-only full replacements: content ≤65,536 UTF-8 bytes, the exact expected prior
SHA256 is required, the prospective inventory is bounded, and an owned staging file
in the protected output directory is atomically replaced. Before/after hashes and a
host-only `cp-<seq>.py` checkpoint are recorded outside the workspace. SPEC may be
replaced only by the controller's configured before-first requirements event.

Output creation refuses to reuse an existing directory or overlap the workspace;
`events.jsonl`, checkpoints and `result.json` are written with exclusive names. Event
or checkpoint write failure stops as `infrastructure_error` without claiming the
failed evidence; no PIDs, timestamps or billing fields are fabricated.

## Checks

`check` calls `check_port(request)` with the CHECK-PORT-v0.1 request fields
(`request_id` = `episode_id:check:<counter>`, host workspace, candidate filename,
actual candidate SHA256, current requirements version). Only
`evidence_kind="synthetic_check"` is accepted. Identity/schema/version/request
mismatch, invalid status/exit/cleanup, excessive output or an exception stops the
episode with `infrastructure_error`; a typed `timeout`/`output_limit`/
`infrastructure_error` receipt stops with its fixed reason, and `cancelled` stops
`cancelled`. A legal unknown cleanup on a non-passed/failed status stops
`check_cleanup_unknown`. Passed/failed receipts require settled cleanup and matching
exit codes; their stdout/stderr are untrusted observations only, returned with an
evidence ID. A public check pass is never promoted to semantic truth.

## Conditions

Ordinary episodes use the standing rules, task files and ordinary role histories.
Structured-progress episodes add `progress_read` for every role and `progress_write`
for O1. The exact record keys are `requirements_version`, `candidate_sha256`,
`findings`, `next_action`, `completion_obligations`; values are agent claims. It is
initialized once after before-first requirements events and before O1 with the
current objective version/hash and empty claims. Every update is preserved with its
serialized bytes/hash outside the workspace; stale or wrong claims are never
repaired or refreshed. O1's current record is rendered in its view on each
invocation, and its bytes count toward the context bound.

## Bounds and ordering

Before a model/check call: cancellation, elapsed limit, context bound (model only),
response/action limits. The final permitted non-finish action executes and its result
is retained; reaching the action cap then stops before any further model call.
A finish returned by a permitted call terminates without increasing the action
count. Context overflow stops `context_limit` rather than trimming or summarizing
standing rules. Blocking callbacks are not silently interrupted, restarted or
resumed.

C2/C3 update timing is fixed: the v2 SPEC replacement happens before the first O1
invocation; a C3 receipt is enqueued after the first O1 response is validated and
recorded, before its action or finish is handled. An immediate finish keeps the
receipt without an extra model call; cancellation before dispatch retains what
arrived and invents no action.

## Result

`result.json` keys: `schema_version="episode-local-0.1"`, `evidence_kind`,
`episode_id`, `scenario_id`, `arm`, `status`, `stop_reason`, `final_claim` (or null),
`current_candidate_sha256`, `current_requirements_version`, `counts`, `usage`,
`event_count`, `artifact_paths`, `limitations`.

Statuses: `completed_claim`, `partial_claim`, `limit_reached`, `provider_error`,
`infrastructure_error`, `protocol_error`, `cancelled`. A terminal claim is recorded,
not certified: missing checks/review, stale evidence IDs and unsupported completion
claims are deliberately preserved for later independent grading.

## Not implemented here

- No live DeepSeek/provider wire format, socket, key, credential, budget ledger or
  aggregate dispatch; a Main-owned bridge must add bounded deadlines and budget.
- No Docker/container check, candidate execution/import, hidden grading or P4
  projection; P3 r1 accepts only synthetic check receipts.
- No crash resume, process isolation, sanitization certification or semantic score.
- Stable-filesystem assumption for staging/atomic replace; source drift/check races
  outside that assumption are not silently claimed solved.
- Cross-episode concurrency, live role models and formal campaign runs are separate
  Main-owned work.

## r2 correction notes (review R1-F1…F8)

- Read observations and persisted read events expose the objective current candidate
  SHA256, requirements version, and the read file's own content SHA256 alongside the
  evidence ID. Check observations/receipts expose the request's actual candidate hash
  and version; write outcomes expose the resulting hash and current version.
  Requirements delivery messages carry the persisted requirements event ID and spec
  hash. Denied attempts return their persisted event ID with a fixed reason and never
  echo raw unknown action payloads. Check dispatch revalidates the workspace
  allowlist and hashes the candidate immediately before the CHECK-PORT seam.
- Structured progress updates persist the full bounded record with its SHA256 and
  byte length outside the workspace; the initial record is persisted too. Every model
  response event records serialized visible-context bytes and structured-progress
  bytes separately. Records use the common bounded finding shape and the frozen
  response/context limits; there is no extra arm-specific record-size or finding-count
  ceiling. False/stale claims and obligations are preserved, never repaired.
- Assistant history stores the bounded normalized action before any C3 receipt is
  enqueued, so the visible order matches the disk order (`model_response`,
  `supplied_receipt`, then action dispatch). The supplied receipt's authored text,
  SHA256, byte length and `supplied_fixture` provenance are persisted; the visible
  message names the persisted evidence ID and does not claim consumption.
- A valid unknown check cleanup is decisive: the result is
  `infrastructure_error/check_cleanup_unknown` even when cancellation or the elapsed
  bound fires during the same callback. The typed receipt and a boundary observation
  are retained; no further operation runs.
- `workspace` and `output_dir` must be absolute before normalization. Workspace and
  output components are checked top-down for symlink/reparse before any directory
  predicate, listing, read or creation; output existence is checked after the guard
  and before creation. Replacing a file with stale/size-invalid input is a policy
  denial, while staging/atomic-replace I/O failure is an infrastructure stop.
- Unpaired surrogates or other unserializable model text become bounded
  `protocol_error`; invalid check output UTF-8 becomes
  `infrastructure_error/check_invalid_receipt`. A parseable action object with bad
  fields is a bounded denied attempt that permits a later valid same-role turn.
  Nullable nonnegative integer usage has no uncontracted magnitude ceiling.
- Validated config (including nested events) is snapshotted, so caller mutation cannot
  change bounds or schedules. Bounds apply in the frozen order cancellation, elapsed,
  context, response, action, preserving the final permitted observation without an
  extra call.
- After a successful replacement the actual new source hash is recorded before any
  fallible event append; an append failure therefore reports the real bytes and does
  not invent checkpoint/event IDs. Checkpoints are listed only after their event
  persists.

## Tests

From the candidate overlay:

```
python -B -m unittest discover -s tests -p test_episode_loop.py -v
```

The suite uses scripted model responses, synthetic check receipts and inert synthetic
workspaces only. It covers the full team flow, no-edit completion, unsupported
claims, failed checks and repair, denied wrong-role/target/stale-hash actions,
role-view separation, both arms/progress/usage unknowns, C2/C3 ordering and
immediate finish, check and port failures, all bounds/cancellation/context cases,
filesystem safeguards, write-failure honesty and deterministic IDs.
