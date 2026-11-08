# Offline scenario-to-episode driver

Status: proposed source for `P1-EPISODE-DRIVER/r1`. Candidate-only until main integrates it. This driver connects the accepted scenario preparer, the package-to-config adapter and the synthetic role loop. It is an offline wiring seam only: no provider, real model, Docker, grader, candidate execution, hidden grading or formal benchmark result.

## Library interface

```python
from episode.driver import run_scenario

envelope = run_scenario(
    project_root,
    scenario_id,
    run_root=new_directory,
    episode_id="episode-1",
    arm="ordinary",              # or structured_progress
    limits={
        "max_model_responses": 4,
        "max_tool_actions": 4,
        "max_elapsed_ms": 30000,
        "max_context_bytes": 60000,
    },
    model_port=model_port,        # existing synthetic seam
    check_port=check_port,        # existing synthetic seam
    cancelled=None,
    monotonic=None,
)
```

The composition order is exactly `prepare_scenario` -> `build_episode_config` -> `run_episode`. The injected ports remain the existing trusted synthetic interface; no live flag or result relabeling is added.

### Owned run layout

```text
<run_root>/                 # must be new and outside protected source trees
  package/                  # prepared package (controller.json + agent/)
  episode/                  # loop events.jsonl, checkpoints and result.json
  driver.json               # small driver envelope/record
```

`run_root` must be absolute, fresh, its parent must exist, and no path component may be a symlink/reparse. `run_root` may be a normal project run directory such as `project/runs/<new>` but may not overlap the project root or the protected `tasks/`, `references/` or `scenarios/` trees. The driver applies the accepted preparer's complete protected-output policy, including the actual loaded `scenarios` package root, independently of the caller's fixture root. Existing output is refused without modification. Failed preparation artifacts are retained, not cleaned or overwritten. Host driver records are never written inside `package/agent`.

The preparer import is deferred into the run path, after basic caller/port checks and before any `mkdir`, so importing `episode.driver` performs no path probe or I/O. The protected-output check runs before `run_root` creation, so a fresh path under the loaded asset tree is refused with `run_root_protected` and leaves no run directory or `driver.json`.

## Driver envelope

```json
{
  "schema_version": "episode-driver-0.1",
  "evidence_kind": "synthetic_episode_control",
  "status": "episode_returned",
  "result": {},
  "paths": {"package": "...", "episode": "..."},
  "error": null
}
```

`result` is the unmodified loop result or `null` for `preparation_error`. `driver.json` is written at `run_root/driver.json` when ownership exists and writing is safe. A loop `completed_claim` or `partial_claim` is not treated as a functional pass; the driver records only a synthetic episode result.

Preparation failures use bounded codes (for example `invalid_scenario`, `invalid_arm`, `ports_not_callable`, `run_root_exists`, `run_root_unsafe`, `run_root_protected`, `invalid_project_root`, `preparation_failed`, adapter/loop fixed codes, `driver_internal_error`). Raw exception messages and chains are not stored in the envelope or `driver.json`.

## Synthetic CLI smoke

```text
python -B -m episode.driver \
  --project-root ROOT \
  --scenario R1 \
  --run-root NEW_DIRECTORY \
  --episode-id ID \
  --arm ordinary|structured_progress \
  --synthetic-smoke
```

All flags are explicit; the smoke uses the documented small limits above. `--synthetic-smoke` is mandatory: without it the CLI refuses before creating anything. The CLI supplies exactly one scripted O1 `finish(partial)` action and a check port that refuses unexpected use. It never contacts a provider, executes candidate Python or calls Docker.

Exit 0 means the synthetic episode returned and evidence was written; it does **not** mean benchmark success. Preparation errors, protocol errors, infrastructure errors and provider errors are nonzero. The printed notice identifies the run as a synthetic wiring smoke and a partial claim as not a functional pass.

## Limits and evidence

- Importing the module performs no I/O. The library and CLI use the accepted offline interfaces; no provider/network/credential/Docker operation exists.
- The driver assumes a stable owned filesystem. It does not claim race-proof access, isolation, sandbox qualification, acceptance or hosted-CI success.
- Main later wires bounded real ports and qualifies that combined path separately.
- Focused tests cover all six IDs with the one-action partial callback, C3 model-response-before-supplied-receipt ordering, C2/C3 initial agent equality, an R1 O1->W1->O1 handoff, pre-existing `run_root` refusal, pre-ownership errors, the separate-fixture-root loaded-asset guard with no `mkdir`, import-time no-probe behavior, and the CLI smoke/required-flag behavior.
