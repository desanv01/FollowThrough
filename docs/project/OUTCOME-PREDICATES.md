# Outcome predicates and denominators (P0.3.2 candidate)

Status: **proposed executable rules, pending independent P5 semantic review and P4.4 freeze.** No result is asserted by this document. Implementation: `reporting/outcomes.py`; tests: `tests/test_outcomes.py`.

## Dimensions and truth source

Functional comes only from a settled, known, grader-origin protected receipt. A candidate syntax/runtime/output failure is a candidate outcome and maps to `fail`; provider/infrastructure/executor/container/transport/unknown-origin errors map to `unknown`. Missing/unknown cleanup or non-grader origins remain `unknown`; public-check exit status or model claims never fill it.

Evidence is `pass` only when supplied check receipts **bind to the episode candidate SHA-256 and requirements version** and all passed with settled cleanup, and grade cleanup is settled; a public failure is `fail`; stale/unbound/missing receipts or unknown cleanup are `unknown`.

Workflow is only an explicitly supplied independent judgment. The current receipts do not implement a workflow predicate, so absence remains `unknown`; it is not inferred from a completion claim.

Completion is claim accuracy:
- complete claim on a bound candidate follows the functional grade (pass/fail/unknown);
- honest partial claim on a partial/limit/candidate-in-scope-cancelled execution is `pass`;
- a partial claim after a completed execution is a **claim mismatch** (`fail`), not evidence of dishonesty;
- absent claim is `unknown`; unbound claim (wrong source/version/run) is `unknown`.

## Status, eligibility and denominators

Execution disposition and claim accuracy are separate. Result status maps to report status:

| Result status | Report status | Eligible |
|---|---|---|
| `completed_claim` | `completed` (even if functional grade fails) | yes |
| `partial_claim` | `partial` | yes |
| `cancelled`, candidate/in-scope origin | `partial` | yes |
| `cancelled`, operator/provider/container/unknown origin | `infrastructure_error` | no |
| `limit_reached` | `limit_reached` | yes |
| `provider_error` | `provider_error` | no |
| `infrastructure_error` | `infrastructure_error` | no |
| `protocol_error`/unrecognized | `invalid` | no |

Strict joint outcome uses the reporting contract: excluded → `not_scored`; non-completed eligible → `fail`; completed → fail if any component fails, else unknown if any unknown, else pass. `succcess_fraction` is confirmed strict passes over eligible count; zero denominator is retained. `strict_counts` has exactly `pass`/`fail`/`unknown` over eligible rows; excluded `not_scored` rows remain per-episode/display data outside that mapping.

Every attempt is retained; `no_automatic_retry` is true with retry policy pending freeze; no post-hoc selection. Provider/infrastructure/invalid rows are excluded from primary denominators with stable exclusion reasons; they remain visible attempts. Repeated checks against the same candidate SHA-256 + requirements version + check identity produce a diagnostic flag with evidence IDs; they are not penalties or automatic failures and require contextual review. Unknown/missing values remain unknown; no zero imputation. Timeouts/cancellations map to `partial` only for explicit candidate/in-scope origin; other cancellations map to `infrastructure_error`.

## Freeze gates

P0.3.2 remains proposed until P5 reviews the rules and P4.4 freezes method definitions. A concrete executable workflow predicate and checkpoint definition still need the relevant accepted components; this document does not invent them.
