# Integration contracts v0.1

Main-owned. Workers propose changes instead of editing concurrently.

## Local Python grader interface
`python grader/retry_processing.py --candidate <absolute-or-relative processor.py> --output <new JSON file>`
Trusted local candidate control evaluation only. Exit 0 = functional grade produced (including functional FAIL); nonzero = candidate execution or grader/infrastructure error. JSON fields: schema_version "0.1", task_family "retry-processing", candidate {path,sha256}, evidence_kind "real_local_control", functional_pass boolean, checks array of {id,passed,detail}, errors array. Do not claim workflow completion from a functional grade.

`python grader/retry_processing.py --self-test` validates reference and deliberately incorrect fixtures. Fixture owner supplies exact documented checks.

Completed error-free retry grades must contain these IDs exactly once each (order immaterial): new_delivery, duplicate_replay, conflicting_units, tenant_and_event_identity, malformed_items, mixed_batch, rollback_after_credit, rollback_after_mark, interrupted_batch_retry, ack_loss_reopen_retry. Missing/duplicate/unknown IDs are invalid complete grades. Nonzero execution receipts may contain partial checks and errors with origin candidate/grader; preserve these for diagnosis, never promote them to functional pass.

## Runner local-control command
The preview-cache grader uses the same envelope with task_family "preview-cache", --candidate <preview.py> and --output <new JSON path>. Its 13 check IDs and task semantics are documented in the cache SPEC and CACHE-CONTRACT.md. It runs directly; the initial Rust controller supports only retry-processing.

`followthrough control --project-root <path> --candidate <path> --python <executable> --output-dir <new path> [--timeout-seconds 60]`
Rust invokes the Python grader, snapshots candidate, records actual command/outcome, emits summary.json and events.jsonl. This is a control harness, NOT model inference or sandboxing. Run directories cannot overwrite existing runs. Paths cannot escape selected project for grader/candidate; reject symlink/reparse traversal where enforceable and document remaining limits.

## Event common fields
schema_version "0.1", run_id, seq (strictly increasing), event_type, timestamp_utc, evidence_kind, payload. Timestamp may be explicit Unix UTC milliseconds if labeled. Actual exit/PID only when observed. stderr/stdout bounded. No credentials. Checkpoints and final grader results distinct.

## Model protocol seam (future)
Provider adapter owns HTTP/auth outside task sandbox. Evaluated tools require workspace-limited read/edit/exec, explicit role/session identity, delegation and review events. Private development instructions never mounted. Shared aggregate budget covers all roles. Formal experiment blocked until isolation and live path qualified.
