# Checkpoint trajectory summaries (P4-TRAJECTORY/r1 candidate)

Status: **candidate proposal, not integrated or accepted.** Main owns the runtime
and protected-grade adapter, integration, scoring/projection decisions and
acceptance. This is the trajectory subcomponent; evidence at creation is offline
synthetic/preparation only.

## Purpose and boundary

`reporting/trajectory.py` implements `summarize_trajectory(data) -> dict` using
only the Python standard library. It is pure: it performs no I/O, executes no
candidate code and has no CLI. It summarizes **explicitly supplied independent
grade records**; it does not authenticate the caller, establish grading truth,
call a provider, judge prose, score a headline, infer causality or penalize
checks. The supplied grades are trusted-grader records, not model claims or
public-check receipts. The current `report-input-0.1` projection is unchanged.

## Input envelope (`trajectory-input-0.1`)

Exactly `schema_version`, `episode_id`, `evidence_kind`, `snapshots`.

- `schema_version` is `"trajectory-input-0.1"`.
- `episode_id` follows the report identifier grammar (ASCII
  `A-Za-z0-9._:-`, nonempty, <=128).
- `evidence_kind` is `synthetic_development` or
  `protected_container_grading`; it applies to the whole file.
- `snapshots` is an array of 0..1000 items in supplied order.

Each snapshot has exactly `checkpoint_id`, `seq`, `candidate_sha256`,
`requirements_version`, `elapsed_ms`, `model_responses`, `grade`:

- `checkpoint_id` is a nonempty UTF-8 string, unique within the file. The packet
  fixes uniqueness, not a narrower character grammar, so no narrower grammar is
  imposed.
- `seq` is a positive integer excluding bool, strictly increasing in file order.
  Snapshots are **not sorted or reordered**; a non-increasing sequence is
  refused.
- `candidate_sha256` is exactly 64 lowercase hexadecimal characters.
- `requirements_version` is `v1` or `v2`.
- `elapsed_ms` and `model_responses` are null or nonnegative safe integers
  (<=9,007,199,254,740,991, bool excluded).
- `grade` is null (not graded) or exactly
  `candidate_sha256`, `requirements_version`, `outcome` (`pass`/`fail`/`unknown`),
  `evidence_kind`. Its identity and requirements version must agree with its
  snapshot, and its evidence kind must agree with the envelope.

Refusals use bounded fixed errors from `TrajectoryError` with stable codes:
`unsupported_schema`, `invalid_input`, `duplicate_checkpoint`,
`invalid_sequence`, `identity_mismatch`, `mixed_provenance`, `input_too_large`.
Messages never echo supplied identifiers, hashes or grade content.

## Output (`trajectory-output-0.1`)

Exactly `schema_version`, `episode_id`, `evidence_kind`, `snapshots`, `segments`.
`snapshots` preserves every validated supplied snapshot in order, including null
grades and null counters.

A segment is a maximal contiguous run of one `requirements_version` and has
exactly:

```json
{
  "requirements_version": "v1",
  "start_seq": 1,
  "end_seq": 3,
  "snapshot_count": 3,
  "known_pass": 1,
  "known_fail": 1,
  "unknown": 1,
  "first_observed_correct": {
    "checkpoint_id": "ckpt-2",
    "seq": 2,
    "elapsed_ms": 20,
    "model_responses": 3
  },
  "observed_regressions": []
}
```

- Counts partition `snapshot_count`: a null grade or `unknown` outcome counts as
  `unknown`; it is never labelled fail.
- `first_observed_correct` is the first snapshot in the segment whose supplied
  grade is `pass`, or null. It is **not** the exact time the agent first became
  correct: unobserved intervals remain unknown.
- `observed_regressions` records only two consecutive snapshots in the same
  segment with grade `pass` then `fail`. Each entry is
  `{from_checkpoint_id, from_candidate_sha256, to_checkpoint_id,
  to_candidate_sha256}` in observation order. Unknown/null grades interrupt the
  inference; a version change is never compared across.
- A later recurring version starts another segment. Empty snapshots produce no
  segments.

## Explicit synthetic examples

1. **fail -> pass -> fail** (one segment): first observed correct is the middle
   snapshot; one confirmed regression from the earlier pass to the later
   fail (`from` = pass snapshot, `to` = fail snapshot).
2. **pass -> unknown -> fail**: first observed correct is the first pass; the
   unknown grade interrupts inference, so `observed_regressions` is empty.
3. **v1 pass -> v2 fail**: two segments, one per version; no regression is
   recorded across the version change.

These are synthetic examples only, not results.

## Deliberately absent or deferred

No headline score, causal conclusion, check penalty, statistical inference,
model judging, provider/container call or report-input change. The summarizer
validates structure but cannot authenticate a caller, so a caller could still
supply untrusted or mislabelled records. Main's runtime/protected-grade adapter
must supply authentic records later, and Main owns any scoring/projection
decision. The implementation adds no I/O and no new dependency.

## Tests

```text
python -B -m unittest discover -s tests -p test_trajectory.py -v
```

The focused suite covers the three literal examples, empty/all-unknown inputs,
exact segment/count/first-correct/regression outputs, and hash/version identity,
mixed-provenance, duplicate-checkpoint, sequence, schema and 1000-snapshot-bound
refusals.
