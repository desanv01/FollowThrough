# P5 offline campaign and analysis tooling

Status: **candidate implementation for local P5 review/integration** (offline only).
No formal result, model/provider evidence or experiment freeze is contained here.

This package supports the P5 checklist items P5.1.1, P5.1.3, P5.2.1, P5.2.2,
P5.2.3 and the source-side of P5.3.1/P5.3.2:

- deterministic interleaved campaign design and affordability arithmetic;
- bounded run-request drafts that are explicitly **not authorized**;
- an attempt ledger that preserves every attempt and refuses selective reruns;
- independent recomputation of status/strict/component/resource summaries;
- checkpoint trajectory flags from future verified observations;
- ZIP/package validation, manifests, exclusion scans and expected-subset checks.

## Layout

```
p5/p5_tools/        standard-library package
p5/tests/           focused unittest suite and labelled fixtures
p5/examples/        generated examples, all labelled PROPOSED/FIXTURE
p5/rehearsal/       45374ce source manifest used by the clean-source rehearsal
docs/research/P5-OFFLINE-REHEARSAL.md   rehearsal record and limits
```

## Commands (offline, standard library only)

Run from this `p5/` directory:

```text
python -B -m unittest discover -s tests -t tests -v
python -B -m p5_tools.cli design --json examples/design-PROPOSED.json
python -B -m p5_tools.cli recompute --input <report-input.json> --report <report.json> --json <out>
python -B -m p5_tools.cli validate-receipt --receipt <attempt-receipt.json> --json <out>
python -B -m p5_tools.cli analyze --dataset examples/empty-formal.json --formal 
    --json examples/analysis-EMPTY-FORMAL.json --md examples/analysis-EMPTY-FORMAL.md
```

The CLI subcommands are `design`, `affordability`, `run-request`, `analyze`,
`compare`, `trajectory`, `inspect-zip`, `extract`, `manifest`, `verify-tree` and
`exclusions`. `extract` writes only to a new explicit destination.

## Labels and safety

- Design/affordability/run-request outputs say `PROPOSED_NOT_FROZEN` or
  `DRAFT_NOT_AUTHORIZED`. Pilot inputs in fixtures are `PROPOSED_PILOT_INPUT_NOT_OBSERVED`;
  they are not observations.
- Fixture datasets are `synthetic_development`/`development` and must never be
  reported as model results. Tests construct test-only `real_provider` rows in
  memory; no provider evidence is claimed.
- The tooling makes no network, credential, Docker, provider or Git operation.
- Unknown resource values stay unknown; zero denominators render as `N/A`.
- Every attempt is retained; the projection requires one frozen primary per
  cell and refuses to replace a model outcome.

## Known environment limitation

Direct `python -B grader/*.py --self-test` execution in the managed Windows
sandbox fails at `tempfile.TemporaryDirectory` setup because mode-0700
directories are inaccessible. The rehearsal used a wrapper that changes only
`tempfile.mkdtemp` creation mode; grader source was not edited. This is tracked
as issue P5-ISS-001 and does not by itself qualify the exact runbook command.


