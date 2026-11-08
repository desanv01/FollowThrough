# Scenario packaging

Status: proposed source for `P1-SCENARIOS/r2`. Candidate-only until main integrates it. Offline deterministic preparation only; no agent loop, provider request, hidden grading, event engine, Docker or formal scoring is implemented.

## Interface

```python
from scenarios import prepare_scenario

controller = prepare_scenario(project_root, scenario_id, output_dir)
```

```text
python -B -m scenarios.prepare --project-root <root> --scenario R1 --output <new-directory>
```

`scenarios.catalog.json` fixes the six scenario IDs and the accepted input path, byte count and SHA-256 for each. Preparation reads only those catalog-selected files below `project_root`. A changed source, unknown ID, existing output, unsafe path, or symlink/reparse component in an input path, fixed asset, or output parent is refused before a package is staged.

The output may be a new directory below the supplied project root when it does not overlap protected source inputs. The project root itself and outputs inside `tasks/`, `references/` or `scenarios/` are refused; an unrelated new run directory such as `project/runs/<new>` is allowed. Protected roots are matched by filesystem identity rather than case-sensitive string comparison, so Windows case variants such as `Tasks/` are still recognised as the same source tree, and unrelated siblings such as `runs/tasks/` are not false matches. The actual loaded scenario package — catalog and visible assets — is protected independently of the explicit `project_root`, so a separate-root layout cannot publish into the module's own input tree. Existing outputs and redirected output parents are refused; refusal occurs before staging.

The preparer builds a complete package in a new owned staging sibling named `.scenario-build-<output-name>`, then publishes it only by renaming that complete staging tree to the requested output path. If publication is refused, the final output is not created; the complete staging tree is retained and named in the error the caller receives. This is a deterministic publication step under a stable-workspace assumption, not a claim of universal atomicity or race resistance.

## Package layout

```text
<output>/
  agent/
    processor.py | preview.py
    public_checks.py
    TASK.md
    SPEC.md
    WORKFLOW.md
    HISTORY.json
  controller.json
```

`agent/` is flat and contains only those six files. `controller.json` records controller-only identity and routing:

- scenario ID, family, requirement version, visible requirement version
- catalog source path, original bytes/SHA-256, exported bytes/SHA-256
- exported agent file paths, byte sizes and SHA-256
- authored-fixture provenance (no live model or tool output is claimed)
- hidden grading routing (controller-only; no grading code is stored)
- ordered controller events for future engines
- source drift expectation
- standing workflow role IDs

Controller data must never be mounted with `agent/`.

## Scenarios

| ID | Family | Starting input | Initial requirements | Controller events |
|---|---|---|---|---|
| R1 | retry-processing | original starter | complete retry requirements | none |
| R2 | retry-processing | mark-first starter | complete retry requirements plus one authored finding | none |
| R3 | retry-processing | transactional starter | complete retry requirements | none |
| C1 | preview-cache | original starter | final v2 requirements upfront | none |
| C2 | preview-cache | v1 checkpoint | v1 checkpoint | full v2 update before first orchestrator invocation |
| C3 | preview-cache | v1 checkpoint | v1 checkpoint | full v2 update before first orchestrator invocation; authored v1 completion receipt after the first orchestrator response, before request dispatch or completion |

R1/R2/R3 share byte-identical visible task, specification and workflow text. R2 differs only by one authored finding in `HISTORY.json`. No scenario tells the evaluated roles which candidate is defective, correct or complete.

C2 and C3 have byte-identical agent workspaces. Their controller difference is exactly the extra C3 receipt event. The receipt names requirement version v1 and the exported checkpoint SHA-256, states that it reports earlier v1-only completion, and explicitly does not replace or downgrade the delivered v2 requirements. Events use symbolic phases, not timers, timestamps or random ordering.

## Provenance neutralization

Exported candidates strip comments and only the module-level provenance docstring. Function docstrings, error strings and all executable code are preserved. Preparation verifies that the exported AST, ignoring the module docstring, is structurally equal to the original AST. Original and exported identities are recorded separately in `controller.json`. The transformation removes provenance clues without hiding behavior: evaluated roles still inspect and test the supplied code.

## Public smoke checks

`public_checks.py` is an original small script derived from visible requirements and separate examples. It is not a hidden grader, not exhaustive and not a correctness proof. It loads the fixed sibling candidate using `__file__` and `importlib`, so it works under:

```text
python -I -B <absolute path to public_checks.py>
```

from any working directory. Cache smoke checks cover full-preview v1 behavior only so the C2/C3 initial workspaces remain identical; v2 obligations are supplied in the updated text. Defective starters can fail these checks and are not modified to force a pass. A public pass is trusted local-control evidence, not hidden functional success.

## Determinism and tests

Preparation is deterministic: no timestamps, random IDs or machine paths appear in visible content. Two preparations of the same scenario produce byte-identical packages, including controller metadata.

The focused tests locate the accepted fixture inputs by required repository files rather than a fixed directory depth. The same suite runs in the candidate overlay (fixtures in the repository above the overlay) and in an owned integrated-shaped checkout containing the proposed package and pinned task/reference inputs. An explicit `FOLLOWTHROUGH_FIXTURE_ROOT` override is supported for unusual layouts and is verified against the required inputs.

Tests use synthetic input roots containing read-only copies of the accepted inputs and cover all six exports, the exact file allowlist, leakage checks, AST equivalence, C2/C3 equality, initial-boundary event secrecy and controller event difference, source drift, protected-tree output refusal (including Windows case variants), loaded-package asset-tree refusal in a separate-root layout, allowed in-project run output and unrelated nested siblings, no-overwrite, failed build, failed publication, reparse guards for source/root/output/catalog/visible assets (including event-only assets), deterministic output, the module CLI from an unrelated cwd with a child-only `PYTHONPATH`, and bounded isolated-mode public smoke runs.

## Scope and limitations

This package prepares neutral fixtures only. It does not implement either experimental workflow arm, provider settings, an event engine, protected execution or hidden grading. The controller event list is fixture data for a later main-owned controller. Integration, acceptance and formal evaluation remain main-owned.
