# Read-only browser report viewer

Status, 9 November 2026: source integrated, independently reviewed and hosted. The actual viewer has been rendered from the three retained real-attempt report rows and inspected in an isolated browser. It is read-only saved-result inspection, not a fresh model request. [Try it from a clean source checkout](../release/TESTING.md).

## Layout decision (P4.6.1)

A single-column accessible document is used for the browser report: dark, high-contrast semantic HTML with tables/sections, no client-side framework, login, database or network dependency. It is intentionally separate from the selected T3 terminal palette; the browser layout choice is documented here for review. The synthetic preview stays labelled.

## Projection and content (P4.6.2–3)

`build_viewer_data(report, trajectory=None, events=None, binding=None)` consumes only an accepted `report-output-0.1` object plus optional supplied trajectory/event/binding receipts. It does not read private coordination, raw prompts, provider reasoning, credentials or arbitrary host paths.

The viewer displays:
- dataset/inventory, separate evidence-kind and stage counts, synthetic warning and supplied-provenance notice;
- per-scenario within-arm comparison with attempted/eligible/excluded counts, strict pass/fail/unknown and denominator; no cross-scenario pooled headline;
- every episode with four separate component outcomes, strict label (`PASS`/`FAIL`/`UNKNOWN`/`EXCLUDED`), trial, stage, evidence kind, all resources, unknown costs as `unknown`, estimated cost vs reserved authorisation labels, and recorded trace as literal data;
- optional source/run binding and checkpoint trajectory segments (observed-only);
- optional event timeline built only from sanitized event type/role/status/action fields.

All supplied strings are HTML-escaped and control characters removed; the artifact contains no scripts, images, external styles, URLs or execution/retry/grade controls. Unknown is never zero; estimated/reserved are not billed cost; design/synthetic previews are not model results.

## Portable artifact (P4.6.4)

`python -B -m reporting.viewer --report report.json [--trajectory trajectory.json] [--events events.json] [--binding binding.json] --output report.html` writes one self-contained HTML file atomically. A focused synthetic artifact and static/visual-equivalent check are produced locally; a real browser pass and acceptance remain Main-owned. The included real-attempt report input and separate synthetic fixture retain distinct provenance labels; current captures and results are described in the dated development record.
