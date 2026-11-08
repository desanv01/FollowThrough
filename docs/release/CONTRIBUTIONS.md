# Contribution ledger

| Date | Component | Contribution | Evidence/status |
|---|---|---|---|
| 2026-11-07 | Scope and methods | User-described workflow frustrations; two task families and controlled comparison designed through discussion | Approved plan; implementation underway |
| 2026-11-07 | Development process | Adapted user-supplied multi-model workflow from prior project; no previous project code transferred | AGENTS.md and private source archive |
| 2026-11-07 | Baseline | New Rust/Python evaluation project | No previous implementation foundation |
| 2026-11-07 | Retry fixture and controller | Codex Sol Medium workers implemented the original task, SQLite fault controls, Python grader and Rust controller; separate Sol High reviewers identified and verified corrections | Seven retry tests and seven Rust boundary tests; actual runner/grader controls; private revisioned receipts |
| 2026-11-07 | Preview-cache fixture | Human-launched DeepSeek Harness worker implemented the six-file fixture/grader/test overlay and its first correction; independent Sol High review identified remaining defects | DeepSeek r1/r2 deliveries preserved; original/v1/correct authored controls |
| 2026-11-07 | Preview-cache final correction | Codex Sol Medium corrected exception-argument preservation and two infrastructure-attribution paths after explicit ownership transfer; separate Sol High review passed | r3 delta changes only grader/tests; 26 cache tests and three controls; six files integrated byte-for-byte |
| 2026-11-07 | Provider adapter | Codex Sol Medium implemented the official DeepSeek protocol seam and finite diagnostic/5xx corrections; Sol High independently reviewed | 17 offline tests; no actual provider call or billing claim |
| 2026-11-07 | Integration | Codex main integrated accepted source and verified combined root-level Python collection/execution | 50 tests passed; corrected retry positive control passed through Rust; no live benchmark result |

Update with accepted source identities and actual checks. Human must review task requirements, reference behavior, grader controls and final claims before submission. The project is licensed under AGPL-3.0-only; third-party Rust crates retain their own licenses. No third-party assets or data were copied into task fixtures.

## Preparation status — 7 November 2026

This ledger was extended for release preparation against frozen baseline `166a304b32d92f66519b8b4074fee510cbffdaeb`; the rows above are the pinned original bytes and retain their attribution. Later work is recorded below only as **delivered but unaccepted**. `COMPLETE`/`COMPLETE_STOPPED` describes a worker stopping at a finite boundary; it does **not** mean integrated, independently accepted or shipped. No commits, professional biographies or third-party grants are asserted.

| Date | Workstream | Contribution | Pinned status |
|---|---|---|---|
| 2026-11-07 | Scenario package | Human-launched DeepSeek worker DS-SCENARIOS-01 delivered P1-SCENARIOS/r1 (scenario definitions, fallback publication, tests). 18 tests were run: 17 passed and 1 was skipped for missing symlink privilege. Independent review required P1-SCENARIOS/r2 corrections (fallback publication, safe output restriction, integrated test root, visible schedule/review wording, asset reparse guards). | `CHANGES_REQUIRED`; `NOT_INTEGRATED`; `NOT_ACCEPTED`. P1-SCENARIOS/r2 is `CORRECTION_READY`; human launch not confirmed. |
| 2026-11-07 | Isolation plan | Human-launched DeepSeek worker DS-ISOLATION-01 delivered P2-ISOLATION-PLAN/r1 (candidate isolation design and tests). 12 tests passed; separately disclosed platform branches were not exercised. Independent review required P2-ISOLATION-PLAN/r2 for a Windows mount path that could name a different directory from the hashed inventory. | `CHANGES_REQUIRED`; `NOT_INTEGRATED`; `NOT_ACCEPTED`. P2-ISOLATION-PLAN/r2 is `CORRECTION_READY`; human launch not confirmed. |
| 2026-11-07 | Budget and credential path | Human-launched DeepSeek worker DS-BUDGET-01 delivered P3-BUDGET/r2 (budget reservation and credential-fallback work, plus tests). 95 tests passed **including 17 copied adapter tests**, so this is not 95 newly authored budget tests. Review closed some exception-boundary and credential-fallback findings within r2 scope and left residual storage-admission and reservation findings requiring P3-BUDGET/r3. | `CHANGES_REQUIRED`; `NOT_INTEGRATED`; `NOT_ACCEPTED`. P3-BUDGET/r3 is `CORRECTION_READY`; human launch not confirmed. |

### Counting and attribution rules

- The accepted foundation is counted separately: **50 accepted Python tests** (7 retry + 26 preview-cache + 17 adapter) plus 7 Rust boundary tests. Do not add candidate test counts to it, and do not present any combined number as a new integrated total or a benchmark score.
- The budget candidate's 95 reported tests include 17 copied adapter tests; they overlap the accepted adapter suite and are not an additional independent budget suite.
- User contribution: motivating workflow experience and approved direction. Main: coordination, integration, acceptance and GitHub publication of the accepted baseline. Codex Sol Medium: original retry/controller/adapter implementation and the cache takeover. Sol High: independent reviews. Human-launched DeepSeek workers: cache, scenario, isolation-plan and budget candidates, each launched through posted prompts. Reviewer findings are not acceptance, and worker `COMPLETE` status is not integration.
- No author's professional experience, employment, credentials or endorsement is claimed; the human user has not been shown to have reviewed every artefact semantically. The selected project license is AGPL-3.0-only; it applies to the project's own code and documentation, while third-party implementations, data and assets retain their own terms. No third-party implementation, data or assets were added by this documentation packet.
