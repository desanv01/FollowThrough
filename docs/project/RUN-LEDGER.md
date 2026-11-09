# Run ledger viewer (T3 terminal fidelity correction)

Status: **candidate T3 fidelity correction; independent review and native acceptance pending.**
This is the actual read-only ANSI terminal viewer, with the approved T3 geometry.
Live progress, execution, runtime preflight, browser report, theme/Unicode/resize
refinement and the optional later palette review are outside this packet. Main
owns independent review, the real-terminal rerun, integration and acceptance.

## Command

```text
python -B -m reporting.run_ledger --input PATH --format text|json|tui
```

`text` is the default and is plain (no ANSI). `json` emits the existing
`report-output-0.1` object unchanged. `tui` is opt-in and requires an
interactive terminal. The viewer reads exactly one explicit `report-input-0.1`
file through the accepted reporting loader and `build_report`; it does no
automatic discovery, execution, provider call, grading, retry or artifact
write.

## Text and JSON

Text shows the synthetic warning, dataset/inventory (evidence and stage counts
kept separate), every attempt as a full-ID readable card when the table cannot
fit (including all eight pinned IDs at 80 and 100 columns), and a hint to use TUI
for details. Outcome and resource summaries are not pooled across provenance or
stage: outcome/resource values are shown in the selected-attempt details, so the
viewer adds no headline score. JSON mode is exactly the report object.

## TUI (T3 dark/cyan)

The saved-report TUI uses a black base, explicit RGB cyan borders and table
header, dark teal selection, a framed selected-attempt panel and cyan keyboard
footer. Wide terminals (108 columns or more) use the actual boxed table; compact
terminals use framed rows with the selected identity and outcomes below. The
title explicitly labels the table's grade as strict. The palette is kept in the
renderer (`PALETTES`, `cyan` default; `muted` retained for the optional
post-phase review).

Font size and family belong to the host terminal. Cascadia Mono or JetBrains
Mono are recommendations; the viewer does not modify global terminal settings.
The RGB black/cyan surface avoids relying on the host's 16-colour cyan theme.
No sample rows, campaign cap or pooled costs/outcomes are added by the layout.

### Frame fitting (F1)

Each redraw is fitted to both terminal columns and rows. The table is a bounded
window centered on the selected attempt, so the selected row remains visible;
the compact framed layout marks it with `>`. Details, filter line and key hints are
also included in the height budget. At 80×24 the pinned eight-attempt fixture
fits in at most 24 lines of at most 80 columns before and after selection,
filtering and evidence actions; taller/wider terminals show more rows. Very
short terminals trim detail/table lines in a deterministic order rather than
scrolling the selection away.

### Details and evidence (F1b)

The initial 80×24 detail view shows the four supplied component outcomes on
separate readable lines, plus trial, elapsed, estimated cost and reserved
budget without clipping. Enter switches to a distinct **EVIDENCE VIEW** that
lists up to eight recorded trace events individually (not the same single-line
excerpt), so the action visibly reveals more recorded display data. Pressing
Enter again returns to details; Escape closes evidence first. The evidence view
is display data only, not execution evidence or grading truth.

### Rows and summaries (F2)

Rows carry a visible `PROV/STAGE` label (`synthetic/dev`, `provider/formal`,
etc.); `real_provider` rows are labelled `provider`, a faithful short label with
no unsupported protection claim. The full supplied provenance and stage also
appear in the selected panel and in the dataset inventory. The header/footer keep
evidence-kind and stage counts separate. No strict/eligible/outcome or resource
totals are pooled across provenance or stage; those values remain attempt detail. When all estimated-cost
or reserved values are missing, the selected detail shows `unknown` rather than
zero; estimated cost remains an estimate and reserved budget an authorisation,
neither billed cost. The dataset inventory (attempted rows and separated
provenance/stage counts) is preserved.

### Selected attempt

The panel shows scenario/arm/trial/stage/provenance, eligibility, all four
supplied component outcomes, available resources, separately labelled estimated
cost and reserved budget, and a one-line bounded excerpt of the recorded display
trace. Missing values show `unknown`; trace text is display data, not execution
evidence or grading truth.

### Interaction

- Up/Down or `j`/`k`: move selection in the bounded window.
- Enter: toggle the evidence excerpt.
- `/`: case-insensitive filter on episode ID, scenario, arm or status; type then
  Enter to apply; Backspace edits.
- Escape: close evidence, else clear the filter, else leave the viewer.
- `q`: quit.
- Empty datasets and no-match filters are explicit and do not retain a stale
  selection.

### Terminal mode and cursor safety (F3/F4)

- POSIX key reading and readiness use **one raw-buffer layer**: `os.read` on the
  descriptor plus `select` on that same descriptor, avoiding buffered-text
  prefetch that turned a Down key into `esc`/quit. Down and standalone Escape
  are covered by an injected byte-layer test; the actual POSIX terminal remains
  a Main rerun gate.
- Mode setup failure (unsupported console/termios setup, failed first hide-cursor
  write/flush) raises a bounded `tui_terminal_setup_failed` diagnostic pointing
  to `--format text`; it does not enter the unsupported session.
- Any applied mode and cursor state are restored on startup failure,
  interruption, normal exit or error, with colors reset and cursor shown.
- Terminal implementation remains best-effort: no alternate screen, no vertical
  scrolling beyond the fitted window, and wrapping measures Python characters
  rather than full East-Asian display widths. Theme, deeper Unicode/resize work
  and live/browser behavior are deferred.

## Safety and evidence

All displayed supplied strings are sanitized by replacing C0/C1 control
characters (including ESC) and embedded line breaks before display. Inputs are
expected to be sanitized by the producer; this is terminal display hardening,
not a universal secret detector. No campaign cap or running state is shown
because the accepted report projection does not carry those fields.

Evidence is offline synthetic/preparation only. Test-only reports, injected keys
and injected terminal modes are not live results, provider results, sandbox
qualification, formal freeze, CI success or acceptance.

## Tests

```text
python -B -m unittest discover -s tests -p test_run_ledger.py -v
```

Focused tests cover the pinned synthetic fixture in text/JSON; 80×24 frame
bounds before/after selection, filtering and evidence; mixed test-only
provenance/stage rows and all-unknown costs; excluded/unknown labels and unknown
resources; TUI selection, evidence, filter and no-match/empty states; literal
escape sanitisation; narrow width/palette; POSIX Down/standalone Escape;
non-interactive TUI refusal; and terminal setup-failure/write-failure/
interruption rollback plus normal restoration.
