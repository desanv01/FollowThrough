# Formal campaign analysis plan — development draft

**Status: DEVELOPMENT DRAFT — NOT THE FINAL EXPERIMENT FREEZE.** Prepared as documentation
preparation under P5-ANALYSIS-PREP/r1 (owner DS-ANALYSIS-01). No formal episode has been run, no
provider call was made, and nothing here freezes methodology, campaign size, settings or outcomes.
Main owns the freeze decision and the final wording of every accepted claim.

**Evidence kind:** `documentation_preparation`. There are no observed formal results, no live
provider result and no analysis conclusion in this document.

**Pinned inputs.** Baseline commit `166a304b32d92f66519b8b4074fee510cbffdaeb`. The eleven inputs
pinned for this preparation were verified at their pinned byte and SHA-256 identities. Four of them
(`AGENTS.md`, `docs/project/ACCEPTANCE.md`, `docs/project/PLAN.md`, `docs/research/METHODS.md`) are
committed at that baseline; seven (`WORKING-RULES.md`, `docs/project/DEADLINE-MODE.md`,
`docs/project/REPORTING-CONTRACT.md`, `docs/project/REPORTING-NUMERIC-CLARIFICATION.md`,
`docs/release/REPRODUCTION-CHECKLIST.md`, `docs/research/LIMITATIONS.md`, `scenarios/catalog.json`)
are separately pinned working-tree inputs that the baseline commit does not contain. A future freeze
manifest must pin both layers; do not describe every pinned input as committed at `HEAD`.

## 1. Study design

### 1.1 Two authored task families, six scenario configurations

The six scenarios are configurations of two families, not six independent domains.
`scenarios/catalog.json` is the authoritative preparation source; the source bytes and SHA-256 for
each initial candidate are recorded there and are not restated here.

| Scenario | Family | Initial candidate (catalog path) | Requirement version | Authored event / designed role |
|---|---|---|---|---|
| R1 | retry-processing | `tasks/retry-processing/processor.py` | `retry-v1` | Original defect; no history finding |
| R2 | retry-processing | `references/retry-processing/mark-first/processor.py` | `retry-v1` | Authored HISTORY finding: an event may be recorded complete before its credited effect is durable. Fixture data, not a live test result |
| R3 | retry-processing | `references/retry-processing/correct/processor.py` | `retry-v1` | Correct candidate requiring verification; no history finding |
| C1 | preview-cache | `tasks/preview-cache/preview.py` | `cache-v2` | Final requirements supplied upfront; no events |
| C2 | preview-cache | `references/preview-cache/v1/preview.py` | `cache-v1` then `v2` | Event 1 before the first orchestrator invocation: the v2 checkpoint supersedes the visible v1 text |
| C3 | preview-cache | `references/preview-cache/v1/preview.py` | `cache-v1` then `v2` | Event 1 as in C2, plus event 2 after the first orchestrator response and before dispatch or completion: authored v1 worker receipt, explicitly not evidence of current v2 completion |

Family-specific materials (candidate filename, public checks, workflow template, hidden grading
route) are defined only in the catalog. Supplied history, receipts and candidates are authored
fixtures; they are not live model output. C2 and C3 must deliver the v2 requirements before the
first orchestrator invocation; the C3 receipt may not replace or downgrade them.

### 1.2 Arms: a workflow-package comparison

- **`ordinary`** — the evaluated team works without a project-maintained structured progress record;
  the record is the ordinary conversation and tool history.
- **`structured_progress`** — the team maintains a structured progress package that may restate
  visible task facts and agent-generated findings only. It must retain mistaken updates rather than
  silently correct them from an oracle, and its additional context and maintenance cost is part of
  the treatment.

The comparison is a **workflow-package comparison**. It is not a test of a model's intrinsic
capability, not a model ranking, and not evidence that JSON or structured records by themselves
cause improvement. A prose-recap ablation was described as optional and is not part of the proposed
design.

### 1.3 Fixed within-scenario settings

Everything except the workflow package above must be identical between the two arms of a scenario.
The following must be fixed before formal execution and recorded in the freeze manifest:

| Setting | Required treatment across arms | Freeze status |
|---|---|---|
| Task instance, initial candidate bytes, requirement version and event schedule (including C2/C3 updates) | Identical within scenario | Main freeze |
| Role prompts and the visible task request | Identical except for the arm's workflow package | Main freeze |
| Role model alias, returned model identity and decoding/settings | Identical within scenario; recorded actual identity | Main freeze |
| Tool set, tool limits and workspace layout | Identical | Main freeze |
| Per-role and team token/cost/time limits | Identical | Main freeze |
| Timeout, retry and exclusion rules | Identical and preregistered | Main freeze |
| Functional/workflow/evidence/completion predicates and checkpoint definitions | Identical; hidden from evaluated roles | Main freeze |
| Execution environment and isolation revision | Identical | Main freeze |
| Reporting projection and bridge revision | Identical | Main freeze |
| Trial numbering and interleaved run order | Preregistered; no mid-campaign change | Main freeze |

Within-scenario settings that cannot be held identical make the comparison invalid; record the
deviation rather than switching methods silently.

### 1.4 Proposed size and order (not a frozen commitment)

The existing proposal is **6 scenarios × 2 arms × 3 repetitions = 36 episodes**. That is a proposal
only. Actual campaign size, repetitions and settings require a Main freeze after pilot qualification
and budget checks; a worker does not decide campaign breadth.

Proposed interleaving (one cell = scenario × arm once; `O→S` means ordinary then structured, `S→O`
the reverse):

| Repetition | Scenario/arm sequence |
|---|---|
| 1 | R1 O→S, C1 S→O, R2 O→S, C2 S→O, R3 O→S, C3 S→O |
| 2 | C1 O→S, R1 S→O, C2 O→S, R2 S→O, C3 O→S, R3 S→O |
| 3 | R2 S→O, C1 O→S, R3 O→S, C2 S→O, R1 O→S, C3 S→O |

Purpose: balance each arm against time, host drift and budget consumption instead of running all
ordinary cells before all structured cells. The exact order, any randomisation seed and any
concurrency rule stay a Main freeze decision. Development and pilot evidence must not be pooled
with formal evidence; the reporting projection groups by `evidence_kind` and `stage` for that reason.

### 1.5 Dependence among trials and scenarios

Cells are not independent samples. Repetitions share the same scenario package, models, tools,
operator, host and time window; scenarios share a family and largely share fixtures. The three
retry scenarios exercise one authored fixture family and the three cache scenarios another. Treating
the 36 episodes as a generalisation sample or as independent evidence about software repair would be
wrong. The analysis therefore reports raw per-scenario/arm counts and presents any comparison as a
small authored-study observation. Within-scenario arms are paired by repetition in presentation, but
this pairing does not establish statistical independence.

## 2. Outcome and denominator conventions (pinned)

These conventions are adopted from the pinned offline reporting projection contract v0.1
(`report-input-0.1` / `report-output-0.1`) and its numeric clarification. They are the declared
reporting convention, not permission to hide attempts, and the later freeze must adopt or version
them explicitly.

### 2.1 Four separate dimensions

Every episode carries exactly four supplied independent-judgment dimensions, each `pass`, `fail` or
`unknown`:

- **functional** — behavioural correctness under the functional grader;
- **workflow** — adherence to the required role/workflow package;
- **evidence** — validity of the verification/evidence the team relied on;
- **completion** — accuracy of the completion or partial-completion claim (an honest partial can
  have `completion=pass`).

Component truth must come from independent grading and executable predicates, never from an agent's
self-reported success or from the prose of a trace summary. The reporter summarizes supplied
judgments; it does not establish truth. Only part of this model is implemented today: the functional
graders exist, while workflow, evidence and completion predicates still require Main-owned
implementation and freeze.

### 2.2 Attempt dispositions and denominators

All dispatched attempts are retained and counted. Group only by
(`evidence_kind`, `stage`, `scenario_id`, `arm`); never pool synthetic with provider rows or pilot
with formal rows.

| Status | Counted in `attempted` | Eligible for primary component/strict denominators | Treatment |
|---|---|---|---|
| `completed` | Yes | Yes | Component outcomes decide strict outcome |
| `partial` | Yes | Yes | Eligible; strict outcome fails because execution is incomplete |
| `limit_reached` | Yes | Yes | Eligible; strict outcome fails because execution is incomplete |
| `provider_error` | Yes | No | Excluded from primary denominators; retained in `excluded` and episode detail |
| `infrastructure_error` | Yes | No | Excluded from primary denominators; retained in `excluded` and episode detail |
| `invalid` | Yes | No | Excluded from primary denominators; retained in `excluded` and episode detail |

Excluded rows keep their original outcomes in episode detail. Exclusion is a declared reporting
convention, not deletion: a failed or interrupted attempt is never removed, and poor model outcomes
are never selectively rerun and dropped.

### 2.3 Strict outcome

For each **eligible** row:

1. If `status` is `partial` or `limit_reached`, strict outcome is **fail**, regardless of component
   unknowns.
2. If `status` is `completed`: **fail** if any component is `fail`; otherwise **unknown** if any
   component is `unknown`; otherwise **pass**.

Excluded rows are `not_scored`. An honest partial can therefore show `completion=pass` while
failing the strict outcome; that is not a dishonest claim and must not be described as one.
Unknown is never silently labelled fail, and a zero denominator means **N/A**, not 0%.

### 2.4 Counts and fractions per scenario/arm

Each of the twelve formal scenario/arm groups reports, among eligible rows:

- `attempted` = all rows in the group, including excluded rows;
- `eligible` = `completed` + `partial` + `limit_reached`;
- `excluded` = `provider_error` + `infrastructure_error` + `invalid`;
- all six status counts (zero values included for keys with no rows);
- four component × {pass, fail, unknown} counts;
- strict {pass, fail, unknown} counts;
- `success_fraction` = strict pass count / eligible count.

The fraction numerator is confirmed strict successes, not confirmed failures. Always show the
denominator and the unknown counts beside any percentage; render `N/A` when the denominator is zero;
Markdown percentages, if used, go to one decimal with an explicit unknown count. No pooled headline
across scenarios or arms is computed. A dataset inventory may count rows by provenance and stage but
is explicitly not a benchmark score.

## 3. Attempts, retries and provenance (freeze decision)

Retries are a freeze decision, not a worker choice.

- Every dispatched attempt is preserved in a separate **provenance/attempt ledger**, including
  technical reruns, aborted operations and uncertain settlements. The ledger records attempt
  identity, dispatch order, trial-cell mapping, request/model identity, real timestamps, reservation
  and settlement, grading receipts and any exclusion reason.
- The current `report-input-0.1` projection **does not model retries**: it forbids duplicate
  (`evidence_kind`, `stage`, `scenario_id`, `arm`, `trial`) cells and treats each row as a distinct
  trial cell. Retry attempt records therefore cannot be smuggled into the projection as duplicate
  rows.
- Before formal execution Main must either (a) freeze a primary-attempt mapping that projects exactly
  one attempt per trial cell, or (b) version the projection to model attempts explicitly. Under
  option (a), the mapping must be preregistered and limited to declared technical/infrastructure
  dispositions; it must never select the better-looking model outcome post hoc.
- No selective rerun of poor model outcomes, and no post-hoc replacement of an unfavourable attempt,
  is permitted. Preserve and report every failure. Any technical redispatch rule must be part of the
  same freeze and produce ledger records, not silent replacement.

**Flag:** this mapping/projection-version choice is an open Main freeze decision (FD-2 below). It
must be settled before formal execution; this draft deliberately invents no selection policy.

## 4. Resource accounting

Resources are aggregated per group over **ALL attempted rows**, including excluded ones, and are
team-level: all evaluated roles and controller work measured within the episode are summed, not just
a single worker. Each of `model_responses`, `tool_actions`, `input_tokens`, `output_tokens`,
`elapsed_ms`, `estimated_cost_micro_usd` and `reserved_micro_usd` reports `known_sum`,
`known_count`, `unknown_count` and `complete_total` (`null` if any row's value is unknown;
`known_sum` otherwise; an empty set totals 0). Null values are never imputed as zero.

Three cost concepts remain separate and must not be merged in prose:

- `estimated_cost_micro_usd` — a supplied estimate; not billed cost and not a forecast;
- `reserved_micro_usd` — an authorisation/reservation amount; arithmetic `reserved >= estimated` is
  not enforced or interpreted as billing validity;
- **actual billed cost** — only from a provider billing record or reconciliation, which does not
  exist today.

No billing guarantee, forecast or "cost per success" is produced here. A cost-per-success display is
permitted only when the relevant cost total is complete and the strict pass count is nonzero;
otherwise it is N/A with the reason stated.

**Current observed resource totals: none.** No formal attempt exists. Empty totals must be shown as
absent (em dash / "no formal data"), never as zero measurements.

## 5. Planned diagnostics — pending, not in the current projection

The intended trajectory diagnostics are:

- first correct candidate per checkpoint;
- subsequent regressions after a correct candidate;
- recurring known findings across attempts;
- unchanged-input check repetition;
- aggregate team resources.

Their **data and implementation are pending**. `report-input-0.1` supplies a sanitized display
trace (`seq`, `role`, `kind`, `summary`) but no checkpoint trajectory, no candidate-snapshot
history and no verified check-result sequence. The projection contract states explicitly that
first-correct-candidate and regression metrics wait for a future checkpoint projection; the
checklist assigns that bridge to a Main-owned task. Therefore no diagnostic values may be computed
or displayed now, and the checkpoint table in the results template is marked `FUTURE INPUT` rather
than a report-input field set.

Repeated-check findings are diagnostic flags requiring contextual review (what was checked, against
which unchanged input, with what result and evidence path), never automatic penalties. The same
applies to repeated candidate edits or repeated requirement restatements.

## 6. Planned analysis and preregistered decisions

1. **Primary presentation** — descriptive per-scenario/arm counts and fractions from accepted
   evidence: attempted/eligible/excluded, status dispositions, the four component patterns, strict
   outcome and explicit denominators/unknowns.
2. **Secondary** — workflow-package comparison within scenario, shown paired by repetition, with
   resource overhead for all attempts and the completeness of those totals stated. No pooled
   cross-scenario headline.
3. **Qualitative review** — inspect failures, partials, limit-reached and excluded attempts, and a
   small set of representative outcomes independently spot-checked from receipts.
4. **No significance test** is planned: the design has three repetitions per cell, related tasks,
   shared models/operator and no independence justification. Any later inferential claim would need
   a separately preregistered method and would still be limited by this design.
5. **No model ranking**, no generalisation beyond these authored fixtures, and no causal claim that
   JSON, structured progress or any single component causes improvement.
6. **No formal/pilot/synthetic mixing**; no result is labelled a benchmark score; fixture and
   checker evidence remains labelled separately from model evidence.

## 7. Main freeze checklist

Settle each item before any formal run, record the decision in the freeze manifest, and treat the
result as the experiment freeze rather than this draft:

1. **Outcome predicates** — freeze executable functional, workflow, evidence and completion grading
   sources; confirm truth comes from independent grading/predicates, not agent self-report or trace
   prose; confirm hidden outcomes stay hidden from evaluated roles.
2. **Attempt policy** — freeze retry scope, technical-redispatch rules, primary-attempt mapping or
   projection version, and the provenance-ledger format; confirm no selective rerun of poor model
   outcomes.
3. **Settings and limits** — freeze campaign size (36 or a pilot-informed revision), role
   models/settings, tools, timeouts, per-role/team budgets, spend bound and the execution/isolation
   revision.
4. **Order and trials** — freeze the interleaved order, trial numbering, stage separation and
   no-mid-campaign-change rule.
5. **Identities** — freeze source commit/package manifest (including the separately pinned
   working-tree inputs), scenario package revision, model alias plus actual returned identity,
   grader/checkpoint revision, runtime/bridge revision and reporting projection revision.
6. **Preregistered analysis decisions** — freeze units, denominators, exclusions, diagnostic
   definitions, comparison presentation, unknown/N-A rules, limitations wording and the
   no-significance-test/no-causality statements.
7. **Gate confirmation** — confirm pilot evidence informed size/settings but is not pooled with
   formal data, and that the paid operation has its own authorisation and bound.

*This draft is useful preparation only. Completion of P5-ANALYSIS-PREP/r1 does not freeze the
experiment, does not authorise any run, and does not create a result.*
