# FollowThrough

An evaluation of agent teams completing software repairs under persistent workflow rules. RepairLoop tracks whether review evidence turns into correct repairs and supported completion.

Implementation is in progress. No live evaluation results are available yet.

Two planned task families: transactional retry processing and preview caching under updated requirements. Six scenarios belong to these two families; they are not six independent domains.

See [project plan](docs/project/PLAN.md), [acceptance gates](docs/project/ACCEPTANCE.md), and [integration contract](docs/project/CONTRACTS.md).

## Current scope

The foundation contains authored repair fixtures, independent Python functional graders, a Rust controller for trusted retry controls, and an offline-tested DeepSeek protocol adapter. Scenario orchestration, protected execution of model-generated code, cumulative budget enforcement and the live evaluation campaign remain in development. The Rust controller currently supports the retry family; the cache grader runs directly.

These local controls demonstrate checker behavior. They are not model benchmark scores, proof of workflow completion, or an execution sandbox. Do not run untrusted candidate code through the current host-based commands.

## Run trusted local controls

Use Python 3.11 or later. No third-party Python packages are required.

```sh
python -B grader/retry_processing.py --self-test
python -B grader/preview_cache.py --self-test
python -B -m unittest discover -s tests -v
```

Each self-test checks the authored correct solution and intentional defects. A valid functional failure is different from a candidate execution error or a grader failure. The graders retain this distinction in their JSON output and exit status.

Build the controller with a Rust toolchain:

```sh
cargo build --locked --manifest-path runner/Cargo.toml
cargo test --locked --manifest-path runner/Cargo.toml
```

Create the `runs` directory, then use a new output directory for each invocation:

```sh
runner/target/debug/followthrough control --project-root . --candidate references/retry-processing/correct/processor.py --python python --output-dir runs/example-001
```

On Windows, the executable has an `.exe` suffix. See the [runner guide](runner/README.md) for output limits, exit codes, path handling and cleanup limitations. Windows environments with restricted temporary directories may require an explicitly writable owned TEMP/TMP directory; permission failures are infrastructure errors, not candidate scores.

## Provider and experiment status

The [DeepSeek adapter](adapters/README.md) has offline transport tests. Its per-request reservation is conditional on documented pricing/token assumptions; it does not yet implement a cumulative campaign budget. Project `.env.local` loading and the aggregate US$20 ceiling must be implemented and reviewed before the live campaign. Never commit credentials or expose them to evaluated task workspaces.

See [methods](docs/research/METHODS.md), [execution board](docs/project/EXECUTION-BOARD.md), [hybrid development workflow](docs/project/HYBRID-WORKFLOW.md) and [contributions](docs/release/CONTRIBUTIONS.md). Private coordination and raw run artifacts are excluded from the repository. License selection is pending; no open-source license grant is asserted yet.
