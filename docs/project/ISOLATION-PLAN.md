# Container check plan (source preparation)

Status: **execution plan not run**. `P2-ISOLATION-PLAN/r2` adds a typed Rust planner
that validates one prepared, flat check workspace and writes a JSON Docker command
plan. It does not start Docker, a container, a shell, a provider, or candidate code.
It is not sandbox qualification, not a runtime isolation test, and not protected
grading. Those are later tasks with separate ownership and evidence. r2 corrects
the r1 mount-source identity defect (F1).

## Files

Proposed new files only:

- `runner/src/isolation_plan.rs` — typed plan, inventory validation, hashing, fixed argv.
- `runner/src/bin/isolation-plan.rs` — `isolation-plan` CLI.
- `runner/tests/isolation_plan.rs` — focused planner/CLI tests.
- `docs/project/ISOLATION-PLAN.md` — this document.

Existing `runner/Cargo.toml`, `runner/Cargo.lock`, `runner/src/main.rs`,
`runner/src/paths.rs` and `runner/src/process.rs` are unchanged. No crate, dependency,
feature or lock change is required; the code uses only `std`, `serde`, `serde_json`
and `sha2`.

## CLI

```text
isolation-plan --workspace <existing-flat-directory> --run-id <id> --output <new-json-file>
```

- `--workspace` may be absolute or relative to the invocation directory. It must
  name an existing directory. Parent (`..`), drive-relative and rooted-relative
  path forms and Windows alternate-data-stream components are refused.
- `--run-id` is 1..48 bytes of ASCII alphanumeric, `_` or `-`. The container name
  is `ft-check-<run_id>` and the label is `org.followthrough.run_id=<run_id>`.
- `--output` must name a new JSON file whose existing parent directory is outside
  the workspace. The file is created with exclusive-create semantics; an existing
  output is never overwritten. Missing parent directories are not created.
- Only `--workspace`, `--run-id` and `--output` are functional options. `--help`
  prints usage. Unknown, duplicate and missing options fail.
- Exit status is `0` when one plan was written and `2` for any validation or write
  failure. Diagnostics are fixed codes plus short messages and never include file
  contents. On success the CLI states that a plan was written and that no execution
  was attempted.

Example:

```text
isolation-plan --workspace "work space" --run-id demo-01 --output plan.json
```

## Output schema (v0.1)

```json
{
  "schema_version": "0.1",
  "kind": "agent_check_plan",
  "evidence_kind": "execution_plan_not_run",
  "run_id": "demo-01",
  "container_name": "ft-check-demo-01",
  "image": "python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3",
  "program": "docker",
  "workspace": "<canonical directory emitted only after conventional-path identity recheck>",
  "candidate_file": "processor.py",
  "inventory": {
    "files": [{"name": "processor.py", "role": "candidate", "bytes": 49, "sha256": "..."}],
    "file_count": 3,
    "total_bytes": 123,
    "per_file_limit_bytes": 1048576,
    "total_limit_bytes": 2097152
  },
  "resources": {
    "read_only_root": true,
    "workspace_read_only": true,
    "network": "none",
    "drop_all_capabilities": true,
    "no_new_privileges": true,
    "pids_limit": 64,
    "memory": "256m",
    "cpus": "1",
    "user": "65534:65534",
    "user_uid": 65534,
    "user_gid": 65534,
    "tmpfs": "/tmp:rw,noexec,nosuid,size=16m",
    "pull": "never",
    "remove_after_exit": true
  },
  "argv": ["run", "..."],
  "executor_policy": {
    "enforcement": "declared_policy_only_not_enforced",
    "runtime_seconds": 60,
    "stdout_limit_bytes": 32768,
    "stderr_limit_bytes": 32768,
    "cleanup_deadline_seconds": 5
  }
}
```

The inventory contains the exact bytes that were read and hashed, sorted by name.
The plan never contains a PID, exit status, grade, execution success, or stream
content. The `executor_policy` values are declarations for a future executor, not
enforcement or observation claims.

## Inventory validation

The workspace is an explicit existing directory. Every path component is inspected
with non-following metadata; a symlink, junction, mount point or other Windows
reparse point anywhere in the workspace path is refused. The final canonical path
must be a Unicode directory. Inventory must be flat and use the exact allowlist:

| Entry | Requirement |
| --- | --- |
| `processor.py` | exactly one candidate; mutually exclusive with `preview.py` |
| `preview.py` | exactly one candidate; mutually exclusive with `processor.py` |
| `public_checks.py` | required |
| `TASK.md`, `SPEC.md`, `WORKFLOW.md`, `HISTORY.json` | optional context files |

Any other file, any subdirectory, both candidates, a missing candidate, or a
missing `public_checks.py` is refused. Names must be Unicode. Each accepted entry
must be a regular file (not a directory, device, socket or link). Per-file size is
at most 1 MiB and the total of accepted bytes is at most 2 MiB; the limit is
enforced against the bytes actually read, not only against a reported size. A file
whose length changes between metadata inspection and the bounded read is refused
as `workspace_changed`.

The canonical workspace path must be safe as a Docker `--mount` source and must
identify the directory whose inventory was hashed. On Windows the canonical path
is an extended-length verbatim path; only canonical drive (`\\?\C:\...`) and UNC
(`\\?\UNC\server\share\...`) forms are converted to conventional Docker mount
strings. Before emission, the conventional path is canonicalized again and must
equal the validated canonical directory exactly. A conversion that resolves to a
different directory (for example a trailing-dot or trailing-space sibling) or
cannot be resolved is refused with `mount_path_identity`; an unsupported Windows
namespace is refused with `unsupported_mount_namespace`. The planner never
silently normalizes to another directory. This is a stable-path identity check,
not race-proof filesystem access. Commas, CR, LF and NUL are refused; ordinary
Windows drive colons and spaces are valid. Alternate data stream components
(`name:stream`) are refused on Windows.

The plan mounts only this flat workspace at `/workspace`, read-only. It cannot
express the project root, grader, references, `.env`, Docker socket or controller
metadata. Those names fail the allowlist before a plan is produced.

## Fixed command plan

`argv` is built as a vector and serialized as a JSON array. It is never assembled
as a shell string and no shell is involved:

```text
docker run
  --pull=never --rm
  --name ft-check-<run_id>
  --label org.followthrough.run_id=<run_id>
  --network=none --read-only --cap-drop=ALL
  --security-opt=no-new-privileges
  --pids-limit=64 --memory=256m --cpus=1 --user=65534:65534
  --tmpfs /tmp:rw,noexec,nosuid,size=16m
  --workdir=/workspace
  --mount type=bind,src=<canonical-workspace>,dst=/workspace,readonly
  --entrypoint=python
  python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3
  -I -B /workspace/public_checks.py
```

The image is referenced by immutable digest. There is no tag fallback and the plan
does not pull. The image is the previously inspected local Python 3.11.15 image;
its availability must be rechecked by the later execution owner. There is no
caller-supplied command, environment, mount, image, network, capability or
privilege override; the CLI has no option that could add one.

## Stable-filesystem assumption

The plan records hashes of a workspace at plan time. It assumes a stable host
filesystem: the workspace is not replaced or mutated between planning and a later
execution, and hashing is not protected against concurrent replacement or hard-link
aliasing. A plan is not a receipt and not a sandbox. Any later change to the
workspace invalidates its recorded hashes; the executor must re-check inventory and
hashes before running and must refuse on mismatch. `process.rs` provides bounded
direct-child control for the existing local runner only; it does not prove
container cleanup and must not be reused as such a claim.

## Later executor obligations (not implemented here)

Before and during actual container execution, the future owner must:

1. Re-validate the workspace inventory and hashes, and recheck that the exact image
   digest is installed and qualifies for the intended platform/architecture.
2. Create or confirm an exclusively owned container identity (`ft-check-<run_id>`
   plus the label) and refuse collisions with any other container.
3. Bound wall-clock runtime to 60 seconds, each captured stream to 32 KiB, and a
   5-second cleanup deadline; enforce the declared resource limits at runtime.
4. Run `docker run` from the fixed argv without shell interpolation, join or bound
   the CLI subprocess, and avoid deadlock on stdout/stderr.
5. On timeout, overflow, cancellation or error, cancel and remove only the owned
   container. If removal or identity cannot be proven, record cleanup as unknown
   rather than claiming success. Do not run `docker kill`/`docker rm` against an
   unverified name in a way that could target an unrelated container.
6. Keep host-side receipts and capture files outside the mounted workspace.
7. Keep hidden grading separate from the visible workspace; `public_checks.py` in
   this plan is not protected grading and this plan is not isolation qualification.

## Tests and limitations

`runner/tests/isolation_plan.rs` contains 12 inherited focused offline tests plus
two Windows path-identity regression tests. The planner module also carries one
Windows namespace-conversion unit test, exercised by the same test harness.
Together they cover the retry and cache
inventories, exact hashes, deterministic re-planning, spaces in paths, invalid run
IDs and mount delimiters, missing/both/unexpected entries, per-file and total
bounds, exclusive output creation, external working directories, the exact fixed
argv and absence of hidden overrides, source-level absence of a process runner, a
fake-Docker sentinel on `PATH` that is never invoked, and distinct conventional and
verbatim trailing-dot/trailing-space directories with different inert contents.
The path-identity tests require the unsafe alias to produce no plan and the
ordinary sibling to produce a plan whose emitted workspace resolves to the bytes
actually hashed. The tests use inert synthetic text and never execute candidate
code.

Run from the candidate overlay with an owned target directory:

```text
set CARGO_TARGET_DIR=<absolute overlay>/tmp/build
cargo test  --offline --locked --bin isolation-plan --test isolation_plan
cargo build --offline --locked --bin isolation-plan
```

Platform limits observed on Windows: creating symlinks/reparse points requires a
privilege the test account does not hold, and Windows refuses newline/CR directory
names. The symlink/reparse and newline/CR workspace checks therefore reported
honest `SKIP` reasons instead of passes. Comma workspaces, NUL/CR/LF delimiter
checks through the validator, drive-colon/space paths, and literal verbatim
trailing-dot/trailing-space directories (created through the verbatim namespace)
were exercised. On Windows, a fake `docker.cmd` is not directly spawnable by
`CreateProcess`; the primary sentinel evidence is that neither the planner nor the
CLI imports or calls a process-spawning API, and the sentinel file was not touched.

This candidate is source-plan and offline-test evidence only. It has not integrated
with the accepted runner, has not been reviewed, and makes no claim about actual
container execution, cleanup, isolation or protected grading.
