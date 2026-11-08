# Guarded R1 live driver (P3)

Status: P3-owned implementation for the local phase campaign. Offline/fake-transport
rehearsal only in this unit. No provider call, canonical ledger initialization,
Docker operation or real R1 episode has been performed. Main/P6 own integration and
acceptance; P2 owns the real check port and protected container grade; P4 owns the
episode/report runner.

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
  call, US$0.80 operation reservation and 900 seconds wall including cleanup;
- injected P2 `LiveCheckBridge`/qualified session grade and the injected P4
  `episode.e2e.run_r1_smoke` runner.

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
  --reviewed-live --runtime-authorized `
  --check-executable <reviewed container-executable> `
  --docker <reviewed docker path> --shim-dir <reviewed protected shim dir>
```

The reviewed `--episode-id` is threaded to the P4 runner with the operation. The
current accepted `e2e.run_r1_smoke` does not accept an episode identity, so real
dispatch refuses with `runner_episode_identity_unsupported` rather than silently
recording the scripted smoke identity. P4 must add that parameter (or an equivalent
explicit identity seam) before a real R1 run.

The key is loaded from the ignored project `.env.local` only by the accepted loader;
it is never printed, copied or placed in evidence. The command creates the canonical
operation only under the reviewed bounds. A timeout or uncertain outcome keeps its
reservation and is never automatically retried. The output JSON contains only the
report row status/outcomes, bounded guard usage, the canonical ledger path and the
safe ledger snapshot.

Pilot/formal invocations use the same shape with their own independently reviewed
finite bounds and frozen scope within the remaining approved budget; this driver
does not authorize them.

## First-use / failure semantics

- Missing canonical ledger, missing credential, missing components, an existing run
  root, alternate ledger path, missing/reviewed flags, invalid episode ID, a runner
  that cannot preserve that episode ID, or existing ledger on initialization refuse
  before a provider call.
- The canonical ledger is the only aggregate budget boundary. `OperationGuard`
  reads sanitized observations and never reserves, releases or invents a balance.
- Input/provider failures retain the ledger reservation and fixed code; they are
  recorded as operation errors, not model quality failures.
- A real check/grade failure or unknown cleanup is not converted into a model
  success; P2/P4 own those labels.
- The 900-second wall includes runner/check/grade cleanup as measured after the
  runner returns; it does not claim to forcibly interrupt a blocking callback.

## Offline evidence in this unit

`tests/test_live.py` uses a fixture project, synthetic key, fixture canonical ledger
and fake transport to prove the actual accepted wrapper + bridge composition; it also
covers guard limits, default/missing-component refusal ordering, injected P4 runner
wiring and explicit first-use initialization (including no replacement). No executor,
Docker, provider, network, real credential or real canonical ledger is used.

## Remaining integration gates

- P2 `LiveCheckBridge`/protected session and P4 `e2e.run_r1_smoke`/report units must
  be integrated before the real CLI can run end to end.
- G3-PORTS requires independent rehearsal review after that integration; G4-R1-READY
  from P4 and the reviewed ledger decision are required before any paid dispatch.
- This unit does not itself authorize R1, pilot or formal spending.
