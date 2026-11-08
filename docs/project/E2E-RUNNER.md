# R1 end-to-end runner identity seam (P4.1.1 candidate)

Status: **offline candidate; no provider/grade/Docker execution.** Implementation: `episode/e2e.py`; tests: `tests/test_e2e_identity.py`.

`run_r1_smoke(run_root, *, episode_id, check_port=None, model_port=None, grade_fn=None, ...)` composes the accepted episode driver, an injected check port and an injected host grade callback. It preserves the reviewed `episode_id` in the driver result, report row, binding and saved records; it refuses invalid identity and never substitutes the scripted `ds-r1-scripted` identity.

- The default model/check ports are authored synthetic fixtures; a protected grade supplied via `grade_fn` is labelled `real_provider` at development stage unless the caller supplies another stage/evidence label. A synthetic grade stays `synthetic_development`/development.
- The row is produced by the reviewed `reporting.runtime_projection`; candidate/requirements/source identity must agree or the row is invalid/excluded. Evidence receipts must carry the exact candidate/version to pass.
- No protected grade is invented: `grade_fn` is required. No provider, credential, Docker, candidate-host or network operation occurs in this module.
- The P3 live driver can call this runner with `episode_id`, `model_port`, `check_port`, `grade_fn`; P3 remains the paid operator and must supply the trusted bridge/grade and reviewed bounds.
- This fixes P3's `runner_episode_identity_unsupported` seam only at source level; P2/P3 integration, canonical ledger, G3 runtime qualification and G4 authorization remain pending.

Durability: the runner writes `report-input.json`, `report.json`, `report.md`, `protected-grade.json`, `episode-binding.json`, plus sanitized `progress-events.jsonl`, `progress-meta.json`, `progress.json` and `progress.txt` under the run root. Progress events contain only bounded type/role/status/action/elapsed/evidence fields—never raw prompts or stdout/stderr—and can be read by `python -B -m reporting.progress --events ... --meta ...`; `progress.json`/`progress.txt` are the same rendered snapshot. Estimated/reserved costs come only from supplied operation metadata and stay unknown when absent. Live host observation and terminal smoke remain P3/Main-gated; this is a safe snapshot, not proof of a live provider run.
