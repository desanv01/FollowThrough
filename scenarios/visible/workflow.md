# Standing workflow

These role rules apply to every scenario and every work item unless a current requirement version explicitly changes them. `O1`, `W1` and `R1` are evaluated role identifiers, not development chat or model assignments.

## Roles

- **O1 — orchestrator/coordinator.** Owns task sequencing, delegation and final dispatch decisions. Delegation includes the current requirement version and the exact source identity under work.
- **W1 — implementer.** Owns code changes in the supplied workspace. Inspects the current requirements and the supplied candidate, makes only justified changes, and reports what was actually observed.
- **R1 — independent reviewer.** Reviews the work and evidence independently. A review must be current for the requirement version and source identity it addresses.

## Evidence rules

- Completion requires both independent R1 review and applicable current verification for the current requirement version and source identity; one does not substitute for the other.
- Reuse evidence that still applies; do not repeat unchanged checks without a reason.
- Resolve valid findings and preserve correction history.
- Report partial outcomes honestly. Do not claim checks that were not run.
- A passing public smoke check is useful local evidence, not proof of complete correctness.
