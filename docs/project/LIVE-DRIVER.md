# Guarded live driver

`python -m episode.live` is the paid-capable entry point. Follow the complete
[qualified evaluation guide](QUALIFIED-EVALUATION.md) for prerequisites, exact
preflight/dispatch templates and current qualification status. CLI flags describe
supported source behavior; they do not authorize spending.

The driver composes the credential loader, durable campaign ledger, JSON-action
model port, serial role controller, public container-check bridge, protected grader
and report projection. It supports `development`, `pilot` and `formal`, all six
scenarios, and `ordinary` / `structured_progress` arms. R1/R2/R3 use Retry v1;
C1/C2/C3 use Cache v2. `--requirements-version` must match that qualified family.

Each operation binds its episode, operation and run IDs, scenario, arm, trial,
dataset, stage and reviewed source through a fresh `--g4-gate`. Use `--source-root`
for the clean Git checkout and `--project-root` for trusted storage. The explicit
`--ledger-path` must name `<project-root>/.coordination/provider-budget.sqlite3`;
alternate ledgers are refused. Run from the source checkout when roots differ.
The source checkout must contain neither `.env.local` nor the canonical ledger.

Current bounds apply to every stage and arm:

| Bound | Value |
| --- | ---: |
| Model call attempts | 16 |
| Output tokens per call | 2048 |
| Provider deadline per call | 25 seconds |
| Reservation ceiling per call | USD0.05 |
| Reservation ceiling per operation | USD0.60 |
| Operation wall allowance including cleanup | 900 seconds |
| Cumulative campaign ceiling | USD20 |

Reservations and estimated costs are distinct from provider invoice charges. A
maximum-reservation sequence can exhaust USD0.60 before the16-call ceiling.
Unknown dispatch/cleanup retains uncertainty; no automatic retry or fallback
provider exists. The sequential controller cannot forcibly interrupt arbitrary
blocking callbacks; transport/executor bridges own their deadlines.

An existing canonical ledger is required. First-use creation is an explicit
reviewed action, never an implicit alternate-budget escape:

```powershell
python -m episode.live --project-root '<absolute trusted project root>' `
  --run-root '<unused absolute run directory>' --episode-id '<fresh episode id>' `
  --campaign-id '<reviewed campaign id>' --reviewed-live --runtime-authorized `
  --initialize-ledger
```

This requires the designated ignored `.env.local` in the project root and refuses
to replace an existing ledger. Normal preflight uses `--preflight-only`, which
makes zero provider requests; it does not qualify Docker execution.

Actual public checks and protected grades are held under the campaign's exclusive
Docker lease. Unknown settlement prevents release. An operation that never
requested a public check has no public container to clean up: settled protected
grading can still finish, while the observable workflow fails for missing checks.
An attempted check with a missing receipt remains infrastructure/unknown and skips
protected grading, including when earlier checks settled.

Trusted live result/event metadata is `real_provider_episode`; the operation/report
provenance is `real_provider`. Default standalone controls remain
`synthetic_episode_control` / `synthetic_development`. Labels record declared host
context; receipts establish execution. Observable workflow PASS requires an actual
current approved R1 review without open findings and a current passed, exit0,
settled public check before finish. It is separate from functional correctness.

Saved outputs include report input/report, episode binding, progress artifacts,
protected grade when started, and an `attempt-receipt-0.1` plus sanitized campaign
copy. Keep raw run artifacts private. Preserve every attempt and its original
identity; infrastructure errors are excluded from model-quality conclusions.

The runtime/report fixes delivered in PRs 79–83 passed hosted source checks.
Current Windows managed Rust-child Docker access still fails at the named pipe
while direct Docker probes work. Ordinary user PowerShell qualification is pending;
no second paid attempt or formal results are claimed by these source changes.
