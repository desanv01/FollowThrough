# Offline reporting (P4-REPORT/r3 candidate)

Status: **candidate implementation r3 narrow correction, not integrated or
accepted**. This document describes the four-file worker proposal under
`.coordination/candidates/P4-REPORT/r3/`, copied from frozen r2 and corrected
only for the residual R1-F2 literal-rendering finding (URL autolinking). R1-F1,
R1-F3 and R1-F4 remain independently closed and their behavior is preserved.
Main owns the reporting contract, integration, independent review and
acceptance.

## Purpose and non-goals

`reporting/report.py` turns a caller-supplied, versioned report-input projection
(`report-input-0.1`) into a deterministic offline report:

- `report.json` — machine-readable summaries and per-episode detail;
- `episodes.csv` — fixed-column episode table;
- `report.md` — human-readable summaries, warnings and recorded traces.

The reporter summarizes **supplied judgments only**. It does not grade
episodes, re-run checkpoints, judge model prose, replay execution, infer a
repaired trajectory, compute significance, forecast cost or call any provider.
The older runtime event envelope is a separate interface and is not interpreted
here. Synthetic development input records are authored fixtures, never model
results.

## API (standard library only, no I/O on import)

```python
from reporting.report import build_report, render_markdown

report = build_report(parsed_input_dict)   # validates; no I/O
markdown = render_markdown(report)         # deterministic string; no I/O
```

`generate(input_path, output_dir)` performs the file workflow used by the CLI,
and `main(argv)` is the command-line entry point. `reporting/__init__.py` resolves
the public names lazily so `python -B -m reporting.report` does not import the
module twice.

## CLI

```text
python -B -m reporting.report --input <explicit JSON file> --output-dir <new directory>
```

- Exit `0` only after all three files are published; otherwise nonzero with a
  bounded `error: <code>: <message>` diagnostic on stderr.
- Diagnostics never dump supplied objects, trace text or raw episode content.
- The output directory may be anywhere explicitly supplied except the input
  file itself, subject to the checks below.
- No directory scan, URL loading, dependency installation or source execution.

## Input contract summary

Top level exactly `schema_version` (`report-input-0.1`), `dataset_id`
(ASCII `A-Za-z0-9._:-`, nonempty, <=128) and `episodes` (array, empty valid).
At most 10,000 episode rows, 1,000 trace events per row, 10 MiB of input bytes
(checked before JSON parsing), and 4,096-character strings; identifiers are
<=128. Duplicate JSON object keys, duplicate `episode_id`, and duplicate
`(evidence_kind, stage, scenario_id, arm, trial)` cells are rejected.
`synthetic_development` requires `stage=development`; `real_provider` provenance
is caller-supplied and is not verified. Trace `seq` must be an integer
(excluding bool) from 1 through 9,007,199,254,740,991 inclusive and strictly
increasing within each episode (gaps allowed). `trial` has the same explicit
1..9,007,199,254,740,991 bound. Booleans, floats, NaN/Infinity and negative
resource values are rejected; resources allow null or an integer up to
9,007,199,254,740,991. JSON numeric-conversion failures, including integer
literals beyond the interpreter digit limit, become a fixed bounded
`invalid_input` diagnostic without echoing raw values or a traceback; the
interpreter-wide digit limit is not changed.

Stable error codes: `unsupported_schema`, `invalid_input`, `duplicate_key`,
`duplicate_episode`, `duplicate_cell`, `input_too_large`, `output_exists`,
`unsafe_path`, `io_error`. The exception class `ReportError` is worker-owned.

## Summary semantics

- Groups are keyed by `(evidence_kind, stage, scenario_id, arm)`. Synthetic
  and provider evidence and pilot and formal stages are never pooled, and no
  cross-scenario/cross-arm headline is produced.
- Every group reports attempted rows, all six status counts, eligible/excluded
  counts and `not_scored` excluded rows. Eligible statuses are `completed`,
  `partial` and `limit_reached`.
- Strict outcome precedence: an eligible non-completed attempt is a strict
  failure; a completed row is a strict failure if any component fails;
  otherwise it is strict unknown only when at least one component is unknown;
  otherwise it passes. Excluded attempts are `not_scored`. An honest partial
  may have completion accuracy pass and still fail strictly.
- Component counts cover the four supplied dimensions separately among eligible
  rows. `success_fraction` is confirmed strict passes over eligible count; a
  zero denominator is retained and rendered as `N/A`, never silently as zero.
- Resources aggregate **all attempts** as `known_sum`, `known_count`,
  `unknown_count` and `complete_total` (null when any value is unknown; an empty
  set totals 0). Unknown values are never imputed as zero. Estimated cost is an
  estimate and reserved allowance is an authorization; neither is billed cost.
  Per-success cost is not computed in v0.1.

Rows are sorted by `(evidence_kind, stage, scenario_id, arm, trial,
episode_id)`; groups by the first four fields. Derived `family` is
`retry-processing` for `R*` and `preview-cache` for `C*`. Result content has no
timestamps, absolute host paths or random identifiers; supplied trace order is
retained.

## CSV and Markdown safety

`episodes.csv` has the fixed columns from the contract, uses Python `csv`
escaping (`\r\n` line endings) and empty cells for null numeric values. Text
cells beginning with `=`, `+`, `-`, `@`, tab or carriage return are prefixed
with an apostrophe so spreadsheet applications keep them as text; numeric
columns stay numeric. There is no trace column.

Markdown escapes backslashes, HTML-sensitive characters, table delimiters,
newlines and Markdown inline punctuation (backticks, emphasis characters,
image/link brackets and parentheses, and `!`/`~`) in table cells, plus the
URL-critical characters `.`, `:`, `@` and `/`. Newlines become `<br>`. The
URL-critical escapes break the source patterns used by URL/email autolinking:
a renderer with `linkify`/autolinking enabled therefore emits no generated
anchor or image for a supplied URL, email, bare domain or protocol-relative
host. Ordinary Markdown rendering removes the backslash escapes, so the same
cell displays the supplied characters literally, including the readable URL
and email text. A supplied summary containing `![alt](...)`, `[text](...)` or a
bare URL is rendered as text, not as an active image or link; the renderer
opens no URL.

This is display hardening for supplied table text, **not a universal
Markdown/HTML sanitizer** and not a guarantee against every possible renderer
extension. The candidate remains standard-library only; the installed offline
parser used for local verification is verification-only evidence, not a
proposal dependency. The synthetic warning is prominent, no synthetic fraction
is presented as frontier-model performance, and recorded traces are shown as
display data only — never executed and never treated as proof.

## Path checks and publication

Input and output-parent paths are walked top-down and refused if an existing
component is a symlink or reparse point. Each component is checked with
`lstat`/reparse attributes only after every ancestor has been cleared, so the
guard rejects a reparse ancestor before `lexists`, `isfile`, `isdir`, reads or
writes probe any descendant. `lexists` is used for the final leaf (which does
not follow a link), and the leaf is also covered by the component walk. Output
must not exist; the input must be a regular file; the output parent must exist.
The complete files are written into a new owned sibling staging directory
(`.<name>.p4-stage`), flushed and fsynced, then the directory is renamed into
place. An existing output or stale staging path is refused and left untouched.
On failure the stage is retained and its path is reported; no partial tree is
copied to the final path.

These are **stable-host assumptions**: one cooperative process, no concurrent
writer racing the final name, a local filesystem whose directory rename does not
overwrite, and no hostile filesystem. The guard order is not a claim of
race-proof filesystem access. A bounded retry tolerates transient Windows
sharing/access denials during the rename; it does not turn the operation into a
transactional guarantee.

## Deliberately absent

No checkpoint regrading, semantic/model judging, repair-trajectory inference,
significance tests, forecasts, provider/network calls, runtime replay, live
demos or credential handling. The input limit is a hard 10 MiB parse bound, not
a security sandbox, and the reporter cannot detect every secret in arbitrary
caller-supplied strings.

## Future runtime-to-projection integration (later, Main-owned)

A future Main-owned adapter must translate qualified runtime/grader receipts
into `report-input-0.1` (or a later explicitly versioned projection): map
runtime attempts to episodes, supplied independent judgments to `outcomes`,
receipt facts to `resources`, sanitized recorded events to `trace`, and retain
provenance/stage labels. Checkpoint-level requirement outcomes,
first-correct-candidate and regression metrics need that future checkpoint
projection and are intentionally not invented here. Until then this reporter
has no runtime integration and produces no live results.

## Tests

```text
python -B -m unittest discover -s tests -p test_reporting.py -v
```

The suite discovers the package root from its own location, so it works in the
worker overlay and in an integrated-shaped root containing `reporting/` and
`tests/` side by side; it does not assume a fixed parent depth or current
directory. It covers Main's eight supplied synthetic records against
`expected.json` (seven groups, strict outcomes/counts, unknown-cost
aggregation), empty/all-excluded data, status/component semantics,
provenance/stage partitions, ordering, malformed/duplicate/bool/negative/
oversized input, trial/trace numeric bounds and over-limit JSON literals,
guard-before-descendant path probing, literal Markdown image/link/inline and
URL/email autolink surfaces, trace order, non-overwrite and publication
failure, escaping, determinism, and CLI use from an unrelated working directory
with a child-only `PYTHONPATH`. Test-only `real_provider`-labeled constructions
exercise partitioning and are never presented as actual provider evidence.
The linkifying-renderer regression is a verification-only offline parser
witness (the review's installed VS Code Markdown-it bundle); the candidate
package adds no parser dependency and the string-level tests run without one.
