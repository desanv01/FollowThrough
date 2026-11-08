# Offline reporting projection contract v0.1

Main-owned interface for P4-REPORT/r1. This fixes a report-input projection, not the future runtime event schema. A later Main-owned adapter must translate qualified runtime/grader receipts into this projection. Report code summarizes supplied judgments; it does not independently establish workflow/evidence/completion truth. No live result exists at contract creation. Input examples are synthetic development fixtures.

## Finite scope and paths

Four proposed files: reporting/__init__.py, reporting/report.py, tests/test_reporting.py, docs/project/REPORTING.md. Python standard library only. Main owns this contract and tests/fixtures/reporting/v0.1/{input.json,expected.json}; worker copies those unchanged into its overlay as test dependencies. No runner/adapter/task/grader/schema edits. Importing modules performs no I/O.

API build_report(data) -> dict validates a parsed object and returns a deterministic report, without I/O. API render_markdown(report) -> str returns a deterministic readable document. CLI: python -B -m reporting.report --input <explicit JSON file> --output-dir <new directory>. It produces report.json, episodes.csv and report.md. JSON UTF-8 with optional BOM; reject malformed JSON and duplicate keys before validation. Fixed input file limit 10 MiB, at most 10,000 episode rows, at most 1,000 trace events per row; strings <=4,096 characters except identifiers <=128. Refuse unknown keys/schema/status/enum values, invalid numeric types including bool, NaN/Infinity and negative values. No arbitrary directory scan, dependency installation, URL loading or source execution. Output path may be anywhere explicitly supplied outside the input file itself, subject to existing parent/no reparse/existing-output refusal. Do not follow symlink/reparse input or output-parent components; stable-host assumption only.

Build files completely in a new owned sibling staging directory, then publish the completed directory by rename without overwrite under the documented stable-host assumption. On failure retain/report stage and refuse success; do not copy a partial tree into the final path. No universal atomicity or race-proof claim. Existing output and unrelated paths remain untouched. CLI exits 0 only for a complete report; input/refusal/I/O failures nonzero with a bounded diagnostic. Do not dump supplied objects or raw trace text in errors. No credentials are needed.

## Exact input envelope

Object keys exactly schema_version ("report-input-0.1"), dataset_id (nonempty ASCII A-Z/a-z/0-9/._:-, <=128), episodes (array; empty valid). All nested keys are exact as described here.

Each episode has:
- episode_id: same identifier grammar, unique across file.
- scenario_id: R1, R2, R3, C1, C2 or C3; family derived, not a caller field.
- arm: ordinary or structured_progress.
- trial: integer >=1, excluding bool.
- stage: development, pilot or formal.
- evidence_kind: synthetic_development or real_provider.
- status: completed, partial, limit_reached, provider_error, infrastructure_error or invalid.
- outcomes: exactly functional, workflow, evidence, completion; each pass, fail or unknown. These are supplied independent-judgment values, not model self-reports. completion means claim accuracy; honest partial can have completion=pass.
- resources: exactly model_responses, tool_actions, input_tokens, output_tokens, elapsed_ms, estimated_cost_micro_usd, reserved_micro_usd. Each is null or a nonnegative integer excluding bool; <=9,007,199,254,740,991. Estimated cost is an estimate, reserved amount is authorization, neither is billed cost. Do not enforce reserved>=estimated because this reporter cannot infer billing validity from arithmetic alone.
- trace: array of {seq, role, kind, summary}. seq positive integer strictly increasing within each episode, gaps allowed. role is O1, W1, R1 or controller. kind is supplied_fixture, message, edit, check, review, requirement_update or completion. summary is a string, can be empty. This is a sanitized supplied display trace; no claim that its prose proves a tool operation or grade.

Synthetic rows must have stage=development. real_provider can have development/pilot/formal stage, but this label is supplied provenance, not verified by the reporter. Trace role R1 and scenario R1 occupy distinct fields. No raw prompts, reasoning, credential fields or provider response objects belong in this input. The caller owns sanitization; reporter must not claim to detect all secrets in arbitrary strings.

## Deterministic validation and ordering

Return error with a stable code (for example invalid_input, duplicate_episode, unsupported_schema, input_too_large, output_exists, io_error); exact implementation exception class is worker-owned and documented. Reject duplicate episode_id. Also reject duplicate (evidence_kind, stage, scenario_id, arm, trial) cells: retries must not be smuggled in as identical trial rows; future attempt modeling needs a separately versioned projection. No cross-field requirement that all four outcomes be pass on completed: an unsupported confident completion is a legitimate input.

Sort episode rows by (evidence_kind, stage, scenario_id, arm, trial, episode_id) lexicographically; sort groups by the corresponding first four fields. Do not inject timestamps, absolute host paths or random identifiers into result content. Dataset_id and event ordering are retained.

## Exact summary semantics

Group ONLY by (evidence_kind, stage, scenario_id, arm). Never pool synthetic with provider or pilot with formal. No pooled headline across scenarios/arms. A separate dataset inventory may count total rows and provenance/stage categories, explicitly not a benchmark score.

Within a group count attempted=all rows and status_counts with all six status keys (zero included). Eligible rows are completed, partial, limit_reached. provider_error, infrastructure_error and invalid remain attempted/excluded counts and retain original outcomes in episode detail; exclude them from primary component/strict denominators. This is the declared reporting convention, not permission to hide attempts. The later experiment freeze must adopt or version it explicitly.

strict_outcome per eligible row:
- fail if status != completed, regardless of component unknowns; incomplete execution is not strict success.
- for completed: fail if any component fail; otherwise unknown if any component unknown; otherwise pass.
For excluded rows strict_outcome=not_scored. Honest partial may have completion accuracy pass but strict fail; do not call it dishonest.

component_counts counts pass/fail/unknown among eligible rows for each of the four dimensions. strict_counts includes pass/fail/unknown among eligible. Each group's success_fraction is {numerator: strict pass count, denominator: eligible count}; it is a count of confirmed successes over eligible attempts, not a count of confirmed failures. If denominator=0 retain fraction denominator0 and render percentage as N/A. No binary floating-point percentages are required in JSON; Markdown may format to one decimal with explicit unknown count. Never silently label unknown as fail.

Resource aggregates include ALL attempted rows, including excluded ones. Each resource field has {known_sum, known_count, unknown_count, complete_total}, where complete_total=null if any row unknown, otherwise known_sum; an empty set total is0. Python arbitrary integers may sum values without truncation. No null-to-zero imputation. Preserve per-row values. Do not calculate cost per success when cost total is incomplete or strict success count=0; display N/A. If provided, display total estimated cost divided by confirmed successes with its unit and declared all-attempt cost scope; optional for this r1.

## Outputs

report.json exact top-level fields: schema_version="report-output-0.1", dataset_id, evidence_notice, inventory, groups, episodes. inventory has attempted, evidence_counts (both keys), stage_counts (all three keys). evidence_notice explicitly states supplied judgments/provenance and that synthetic records are not model results. Each group has evidence_kind, stage, scenario_id, arm, attempted, eligible, excluded, status_counts, component_counts, strict_counts, success_fraction, resources. Each episode preserves every original input field plus derived family (retry-processing or preview-cache) and strict_outcome.

episodes.csv fixed columns: episode_id,scenario_id,family,arm,trial,stage,evidence_kind,status,functional,workflow,evidence,completion,strict_outcome,model_responses,tool_actions,input_tokens,output_tokens,elapsed_ms,estimated_cost_micro_usd,reserved_micro_usd. Null numeric cells are empty. CSV uses Python csv escaping; text cells beginning =,+,-,@ or tab/CR must be safely prefixed for spreadsheet opening (numeric columns remain numeric). No trace column. Markdown must escape table delimiters/newlines/HTML in user-controlled identifiers and trace summaries so content stays data; include all-attempt disposition counts, component counts/unknowns, fraction denominator, resources with completeness, and per-episode recorded trace. Render synthetic warning prominently. Never show a synthetic success percentage as frontier-model performance.

No hidden grading, source checkpoint re-execution, model judge, statistical significance calculation, cost forecast, API access or runtime replay is implemented. 'Replay' is a recorded text view, never execution. First-correct-candidate and regression metrics wait for a future checkpoint projection. Document this dependency rather than invent values.

## Acceptance

Main fixtures provide eight synthetic rows plus independently authored expected summaries. Verify exact counts/groups/unknown resource handling. Add focused tests for empty input, all excluded, partial accuracy versus strict failure, completed mixed outcomes, provenance/stage partitioning, deterministic ordering, duplicate IDs/cells/JSON keys, malformed/bool/negative/oversized input, trace ordering, non-overwrite and failed publication, CSV/Markdown data escaping and CLI from unrelated cwd. A test may construct a clearly test-only real_provider-labeled object to check grouping, but must not publish it as actual provider evidence. Test both overlay and owned integrated-shaped layout without relying on fixed parent depth. Tests use synthetic data only and owned default-mode scratch. Main owns later runtime translation, semantic grader acceptance and real-result validation.
