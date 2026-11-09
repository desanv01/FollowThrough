# Reproduction checklist

**Preparation status — 7 November 2026 (r2 correction).** Prepared for P6-RELEASE-PREP/r1 and revised by the bounded r2 documentation correction against the same frozen baseline commit `166a304b32d92f66519b8b4074fee510cbffdaeb`. This checklist records what can be reproduced **today** (trusted local controls) separately from what a future real evaluation would require. It is not evidence that any command below was run for this packet, and it is not a formal-evaluation runbook. Main must confirm every command, environment and identity after qualification. No CI, campaign, video or submission is performed here.

## 1. What is and is not reproducible

| Evidence class | Reproducible now? | What it shows |
|---|---|---|
| Authored fixture graders and self-tests | Yes, trusted local controls | Checker behaviour on the original, partial and correct authored candidates |
| Python unit/integration collection | Yes, trusted local environment | 50 accepted tests over retry, preview-cache and adapter code |
| Rust trusted retry controller | Yes, if a Rust toolchain is available | Local control invocation and boundary tests; not a sandbox |
| DeepSeek adapter transport | Offline tests only | Protocol/error handling with injected transports; no provider call |
| Live DeepSeek readiness | **No** — gated | Requires separate authorisation, reviewed credential loading and a real paid request |
| Scenario/executor/role loop | **No** — not accepted | Scenario, isolation-plan and budget candidates are delivered but unaccepted |
| Formal 36-episode campaign | **No** — not run or frozen | Future accepted results only; no current results exist |

Commands below are quoted from pinned repository guides. They are documented, not executed by this packet. Do not run untrusted candidate code through them: the Python grader imports the candidate with local permissions and is **not a security boundary**.

## 2. Environment prerequisites

- Python 3.11 or later; the trusted graders and tests use only the standard library. The pinned guide states that no third-party Python packages are required.
- A Rust toolchain for the controller build and Rust tests. The runner guide's build form uses `--offline`; dependencies must already be available locally for that form to succeed.
- A local checkout at the frozen commit. Checkout bytes can differ from raw Git blob bytes through newline conversion; compare commit/object identity, not filtered checkout bytes. Outbound network access is not needed for the trusted local controls and is **not authorised** for this preparation pass.
- A writable `runs` directory (create it once) and a **new** output directory per control invocation; existing run directories are refused.
- On Windows, `runner/target/debug/followthrough.exe`. A restricted or non-writable `TEMP`/`TMP` is an infrastructure condition, not a candidate failure; use an explicitly writable directory you control rather than changing ACLs or system permissions.

## 3. Source identities

Baseline commit: `166a304b32d92f66519b8b4074fee510cbffdaeb` on `main`. Public repository: <https://github.com/desanv01/FollowThrough>.

**Baseline Git blob identities at commit `166a304`.** The eight rows below are raw Git blob sizes and SHA-256 values for committed inputs at that commit. They are **not** invariant checkout-file hashes:

| Repository path | Blob bytes | Blob SHA-256 |
|---|---|---|
| `AGENTS.md` | 2297 | `581d258f36628324c5017aeee84ec728e1f202a8962bfd810e6a06dee0ec698c` |
| `README.md` | 3356 | `efdde57a76d48c02fd8218e31236d861057c9dc39648c15c2e69b1b90ba08698` |
| `runner/README.md` | 3221 | `6271566812e3882969053850613e3402b1463e6169e1f88bac181179fa981143` |
| `adapters/README.md` | 3866 | `134850ee57ff6c42aac1c33b338fa9a55b5c98c26a24431ff9e692dfcf871262` |
| `docs/project/CONTRACTS.md` | 2877 | `e273c329500eb3992482ca56bb0067060d8d32336b068c12deb98de43b95b9b0` |
| `docs/project/ACCEPTANCE.md` | 1751 | `b538f0355b37f38bd6323c531c159cb738502f5b0f4af5e7c255ae8c35d342d2` |
| `docs/research/METHODS.md` | 2539 | `d5054ab2aa7fd38272c15c0667fd9f47c5f2ec77071b9de920b095006ca5b8d6` |
| `docs/release/CONTRIBUTIONS.md` | 2225 | `1795ab3fca22933f76a07ecf94cdeba854d89f851eb699ea5662e19888409115` |

On Windows, `core.autocrlf` or `text` conversion can change the bytes and SHA-256 of a checkout file without changing the commit or its blob identity; a converted checkout can still be a valid checkout of the same commit. Do not rewrite the stored identities above or normalise bytes to force a match. To check source identity, use read-only Git object inspection (commit, path and object identity) or a manifest generated for the exact package; avoid PowerShell text redirection when byte identity matters.

The `docs/release/CONTRIBUTIONS.md` row is the **original 2,225-byte baseline blob** and its hash. The proposed contribution file preserves exactly those first 2,225 bytes as a prefix, but the complete proposed file is longer and has a different identity: the r1 proposal was 5,755 bytes with SHA-256 `9834a6cb35833b44b91e740921d34d73e9bb76112f94b8a55009f3d4aa7bdbb4`, and the r2 copy is byte-identical. The prefix hash is not the hash of the complete file.

The Main-owned `docs/project/RELEASE-PREPARATION-BASELINE.md` (4,206 bytes) is a **working-tree-only** input and is not present in commit `166a304`; it has no baseline blob identity in this table. A future release archive must be verified using its **own frozen package manifest**, not this table or the working tree. The working tree at preparation time was not clean: `EXECUTION-BOARD.md` and `HYBRID-WORKFLOW.md` were modified, and several other `docs/project` files plus `tests/fixtures/` were untracked. A reproduction claim must name the exact commit or package used, not "the workspace".

## 4. Trusted local-control commands

### 4.1 Grader self-tests (from `README.md`)

```sh
python -B grader/retry_processing.py --self-test
python -B grader/preview_cache.py --self-test
```

Expected: the preview-cache specification states its self-test succeeds only when the correct reference passes and each negative candidate fails exactly its specified checks. The retry self-test validates the reference and deliberately incorrect fixtures; its contract names the required error-free check IDs. A self-test result is checker evidence, not model evidence.

### 4.2 Combined Python tests (from `README.md`)

```sh
python -B -m unittest discover -s tests -v
```

Expected baseline: **50 accepted tests** pass. This total combines the 7 retry, 26 preview-cache and 17 adapter tests. Do not add unaccepted candidate counts (for example the scenario package's 18-test report or the budget package's 95-test report) to this number.

### 4.3 Adapter offline tests (from `adapters/README.md`)

```sh
python -B -m unittest discover -s tests -p test_deepseek_adapter.py -v
```

Expected: offline protocol tests pass using injected transports and mocked HTTPS connections/credentials. No provider call and no billing claim.

### 4.4 Rust controller build and tests

The repository guide gives:

```sh
cargo build --locked --manifest-path runner/Cargo.toml
cargo test --locked --manifest-path runner/Cargo.toml
```

The runner guide gives the offline forms:

```sh
cargo build --offline --locked --manifest-path runner/Cargo.toml
cargo test --offline --locked --manifest-path runner/Cargo.toml
```

Expected: the build produces `runner/target/debug/followthrough` (`.exe` on Windows); Rust tests exercise the CLI with a Python fake boundary subprocess. `FOLLOWTHROUGH_TEST_PYTHON` may name an executable if `python` is not on `PATH`. Test runs under `runner/target/test-runs/fake-boundary-*` are synthetic grader results, not model results.

### 4.5 Trusted retry control invocation

Create `runs` first, then use a fresh output directory. The repository guide's form is:

```sh
runner/target/debug/followthrough control --project-root . --candidate references/retry-processing/correct/processor.py --python python --output-dir runs/example-001
```

The runner guide prints the bare command below; it requires the built binary to be resolvable:

```text
followthrough control --project-root . --candidate references/retry-processing/correct/processor.py --python python --output-dir runs/control-001
```

The integration contract's command shape is:

```text
followthrough control --project-root <path> --candidate <path> --python <executable> --output-dir <new path> [--timeout-seconds 60]
```

The contract form contains placeholders by design; it is not a copy-paste run. Relative candidate paths start at the selected project root; relative output and explicit Python executable paths start at the invocation directory; a bare Python name is resolved from absolute `PATH` entries.

Expected: exit 0 for a functional pass, 1 for a functional failure, 2 for candidate execution or infrastructure error; `summary.json` and `events.jsonl` are written for runs that reach a new output directory. A refused output path is a preflight failure and may leave no summary. The runner verifies the grade's candidate path and SHA-256 against the checkpoint it copied.

### 4.6 Direct grader invocation (contract form)

```text
python grader/retry_processing.py --candidate <absolute-or-relative processor.py> --output <new JSON file>
python grader/preview_cache.py --candidate <preview.py> --output <new JSON path>
```

The retry grader is also documented with `--self-test`. Use a new output path for each invocation; the preview-cache specification explicitly refuses an existing path and leaves it unmodified. Exit 0 means a complete functional grade was produced, including a functional FAIL; nonzero means candidate execution or grader/infrastructure error. A nonzero receipt may contain partial checks and must never be promoted to a pass.

### 4.7 Live readiness — gated, no command supplied

The adapter's entry point requires the exact `--live` flag, a finite positive `--max-cost-usd` covering the local reservation, and `DEEPSEEK_API_KEY` from the process environment. Run it **only** under a separate authorised paid operation with a verified access path and bounded spending scope. This checklist deliberately prints no live invocation: the exact command, key handling and spend bound must be reviewed before use. Never commit credentials or expose them to evaluated workspaces.

## 5. Expected control behaviour and receipts

| Observation | Correct interpretation |
|---|---|
| Self-test passes; correct fixture passes; negatives fail exactly their checks | Checker is behaving on authored fixtures |
| Runner exit 0 / `functional_pass` | Trusted local functional control passed; not workflow completion |
| Runner exit 1 / `functional_fail` | Valid functional failure; not an execution error |
| Runner exit 2 / `candidate_execution` | Candidate execution problem; not a functional score |
| Runner exit 2 / `infrastructure_error` or no summary | Environment/preflight/grade/receipt problem; not a candidate score |
| `evidence_kind: real_local_control` | Trusted local control evidence; not a model benchmark result |

Bounds to expect: default timeout 60 seconds (1–3600 accepted), each stdout/stderr reader stops after more than 32 KiB while receipts keep counts and limit flags, and candidate/grader/parsed-grade reads are each limited to 1 MiB. Cleanup attempts only the direct child and records whether it was reaped; descendants may survive. Interrupting the runner may leave an incomplete directory. Do not reuse a run directory or present an old receipt as evidence for a changed candidate.

## 6. Windows-specific considerations

- Use `runner\target\debug\followthrough.exe`; the bare `followthrough` form depends on how the build directory is placed on `PATH`.
- Pass an explicit Python executable when the bare name is unreliable; the runner resolves bare names from absolute `PATH` entries and resolves explicit relative paths from the invocation directory.
- A restricted temporary directory can cause permission failures. Use an owned, writable `TEMP`/`TMP` for the process and treat failures as infrastructure errors. Do **not** rewrite ACLs, disable protections or run as a different privilege level to make a control pass.
- Path checks reject parent traversal, symlinks and Windows reparse points in selected paths and assume a stable filesystem; they do not defend against concurrent replacement or hard-link aliasing. Keep the checkout, candidate and output parent under local control.
- Some candidate suites may skip a test without symlink-creation privilege; that is a disclosed platform condition, not a pass. The accepted 50-test suite does not depend on that privilege as far as the pinned baseline records.

## 7. Clean-package checks before publishing

1. Confirm the exact commit and package tree; for a release archive, verify it against the archive's **own frozen SHA-256 manifest**, not the baseline blob table above. Run the trusted commands only if the operator intends to reproduce them, and retain their output separately from formal-result claims.
2. Confirm no `.env.local`, `.coordination/`, raw provider traces, credential stores, private conversations, `runs/` artifacts or `tmp/` content are included. `.env.local` is the designated credential source and is ignored by Git; it must not be read into logs or copied.
3. Confirm README links and documentation links resolve, and that untracked preparation drafts (including the release-preparation baseline and the proposed release documents) are either deliberately added or not linked.
4. Confirm licence status is recorded as MIT in `LICENSE`/`NOTICE` unless Main records a later decision. No unlicensed third-party assets or code should appear.
5. Confirm CI status is described as unconfigured until the later sequential P6 CI assignment lands. Do not claim CI has run.
6. Confirm no statement calls the host controls a sandbox or presents fixture results as model, provider or campaign results.

## 8. Future real-evaluation steps — gates only

The following are **not runnable instructions** and no future command is invented here. Each is a gate that Main must close and then document with its own reviewed command:

1. Accept the scenario, isolation-plan and budget corrections (P1-SCENARIOS/r2, P2-ISOLATION-PLAN/r2, P3-BUDGET/r3) and integrate them.
2. Implement/accept the scenario executor, role loop and protected grading path; qualify isolation with a sanitized environment, no network, resource limits and no grader/secret mounts during agent actions.
3. Review credential loading and an aggregate budget ledger covering all roles; reconfirm rates and token semantics.
4. Run a separately authorised live readiness request and record the actual model string, usage and reservation outcome.
5. Freeze task/checker/request/settings/tool versions; then, if separately authorised, run the proposed 36 episodes with interleaved arms and preserved attempts.
6. Produce sanitised results and reporting, then have Main accept the release claims. Until then, this checklist reproduces controls only.

## 9. Facts Main must fill after qualification

| Field | Status at preparation |
|---|---|
| Final commit/tag and package filename | Unknown; baseline is `166a304` |
| Live model alias/snapshot and date | Unknown; no paid request made |
| Credential path and loader procedure | `.env.local` designated; loader/aggregate ledger not accepted |
| Aggregate spend bound and reconciliation | US$20 cap authorised; campaign ledger not implemented |
| Campaign command, environment and run count | Not defined; 36 episodes only proposed |
| Formal results, sanitised traces and dispositions | None exist |
| Video recording, captions and URL | None exist |
| Release archive identity | None yet; verify against the archive's own frozen manifest |
| Licence selection and attribution approval | MIT (`LICENSE`/`NOTICE`); third-party terms retained |
| Official rules, deadline, ZIP/video limits | Historical only; must be rechecked |
| CI workflow and evidence | Later sequential P6 assignment; none configured |
| Submission receipt/confirmation | None; no submission performed |

If any identity or gate is unknown, report it as unknown rather than filling it from assumption. Stop and return the question to Main.
