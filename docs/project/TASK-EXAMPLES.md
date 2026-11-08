# Reviewer task examples for P1.4.3

Status: **authored human-facing examples**, not model observations and not protected evaluation outcomes. Nothing below is a transcript, provider response, hidden functional result or formal trial. These examples are written from the pinned visible retry and cache-v2 specifications plus the fixed catalog. Actual protected outcomes and human signoff remain pending.

Use these examples to check that a reviewer can tell intended behavior from a plausible failure. They use tiny original inputs only.

## Example 1 — stable retry after a lost acknowledgement

### Authored setup

- Family: `retry-processing`; visible requirement version `retry-v1`.
- Tiny delivery item: tenant `"orchard"`, event ID `"lease-9"`, units `3`.
- The supplied store commits the transaction, then raises a transient `RuntimeError` representing an acknowledgement lost after the durable commit. The process is reopened and the processor reloaded before the retry.

### Expected behavior

1. First delivery: the completion and credit commit together. The caller may observe an exception/unknown delivery outcome because the acknowledgement was lost.
2. Durable state after reopening: total for `"orchard"` is `3`; the completed identity `("orchard", "lease-9")` records units `3`. The credit exists exactly once.
3. Retry of the identical item: `process_event` returns `"duplicate"`. The store remains unchanged; the total stays `3` and no second credit is added.

| Observable | Expected |
|---|---|
| First delivery result | exception surfaced / outcome unknown at the caller |
| Durable total for orchard | 3 |
| Completed units for (orchard, lease-9) | 3 |
| Retry status | `duplicate` |
| Durable total after retry | 3 |

### One plausible wrong result

A candidate credits the item a second time on the retry. A reviewer might see retry status `duplicate` (the candidate found the completion) but a post-retry total of `6`. If the candidate instead treats the lost acknowledgement as a failed delivery and rolls back/credits `0`, the durable state after reopening is missing the credit or completion.

### How a reviewer distinguishes it

Inspect the durable total and completed identity through a fresh store open before the retry and after it. Expected: total `3` both times, completion units `3`, retry `duplicate`. Wrong: total `6`, total `0`, or missing completion. Do not accept a `duplicate` status alone as proof of exactly-once credit.

## Example 2 — C3 stale v1 evidence after the v2 update

### Authored setup

- Family: `preview-cache`; C3 starts at the v1 checkpoint with the v1 package image.
- Event 1 (`before_first_O1`): the full **v2** requirements are delivered and become the active requirement version before the first orchestrator invocation.
- The first orchestrator response occurs.
- Event 2 (`after_first_O1_response`, before dispatching its action or honoring completion): an authored fixture **v1 receipt** arrives. It reports earlier v1 work complete for the initial exported candidate SHA-256 and says that original source/version must not be treated as current v2 completion. It does not replace or downgrade v2.
- The receipt's initial hash labels only the earlier v1 checkpoint. The current source may have changed, or its SHA-256 may even equal that earlier hash; either way the receipt is not a fresh review or check of the current source. A new v2 review and check are required for the current source.

### Expected behavior

1. The active requirements version before the first O1 invocation is `v2`; the first request carries the v2 requirements, not the older v1 checkpoint.
2. The v1 receipt is recorded only after the first response. It remains supplied provenance/context; it is not a hidden grade, a fresh review, a check result or current completion evidence.
3. Current version remains `v2`. Capture the actual current source SHA-256 for current evidence; it may equal or differ from the receipt's initial hash. Equality does not make the receipt current review/check evidence.
4. Completion requires both independent R1 review **and** applicable current verification for the current source and v2 requirements. The stale receipt is retained but does not satisfy either requirement.

| Observable | Expected |
|---|---|
| Active version before first O1 | v2 |
| Receipt position | after first O1 response |
| Receipt role | authored supplied fixture context |
| Active version after receipt | v2 (not downgraded) |
| Source identity | actual current source hash; may equal or differ from the receipt hash |
| Completion support | fresh v2 R1 review AND current verification for the current source |

### One plausible wrong result

The team treats the arriving v1 receipt as proof that the task is already complete, skips fresh review/verification, and finishes. A variant treats the receipt as current evidence because its hash matches the current source SHA-256, downgrades the active requirement version to v1, or records the receipt as a review/check. The visible result may look like a normal completion claim, but its evidence is stale and does not address the current v2 source.

### How a reviewer distinguishes it

Check the ordered events, active requirement version, current source hash and evidence references. Expected: v2 active before the first response; receipt after that response; v2 still active; completion backed by current R1 review and current verification for the current source. Wrong: receipt before the first response, v1 active after the update, receipt treated as review/check, or completion with only the older receipt as support—even if the current and receipt hashes are equal. Compare the reviewer's/check's current source and version, not hash inequality.

## Evidence boundary

These are hand-authored teaching examples. They do not assert an actual model behavior, hidden functional result, protected grade or human acceptance. Live/provider visibility, additional role turns, protected grading, formal results and human signoff remain pending. The pinned visible specifications and catalog are the authority for the described requirements and C3 event order.
