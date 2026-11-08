# Offline-source CI preparation contract v0.1

Main-owned finite scope for P6-CI/r1, P6.1.2. This prepares a workflow proposal; it does not activate GitHub Actions, publish source, or establish a hosted result. Main reviews and integrates the proposal and checks the exact published commit separately.

## Proposed files

- .github/workflows/ci.yml
- docs/project/CI.md

No product source, tests, dependencies, Cargo.lock, root configuration, README badges, licence or prior release drafts may change. Main explicitly delegates candidate authorship of this workflow only. Public installation stays Main-owned. Development uses an owned overlay and accepted source copies.

## Workflow behavior

Use push on main and codex/**, pull_request targeting main, and workflow_dispatch. No schedule, pull_request_target, workflow_run, deployment, publishing, artifact upload, provider evaluation or secrets. Top-level permissions contents: read. Concurrency group includes workflow and ref; cancel-in-progress true. No input expressions interpolated into shell code. Checkout persist-credentials: false.

Matrix ubuntu-latest and windows-latest, fail-fast false, one job per OS, timeout-minutes 15. Python 3.11 via the pinned official action. Use shell pwsh for common steps (available on GitHub hosted Windows/Ubuntu); explicitly propagate each native failure using $LASTEXITCODE, including multiple commands in a step. Do not rely on shell default error behavior to fail a job.

Official actions Main verified from their release-to-commit links on 7 November 2026:
- actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 (v7.0.1): https://github.com/actions/checkout/releases/tag/v7.0.1 and https://github.com/actions/checkout/commit/3d3c42e5aac5ba805825da76410c181273ba90b1
- actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 (v7.1.0): https://github.com/actions/setup-python/releases/tag/v7.1.0 and https://github.com/actions/setup-python/commit/6d4fd22b20465ccc0f7354476588e8df7c9ca0e0

No other actions or third-party install scripts. Hosted toolchain provisioning: rustup toolchain install 1.95.0 --profile minimal, then explicit cargo +1.95.0 commands (avoid mutating global default). Network to download action/Python/Rust/crates is expected in future hosted setup; this is not an air-gapped workflow. No network/provider calls are authorized during the local worker assignment.

Set PYTHONDONTWRITEBYTECODE=1. Set FOLLOWTHROUGH_TEST_PYTHON to the resolved Python executable for Rust boundary tests without dumping the environment. Run:
- python -B -m unittest discover -s tests -v
- python -B grader/retry_processing.py --self-test
- python -B grader/preview_cache.py --self-test
- cargo +1.95.0 fetch --locked --manifest-path runner/Cargo.toml (hosted dependency setup)
- cargo +1.95.0 test --offline --locked --manifest-path runner/Cargo.toml
- cargo +1.95.0 build --offline --locked --manifest-path runner/Cargo.toml

No hardcoded test-count success rule. Honest platform skips stay visible. Product tests use authored trusted fixtures and fake transports, not paid evaluation or qualified sandboxing. The workflow must not load .env.local, inspect credentials or run --live. Do not cache/upload private runtime directories. No CI badge until Main observes a hosted result.

## Local validation and delivery

Inspect actual source commands and workflow syntax. If an installed YAML parser/actionlint exists, use it without installs; distinguish YAML parsing from GitHub Actions semantic validity (YAML1.1 may interpret unquoted on as Boolean, so quote "on"). If unavailable, record that limitation; do not invent lint success. No tests mirroring YAML string literals are required.

Validate documented offline test commands once on an owned integrated-shaped copy made from the packet-pinned accepted file list. Use already-installed Rust and an exclusive CARGO_TARGET_DIR; no local toolchain/download command. Preserve failing output and stop on product defects without changing product code. Python/Rust local evidence is Windows-only and does not prove hosted Ubuntu/Windows compatibility. If pin baseline lacks a newer component, record the tested source identity explicitly; never silently copy an active candidate.

Report workflow event/job/permission/command audit, actual validator availability, local commands with exit codes and skips, excluded operations, hosted verification still required, and settled processes. Freeze both proposals plus report, command ledger, settlement and byte/SHA256 manifest. Main independently reviews before installation/publication. Do not claim the whole P6 phase complete.

