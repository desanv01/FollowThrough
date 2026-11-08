# FollowThrough

An evaluation of agent teams completing software repairs under persistent workflow rules. RepairLoop tracks whether review evidence turns into correct repairs and supported completion.

Implementation is in progress. No live evaluation results are available yet.

Two planned task families: transactional retry processing and preview caching under updated requirements. Six scenarios belong to these two families; they are not six independent domains.

See [project plan](docs/project/PLAN.md), [acceptance gates](docs/project/ACCEPTANCE.md), and [integration contract](docs/project/CONTRACTS.md).

## Current scope

The accepted source contains authored repair fixtures, independent Python functional graders, a Rust controller for trusted retry controls, six reproducible scenario packages, a source-only container planner, and an offline-tested DeepSeek adapter with credential loading and a cumulative reservation ledger. Actual container execution, protected grading, the evaluated-role loop and the live campaign remain unaccepted. The trusted Rust controller supports the retry family; the cache grader runs directly.

The [scenario guide](docs/project/SCENARIOS.md) describes package preparation. Preparation does not execute the controller's scheduled events or establish an agent result. The [isolation planner](docs/project/ISOLATION-PLAN.md) emits a plan; it has not qualified an execution sandbox. Release checklists and the demo outline are dated preparation documents, not completed release artifacts.

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

The [DeepSeek adapter](adapters/README.md), project `.env.local` loader and cumulative reservation ledger have passed independent offline source review. The [provider-access guide](docs/project/PROVIDER-ACCESS.md) describes their behavior and restrictions. The local US$20 authorization envelope depends on the documented pricing/token assumptions and is not a provider-billing guarantee. No live provider call or billing validation has been performed. Live dispatch still requires a verified access path, accepted episode bridges and qualified execution. Never commit credentials or expose them to evaluated task workspaces.

See [methods](docs/research/METHODS.md), [execution board](docs/project/EXECUTION-BOARD.md), [hybrid development workflow](docs/project/HYBRID-WORKFLOW.md) and [contributions](docs/release/CONTRIBUTIONS.md). Private coordination and raw run artifacts are excluded from the repository. This project is released under the GNU Affero General Public License, version 3 only (`AGPL-3.0-only`); see [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party components retain their own licenses.
