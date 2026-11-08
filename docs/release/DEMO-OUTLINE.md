# Demo outline — draft, not a recording

**Preparation status — 7 November 2026 (r2 correction).** Prepared for P6-RELEASE-PREP/r1 and revised by the bounded r2 documentation correction against the same baseline commit `166a304b32d92f66519b8b4074fee510cbffdaeb`. **No footage, recording, rendering, browser action or formal result exists.** This is a ≤5-minute scene plan for a future video, not a script of verified outcomes. Every scene below must be captured only after Main authorises recording and confirms the facts; anything not yet demonstrated is labelled as planned or absent. Do not invent scores, testimonials, terminal output, model runs or an end-to-end result.

## Constraints and labels

- Target length: 5:00 maximum. Times below are approximate and sum to 5:00.
- Use only authored-fixture controls and, later, accepted campaign evidence. Controls are **not** model repairs or benchmark scores.
- Any replay of earlier terminal activity must carry the on-screen label **“Recorded trusted local control — authored fixture, not a live model run.”** Live model footage does not exist today.
- No scene may imply that the 36-episode campaign ran, that structured progress improved anything, or that a provider/model comparison was performed.
- The video must be accessible: captions/transcript and a clear spoken/visual description. Accessibility checks are release tasks, not performed here.
- For public terminal or receipt footage, redact local absolute directory components from candidate and checkpoint paths while keeping result labels, check IDs and SHA-256 values visible. Label any redaction; never alter hashes, hide result labels or fabricate specimen output.

## Scene plan

| # | Approx. | Scene and purpose | Required capture | Supporting evidence | Fallback |
|---|---|---|---|---|---|
| 1 | 0:00–0:12 (12 s) | **Title and honest status.** Name FollowThrough, state that this is a development-preview demonstration with no formal campaign results, and show the exact baseline commit. | Title card with project name, “development preview — no formal results”, commit `166a304` and date. | [README.md](../../README.md); [METHODS.md](../research/METHODS.md) (“No formal results yet”). | Static card only; no B-roll needed. |
| 2 | 0:12–0:45 (33 s) | **Problem and design.** Explain two task families — retry-safe account credits and preview caching — and that six configurations sit inside them, not six domains. Show ordinary versus structured-progress arms and the proposed 6 × 2 × 3 = 36 episodes. | Simple diagram: 2 families → 6 configurations → 2 arms → 3 repeats; on-screen “proposed, not run”. | [METHODS.md](../research/METHODS.md); [LIMITATIONS.md](../research/LIMITATIONS.md). | Narrated slide with the same diagram; no animation dependency. |
| 3 | 0:45–1:30 (45 s) | **Stable retry repair.** Show the authored retry control: one credit per `(tenant, event_id)`, duplicate versus conflict, transactional credit + completion, rollback on pre-commit failure, and the acknowledgement-loss case where both effects persist so a replay returns `duplicate` instead of double-crediting. Emphasise “stable under retry” rather than “model fixed it”. | Terminal capture of `python -B grader/retry_processing.py --self-test` on the trusted fixture; highlight the correct/negative control result and check IDs. | [tasks/retry-processing/SPEC.md](../../tasks/retry-processing/SPEC.md); [CONTRACTS.md](../project/CONTRACTS.md); [ACCEPTANCE.md](../project/ACCEPTANCE.md). | Annotated slide quoting the documented check names and expected statuses; label “control not recorded — no fabricated output”. |
| 4 | 1:30–2:15 (45 s) | **Preview-cache requirement and stale receipts.** Explain that a cached preview must key on the full semantic identity `(project, document, revision, normalized_mode)`, so a new revision or compact/full mode cannot reuse a stale entry. Show renderer content returned verbatim, invalid requests leaving the cache untouched, and renderer exceptions leaving no partial cache. Frame **stale-receipt behaviour**: a cached result or run record that no longer matches the requested identity or candidate must never be presented as current. | Terminal capture of `python -B grader/preview_cache.py --self-test` showing each control's `candidate` metadata (path and SHA-256), `functional_pass`, `failed_checks`, `expected_failures`, `check_count`, `errors` and `matched_expectation`, followed by the aggregate `self_test_passed` and `controls` fields. The emitter has **no renderer-call counters**; for reuse, use a labelled read-only specification/source walkthrough of the renderer spy rather than a counter capture. | [tasks/preview-cache/SPEC.md](../../tasks/preview-cache/SPEC.md); [CACHE-CONTRACT.md](../project/CACHE-CONTRACT.md); [LIMITATIONS.md](../research/LIMITATIONS.md). | Static comparison table: v1 keys `(project, document, revision)` versus v2 adds normalized mode; label as documented, not recorded. |
| 5 | 2:15–2:55 (40 s) | **Receipts and provenance.** Show a trusted runner control creating a fresh run directory, writing `summary.json`/`events.jsonl`, and binding the checkpointed candidate path and SHA-256. Explain why an old receipt cannot be reused for a changed candidate: new output directories are required and stderr/stdout are bounded. Repeat that the runner is **not a sandbox**. This path/hash check does not decide whether a requirement version is current; the C3 completion-receipt illustration is separate. | Screen capture of a fresh trusted-control run and its receipt files, with candidate hash visible; no model call. | [runner/README.md](../../runner/README.md); [CONTRACTS.md](../project/CONTRACTS.md). | Slide showing the documented exit-status table and the “not a sandbox” warning; mark “not recorded”. |
| 6 | 2:55–3:35 (40 s) | **How the comparison would be judged.** Explain functional correctness, workflow adherence, verification validity and completion accuracy as separate outcomes; joint success would require all four. Show that only the functional checker exists now, and that reporting is planned against synthetic projection inputs — not results. If a C3 card is shown here, label it supplied/planned and keep it separate from the runner receipt. | Slide or planned report mock-up (labels visible: “synthetic/planned — no campaign data”). | [METHODS.md](../research/METHODS.md); [ACCEPTANCE.md](../project/ACCEPTANCE.md); [LIMITATIONS.md](../research/LIMITATIONS.md). | Narrated outcome-definition slide; no dashboard needed. |
| 7 | 3:35–4:20 (45 s) | **Honest status and limitations.** State plainly: no live provider call, no qualified isolation, no aggregate budget, unaccepted scenario/isolation/budget candidates, no formal results. Mention the small proposed campaign and limited generalisation. | Static status board or checklist with each item marked “not done / unaccepted / not qualified”. | [LIMITATIONS.md](../research/LIMITATIONS.md); [CONTRIBUTIONS.md](CONTRIBUTIONS.md); the pinned release-preparation baseline (working-tree input, not in commit `166a304`). | Read the status list over a neutral background; no fabricated screenshots. |
| 8 | 4:20–5:00 (40 s) | **What would make a real claim.** List the gates: accepted corrections, qualified protected execution, reviewed credential/aggregate budget, authorised readiness, frozen campaign, protected grading, accepted reporting, then release. Close with “candidate preparation only; no submission”. | Simple gate list or roadmap; repository/source links on final card. | [ACCEPTANCE.md](../project/ACCEPTANCE.md); [REPRODUCTION-CHECKLIST.md](REPRODUCTION-CHECKLIST.md). | Static closing card with the same gate list. |

## How to explain the two technical demonstrations

### Stable retry repair

The retry task is about durable identity, not about retrying harder. A delivery is identified by tenant plus event ID. A first delivery credits units and records completion in one transaction; an identical replay is a duplicate and changes nothing; different valid units for the same identity are a conflict and change nothing. If the store raises before commit, neither effect may survive; if it raises after commit (lost acknowledgement), both effects persist and the next identical delivery must return `duplicate`. In a batch, invalid items and conflicts are skipped, while a store exception stops processing and preserves earlier commits. “Stable” means repeated attempts converge on one durable outcome. The demo should show the checker on the authored correct and intentionally incorrect controls, and should say without hedging that this is fixture evidence, not a live repair.

### Cache requirement and stale receipts

The preview cache is about identity, not about storing bytes. Two requests may reuse a cached string only when project, document, revision and normalized mode all match; an omitted mode normalizes to `full`. Different revisions or modes must not collide, so a stale entry can never masquerade as the current document. Renderer output is returned verbatim; invalid arguments raise before any cache or renderer effect; a renderer exception propagates unchanged and leaves no partial entry. The same “stale receipt” discipline applies to evidence: a cached preview for an old revision, or a run receipt for an old candidate, is stale and must not be shown as the current result. The runner verifies the grade's candidate path and SHA-256 against the checkpoint and requires a fresh output directory.

### C3 delayed v1 completion receipt — supplied history, planned illustration

C3 is a separate revision scenario, not another stale-cache example. It starts from the same source and visible files as C2, delivers the v2 requirements before the first O1 model invocation, and then introduces an **authored older v1 completion receipt** after O1's first response. The pinned design fixes this event sequence:

```text
Prepare shared v1 checkpoint and history
Deliver v2 requirements before the first O1 model invocation
Receive O1's first response
Deliver authored v1 worker receipt, tied to initial exported source hash
Only then dispatch requested tools or handle a proposed final completion
Continue under v2
```

Label it **supplied fixture history / planned illustration**: it is not an executed agent event and not an observed model failure. The version boundary is the point:

| Item | Status in the illustration |
|---|---|
| Current requirement | **v2** — full/compact mode separation and omitted/full equivalence, retaining all earlier v1 obligations |
| Delayed receipt | Authored **v1** worker completion, tied to the initial exported v1 source hash |
| Current completion evidence | Still needed: a v1 receipt cannot establish completion under v2, even when the source hash matches |
| Runtime handling | Requested tools or a proposed final completion wait until after the receipt arrives; the receipt never downgrades current requirements |

If O1's first response is already a final claim, the old receipt still arrives before completion is honored. The illustration should show what current verification is still required, not claim that the model made a particular mistake. This is distinct from the runner's receipt scene (which binds a copied candidate path and SHA-256) and from stale preview-cache content (which is an old revision or mode entry): path/hash binding and cache identity do not determine whether a requirement version is current.

## If structured progress shows no benefit

If a future accepted campaign finds that the structured-progress arm is equal to or worse than ordinary progress, the video must say so. Show the raw per-scenario outcomes, the added context/maintenance cost, and the limited repetition count. Do not cherry-pick favourable episodes, do not describe a null or negative result as a success, and do not claim that structured progress or JSON “causes” improvement. A clear statement such as “in this small six-configuration study, the workflow package did not produce a detectable functional advantage; the extra tokens are part of the treatment” is more credible than an inflated narrative. If the comparison is inconclusive, say that too.

## Footage status

| Asset | Status |
|---|---|
| Terminal capture of trusted controls | **Not recorded** |
| Trusted runner receipt capture | **Not recorded** |
| C3 supplied-history illustration | **Does not exist** — planned fixture history, not an executed event |
| Live model session footage | **Does not exist** — no paid call made |
| Formal results/dashboard footage | **Does not exist** — campaign not run |
| Voiceover, captions, transcript | **Not produced** |
| Final video, thumbnail and URL | **Not produced** |

No recording should be made from this outline until Main confirms the underlying facts and authorises capture. Render, browser, upload and publication actions are outside this packet.
