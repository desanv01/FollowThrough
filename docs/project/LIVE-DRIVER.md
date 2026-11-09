# Guarded R1 live driver (P3)

Status: P3-owned implementation for the local phase campaign. Source-level
rehearsal only in this unit. No provider call, canonical-ledger operation, Docker
operation or real R1 episode has been performed. Main/P6 own integration and
acceptance; P2 owns the real check port and protected container grade; P4 owns the
episode/report runner; P5 owns the attempt-receipt validator.

## Purpose

`episode/live.py` is the single paid-capable P3 entry. It composes the accepted
source without reimplementing it:

- accepted credential loader (`adapters.credentials.load_deepseek_key`) and durable
  `BudgetedDeepSeekClient` (`adapters.budgeted_deepseek`), which reserves the full
  envelope in the canonical SQLite ledger before transport;
- `episode.provider.BudgetedModelPort`, which enforces the frozen
  `json-action-0.1` envelope, local conservative reservation, per-call cap and
  dispatch deadline;
- `OperationGuard`, which bounds the authorized R1 operation: 16 model attempts,
  2,048 output tokens per call, 25 seconds per call, US$0.05 local reservation per
  call, US$0.60 operation reservation and 900 seconds wall including cleanup;
- injected P2 `LiveCheckBridge`/qualified session grade and the injected P4
  `episode.e2e.run_r1_smoke` runner, plus P4's merged `run_scenario_smoke` for
  non-R1 scenarios and structured-progress arms;
- one exclusive P2-authored Docker lock across public checks and protected grades;
- an exact `attempt-receipt-0.1` written atomically under the run root and a
  sanitized campaign `artifacts/attempts/<attempt_id>/` copy.

The Retry family uses `DockerSession`/`grade_session`; C1/C2/C3 use P2's
`cache_session`/`grade_cache` path, never the Retry grader. R1/R2/R3 are
version `v1`; C1/C2/C3 are version `v2`. An unqualified family/version is refused
before dispatch. `checkpoint_id` is forwarded only when explicitly supplied by a
reviewed later-pilot operation; R1 does not invent one.

No retry, fallback provider, parallel model call, alternate ledger, raw provider
output, prompt text, credential or host path is exposed. Unknown outcomes stay
unknown; failures print fixed codes only.

## Authorization sequence

Real dispatch requires **all** of the following, checked before provider
construction where possible:

1. `--reviewed-live` (exact reviewed live artifact) and `--runtime-authorized`.
2. The canonical ledger `<project>/.coordination/provider-budget.sqlite3` already
   exists. `authorize` refuses when absent; `build_provider` also refuses rather than
   letting the ledger constructor initialize implicitly.
3. A fresh `--run-root` whose parent exists and which does not already exist.
4. `--check-executable`, `--docker` and `--shim-dir` for the integrated P2
   check/grade components, each an existing file/directory. Missing or invalid
   paths refuse as `integration_components_missing`/`integration_components_invalid`
   before credential/ledger/provider work.
5. Executor paths are supplied by the reviewed integration; this module does not
   hardcode or copy P2 source.

If the first-use ledger is absent, the only supported creation path is the explicit
reviewed action below. It records the fixed campaign identity and US$20 ceiling and
never replaces an existing ledger:

```powershell
python -m episode.live --project-root <absolute project> `
  --run-root <unused absolute run path> --episode-id <id> `
  --campaign-id <reviewed campaign id> --reviewed-live --runtime-authorized `
  --initialize-ledger
```

It prints only the canonical path, `canonical_ledger_initialized` and `cap_usd`.
No provider request is made. Running it without both authorization flags refuses.

## Real R1 invocation

```powershell
python -m episode.live --project-root <absolute project> `
  --run-root <fresh absolute run directory> --episode-id <reviewed id> `
  --campaign-id <reviewed campaign id> `
  --operation-id <reviewed operation id> --run-id <reviewed run id> `
  --stage development|pilot|formal `
  --scenario-id R1 --arm ordinary --trial 1 `
  --dataset-id ds-end-to-end-r1 `
  --campaign-root <absolute campaign root> `
  --reviewed-live --runtime-authorized `
  --check-executable <reviewed container-executable> `
  --docker <reviewed docker path> --shim-dir <reviewed protected shim dir>
```

The reviewed episode, operation, run and stage identities are validated before
dispatch and forwarded with `live=True` and an operation dictionary to the merged
P4 runner. R1/ordinary uses `run_r1_smoke`; non-R1 or structured-progress runs use
`run_scenario_smoke` with scenario, arm, trial, dataset, stage and operation/run
identity. The P4 runner requires `evidence_kind=real_provider`, explicit
operation/run IDs and binds them into the report row and `episode-binding.json`.
The P3 guard updates the operation with known reservation/estimate/call facts;
unknown amounts stay null.

Before any provider dispatch, the driver acquires
`lease/P2-AUTHORED-001/ACTIVE` with exclusive creation, verifies its P3
owner/operation/run record, and holds it across the P4 episode, public checks and
protected grade. It preserves P2's parent owner record. The lock is released only
after all check/grade cleanups are settled; unknown cleanup leaves the lock held
and the operation uncertain. The `leases/docker.json` file is a handoff/status
record, not the lock.

The key is loaded from the ignored project `.env.local` only by the accepted loader;
it is never printed, copied or placed in evidence. The command creates the canonical
operation only under the reviewed bounds. A timeout or uncertain outcome keeps its
reservation and is never automatically retried. The output JSON contains only the
report row status/outcomes, episode/operation/run/stage identity, bounded guard usage,
the canonical ledger path, the safe ledger snapshot and the relative
`attempt-receipt.json` path/hash.

Pilot/formal invocations use the same shape with their own independently reviewed
finite bounds and frozen scope within the remaining approved budget; this driver
does not authorize them.

## First-use / failure semantics

- Missing canonical ledger, missing credential, missing components, an existing run
  root, alternate ledger path, missing/reviewed flags, invalid/missing episode,
  operation, run, stage or scenario/arm identity, a runner that cannot preserve the
  runner contract, or existing ledger on initialization refuse before a provider call.
- The canonical ledger is the only aggregate budget boundary. `OperationGuard`
  reads sanitized observations and never reserves, releases or invents a balance.
  It records known reservation/estimated-cost/call-count facts into the operation
  passed to P4; unknown amounts remain null.
- Input/provider failures retain the ledger reservation and fixed code; they are
  recorded as operation errors, not model quality failures.
- A real check/grade failure or unknown cleanup is not converted into a model
  success; P2/P4 own those labels.
- The 900-second wall includes runner/check/grade cleanup as measured after the
  runner returns; it does not claim to forcibly interrupt a blocking callback.

## Attempt receipt

After a terminal outcome, the driver writes exact `attempt-receipt-0.1` JSON
under `<run-root>/attempt-receipt.json` and a sanitized copy under
`<campaign>/artifacts/attempts/<attempt_id>/attempt-receipt.json`. It uses the
merged P5 validator; success, failure, uncertain spend and unknown cleanup are
retained. Canonical reservation IDs are copied from the ledger snapshot under
`budget.operation_ids`; unknown usage/model/cleanup fields stay null. The receipt
contains no secrets, raw prompts/messages, host paths, grader source or synthetic
output presented as runtime evidence. Structurally valid, ledger-ready and
report-ready are separate validator results.

## Offline evidence in this unit

`tests/test_live.py` covers guard limits, refusal ordering, operation/run/episode/
stage forwarding, live-mode report/binding identity, integrated non-R1
scenario/structured-arm routing, explicit version refusal, protected-grade version/
checkpoint forwarding, Retry vs Cache grade paths, receipt success/unknown fixtures
against the P5 validator, exclusive lock acquire/release and an isolated
fake-provider ledger+lock rehearsal. These are source tests, not runtime evidence.

## Remaining integration gates

- P6 must review and merge the corrected P3 head; this unit is not integrated.
- P4's reviewed G4-R1-READY and native Codex launch are required before paid
  dispatch; the no-dispatch canonical-ledger preflight is recorded separately.
- The managed shell cannot reach Docker; the reviewed native route is required.
- This unit does not itself authorize R1, pilot or formal spending.
