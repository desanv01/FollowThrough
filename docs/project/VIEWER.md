# Read-only browser report viewer (P4.6 candidate)

Status: **offline static-viewer candidate pending independent review and Main acceptance.** Implementation: `reporting/viewer.py`; tests: `tests/test_viewer.py`; portable artifact generated from the pinned synthetic report for local inspection only.

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

`python -B -m reporting.viewer --report report.json [--trajectory trajectory.json] [--events events.json] [--binding binding.json] --output report.html` writes one self-contained HTML file atomically. A focused synthetic artifact and static/visual-equivalent check are produced locally; a real browser pass and acceptance remain Main-owned. Actual qualified records are not yet available in the local campaign, so all current evidence is synthetic/preparation only.
