# Hosted CI portability fixes

**Preparation status — 8 November 2026.** Candidate proposal for packet P6-CI-PORTABILITY/r1 against local baseline commit `166a304b32d92f66519b8b4074fee510cbffdaeb`. The retained hosted failure logs are from published head `9f6911f614ba23bf09b14724ef97bd185d30e27c`. Main owns review, publication, both hosted reruns, acceptance and merge. **No hosted run was performed by this packet; no Linux pass, green CI, merge or acceptance is claimed.** Evidence kind: `offline synthetic/preparation`.

## Retained hosted failures reused

- **Ubuntu:** 254 Python tests ran with 4 errors — three tests invoked `cmd /c mklink` and failed with `FileNotFoundError: 'cmd'` before their assertions, and the reporting transient-rename test raised the injected `OSError` without a Linux `winerror`.
- **Windows:** 254 Python tests passed after Main added `.gitattributes` with `* -text` to preserve exact catalog bytes. Six `local_control` tests failed before Python spawn with `followthrough: invalid Python executable: alternate data stream paths are invalid`. The workflow's `Get-Command python -CommandType Application` returned three `Source` values; `FOLLOWTHROUGH_TEST_PYTHON` became the space-joined list, which the Rust ADS/path validator correctly rejected.

The `.gitattributes` file, catalog bytes/hashes, runner source and platform matrix are untouched by this proposal.

## Changes

### 1. Cross-platform directory links in tests

`tests/test_episode_config.py` and `tests/test_scenarios.py` now use a small local helper:

- POSIX: `os.symlink(target, link, target_is_directory=True)`.
- Windows: `cmd /c mklink /J` junction.
- Cleanup removes only the owned link: `os.unlink` for symlinks, `os.rmdir` for Windows junctions; no recursive delete and no ACL changes.
- If the platform cannot create a directory link, only that link-specific test skips with the concrete reason. Assertions about refusal (`unsafe_output_path`, `ScenarioError`, no staged output, no descendant probes) are unchanged when the link is created.

Affected methods: `test_guard_order_and_real_reparse_parent`, `test_reparse_output_parent_and_input_root_are_refused`, `test_real_redirected_visible_asset_is_refused`.

### 2. Explicit synthetic `winerror` in the reporting retry test

`tests/test_reporting.py` now creates the injected first-rename exception as a `PermissionError` and assigns `winerror = 5` explicitly rather than relying on the constructor's platform-specific fourth argument. The test also patches `reporting_report.os.name` to `"nt"` only for the injected publication call, so the Windows-only bounded-retry branch is genuinely exercised on any host.

`reporting/report.py` is unchanged: its `os.name` guard, `_TRANSIENT_WINERRORS`, retry count and failure propagation are untouched. The assertions remain `calls == 2`, published report exists and stage absent.

### 3. Single checked Python executable in the workflow

`.github/workflows/ci.yml` changes only the “Resolve Python executable for Rust tests” step. It now:

1. Runs `python -c "import sys; print(sys.executable)"` and captures the output as an array.
2. Checks the native exit code with `$LASTEXITCODE`.
3. Requires exactly one nonempty line.
4. Requires an existing absolute file via `IsPathFullyQualified` and `Test-Path -LiteralPath ... -PathType Leaf`.
5. Writes exactly that path to `GITHUB_ENV`; spaces are retained because no shell splitting or command-string interpolation is used.

Triggers, permissions, concurrency, matrix, action pins, toolchain versions, commands and `$LASTEXITCODE` checks are unchanged. The old `Get-Command` expression is absent.

## Local checks (Windows only)

| Check | Result |
|---|---|
| Four affected unittest selectors with `PYTHONPATH` at the overlay tests | **4 tests, OK**, 1.004 s |
| Two-path fixture (literal old multi-result value) | Combined value contains both fixture paths; `Test-Path` on that combined value is **False** |
| New resolution probe with actual trusted Python | **one** path; absolute **True**; existing file **True** |
| Focused `cargo test ... fake_grader_observes_snapshot_and_ordered_events` | **1 passed**, 6 filtered, compiles in 24.26 s, test 0.33 s |
| Workflow YAML parse/structural audit | PyYAML parsed; triggers/permissions/concurrency/matrix/timeout/actions/other steps unchanged; new step checks present; old `Get-Command` absent |

The four focused selectors were:

```text
test_episode_config.EpisodeConfigTests.test_guard_order_and_real_reparse_parent
test_scenarios.ScenarioPreparationTests.test_real_redirected_visible_asset_is_refused
test_scenarios.ScenarioPreparationTests.test_reparse_output_parent_and_input_root_are_refused
test_reporting.FileAndCliTests.test_transient_windows_rename_denial_is_retried
```

The Windows local host exercised the junction branch. **No POSIX runtime was available, so the POSIX symlink branch and Ubuntu behavior were not executed locally.** The reporting test exercised the explicit `winerror`/`os.name` retry path locally.

## Limitations and deferred items

- No local Linux/POSIX run; no claim that the Ubuntu suite passes. Main must rerun both hosted matrix jobs after review/publication.
- Directory-link creation can still be unavailable on an unusual host; the affected test then skips honestly rather than passing.
- The workflow still downloads actions, Python, Rust and locked crates on hosted infrastructure; those downloads were not exercised locally.
- `actionlint` was not installed or used; YAML was parsed and the workflow structure compared against the pinned workflow.
- No broader Python/Cargo suite was rerun; the retained hosted results and four focused selectors are reused.
- No product logic, runner source, catalog bytes, `.gitattributes`, platform matrix or trigger was changed.

## Verification still owned by Main

Publish the candidate at the intended commit, run both `ubuntu-latest` and `windows-latest`, confirm the four previously failing Python methods and the six Windows `local_control` failures pass, and record the run URL/head SHA. Only then may the portability fix be described as hosted-verified. Until then this remains candidate-only preparation.
