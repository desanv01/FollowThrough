# Scenario package to offline episode configuration

Status: proposed source for `P1-EPISODE-CONFIG/r1`. Candidate-only until main integrates it. This adapter is read-only and host-side; it does not import or run the episode loop, ports, providers, graders or candidate Python, and it does not create or mutate files.

## Interface

```python
from scenarios.episode_config import build_episode_config, ScenarioConfigError

config = build_episode_config(
    package_root,                 # existing prepared package directory (absolute)
    episode_id="episode-1",       # ASCII [A-Za-z0-9._:-], 1..64
    arm="ordinary",               # ordinary | structured_progress
    output_dir=new_output_path,   # absolute new path with existing parent
    limits={
        "max_model_responses": 12,
        "max_tool_actions": 24,
        "max_elapsed_ms": 60000,
        "max_context_bytes": 32000,
    },
)
```

The returned mapping contains exactly the host-side loop fields:

`episode_id`, `scenario_id`, `arm`, `workspace`, `output_dir`, `requirements_version`, `scheduled_events`, `max_model_responses`, `max_tool_actions`, `max_elapsed_ms`, `max_context_bytes`, `evidence_kind`.

`evidence_kind` is always `synthetic_episode_control`. `workspace` is the validated canonical `agent/` directory; `output_dir` is the validated new destination. No controller, provenance, grading, catalog or template metadata is copied into the result.

## Limits

Exactly the four keys above are required. Values must be non-boolean positive integers within the loop bounds:

| Key | Bound |
|---|---|
| `max_model_responses` | 1..60 |
| `max_tool_actions` | 1..120 |
| `max_elapsed_ms` | 1..600000 |
| `max_context_bytes` | 1..60000 |

No defaults are chosen. `episode_id` is not generated.

## Fixed scenario mapping

| ID | Family | Candidate | Controller versions | Initial config version | Scheduled events |
|---|---|---|---|---|---|
| R1/R2/R3 | retry-processing | processor.py | retry-v1 / retry-v1 | v1 | none |
| C1 | preview-cache | preview.py | cache-v2 / cache-v2 | v2 | none |
| C2 | preview-cache | preview.py | cache-v1 / cache-v1 | v1 | one before-first-O1 v2 requirements update |
| C3 | preview-cache | preview.py | cache-v1 / cache-v1 | v1 | C2 update plus one after-first-response v1 supplied receipt |

C2/C3 event text is taken from the accepted controller: the update text is preserved as authored; the C3 receipt must equal the pinned receipt template formatted with the actual exported initial candidate SHA-256. Event objects returned in `scheduled_events` are exactly `{boundary, kind, version, text, source_sha256}`. The C3 receipt's `source_sha256` is the initial candidate hash, not a recomputed post-edit hash. No event is injected early, consumed or converted into review/check evidence.

## Validation

- The package root must exist and contain exactly `controller.json` and a flat `agent/` directory with exactly six regular files: one of `processor.py`/`preview.py`, `public_checks.py`, `TASK.md`, `SPEC.md`, `WORKFLOW.md`, `HISTORY.json`.
- The controller is limited to 262144 bytes; each agent file to 1 MiB; the aggregate to 2 MiB. Size limits are checked before bounded reads, and actual byte bounds are rechecked.
- Controller JSON must be UTF-8, duplicate-key-free, finite-constant-free and shaped exactly like the pinned accepted preparer output. `agent_files` must be a unique six-entry inventory with exact keys, integer non-boolean byte counts, lowercase 64-hex hashes and actual byte/hash equality. The selected candidate must match `source.exported_bytes/exported_sha256`.
- The controller's `catalog_path`, hidden routing, provenance and source expectations are validated only as shapes. They are never followed, read or returned.
- Unknown or extra controller/agent/event fields, malformed scenario/family/version, reordered/extra/missing/mixed events, forged receipt text or hash, source drift, and prospective SPEC updates beyond the loop's per-file/aggregate limits are refused.
- The prospective C2/C3 SPEC replacement is checked against the 1 MiB per-file and 2 MiB aggregate limits before returning.

## Paths and safety

`package_root` and `output_dir` must be absolute non-empty strings/`Path` objects without NUL. Windows namespace aliases (`\\?\`, `\\.\`, `\??\`) are refused rather than redirected. Path components are inspected top-down with `lstat`; a symlink or reparse component is refused before any descendant exists/is_dir/resolve/read/list probe. The output target must be new, its parent must exist, and the output must be neither equal to, inside, nor an ancestor of the package root.

This is a stable-owned-filesystem contract. It is not race-proof and makes no universal atomicity or isolation claim.

## Errors

`ScenarioConfigError` subclasses `ValueError` and carries only a fixed code string. Examples: `invalid_package_root`, `invalid_output_dir`, `unsupported_path_alias`, `unsafe_path`, `unsafe_output_path`, `output_exists`, `output_overlaps_package`, `invalid_episode_id`, `invalid_arm`, `invalid_limits`, `package_inventory_invalid`, `controller_too_large`, `controller_json_invalid`, `controller_schema_invalid`, `agent_inventory_invalid`, `agent_size_invalid`, `source_identity_mismatch`, `scenario_mapping_invalid`, `event_schedule_invalid`, `receipt_invalid`, `spec_oversize`, `validation_failed`.

Messages never contain caller paths, controller values, source text or underlying exception text. The public wrapper raises a fresh error after validation, so `__cause__` and `__context__` are null.

## Testing and limits

Tests prepare all six packages from pinned accepted inputs in owned scratch, check both arms, exact fields, no filesystem mutation, C2/C3 byte equality and event differences, and negative inventory/JSON/hash/version/event/receipt/size/path/limit cases. A real directory-junction guard-order test is included when the platform permits it; unsupported platform cases are reported as skips.

No evaluated episode, provider call, hidden grading, candidate execution or engine integration is performed by this adapter. Main later connects it to the reviewed loop and qualifies the combined path.
