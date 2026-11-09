# Limitations and open boundaries

**Preparation status — 7 November 2026 (r2 correction).** Prepared for P6-RELEASE-PREP/r1 and revised by the bounded r2 documentation correction against the same frozen baseline commit `166a304b32d92f66519b8b4074fee510cbffdaeb`. This is a release-preparation document, not a results report, a final claim set or a statement that the campaign ran. Facts below come from the pinned release-preparation baseline (the Main-owned working-tree input `docs/project/RELEASE-PREPARATION-BASELINE.md`, which is **not yet in the baseline commit**), [METHODS.md](METHODS.md), [ACCEPTANCE.md](../project/ACCEPTANCE.md) and the public guides. Later unaccepted worker deliveries are named only as delivered work, never as integrated behaviour. Main owns every final release claim; this draft must be replaced or re-confirmed after qualification.

## 1. Design scope: two families, six configurations, one small study

FollowThrough targets **two task families**: retry-safe account credits and preview caching. The six planned scenarios are configurations within those families, not six independent domains and not evidence of general software-engineering ability. A proposed campaign of **36 episodes** would cross 6 scenarios × 2 workflow arms (ordinary progress versus structured progress) × 3 repetitions. That design is proposed, not frozen, and no episode has been run.

Consequences:

- Thirty-six episodes over six related configurations cannot support broad claims about agents, providers or software repair in general.
- Repeats within a scenario are not independent domains. Report raw per-scenario outcomes rather than treating repetitions as a generalisation sample.
- Two families limit external validity by construction. Any conclusion should be phrased as a statement about these authored repairs under the frozen harness, if a campaign is ever accepted.
- The workflow-arm comparison is a package comparison. It is not a test of a model's intrinsic capability and must not become a model ranking.

## 2. Evidence status: fixtures and checkers, not model evidence

The accepted foundation is narrow and local. It contains authored repair fixtures, independent Python functional graders, a Rust trusted-control runner for the retry family, and an offline-tested DeepSeek protocol adapter. The accepted Python total is **50 tests** (7 retry + 26 preview-cache + 17 adapter). The Rust controller has 7 boundary tests plus actual trusted controls.

These are **checker and fixture results**. The graders execute authored candidate code under local permissions; they never call a model, never measure a model's repair trajectory and do not implement full workflow scoring. `evidence_kind: real_local_control` means trusted local control grading, not frontier-model performance. The original, v1-intermediate and correct candidates are authored controls; they are not live repairs. No current end-to-end evaluation result exists, and no test count may be presented as a benchmark score.

Functional grading covers only part of the intended outcome model. [METHODS.md](METHODS.md) distinguishes functional correctness, workflow adherence, verification validity and completion accuracy; joint success would require all four. Only the functional-checker portion exists today. An accurate partial report can fail completion without being a false completion claim, and the current grader cannot decide that.

## 3. Supplied history and agent-session independence

The planned evaluated roles (O1, W1, R1) are separate sessions from the development coordinator and workers, and evaluated sessions must not inherit development instructions, private packets or reference solutions. Separate session identities are a design control, not proof of independence:

- Sessions may share the same provider, model alias, host, operator and supplied history. Statistical independence across episodes is not established.
- "Supplied history" is fixture context authored by the project, not fabricated live tool output. It must be labelled as supplied whenever shown.
- A model acting as a reviewer is not the independent functional grader. A worker's self-reported success, including `COMPLETE`, is not ground truth.
- No claim is made that the user semantically reviewed every grader, reference or scenario. User direction and motivating experience are credited; exhaustive semantic review is not.

## 4. Intervention overhead and causal limits

The structured-progress arm adds context and maintenance work. Its extra tokens and time are part of the treatment and must be counted in comparisons, not treated as free. Structured progress may restate visible facts and agent-generated findings only; it must retain mistaken updates and may not use a hidden oracle.

Even a completed campaign would not justify saying that structured progress or JSON alone *causes* improvement. Possible confounds include prompt length, additional review time, repeated exposure to the task, and the three-repetition design. A prose-recap ablation was described as optional and has not been designed. Any effect remains a workflow-package observation under this harness.

## 5. Runtime and isolation boundaries

The current host commands are **not a sandbox**. The Python grader imports and executes trusted candidate code with local permissions. Path checks reject parent traversal, symlinks and Windows reparse points in selected paths, but assume a stable filesystem and do not defend against concurrent replacement or hard-link aliasing. On timeout or output failure the runner attempts to reap only the direct child; descendants are outside cleanup scope. Interrupting the runner may leave an incomplete run directory.

No protected execution environment for arbitrary model-generated code has been accepted. The isolation plan (P2-ISOLATION-PLAN/r1) was delivered with 12 passing tests, but its review requires an r2 correction and it is **not integrated or accepted**; its separately disclosed platform branches were not exercised. Until a container or equivalent is qualified with a sanitized environment, no network, limited resources and no grader/secret mounts during agent actions, untrusted candidates must not run through the current commands. The live adapter path is also unqualified: no paid request has been made, and no actual model identity, tool loop or campaign budget enforcement has been demonstrated.

## 6. Conservative reservations and billing limits

The official DeepSeek API route was selected and a cumulative **US$20** cap was authorised, with `.env.local` designated as the credential source. No paid request has been made. The adapter is a one-request protocol client: it reserves twice the serialized request byte count plus 4,096 tokens at the peak uncached input rate, plus the full `max_tokens` at the peak output rate. That envelope is conservative but **conditional** on documented assumptions — provider tokenisation, server-inserted prompt size, `max_tokens` capping all billed output including reasoning, and configured rates bounding the returned model's charges. It is not a billing guarantee and does not reconcile charges.

The adapter does not implement a cumulative campaign budget. Per-request reservations are not an aggregate ledger, and an uncertain outcome after dispatch retains its reservation rather than proving no charge. Rates and token semantics must be reconfirmed before any paid operation. The budget work (P3-BUDGET/r2) reported 95 passing tests including 17 copied adapter tests, but review requires an r3 correction; residual storage-admission and reservation findings remain, and the contribution is **not integrated or accepted**. Credential availability and qualified budget composition are still pending. Do not add the 95 tests to the accepted 50-test total.

## 7. Infrastructure, release and historical-rule dispositions

- Repository baseline is published on `main` at the pinned commit; GitHub is the source-delivery route. No CI workflow is configured. CI is planned as a later sequential P6 assignment, not part of this packet.
- No Docker operation, deployment, desktop application or local GPU hosting is part of the current evidence path for the core CLI benchmark.
- The project is licensed under MIT (see `LICENSE` and `NOTICE`). Third-party code assets retain their own terms; no third-party code or assets were copied into the fixtures.
- Private coordination, `.env` files, raw provider traces and unrelated material must stay out of any public package.
- The deadline, ZIP size, root-README and ≤5-minute video limits in earlier planning are **historical and were not reverified** by this preparation pass. Eligibility, consent and field requirements are unverified. They must be rechecked against the official rules before any submission.

### Failure dispositions

The frozen method ([METHODS.md](METHODS.md), “Data freeze and reporting”) separates two classes of failure rather than treating every unsuccessful attempt alike. Candidate syntax or runtime failures remain candidate outcomes: record them against the attempt and do not relabel them as provider, authentication or container problems. Provider, authentication or container failures are infrastructure events, recorded and reported separately from candidate behaviour. Every attempt is retained and reported, including poor or incomplete outcomes; an arm is not selectively rerun merely because its first observed outcome was unfavourable. This preparation adds no retry budget, replacement-run rule or denominator policy beyond that distinction, and it does not claim that formal settings, retry rules or denominators are already frozen. Those remain gates for the accepted campaign design.

## 8. Present limitations versus future acceptance gates

| Present limitation (as of the pinned baseline) | Gate that would change it |
|---|---|
| No live provider call; adapter offline-tested only | Authorised readiness request with reviewed credential loading; returned model identity and usage recorded |
| No aggregate campaign budget or charge reconciliation | Reviewed aggregate ledger, bounded spending scope and reconciliation procedure |
| No qualified protected execution for untrusted code | Accepted isolation/executor with sanitized environment, no network, resource limits and protected grader records |
| Scenario package delivered but not accepted | P1-SCENARIOS/r2 correction delivered, reviewed and accepted |
| Isolation plan delivered but not accepted | P2-ISOLATION-PLAN/r2 correction delivered, reviewed and accepted |
| Budget work delivered but not accepted | P3-BUDGET/r3 correction delivered, reviewed and accepted |
| No executor, role loop or frozen campaign | Integrated executor, frozen scenarios/prompts/settings and a separately authorised campaign |
| No formal results or reporting | Protected grading, all attempt dispositions, sanitised results and Main acceptance of claims |
| No video or submission package | Licence is MIT; recorded ≤5-minute video; eligibility/rules recheck; authorised submission |
| Six related configurations only | A broader, separately designed study — not implied by this release |

Nothing in this document grants permission to run paid evaluation, publish, upload or submit. Those remain Main/user decisions under the project controls.
