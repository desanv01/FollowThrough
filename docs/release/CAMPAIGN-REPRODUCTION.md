# Campaign reproduction supplement — development draft

**Status: documentation preparation, not an accepted runbook.** Prepared under
P5-ANALYSIS-PREP/r1 (owner DS-ANALYSIS-01). This is a campaign-specific supplement to the pinned
[reproduction checklist](REPRODUCTION-CHECKLIST.md); it does not replace, rewrite or extend that
checklist's trusted-control claims. No command in this document was executed by this preparation,
and no formal data, freeze manifest, accepted runtime/protected-grade/report bridge revision or real
result exists yet.

**Evidence kind:** `documentation_preparation`.

## 1. Relationship to the existing checklist

[REPRODUCTION-CHECKLIST.md](REPRODUCTION-CHECKLIST.md) remains authoritative for:

- what is and is not reproducible today, and the evidence class of each item (its §1);
- environment prerequisites and source identities, including the Git blob versus checkout-byte
  distinction (its §2–§3);
- the documented trusted-control, test and Rust commands and their expected receipts (its §4–§6);
- clean-package exclusions and checks before publishing (its §7);
- the future real-evaluation gates and the facts Main must fill (its §8–§9).

This supplement adds only the campaign-specific reproduction sequence, the separation of offline
recomputation from a fresh paid campaign, and the identity/provenance rules that a future formal
package must satisfy. It deliberately does not duplicate the checklist's command list.

## 2. Identity layers a reproduction claim must name

| Layer | Identifies | Status at this preparation |
|---|---|---|
| Git commit `166a304b32d92f66519b8b4074fee510cbffdaeb` on `main` | Committed source baseline | Published baseline |
| Raw Git blob bytes/SHA-256 per path | Committed file object identity | Recorded for eight paths in checklist §3 |
| Checkout file bytes | A checkout under that commit, subject to line-ending conversion | Not an identity; never compare to blob hashes to "fix" a mismatch |
| Separately pinned working-tree inputs | Inputs used by preparation that the baseline commit does not contain | Pinned by byte and SHA-256 when the packet was issued; must appear in the freeze manifest |
| Campaign freeze manifest | Source/package, scenario package, runtime, model, grader/checkpoint, reporting/bridge, settings, order, limits | Does not exist (TODO: Main) |
| Dataset identity (`dataset_id`) with `evidence_kind` and `stage` | Which rows are formal provider evidence versus development/pilot or synthetic | Does not exist (no formal data) |
| Release/package manifest | A specific distributable archive, verified against its own SHA-256 manifest | Does not exist |

The baseline commit does not contain seven inputs that were used and pinned for this preparation:
`WORKING-RULES.md`, `docs/project/DEADLINE-MODE.md`, `docs/project/REPORTING-CONTRACT.md`,
`docs/project/REPORTING-NUMERIC-CLARIFICATION.md`, `docs/release/REPRODUCTION-CHECKLIST.md`,
`docs/research/LIMITATIONS.md` and `scenarios/catalog.json`. They are **separately pinned inputs, not
files committed at `HEAD`**. A future freeze manifest must carry both layers: the commit/package
identity and the byte/SHA-256 identities of separately pinned inputs.

Do not use filtered checkout bytes to assert blob identity; compare commit/path/object identity or
verify a manifest generated for the exact package. Avoid text redirection when byte identity
matters. A working tree that changed after preparation is not a reproduction of the frozen package.

## 3. Future campaign reproduction sequence (gates, not executed steps)

Each step is a future gate. Where no reviewed command exists, the step is `TODO_MAIN`; no executable
command is invented here.

1. **Obtain frozen source and data identities.** Take the Main-frozen manifest: source commit/tag/
   package, separately pinned input hashes, scenario package revision, runtime/executor revision,
   model alias and actual returned identity, grader/checkpoint revision, reporting/bridge revision,
   settings, limits, order and trial numbering. Output: one manifest that a reader can verify
   without consulting the preparation workspace.
2. **Validate artifact manifests and provenance/stage.** Verify package hashes against the manifest;
   confirm the dataset contains only the declared `evidence_kind` and `stage` and that synthetic,
   pilot and formal rows are separate datasets. Output: verification receipt naming each checked
   artifact and its result. TODO_MAIN for the packaging/sanitisation command.
3. **Verify original inputs and outcome-judgment provenance.** Confirm that each projected episode
   maps to its original task input, candidate submission and grading receipt; confirm the four
   component judgments came from independent grading/predicates and not from an agent's claim or
   trace prose; confirm hidden outcomes were not exposed to evaluated roles. Output: provenance
   record per episode or per representative sample.
4. **Reproduce protected grading/checkpoints with qualified isolation.** Run grading and checkpoint
   derivation only through an accepted protected path with sanitized environment, no network,
   resource limits and no grader/secret mounts during agent actions. The host-local Python grader
   imports candidate code and is not a security boundary. TODO_MAIN: protected-grade command and
   isolation qualification evidence do not exist yet.
5. **Generate reports.** Run the accepted reporting revision on the sanitized projection; produce
   `report.json`, `episodes.csv` and `report.md` in a new output directory. TODO_MAIN: the reporting
   revision is still a correction candidate; acceptance/integration must precede this step.
6. **Independently spot-check representative outcomes.** Choose a small declared sample spanning
   cells, statuses and unknown resources; recompute counts and `strict_outcome` from accepted
   receipts; compare with the generated report. This is an independent check, not a re-run of the
   paid model campaign. TODO_MAIN: sampling rule and tooling.
7. **Compare recorded outputs and preserve failures.** Compare the reproduction output with the
   recorded accepted copies by identity/content; document every difference as a finding. Never
   overwrite or discard a failed reproduction, and record exits and diagnostics. A failed
   reproduction is retained evidence, not a deleted attempt.

## 4. Offline recomputation versus a fresh paid model campaign

| Dimension | Offline recomputation | Fresh paid model campaign |
|---|---|---|
| What is reproduced | Grading/checkpoint derivation and report arithmetic from accepted sanitized inputs, plus the recorded trace view | The procedure, frozen settings and provenance of a new campaign |
| Provider spend | None, but data/privacy and compute approval still apply | Requires its own explicit authorisation and per-operation bound within the cumulative cap |
| Prerequisites | Accepted code, accepted input projection and a clean package | Accepted executor/role loop, qualified isolation, protected grading, aggregate budget, frozen manifest and campaign authorisation |
| Output identity | Byte-identical report outputs are expected from the same input and revision; differences are findings | **Never** a byte-for-byte reproduction of model text, tool calls or token counts; settings and seeds are recorded, outcomes may differ |
| Failures | Preserve the failed run and its diagnostics | Preserve every attempt, exclusion and settlement; no selective rerun of poor outcomes |
| Command | Reporting CLI documented in the pinned contract once the revision is accepted | `TODO_MAIN`; no live campaign command is supplied here |

A "reproduced campaign" therefore means a reproduced **procedure and analysis from frozen evidence**,
not a claim that a stochastic model campaign can be replayed identically.

## 5. Documented commands and their current acceptance scope

Only command forms already documented in pinned inputs are quoted. The checklist's §4 remains the
authoritative list for trusted controls; this section names the two campaign-relevant forms. None was
executed by this preparation.

| Documented command | Documented in | Current acceptance scope |
|---|---|---|
| `python -B -m reporting.report --input <explicit JSON file> --output-dir <new directory>` | Pinned reporting projection contract v0.1 | Contract-documented API/CLI. The reporting revision is **not accepted or integrated** at the pinned baseline; do not treat it as a runnable campaign step until Main accepts it. Input must be a sanitized supplied projection; the tool summarizes judgments and does not establish truth. |
| `python grader/retry_processing.py --candidate <absolute-or-relative processor.py> --output <new JSON file>` | Checklist §4.6 | Trusted-fixture contract form only, not a sandbox and not formal protected grading. Exit 0 means a complete functional grade was produced, including a functional FAIL; a nonzero receipt may be partial and must not be promoted to a pass. |

Trusted test, self-test, Rust and local-control commands are not repeated here; use checklist §4 with
its stated scope (checker/control evidence, never campaign or model results). The live adapter path
is gated and deliberately has no command in the checklist or this supplement.

## 6. Missing commands — `TODO_MAIN`

| Needed operation | Status | Placeholder |
|---|---|---|
| Formal campaign execution/run-book command | Not defined; campaign not authorised | `TODO_MAIN` |
| Protected grading command and isolation invocation | No accepted protected path | `TODO_MAIN` |
| Checkpoint/trajectory extraction from verified snapshots | Projection does not supply it; bridge not implemented | `TODO_MAIN` |
| Runtime/grader receipt → `report-input-0.1` translation bridge | Not implemented/accepted | `TODO_MAIN` |
| Artifact sanitisation and release-package assembly | Procedure not accepted | `TODO_MAIN` |
| Independent outcome spot-check sampling rule/tooling | Not defined | `TODO_MAIN` |
| Accepted reporting revision/version | Correction candidate only | `TODO_MAIN` |
| Freeze manifest and campaign authorisation record | Do not exist | `TODO_MAIN` |

No invented executable command may replace a `TODO_MAIN` row.

## 7. Secrets, private data and eventual sanitized artifacts

Excluded from any release, reproduction package or log, consistent with checklist §7: `.env.local`
and any credential values or environment dumps; raw provider requests, responses, reasoning or
streams; the private coordination tree; raw `runs/` and `tmp/` content; evaluated-workspace content;
grader/reference protections configured for evaluated agents; absolute host paths and personal data
that the reporting contract forbids injecting into result content.

Eventual sanitized artifacts needed for a credible campaign reproduction:

| Artifact | Contents | Purpose |
|---|---|---|
| Freeze manifest | All identities, settings, limits, order, trial numbering, hashes | Defines what is being reproduced |
| Sanitized `report-input` JSON | Only the projection fields (`schema_version`, `dataset_id`, episodes with supplied judgments/resources/trace) | Report input; no raw provider objects |
| `report.json`, `episodes.csv`, `report.md` | Generated accepted outputs | Compared outputs and displayed results |
| Sanitized trace display | Supplied `seq`/`role`/`kind`/`summary` rows | Recorded text view only; never presented as execution proof |
| Protected grading receipts | Check IDs, outcomes, candidate identity/hash, error classification | Outcome-judgment provenance |
| Methods/limitations update | Final methods, environment and limitation wording | Scope of claims |
| Reproduction record | Actual commands, environment, exit codes, output hashes, deviations, failures | Honest reproducibility evidence |
| Release package manifest | Own SHA-256 manifest for the archive | Package identity, independent of the baseline blob table |

Do not publish a raw trace, credential-bearing log or private coordination material to make a
reproduction look complete.

## 8. What a reproduction claim must state

- Exact identity used: commit/tag or package manifest, separately pinned input hashes, dataset
  identity, runtime and reporting revisions.
- Environment and prerequisites actually used, including any deviation from the frozen manifest.
- Commands actually executed (with their accepted scope), exit codes and output identities.
- Results observed and every difference from the recorded outputs; failed attempts retained.
- Whether the run was offline recomputation or a fresh paid campaign, and its authorisation/bound.
- Remaining limitations, including that a fresh paid campaign is not a byte-for-byte replay.

## 9. Current status

Formal campaign data: none. Freeze manifest: none. Accepted runtime, protected-grade, checkpoint and
report-bridge revisions: pending. Real results: none. The existing checklist's §8 gates remain open,
and completing this preparation document does not close any of them or authorise any run.
