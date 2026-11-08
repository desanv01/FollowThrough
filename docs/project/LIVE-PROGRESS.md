# Live progress presentation (P4.5.4 offline candidate)

Status: **offline renderer candidate; no live host observation or real runtime row tested.** Implementation: `reporting/progress.py`; tests: `tests/test_progress.py`.

`python -B -m reporting.progress --events PATH [--result PATH] [--meta PATH] [--json]` reads explicit sanitized event/result receipts and prints a stable one-line or JSON status. It does not poll, watch, dispatch, retry or infer success.

Displayed fields: episode/run, scenario/arm/stage, evidence kind, latest role, supplied state, cleanup label, event count, last event type, model responses/actions/writes/checks/reviews/requirement-updates, elapsed milliseconds, estimated cost and reserved amount separately (unknown remains unknown; never billed cost), last evidence IDs, and saved event/result/report paths. No raw stdout/stderr, prompt, credential or arbitrary event text is displayed; event type/role/status/action labels are sanitized and bounded.

Early stops are displayed as supplied (`provider_error`, `infrastructure_error`, `limit_reached`, `partial_claim`, etc.); they are not converted into completion or success. The renderer is not a substitute for P3 host telemetry: an actual live progress/terminal smoke remains a Main/P3 gate. Unicode/resize refinement and optional palette remain deferred.
