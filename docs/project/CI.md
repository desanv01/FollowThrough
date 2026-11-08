# Offline-source CI

The workflow is active for the scoped hosted delivery. Ubuntu and Windows checks run on each published source head, and merge readback verifies the delivered tree. Historical preparation evidence below remains tied to its original snapshot. The current workflow is at [.github/workflows/ci.yml](../../.github/workflows/ci.yml).

## Historical preparation record

**Preparation status — 8 November 2026.** Candidate proposal for packet P6-CI/r1 against frozen baseline commit `166a304b32d92f66519b8b4074fee510cbffdaeb`. Main owns installation, publication, hosted execution, acceptance and merge. **This workflow is not installed or active, no hosted run exists, and no CI badge or hosted result is claimed.** Evidence kind: `offline_ci_preparation`.

## Scope

Two files are proposed: the workflow at [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) and this document. They add no product source, tests, dependency, licence, README badge or release material. The workflow tests the frozen accepted source at the pin baseline; it is not an evaluation run, a provider call or a sandbox qualification.

## Triggers

The top-level key is quoted as `"on"` to avoid YAML 1.1 treating it as a Boolean.

| Event | Filter |
|---|---|
| `push` | branches `main` and `codex/**` |
| `pull_request` | target branch `main` |
| `workflow_dispatch` | manual dispatch |
| `schedule` | **not present** |

No `pull_request_target`, `workflow_run`, deployment, publishing or upload event is configured.

## Permissions, concurrency and credentials

- Top-level `permissions: contents: read`.
- Concurrency group includes the workflow and ref: `${{ github.workflow }}-${{ github.ref }}`; `cancel-in-progress: true`.
- Checkout uses `persist-credentials: false`; no repository token is retained for later steps.
- No secrets, provider requests, `--live` invocation, `.env.local` loading or credential inspection.
- No untrusted event input is interpolated into shell code. The only expressions are the workflow/ref concurrency group, the matrix OS and the matrix OS in the job name/runner.
- No artifact upload, cache of private runtime directories, deployment, publication or badge.

## Jobs, platforms and toolchain

One job, `offline-source`, runs one job per OS:

| Setting | Value |
|---|---|
| Runner matrix | `ubuntu-latest`, `windows-latest` |
| `fail-fast` | `false` |
| `timeout-minutes` | `15` |
| Shell for native commands | `pwsh` (available on both hosted platforms) |
| Python | `3.11` via `actions/setup-python` |
| Rust | `1.95.0`, installed explicitly with `rustup` (no global default mutation) |
| Official actions | `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1); `actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` (v7.1.0) |

Each native command is followed by an explicit `$LASTEXITCODE` check that exits with the failing code. The workflow does not rely on shell default error behaviour. `PYTHONDONTWRITEBYTECODE=1` is set at job scope. `FOLLOWTHROUGH_TEST_PYTHON` is written to `$GITHUB_ENV` from the resolved `python` application path without dumping the environment.

## Commands

| Purpose | Command |
|---|---|
| Install Rust | `rustup toolchain install 1.95.0 --profile minimal` |
| Fetch locked crates | `cargo +1.95.0 fetch --locked --manifest-path runner/Cargo.toml` |
| Python suite | `python -B -m unittest discover -s tests -v` |
| Retry self-test | `python -B grader/retry_processing.py --self-test` |
| Preview-cache self-test | `python -B grader/preview_cache.py --self-test` |
| Rust tests | `cargo +1.95.0 test --offline --locked --manifest-path runner/Cargo.toml` |
| Rust build | `cargo +1.95.0 build --offline --locked --manifest-path runner/Cargo.toml` |

Discovery is intentional: when Main integrates newly accepted tests, the hosted Python suite will collect them without editing this workflow. There is no hardcoded test-count success rule. Honest platform skips remain visible.

## Tested source snapshot

Local validation used a copy of exactly the 33 files in the packet's frozen accepted-source list. All 33 matched their pinned byte sizes and SHA-256 values. The set contains the accepted foundation, source planner and budget guard:

- **adapters (6):** `README.md`, `__init__.py`, `budget.py`, `budgeted_deepseek.py`, `credentials.py`, `deepseek.py`
- **grader (2):** `preview_cache.py`, `retry_processing.py`
- **references (4):** preview-cache `correct` and `v1`; retry-processing `correct` and `mark-first`
- **runner (11):** `Cargo.lock`, `Cargo.toml`, `README.md`, `src/bin/isolation-plan.rs`, `src/isolation_plan.rs`, `src/main.rs`, `src/paths.rs`, `src/process.rs`, `tests/fake_grader.py`, `tests/isolation_plan.rs`, `tests/local_control.rs`
- **tasks (4):** preview-cache `SPEC.md` and `preview.py`; retry-processing `SPEC.md` and `processor.py`
- **tests (6):** `test_budget.py`, `test_budgeted_deepseek.py`, `test_credentials.py`, `test_deepseek_adapter.py`, `test_preview_cache_grader.py`, `test_retry_grader.py`

The copy deliberately **excludes** unaccepted executor/role-loop work, the scenario package, report proposals and any file outside the frozen accepted list. Copies are test dependencies under owned scratch, not proposed product changes.

## Local validation evidence (Windows only)

Host: Windows, Python `3.11.15`, Cargo/Rust `1.95.0`, PyYAML `6.0.3`. `actionlint` is not installed; no install was attempted.

| Check | Result |
|---|---|
| Python discovery suite (`-v`) | **143 tests, OK**, 40.174 s |
| Retry grader self-test | **pass**, three controls with expected outcomes |
| Preview-cache grader self-test | **pass**, `self_test_passed: true`, 3 controls |
| `cargo test --offline --locked` | **pass**: 15 isolation-plan integration, 7 local-control, 1 isolation-plan binary; 0 failed |
| `cargo build --offline --locked` | **pass** (dev profile) |
| YAML parse (PyYAML `safe_load`) | **pass** with quoted `"on"` |
| GitHub Actions semantics | Manual audit: triggers, permissions, concurrency, matrix, timeout, action SHAs, `pwsh`, `$LASTEXITCODE`, no forbidden operations |

**Local Windows sandbox limitation:** two retained full-suite attempts failed before any assertion because Python's `tempfile` mode-0700 directories did not inherit the sandbox write capability. The suite was then run with a test-only `sitecustomize` that creates default-mode UUID temp directories. No product or test file was changed, and the proposed workflow does **not** include this accommodation; hosted runners are expected to allow normal temporary directories. Failed-attempt logs are retained in the delivery evidence. The self-tests and Rust runs also used the same owned temp environment; their assertions are unaffected.

`cargo test` and `cargo build` used an exclusive `CARGO_TARGET_DIR` under owned scratch and the already-installed toolchain. No local toolchain install, network fetch, provider call or package install occurred.

## Hosted downloads and expected setup

A future hosted run is expected to download the two pinned actions, a Python 3.11 distribution, the Rust 1.95.0 toolchain and the locked crates. It is **not** an air-gapped workflow. Hosted setup is Main-owned and has not been executed. Local validation could not test these downloads, Ubuntu pwsh behaviour, hosted paths or hosted resource limits.

## Limitations

- A Windows local pass is **not** an Ubuntu pass and **not** a GitHub Actions pass.
- The workflow is not installed, has not run on GitHub, and cannot be called active or green; no check-run, PR merge or badge exists.
- YAML parsing proves syntax only. It does not prove GitHub Actions semantic acceptance; that requires a hosted run at the published commit.
- Action SHAs are taken from `CI-CONTRACT.md` as Main-verified release-to-commit links; this packet did not use the network to re-verify them.
- The local Python run required the sandbox temp-directory accommodation described above; hosted environments are expected not to need it.
- No test-count assertion is embedded in the workflow, so a later test addition cannot silently require workflow edits.

## Later hosted-head verification

Main must, after installation and publication:

1. Confirm the workflow exists at the exact published commit and that the commit is the one recorded in the run.
2. Confirm both `ubuntu-latest` and `windows-latest` jobs start, the matrix has `fail-fast: false`, and the job timeout is 15 minutes.
3. Confirm triggers, `contents: read`, concurrency cancellation, `persist-credentials: false` and absence of secrets/upload/deploy steps.
4. Observe the Python suite, both self-tests, `cargo test` and `cargo build` in both jobs; record any honest skips.
5. Record the run URL, event, head SHA and conclusion. Only then may a hosted result or badge be claimed.
6. If any hosted command fails, return the concrete log to Main; do not loosen product tests or edit accepted source in this proposal.

Until that evidence exists, this remains a candidate workflow proposal. No hosted CI, publication, acceptance or merge is claimed.
