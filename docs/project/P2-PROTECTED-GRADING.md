# P2 protected grading

Status: local source unit, not yet natively qualified.

The trusted host owns Retry/Cache expected inputs, durable state, fault schedule,
snapshots, observation framing and the grade. The candidate runs in a separate
container with only its candidate file and the minimal shim. Candidate stdout/status
is an untrusted reply. Reference and control directories, credentials, `.env.local`,
provider code and the canonical ledger are never mounted.

## Retry boundary

- Host: `episode/protected_session.py` owns a SQLite Store, transaction
  begin/commit/rollback, faults (`after_credit`, `after_mark`, `after_commit`),
  reopen/lost-ack snapshots and expected checks.
- Candidate: `protected/retry_shim.py` exposes a line-framed Store RPC. It receives
  one event at a time and cannot define durable state or a passing result.
- Controls: correct reference, known-incorrect mark-first, forged-observation canary
  and combined boundary/failure canary under `protected/controls/`.
- Grade receipt binds candidate SHA-256, requirements version, checkpoint ID and
  cleanup. Protected evidence requires an actual Docker inspect witness; synthetic
  tests use the explicit `synthetic_development` label.

## Cache boundary

- Host: `episode/protected_cache.py` owns the cache mapping, renderer fixtures,
  renderer authority, request sequence, cache snapshots and grade. v1/v2 version and
  checkpoint ID are bound in the receipt.
- Candidate: `protected/cache_shim.py` exposes a remote MutableMapping and renderer.
  Cache reads/writes and renderer calls are host-served requests. Invalid arguments
  must make zero cache/renderer accesses; renderer failures must preserve cache state.
- Checks cover cold/repeat, omitted vs explicit full, project/document/revision/mode
  separation, second fixtures, authoritative literal renderer content, invalid
  arguments, renderer failure and retry, plus host cache-state and renderer-call
  authority.

## Docker lifecycle

`DockerSession` uses `docker create` with a pinned image and digest, no network,
read-only root, cap-drop ALL, no-new-privileges, nonroot user, PID/memory/CPU/tmpfs
bounds, then verifies the actual `docker inspect` facts before `docker start -a -i`.
Cleanup is exact-name `docker rm --force` followed by an absence readback. Names are
run-ID + random suffix. Docker CLI receives a sanitized environment. Frames are
bounded; timeouts, overflow, malformed output and broken pipes are classified.

## Authored native commands (review-gated)

Retry (four isolated candidate controls and one failing public control):

```powershell
python -m episode.protected_batch --execute `
  --docker "C:\Program Files\Docker\Docker\resources\bin\docker.exe" `
  --run-root "<P2-worktree>\tmp\Authored-Retry-01"
```

Expected receipts: `correct.receipt.json` passed; `incorrect.receipt.json` failed;
`forged.receipt.json` not passed; `boundary_canary.receipt.json` not passed;
`failing_public.receipt.json` check_failed, each with cleanup settled.

Cache (three isolated controls; ≤3 lifecycles):

```powershell
python -m episode.protected_cache_batch --execute `
  --docker "C:\Program Files\Docker\Docker\resources\bin\docker.exe" `
  --run-root "<P2-worktree>\tmp\Authored-Cache-01"
```

Expected receipts: `correct_v2.receipt.json` passed; `v1_partial.receipt.json` failed
(mode separation); `original.receipt.json` failed (project/revision/mode collisions).

Both commands preflight Docker before creating a run root and default to refusal
without `--execute`. Managed-shell Docker access is currently denied; native receipts
are required before G2-RETRY-NATIVE/G2-CACHE-NATIVE can be emitted.

## Evidence limits

- `tests/test_p2_protected.py` and `tests/test_p2_protected_cache.py` use synthetic
  protocol speakers; they prove host authority and control behavior, not native
  isolation.
- The native R3 public-check pass is separate and must not be presented as a
  protected Retry/Cache grade.
- No provider, credential, canonical ledger, publication or hosted Git operation is
  part of P2.
