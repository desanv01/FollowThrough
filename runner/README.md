# FollowThrough local runner

Build from the repository root with `cargo build --offline --locked --manifest-path runner/Cargo.toml`. The Cargo package builds `runner/target/debug/followthrough` with an `.exe` suffix on Windows.

```text
followthrough control --project-root . --candidate references/retry-processing/correct/processor.py --python python --output-dir runs/control-001
```

Create `runs` first. Each invocation requires a fresh output directory. Relative candidate paths start at the selected project root. Relative output and explicit Python executable paths start at the invocation directory. A bare Python executable name is resolved using absolute entries in `PATH`.

Use this command only with trusted fixture code. **This is NOT a sandbox.** The Python grader executes the checkpointed candidate with local permissions. Path checks reject parent traversal, symlinks and Windows reparse points in project, candidate, grader and output parent paths. They assume a stable filesystem and do not defend against concurrent path replacement or hard-link aliasing. Keep the checkout and output parent under local control.

The runner copies the candidate to `checkpoint/processor.py` and hashes the copied bytes. `events.jsonl` records the checkpoint, grader launch command, grader source hash, observed process outcome and validated grade as separate events. The grader writes `grade.json` and the runner verifies the reported candidate path and SHA-256 against the checkpoint. The runner writes `summary.json` for executions that reach a writable new output directory. Detailed grader diagnostics remain in `grade.json`.

| Exit status | Summary status | Meaning |
| --- | --- | --- |
| 0 | `functional_pass` | Valid grade reports a functional pass |
| 1 | `functional_fail` | Valid grade reports a functional failure |
| 2 | `candidate_execution` | Grader exits unsuccessfully with a valid grade that attributes all errors to the candidate |
| 2 | `infrastructure_error` | Execution, timeout, output cap, grade, or receipt failure |
| 2 | No summary for a refused output path | Argument or preflight failure |

The evidence kind `real_local_control` identifies trusted local grading. Functional results do not establish workflow completion. The timeout defaults to 60 seconds and accepts 1 through 3600 seconds. Each stdout and stderr reader stops after detecting more than 32 KiB. Receipts keep counts and limit flags without retaining raw stream contents. Candidate, grader source and parsed grade are each limited to 1 MiB when the runner reads them.

On timeout or output failure the runner attempts to terminate and reap the direct child with a one-second cleanup allowance. It records whether the direct child was reaped. Descendants are outside the cleanup scope and may keep pipes open or survive termination. Interrupting the runner externally may leave the run directory incomplete.

`cargo test --offline --locked --manifest-path runner/Cargo.toml` uses a Python fake boundary subprocess to exercise the Rust CLI. Set `FOLLOWTHROUGH_TEST_PYTHON` to an executable path if `python` is unavailable on `PATH`. Test runs under `runner/target/test-runs/fake-boundary-*` are synthetic grader results.
