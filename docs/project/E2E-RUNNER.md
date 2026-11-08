# R1 end-to-end runner identity and live-mode seam (P4.1.1/P4.1.2 candidate)

Status: **offline candidate; completion of six-scenario/two-arm composition; no provider/grade/Docker execution.** Implementation: `episode/e2e.py`; tests: `tests/test_e2e_identity.py` (11) and `tests/test_e2e_scenarios.py` (12 cells).

`run_r1_smoke(...)` is the preserved R1/ordinary entry point. `run_scenario_smoke(..., scenario_id, arm, ...)` is the generic composition over the accepted six-scenario catalog and both arms. Both require actual model/check ports and an injected grade callback; neither selects an authored port or grade.

## Six-scenario/two-arm composition

- The generic runner passes `scenario_id` and `arm` to the accepted driver, derives the candidate filename (`processor.py` for Retry, `preview.py` for Cache), and preserves the episode/run/source/version identity in the row, report and binding.
- It rejects unknown scenario/arm values before dispatch.
- It uses the accepted scenario config/event schedule, including C2/C3 requirements-update and authored-receipt events; synthetic composition tests assert C2/C3 end at requirements v2.
- Public-check receipts and the host grade callback are injected by the caller. The runner does not synthesize a protected grade, and it does not call the grade callback when check receipts are missing or any cleanup is unknown.
- Twelve synthetic cells (six scenarios × two arms) build one report with 12 groups, all synthetic/development and strict-fail partial claims; this is composition evidence, not correctness or pilot evidence.

## Safety rules

- Actual ports required: `model_port` and `check_port` must be callable; no authored fallback/reference read.
- Unsafe check cleanup: no grade call; `infrastructure_error`/all-unknown row, `grade-skipped.json`, no protected-grade file.
- `live=True`: operation must carry `evidence_kind=real_provider`, nonempty `operation_id` and `run_id`, and the grade must be protected-container provenance; P3 owns forwarding these. `stage`/provenance come from the operation or caller; synthetic stays synthetic. A protected grade or real-provider label without live mode is refused.
- Identity mismatch: invalid/excluded, unknown outcomes; no guessing.
- Progress: sanitized `progress-events.jsonl`, `progress-meta.json`, `progress.json`/`.txt`; raw prompts/stdout/stderr never copied. Costs only from supplied operation metadata.
- No provider/ledger/Docker/candidate-host operation in this module. P3 is the dispatcher; P2 owns trusted check/grade.

## Files written

`report-input.json`, `report.json`, `report.md`, `protected-grade.json` (only when grade started), `episode-binding.json`, `progress-events.jsonl`, `progress-meta.json`, `progress.json`, `progress.txt`; unsafe-check stops add `grade-skipped.json` and no grade file.

