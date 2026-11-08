# R1 end-to-end runner identity and live-mode seam (P4.1.1 candidate)

Status: **offline candidate; no provider/grade/Docker execution.** Implementation: `episode/e2e.py`; tests: `tests/test_e2e_identity.py` (11 tests).

`run_r1_smoke(run_root, *, episode_id, check_port, model_port, grade_fn, ...)` composes the accepted episode driver, an injected check port, the actual model port and an injected host grade callback. It preserves the reviewed `episode_id` in the driver result, report row, binding and saved records; it refuses invalid identity and never substitutes the scripted `ds-r1-scripted` identity.

## Safety rules

- **Actual ports required:** `model_port` and `check_port` are mandatory and callable. The runner has no authored model/check fallback; the `scripted_r1_model_port` helper exists only for tests and is never selected automatically. Authored reference source is not read by the runner.
- **No grade after unsafe checks:** if there is no check receipt or any check receipt has `cleanup != settled`, the runner does **not** call `grade_fn`. It writes an `infrastructure_error`/all-unknown row, a binding with `grade_skipped_reason`, `grade-skipped.json`, and progress receipts. No `protected-grade.json` is written.
- **Provenance:** evidence kind comes from the supplied operation (`operation.evidence_kind`) when provided, otherwise from the actual protected grade receipt; synthetic stays synthetic. In `live=True` mode the operation must carry `evidence_kind=real_provider`, an `operation_id` and a `run_id`; otherwise the runner refuses before dispatch. `stage` comes from the operation or caller; default development.
- **Identity:** row/binding preserve episode, run/operation IDs, candidate SHA-256 and requirements version. Result/source/grade mismatch produces an invalid/excluded row with unknown outcomes; no guessing.
- **P3 contract:** `episode.live.run_live_r1` passes `episode_id`, `model_port`, `check_port` and `grade_fn`; the integration test plus a fake-port campaign smoke prove the identity is preserved and a 9-call report row is produced. This is **not** provider evidence.
- **Progress/report:** sanitized `progress-events.jsonl`, `progress-meta.json`, `progress.json`/`progress.txt` and report files are written. Progress fields are bounded type/role/status/action/elapsed/evidence only; raw prompts/stdout/stderr are never copied. Costs come only from supplied operation metadata and remain unknown when absent.
- **No provider/ledger/Docker:** this module never launches them. P3 remains the paid operator; P2 owns the trusted bridge/grade; G2/G3/G4 and the canonical ledger remain pending.

## Files written

`report-input.json`, `report.json`, `report.md`, `protected-grade.json` (only when a grade was actually started), `episode-binding.json`, `progress-events.jsonl`, `progress-meta.json`, `progress.json`, `progress.txt`; unsafe-check stops add `grade-skipped.json` and no grade file.
