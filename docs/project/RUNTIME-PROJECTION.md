# Runtime report/trajectory projection (P4.1 candidate)

Status: **offline candidate, pending integration/independent review; no real row exists in the local campaign.** Implementation: `reporting/runtime_projection.py`, `reporting/trajectory_projection.py`; tests: `tests/test_runtime_projection.py`, `tests/test_trajectory_projection.py`.

## Report row binding

`project_episode(result, events, grade, source=..., evidence_kind=..., stage=...)` requires agreement between:
- `result.current_candidate_sha256` and `result.current_requirements_version`;
- supplied `source.candidate_sha256` / `source.requirements_version`;
- protected `grade.candidate_sha256` / `grade.requirements_version`.

A mismatch (or missing identity) produces an **invalid** row excluded from denominators; outcomes become unknown. The binding record (`episode-binding-0.1`) retains episode/run, candidate/version identities and `binding_match`. `run_id` and `operation_id` come only from the supplied result/operation; they are not fabricated from the episode ID. The attempt ledger owns operation/reservation settlement; the sidecar exposes the join keys.

Rows validate episode identifier grammar, the six accepted scenarios and the two accepted arms before returning, so malformed identity is refused rather than passed downstream. Elapsed time prefers an explicit cumulative `result.elapsed_ms`, then `operation.elapsed_ms`, then the last event's `elapsed_ms` as a documented fallback.

This corrects the earlier end-to-end row behavior: execution disposition is mapped independently (`completed_claim` stays `completed` even with a failing functional grade), provider/infrastructure stops are not collapsed to `partial`, workflow is not inferred from a completion claim, unknown token totals remain null, and estimated/reserved amounts come only from supplied operation data (never invented zero). The display trace is built from event type/role/status/action labels only; raw stdout/stderr/prompts are not copied. Malformed events without a type are skipped; `supplied_receipt` is classified as supplied-fixture provenance.

The row is a supplied-judgment projection, not authentication. Main/P3 must supply authentic runtime/grade receipts; the caller can still mislabel records.

## Checkpoint trajectory

`project_trajectory` consumes explicitly ordered checkpoint-grade receipts, preserving order and assigning sequence numbers without sorting. Each snapshot carries checkpoint ID, candidate SHA-256, version, optional counters and either a matching grade (`pass`/`fail`) or null when no semantic grade exists (cleanup unknown/provider/infrastructure). Candidate and grader-origin failures map to `fail` via the shared `reporting.outcomes.functional_from_grade` rule; provider/infrastructure failures remain ungraded. Duplicate IDs, mixed provenance, missing identity and invalid versions are refused. The result is validated by the accepted `trajectory.summarize_trajectory` and keeps observed-only/version-segment semantics.

Trajectory `evidence_kind` is a **grading-provenance axis** (`synthetic_development` / `protected_container_grading`), distinct from report-row provenance (`synthetic_development` / `real_provider`). A formal report row joins its trajectory only by `episode_id`; the two schemas must not be pooled by evidence-kind equality.

## Remaining gates

- G0 local baseline/integration is **PASS** at `68e97be`; this unit has local commit/PR/CI records, pending independent review and P6 merge.
- Authentic runtime adapter wiring into the accepted episode/grade path.
- Actual real row, checkpoint grading and qualified trajectories; independent review and Main acceptance.
- Analysis/freeze rules remain P0.3.2/P4.4 pending.
