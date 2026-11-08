# FollowThrough

FollowThrough evaluates whether a coding agent team completes a repair, follows its workflow, uses current verification evidence, and supports its completion claim.

The CLI prepares six scenarios across two authored task families, runs an orchestrator/worker/reviewer loop, records check receipts and source identities, and renders saved results in a terminal ledger or a portable read-only report. Ordinary and structured-progress workflows use the same task requirements.

## Development preview

One real DeepSeek development episode has been recorded. Its protected Retry v1 functional grade passed. A public check failed during Docker setup, and the overall episode was recorded as an infrastructure error and excluded from scoring. Workflow, evidence and completion remained unknown.

The trace also exposed a mismatch between the live check bridge and the episode loop. The released source includes the reviewed correction and infrastructure-classification regression checks. This corrected source has not been demonstrated by a second paid episode. Pilot and formal evaluation results remain pending.

See [development status](docs/release/DEVELOPMENT-STATUS.md) for measured scope, limitations and resource accounting. Native authored Retry, Cache v2 and Cache v1 checkpoint controls and both direct grader self-tests have recorded expected outcomes. Authored controls establish checker behavior and are labeled separately from model episodes.

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

See the [runner guide](runner/README.md), [container executor](docs/project/CONTAINER-EXECUTOR.md), and [live driver](docs/project/LIVE-DRIVER.md) for supported invocation, limits and cleanup behavior. Native evidence is scoped to the recorded Windows/Docker environment.

## Inspect a saved report

The included report fixture contains synthetic development records.

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

