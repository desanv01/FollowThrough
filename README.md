# FollowThrough

FollowThrough evaluates whether a coding agent team completes a repair, follows its workflow, uses current verification evidence, and supports its completion claim.

The CLI prepares six scenarios across two authored task families, runs an orchestrator/worker/reviewer loop, records check receipts and source identities, and renders saved results in a terminal ledger or a portable read-only report. Ordinary and structured-progress workflows use the same task requirements.

## Video walkthrough

[Watch the narrated walkthrough — 4m45s](https://desanv01.github.io/FollowThrough/). The public player includes English captions and needs no sign-in. It shows the actual terminal/report interfaces and retained development evidence, including the incomplete study and operational limitations. [Direct MP4 and caption downloads](https://github.com/desanv01/FollowThrough/releases/tag/walkthrough-2026-11-09) are also available.

## Development preview

Three real DeepSeek-backed attempts are retained: two development episodes and one incomplete pilot episode. All received passing protected functional grades; the first stopped after a public-check infrastructure error and the later two stopped after invalid provider content. All three are excluded from scoring. No complete pilot, experiment freeze, formal result or arm comparison is claimed. Evaluation work is paused.

The explicit Python public-check backend has passed authored native positive and negative controls on the recorded Windows/Docker host. Later model episodes exercised this backend successfully before their provider/protocol stops. The Rust child-process Docker setup problem remains unresolved. Authored controls, actual model episodes and posthoc checkpoint assessments are labeled separately.

See [development status](docs/release/DEVELOPMENT-STATUS.md) and the [three-attempt record](docs/research/DEVELOPMENT-RUNS-2026-11-09.md) for scope, unknowns and accounting. The terminal ledger now implements the selected dark/cyan T3 layout in the actual CLI.

## Tasks and evidence

- Retry processing tests interrupted transactions, duplicate delivery and replay.
- Preview Cache tests project/document/revision/mode identity, requirement changes and stale verification.
- Each episode records functional, workflow, evidence and completion outcomes separately.
- Candidate hashes and requirement versions identify the source a check or review actually covered.
- Host checkpoints support observed trajectory analysis when independently graded.

The [scenario guide](docs/project/SCENARIOS.md), [task examples](docs/project/TASK-EXAMPLES.md), [methods](docs/research/METHODS.md), and [integration contract](docs/project/CONTRACTS.md) describe the task and evidence model.

## Run authored local controls

Use Python 3.11 or later.

~~~sh
python -B grader/retry_processing.py --self-test
python -B grader/preview_cache.py --self-test
python -B -m unittest discover -s tests -v
python -B -m unittest discover -s p5/tests -v
~~~

These commands execute the authored trusted fixtures. The output distinguishes functional failure, candidate execution error and grader/infrastructure error. Use the isolated execution path for evaluated candidate code.

Build and test the Rust controller with the pinned CI toolchain or a compatible installed Rust toolchain.

~~~sh
cargo build --locked --manifest-path runner/Cargo.toml
cargo test --locked --manifest-path runner/Cargo.toml
~~~

See the [runner guide](runner/README.md), [container executor](docs/project/CONTAINER-EXECUTOR.md), and [live driver](docs/project/LIVE-DRIVER.md) for supported invocation, limits and cleanup behavior. Use the [qualified evaluation guide](docs/project/QUALIFIED-EVALUATION.md) for current prerequisites, preflight and guarded dispatch templates. Native evidence is scoped to the recorded Windows/Docker environment. The explicit [Python check backend](docs/project/PYTHON-CHECK.md) has [actual passing and failing authored native controls](docs/research/NATIVE-CONTROLS-2026-11-09.md); the Rust launch failure on this host remains open. The [observed R1 checkpoint assessment](docs/research/OBSERVED-R1-TRAJECTORY.md) is posthoc development evidence, separate from pending pilot/formal results.

## Inspect a saved report

Start with the three retained real attempts. This read-only command needs Python 3.11+ and a supported interactive terminal; it makes no API or Docker calls:

~~~sh
python -B -m reporting.run_ledger --input docs/research/recorded-development-input.json --format tui
~~~

Use `j/k` or arrows to select, `Enter` to inspect evidence, `/` to filter, and `q` to close. Use `--format text` when an interactive terminal is unavailable. The saved trace is a bounded sanitized event projection, not the complete private transcript. These excluded attempts are development evidence, not benchmark scores.

The separate report fixture below contains synthetic development records.

~~~sh
python -B -m reporting.report --input tests/fixtures/reporting/v0.1/input.json --output-dir runs/report-example
python -B -m reporting.run_ledger --input tests/fixtures/reporting/v0.1/input.json --format text
python -B -m reporting.run_ledger --input tests/fixtures/reporting/v0.1/input.json --format tui
~~~

Use a fresh output directory for report generation. The interactive ledger requires a supported terminal. Text and JSON modes are available for logs and automation. See the [ledger guide](docs/project/RUN-LEDGER.md) and [read-only browser viewer](docs/project/VIEWER.md).

## Provider and budget

The official DeepSeek adapter loads credentials from the ignored local environment file and uses one campaign reservation ledger. Reviewed live operations bind source, scenario, operation identity and resource limits before dispatch. Unknown usage, reservations and cleanup remain explicit. Reservation estimates depend on the documented pricing/token assumptions.

See the [adapter guide](adapters/README.md) and [provider access](docs/project/PROVIDER-ACCESS.md). Keep credentials, private coordination, budget files and raw run artifacts outside published source and evaluated workspaces.

## Licence

FollowThrough is released under GNU Affero General Public License version 3 only, AGPL-3.0-only. See [LICENSE](LICENSE), [NOTICE](NOTICE), and [contributions](docs/release/CONTRIBUTIONS.md). Third-party components retain their own licence terms.

