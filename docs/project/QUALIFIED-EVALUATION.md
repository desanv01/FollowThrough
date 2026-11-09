# Reproduce a qualified evaluation

Status, 9 November 2026: runtime/report corrections in PRs 79–83 are merged and passed
hosted checks. They are source evidence. One earlier paid development attempt is
retained as an excluded infrastructure error; its separate protected Retry v1
functional pass is not an overall success. No new paid attempt or formal dataset
is claimed here. The candidate formal design is six scenarios × two arms × two
trials (24 episodes, 12 scenario/arm cells); it is reviewed but **not frozen until
a qualified pilot and remaining-budget review**.

## Prepare the trusted host

Use Python 3.11+, Git and a compatible Rust toolchain. Build the executor from the
exact clean reviewed source checkout with `cargo build --locked --manifest-path
runner/Cargo.toml --bin container-check`. Preserve its SHA-256; do not substitute
an older binary. Run Python from that source checkout. Source commit/tree and
executable identities must agree with the reviewed operation gate.

Keep trusted storage separate: `--project-root` contains the ignored `.env.local`
with `DEEPSEEK_API_KEY` and the existing canonical
`.coordination/provider-budget.sqlite3`. The clean `--source-root` contains neither
file. Never copy keys, ledger, raw model traffic or protected grader material into
an evaluated workspace or public artifact. The accepted credential loader reads
the explicit project root; do not print the key.

Use one reviewed campaign ID and its existing cumulative USD20 ledger. Inspect its
remaining reservation allowance before approving an operation; never recreate a
ledger to regain budget. The campaign operator must prepare a fresh operation,
released exclusive Docker lease, absolute output parent and reviewed gate. Every
run directory and episode/operation/run identity is fresh. Preserve failed and
uncertain attempts; no automatic retries.

Docker must provide the trusted local Linux daemon through the executor's fixed
endpoint, with the exact pinned image already present (no implicit pull). Obtain
the supported image value from `episode.protected_session.IMAGE`; use that exact
value for `--image-digest`. The executor and protected shims must come from the
reviewed source. Retry R1/R2/R3 use v1; Cache C1/C2/C3 use v2. Both arms
`ordinary` and `structured_progress` and stages `development`, `pilot`, `formal`
use the same guarded entry. Changing stage does not authorize a formal campaign.

Current native limitation: direct Docker commands work, but the managed Rust child
has returned a named-pipe access denial before container creation. This is a host
execution-context difference, not a demonstrated model failure. Qualification in
the user's ordinary PowerShell host is **pending**. Preflight success does not
resolve this; perform the separately reviewed bounded native qualification before
paid dispatch. Do not change daemon permissions or bypass executor restrictions.

## Prepare portable campaign state without execution

A clean checkout can create its own draft campaign registry and gate through the
public setup command below. Use distinct existing source and trusted storage roots,
a fresh campaign directory and a fresh run path. All paths must be absolute;
source/task/reference/scenario trees and reparse paths are protected. Execute from
the reviewed source checkout. The helper requires a clean Git source identity and
never runs Docker, contacts a provider or reads a key.

```powershell
python -m episode.setup --prepare `
  --source-root '<absolute clean reviewed source checkout>' `
  --project-root '<absolute existing trusted project storage>' `
  --campaign-root '<fresh absolute campaign directory>' `
  --run-root '<fresh absolute run directory>' `
  --campaign-id '<reviewed campaign id>' --episode-id '<fresh episode id>' `
  --operation-id '<fresh operation id>' --run-id '<fresh logical run id>' `
  --stage pilot --scenario-id R1 --arm ordinary --trial 1 `
  --dataset-id '<reviewed dataset id>' --initialize-ledger
```

`--initialize-ledger` is explicit first-use creation through the accepted budget
API with the fixed USD20 ceiling; it refuses any existing canonical ledger. Omit
that flag when reusing the same existing campaign ledger: it is validated
read-only, never reset. Setup does not need the key; the subsequent live path
requires the designated ignored `.env.local` in trusted storage.

The command creates an empty released resource registry (zero active operations,
no operation started), `gate-draft.json` and the run's parent when needed. It does
not create the run directory or an evaluated workspace. Existing campaign/run
paths are refused; no prior campaign files or ledger are overwritten. Its
`DRAFT_REQUIRES_REVIEW` gate binds current Git commit/tree, operation identities,
current derived limits and canonical campaign ceiling. This draft is deliberately
rejected by the paid live verifier.

Separately qualify the real native host/runtime and review the exact gate,
remaining budget and component/image hashes before approving the gate for the
preflight/paid templates below. Empty released state means no operation has begun;
it is not proof of Docker access, isolation, cleanup or model performance. Setup
requires no private project-specific preparation artifacts and grants no runtime
or spending authorization.

## Bind a fresh operation gate

A reviewed JSON gate is required for each operation through `--g4-gate`. Its
`gate_id` remains `G4-R1-READY` for the supported scenario routes; it is not a
reusable permission slip. The gate binds current source commit (and tree when
present), episode/operation/run IDs, stage, scenario, arm, trial and dataset.
Its budget binds the same campaign and `cap_microdollars:20000000`. Required limits:

```json
{
  "max_calls": 16,
  "max_output_tokens_per_call": 2048,
  "max_call_seconds": 25,
  "max_call_reservation_micro_usd": 50000,
  "max_operation_reservation_micro_usd": 600000,
  "max_wall_seconds": 900
}
```

USD0.60 is a reservation ceiling, not an invoice estimate or guaranteed episode
cost. At USD0.05 reservation per call it can stop dispatch before 16 attempts.
The canonical ledger remains the cumulative spending boundary. A gate must be
reviewed against actual remaining allowance and qualified host access before use.

## Preflight and dispatch template

Replace every placeholder with the reviewed absolute path or identity. This
PowerShell template shows a pilot R1/ordinary operation, Retry v1. For other
supported cells change scenario/arm/version/stage/trial/dataset consistently in
both CLI and gate. The image placeholder is the exact pinned value described
above. Use the source checkout as the current directory.

```powershell
$SourceRoot = '<absolute clean reviewed source checkout>'
$ProjectRoot = '<absolute trusted project storage>'
$CheckExecutable = '<absolute freshly built container-check.exe>'
$DockerExecutable = '<absolute Docker executable>'
Set-Location -LiteralPath $SourceRoot
$LiveArgs = @(
  '-m', 'episode.live',
  '--project-root', $ProjectRoot,
  '--source-root', $SourceRoot,
  '--ledger-path', (Join-Path $ProjectRoot '.coordination/provider-budget.sqlite3'),
  '--run-root', '<fresh absolute run directory with existing parent>',
  '--episode-id', '<fresh reviewed episode id>',
  '--campaign-id', '<existing reviewed campaign id>',
  '--operation-id', '<fresh reviewed operation id>',
  '--run-id', '<fresh reviewed logical run id>',
  '--stage', 'pilot', '--scenario-id', 'R1', '--arm', 'ordinary', '--trial', '1',
  '--dataset-id', '<reviewed dataset id>', '--requirements-version', 'v1',
  '--campaign-root', '<absolute prepared campaign directory>',
  '--g4-gate', '<absolute fresh reviewed gate JSON>',
  '--check-executable', $CheckExecutable,
  '--check-executable-sha256', (Get-FileHash -LiteralPath $CheckExecutable -Algorithm SHA256).Hash,
  '--docker', $DockerExecutable,
  '--docker-sha256', (Get-FileHash -LiteralPath $DockerExecutable -Algorithm SHA256).Hash,
  '--image-digest', '<exact supported pinned image value>',
  '--shim-dir', (Join-Path $SourceRoot 'protected'),
  '--reviewed-live', '--runtime-authorized'
)
python @LiveArgs --preflight-only
```

Require `PREFLIGHT_OK`, exact source/canonical storage identities and zero provider
calls. Preflight validates setup inputs; it does not execute a container, create
formal results or authorize payment. Only after the reviewed host qualification,
operation approval and budget check, the complete paid template using those same
fresh inputs is:

```powershell
python @LiveArgs
```

Do not rerun this command after a terminal/uncertain attempt. Inspect and retain
its receipts; any successor operation requires fresh identities and a fresh gate.

## Read outcomes and retain evidence

Functional outcome comes from independent protected grading bound to exact
candidate SHA-256, requirement version and logical run ID. Observable workflow is
a separate host-event predicate: a complete host stream must show an independent
R1 approval, matching claimed and observed final candidate/version, no open
findings, and an actual validated current passed/exit0/settled public check before
finish. Review and check may occur in either order; unchanged-source evidence may
be reused. This measures observable workflow, not semantic review quality.

A completed claim without a public check remains a model completion: final grading
can settle, workflow FAIL, evidence UNKNOWN. An actual check attempt with any
missing receipt remains infrastructure/unknown and skips final grading, even if
an earlier check settled. Missing/untrusted/incomplete workflow streams stay
UNKNOWN. Functional, workflow, evidence, completion and joint outcome remain
separate; infrastructure rows are excluded rather than counted as model failures.

Live result/events use `real_provider_episode`; report provenance is
`real_provider`. Standalone controls retain their synthetic labels. Host metadata
does not authenticate provider activity; retain actual provider/ledger and
execution receipts separately. Checkpoint files alone are not checkpoint grades.

The private executor receipt records per-command `stderr_excerpt`, bounded to 4096
UTF-8 bytes, alongside full observed byte counts and process settlement. Setup
diagnostics never become agent public-check stdout/stderr. Keep these diagnostics
private; they can reveal host details. See [container executor](CONTAINER-EXECUTOR.md)
and [live driver](LIVE-DRIVER.md) for lifecycle/deadline details.

Known limits include trusted local daemon/stable filesystem assumptions, bounded
sampling of checkpoints, direct-child rather than whole-process-tree settlement,
and a sequential controller whose blocking callbacks depend on bridge deadlines.
Hosted CI and synthetic controls do not establish native isolation qualification,
provider quality or a frontier-model comparison. Preserve exact source, gate,
binary/image identities, attempt receipts, cleanup/spend uncertainty and report
bindings before drawing conclusions.
