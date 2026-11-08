# Container check executor (source)

Status: **synthetic_executor_control only**. `P2-EXECUTOR/r1` implements the real
Docker CLI lifecycle behind an injected command boundary. The production CLI uses a
real subprocess wrapper; offline tests inject a scripted boundary or an owned fake
executable. This assignment does **not** invoke installed Docker, real containers,
candidate Python, providers or a real daemon. It is not isolation qualification,
not cleanup qualification and not protected grading. Actual Docker/runtime
qualification remains Main-owned.

## Files

Five proposed files only:

- `runner/src/container_exec.rs` Ã¢â‚¬â€ lifecycle, receipt, process wrapper, injectable boundary.
- `runner/src/bin/container-check.rs` Ã¢â‚¬â€ production CLI.
- `runner/tests/container_exec.rs` Ã¢â‚¬â€ synthetic lifecycle/CLI tests and spawned-helper probes.
- `runner/tests/fixtures/fake_docker.rs` Ã¢â‚¬â€ owned fake Docker helper; no Docker/candidate code.
- `docs/project/CONTAINER-EXECUTOR.md` Ã¢â‚¬â€ this document.

The accepted planner (`runner/src/isolation_plan.rs`) and trusted controller files
are included unchanged. No crate, lock, dependency, planner or controller edit is
required. Rust std plus locked `serde`, `serde_json` and `sha2` are used.

## CLI

```text
container-check --workspace <absolute-flat-directory> --run-id <id> \
  --output-dir <new-absolute-directory> --docker <absolute-executable> --execute
```

- All path arguments must be absolute. The workspace and output/output-parent must
  contain no symlink or Windows reparse component. The output must be a new
  directory that is neither the workspace, inside the workspace, nor an ancestor of
  it. The Docker executable must be an existing regular file outside the workspace.
- `--execute` is required. Without it the CLI refuses before spawning anything.
- There are no command, image, mount, host, environment, timeout or network
  override options. The immutable image and all container restrictions come from
  the accepted planner.
- The local daemon endpoint is fixed: `npipe:////./pipe/docker_engine` on Windows
  and `unix:///var/run/docker.sock` on Unix. Unsupported platforms refuse.
- The CLI exit is `0` for `check_passed`, `1` for `check_failed`, and `2` for
  refusal/error. `--help` prints usage.

### Environment and endpoint

Every Docker invocation uses `env_clear` and then adds only present `PATH`,
`SystemRoot`, `WINDIR`, `TEMP`/`TMP` on Windows (`PATH`, `TMPDIR` on Unix) plus an
executor-owned `DOCKER_CONFIG` empty directory under the output root. `DOCKER_HOST`,
`DOCKER_CONTEXT`, TLS/proxy/API-key variables, `HOME`/`USERPROFILE`, stdin and host
shell/socket paths are not forwarded. All commands are argv vectors with `--host`
and the fixed endpoint before the subcommand; there is no shell construction.

## Lifecycle

1. **Validate and prepare.** The planner rebuilds and validates the flat workspace
   inventory itself (a saved argv is never trusted). Output and Docker paths are
   validated as above. The output root, `docker-config/`, bounded streams and
   receipt live outside the agent mount. A per-invocation owner token is derived
   from high-resolution time, PID and a process-local counter through SHA256; it is
   added as `org.followthrough.owner_token`, alongside the planner
   `org.followthrough.run_id` label.
2. **Bounded prerequisites.** `docker --host ... info` and immutable
   `docker image inspect` run within the setup budget. Requirements: Linux daemon,
   exact immutable image present, `RepoDigests` contains the contract image,
   normalized daemon/image architectures match (`x86_64`/`amd64`,
   `aarch64`/`arm64` only), image OS Linux. No pull, login, tag fallback or
   ambiguous accepted fact.
3. **Create stopped container.** The planner's fixed 25-element argv is transformed
   in memory from `run` to `container create`; `--rm` is removed so the lifecycle is
   observable; `--log-driver=none`, the ownership label and all other planner
   restrictions remain. Create must emit exactly one full 64-hex container ID
   (surrounding whitespace allowed) and exit successfully with complete, clean
   capture. Name collision is a failure and never removes an existing unrelated name.
4. **Inspect before start.** By ID, match: full ID, owned name, both labels,
   inspected image reference and image ID, `65534:65534`, entrypoint `python`,
   command `-I -B /workspace/public_checks.py`, `/workspace`, read-only root,
   `network=none`, `cap-drop=ALL`, exact `no-new-privileges` or `no-new-privileges:true` (never a disabled form or extra security option), explicit boolean `Privileged=false`, PID limit 64, memory
   `268435456`, one CPU (`NanoCpus=1000000000`), exact `/tmp` tmpfs restriction,
   logging `none`, exactly one `/tmp` tmpfs, explicit `AutoRemove=false`, no extra mounts/privileges/host namespaces, and exactly one
   read-only bind whose Docker-normalized `Source` equals the emitted canonical
   workspace. Unknown/missing fields refuse start with a sanitized diagnostic.
5. **Rehash before start.** The planner is rebuilt and inventory names/bytes/hashes
   must match. Changed inputs refuse start; no candidate code executes on the host.
6. **Attach with bounds.** `container start --attach <owned-ID>` runs with stdin
   null, 60-second execution budget, 32 KiB retained per stream (overflow detected
   after `cap+1`), and cooperative cancellation. Raw check stdout/stderr are written
   to bounded files outside the mount. The CLI is never executed as arbitrary
   caller argv.
7. **Inspect actual state.** A separate total 5-second observation inspects the
   container again. `check_passed`/`check_failed` require `exited`, not running,
   non-zero `StartedAt`/`FinishedAt`, clear error, `OOMKilled=false`, owned identity
   and an integer `State.ExitCode`. A `created`/`dead`/paused container or default
   zero exit is not proof of execution. Attach code is accepted only if zero or
   consistent with the inspected exit; otherwise infrastructure error.
8. **Cleanup.** In a separate total 5-second allowance, inspect the exact owned
   identity before `container rm --force <full-owned-ID>`, then verify absence with
   `container ls --all --quiet --no-trunc` restricted by both labels and the owned name.
   Cleanup commands ignore an already-set cancellation flag but obey the remaining
   deadline. Any inspect/rm/list failure or non-empty listing is unknown, not
   absence. An uncertain create response is queried by unique owner token plus
   run-ID label and owned name. Only one fully identity-matched result may be removed.
   A clean nonzero exit with empty stdout and a Docker name-conflict diagnostic
   for the requested name on stderr is reported as a collision without removing
   the existing container. An empty listing after ambiguous create is still
   unknown because a late creation may appear. A matched owned container can be
   removed, but ambiguous creation still yields infrastructure error.
9. **Receipt.** A single `receipt.json` is written with `create_new`, plus bounded
   `stdout.txt`/`stderr.txt` when attach ran. A receipt write failure is nonzero and
   never claims persisted evidence. Stream write failure forces
   `infrastructure_error`.

### Status and exit

| Receipt status | CLI | Meaning |
| --- | ---: | --- |
| `check_passed` | 0 | Corroborated inspected exit 0, settled cleanup |
| `check_failed` | 1 | Corroborated nonzero inspected exit, settled cleanup |
| `refused` | 2 | Pre-start refusal (identity/source/policy), settled cleanup |
| `timeout` | 2 | Attach exceeded 60 s |
| `output_limit` | 2 | A stream exceeded 32 KiB |
| `cancelled` | 2 | Cooperative cancellation observed |
| `infrastructure_error` | 2 | Setup/state/identity/cleanup/receipt failure, unknown cleanup |

Unknown cleanup overrides `check_passed`/`check_failed` with
`infrastructure_error`, retaining the observed public-check exit. Public check
exit 0 is not hidden functional correctness, workflow completion, isolation, or
permission to skip review.

## Budgets

| Phase | Bound |
| --- | --- |
| Total setup (server/image/create/prestart inspect/rehash) | 30 s |
| Each setup command | 5 s |
| Check execution (`start --attach`) | 60 s |
| Each captured stream | 32 KiB retained, overflow after 32 KiB + 1 |
| Post-attach state observation | 5 s total |
| Cleanup | separate 5 s total |

Each command wrapper receives one absolute allowance that covers the wait,
termination/kill-reap reserve and concurrent reader settlement. Reader waits and
reap polling are taken from that same deadline, never appended to it. The setup
budget is rechecked after the pre-start inventory rehash. Post-attach observation
and cleanup each keep their own separate allowance. The production CLI exposes no
extension/weakening switch; tests shorten deadlines only through the library
configuration.

## Process wrapper

`ProcessRunner` spawns the Docker executable with `env_clear`, the sanitized
environment, stdin null and piped stdout/stderr. Concurrent reader threads retain
at most the stream cap and detect overflow after `cap+1`. A poll loop enforces
timeout and cancellation, kills the direct child, reaps it within a finite grace
window, and reports whether the direct child was reaped and whether reader reports
settled. Readers are never joined unboundedly. This is direct-child settlement
only; descendants are not tracked and are not container cleanup proof.

## Receipt (`container-check-0.1`)

Fields include: `schema_version`, `evidence_kind` (`synthetic_executor_control` or
`real_container_check`), `run_id`, status/diagnostic, workspace path/candidate
identity/inventory, immutable image and observed image ID plus daemon/image
architecture, container ID/name/owner token/attach facts, `public_check_exit`,
per-command observations (argv, PID, exit/null, stream byte counts, timeout/
overflow/cancel/spawn/reap/reader flags, elapsed), cleanup state/method/removal
verification/detail, bounded stream file paths, elapsed, declared budget fields, and
limitation strings. Raw Docker diagnostics are not retained; raw check streams are
bounded host-only files outside the mount.

## Offline evidence

`runner/tests/container_exec.rs` runs 19 integration tests plus the accepted
planner's own namespace test; the bin harness runs that planner test too. They cover:

- successful check, nonzero check, and created/dead/OOM/error/missing-times/default-
  zero states refusing success;
- Linux daemon, exact image digest/RepoDigest, image missing and architecture
  mismatch;
- exact create transformation (`--rm` removed, `--log-driver=none`, owner label),
  fixed endpoint, sanitized environment and no env/volume override;
- token/ID/mount/security/image inspection mismatch and source drift refusing start;
- image formatted-object shape, no-trunc default-width cleanup, exact typed security facts,\n  complete typed start/goal evidence and either-stream overflow;\n- create collision (recognized collision text), malformed/nonzero create with owned query, ambiguous create with matching owned cleanup,
  ambiguous create with empty or unrelated listing remaining unknown;
- unrelated-container protection; cleanup rm/list/identity failure unknown overriding
  a successful check; cleanup deadline behavior;
- attach timeout, output overflow, cancellation; post-attach inspection timeout;
- existing output, output inside workspace, missing/contained Docker, execute gate;
- CLI refusal paths and `--execute` gate;
- receipt planting causing a reported receipt-write failure;
- a real spawned `fake_docker.rs` helper through `ProcessRunner`: bounded capture
  with overflow (`32 KiB+1` detection), timeout, direct-child kill/reap, and a full
  library lifecycle whose first setup command times out and is reaped.

All tests are synthetic. The fake helper is an owned local executable; it never
invokes Docker, a container, candidate Python or a provider. No real daemon is
contacted.

## Assumptions and limitations

- A stable workspace and trusted local Docker daemon are assumed; concurrent
  filesystem replacement and racing external container mutation are outside this
  finite guarantee.
- Setup/execution/post-inspection/cleanup budgets are command/phase bounds, not a
  hard real-time guarantee for host filesystem or receipt flush.
- Reader threads are detached; `reader_settled` reports whether their bounded report
  arrived rather than proving thread join.
- Only direct Docker CLI children are settled; descendants and daemon-side effects
  are outside this wrapper's proof.
- Ambiguous creation may leave a late container; empty observations do not prove
  daemon-side settlement and are recorded unknown.
- The wrapper deliberately passes a minimal environment and the fixed endpoint; the
  later actual qualification must establish installed-daemon compatibility with that
  deliberate environment.
- No actual container execution, isolation, negative sandbox qualification, hidden
  grading, image availability qualification or cleanup acceptance is claimed.
