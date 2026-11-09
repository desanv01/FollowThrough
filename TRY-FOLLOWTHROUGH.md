# Inspect saved FollowThrough attempts

On Windows, double-click **OPEN-FOLLOWTHROUGH.cmd** in this checkout. It runs the current checkout's T3 renderer against the included sanitized saved attempts. Python 3.11 or later must be on PATH; optionally set `FOLLOWTHROUGH_PYTHON` to an absolute Python executable path. No nested source clone or private data is needed for saved-result inspection.

From this source folder, the equivalent commands are:

```powershell
python -X utf8 -B -m reporting.run_ledger --input docs/research/recorded-development-input.json --format tui
python -X utf8 -B -m reporting.run_ledger --input docs/research/recorded-development-input.json --format text
```

Use arrows or `j`/`k` to select, Enter for saved evidence, `/` to filter, Esc to return, and `q` to exit. The viewer reads saved results without starting Docker, contacting a provider, or executing candidate code. The three retained real attempts remain excluded after infrastructure/provider stops; their functional passes are separate outcomes.

For live preparation, use a **separate private data folder** with the original credential file and canonical budget ledger. Pass that folder as `--project-root`, and this clean Git checkout as `--source-root`. Campaign and run output folders must be fresh and outside protected source/private areas as required by [setup and qualification](docs/project/QUALIFIED-EVALUATION.md). Existing `.env.local` belongs in the private data folder, and the existing ledger stays at `<data>/.coordination/provider-budget.sqlite3`. Never initialize another ledger to regain allowance.

Example path configuration after local workspace consolidation:

```powershell
$source = 'C:/Users/Dv/Desktop/FollowThrough'
$data = 'C:/Users/Dv/Desktop/FollowThrough-Data'
Set-Location $source
python -B -m episode.setup --help
```

These commands inspect the interface only. Fresh reviewed gates, source/backend identities, operation IDs, settlement and bounded authorization are still required for actual execution. Historical frozen gates and receipts retain their original paths and are not reusable after relocation. Local private progress and the recovery index are reached through the ignored `.coordination/CONTROL.md` pointer when present; published project documents describe source contracts and historical milestones.
