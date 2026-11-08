# Offline synthetic check-receipt projection

Status: P3-CHECK-RECEIPT/r1 candidate source for the offline part of P3.6.3.
`episode/check_receipt.py` maps a trusted **synthetic executor control** receipt to
the loop's accepted `check-port-0.1` response. It performs no I/O, launches nothing,
reads no files and accepts only `synthetic_executor_control` evidence. Qualifying or
invoking an actual executor/container remains a Main-owned integration and
qualification operation; this module does not claim one.

## API

```python
from episode.check_receipt import (
    CheckReceiptProjectionError,
    project_synthetic_receipt,
)

response = project_synthetic_receipt(
    request,                      # check-port-0.1 request
    receipt,                      # trusted container-check-0.1 receipt
    expected_run_id=run_id,       # fresh trusted run ID bound by the caller
    public_stdout=public_stdout,  # separately approved display text
    public_stderr=public_stderr,
)
```

The caller owns launching the executor, binding a fresh trusted run ID to the check
request and its requirements version, and supplying safe public checker output. The
executor receipt does **not** contain a requirements version; the projection preserves
the request's version through the caller's operation binding. `public_stdout` and
`public_stderr` are the only text copied to the response; they must be UTF-8 strings
within 32,768 bytes each.

## Request validation

The request must contain exactly the check-port request keys
(`request_id`, `workspace`, `candidate_filename`, `candidate_sha256`,
`requirements_version`). `request_id` is a bounded ASCII identifier; `workspace` must
be an absolute path string (no NUL, not accessed); the filename must be `processor.py`
or `preview.py`; the SHA-256 must be lowercase 64-hex; version must be `v1` or `v2`.
Malformed requests raise `CheckReceiptProjectionError("invalid_check_request")` (or
`invalid_expected_run_id`) without echoing raw data.

## Receipt binding

A receipt is accepted only when all of the following hold:

- `schema_version == "container-check-0.1"` and
  `evidence_kind == "synthetic_executor_control"` (production labels are rejected, not
  upgraded);
- `run_id` exactly equals the trusted `expected_run_id`;
- `workspace.candidate_file` equals the request filename and
  `workspace.candidate_sha256` equals the request SHA-256;
- `status` is one of the executor statuses (`check_passed`, `check_failed`, `refused`,
  `timeout`, `output_limit`, `cancelled`, `infrastructure_error`);
- every command observation has boolean `spawn_failed`/`direct_child_reaped`/
  `reader_settled`; every spawned command has both settlement observations true;
- cleanup is either a settled state with verified removal of the owned container, or
  the executor's explicit settled `not_created` marker for a non-pass/fail status with
  a null container;
- a passed/failed status additionally has an observed integer public exit consistent
  with the status, a matching integer `container.attached.exit_code`,
  `container.attached.direct_child_reaped == true`, and at least one command.

Anything invalid, mismatched, production-labelled, unbounded or unobserved fails
closed to an `infrastructure_error` with unknown cleanup and empty output, preserving
the request ID, source hash and requirements version. Unknown cleanup can never yield
a pass or fail.

## Status and exit mapping

| Receipt status | Check-port status | exit_code |
| --- | --- | --- |
| `check_passed` | `passed` | 0 (must be observed and settled) |
| `check_failed` | `failed` | observed nonzero integer |
| `timeout` | `timeout` | observed integer or null |
| `output_limit` | `output_limit` | observed integer or null |
| `cancelled` | `cancelled` | observed integer or null |
| `refused` | `infrastructure_error` | observed integer or null |
| `infrastructure_error` | `infrastructure_error` | observed integer or null |

Cleanup maps to `settled` only through the rules above; otherwise `unknown`. A valid
timeout/output-limit/cancelled/infrastructure_error receipt retains its category with
unknown cleanup when settlement was not verified. A valid `not_created` pre-create
failure is settled. A public check exit 0 is not a hidden functional grade, workflow
completion or permission to skip review.

## Output

The response has exactly the check-port response keys: `schema_version`
(`check-port-0.1`), `request_id`, `evidence_kind` (`synthetic_check`),
`candidate_sha256`, `requirements_version`, `status`, `exit_code`, `stdout`, `stderr`
and `cleanup`. It contains no host paths, workspace inventory paths, image/container
identities, owner tokens, command records, argv, PIDs, tokens, diagnostics or raw
executor records.

## r2 correction notes (review R1-F1/F2)

- **R1-F1:** passed/failed consistency now mirrors the pinned executor classifier:
  the attach CLI exit may be `0` **or** the inspected public exit. A settled
  `check_failed` receipt with attach exit 0, public exit 7, verified removal and
  reaped child is preserved as `failed` with exit 7. Any other attach/public mismatch
  is still refused as infrastructure failure with unknown cleanup.
- **R1-F2:** the receipt status is type-checked before map lookup, so a malformed
  status such as `[]` returns the bounded synthetic `infrastructure_error` with
  unknown cleanup and empty output instead of raising. Status/exit consistency,
  binding and settlement rules are unchanged.

## Boundaries and limitations

- Offline projection only; no executor was launched or qualified, and no Docker,
  network, credential, provider or real-container evidence is involved.
- The trusted caller must not pass a forged production label; this function refuses
  `real_container_check`. Real receipt integration and container qualification belong
  to Main.
- No independent semantic grading, P4 projection, persistence or budget behavior is
  added here. Stable-filesystem/executor assumptions remain with the executor.
- The function intentionally reads no files and follows no receipt paths; any display
  text must be approved and bounded by the caller.
- Deferred hardening: broader schema variants from future executor revisions and
  live-receipt integration tests.

## Minimal verification

From the candidate overlay:

```
python -B -m unittest discover -s tests -p test_check_receipt.py -v
```

The tests use authored in-memory fixtures and cover successful/failed checks,
pre-create failure, unknown cleanup with retained failure categories, source/run and
candidate mismatches, production-label refusal, unsettled commands and inconsistent
exits, `not_created` restrictions, bounded/UTF-8 display output, malformed requests,
and a no-file-read check.
