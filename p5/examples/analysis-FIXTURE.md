# P5 analysis (independent recomputation)

**Dataset:** `p5-fixture-synthetic-development-001`

**Evidence notice:** Supplied independent judgments only. Synthetic/development rows are fixture data, not model results. No pooled headline, significance test or causal claim.

## Inventory (not a benchmark score)

| Attempted | synthetic_development | real_provider | development | pilot | formal |
|---|---|---|---|---|---|
| 5 | 5 | 0 | 5 | 0 | 0 |

## C1 × ordinary (development, synthetic_development)

Attempted 2; eligible 1; excluded 1.

| completed | partial | limit_reached | provider_error | infrastructure_error | invalid |
|---|---|---|---|---|---|
| 1 | 0 | 0 | 1 | 0 | 0 |

| strict pass | strict fail | strict unknown | success fraction |
|---|---|---|---|
| 0 | 1 | 0 | 0/1 |

| component | pass | fail | unknown |
|---|---|---|---|
| functional | 0 | 1 | 0 |
| workflow | 1 | 0 | 0 |
| evidence | 1 | 0 | 0 |
| completion | 0 | 1 | 0 |

| resource | known_sum | known_count | unknown_count | complete_total |
|---|---|---|---|---|
| model_responses | 2 | 1 | 1 | null |
| tool_actions | 2 | 1 | 1 | null |
| input_tokens | 90 | 1 | 1 | null |
| output_tokens | 40 | 1 | 1 | null |
| elapsed_ms | 900 | 1 | 1 | null |
| estimated_cost_micro_usd | 8 | 1 | 1 | null |
| reserved_micro_usd | 50 | 2 | 0 | 50 |

## R1 × ordinary (development, synthetic_development)

Attempted 2; eligible 2; excluded 0.

| completed | partial | limit_reached | provider_error | infrastructure_error | invalid |
|---|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 0 | 0 |

| strict pass | strict fail | strict unknown | success fraction |
|---|---|---|---|
| 1 | 1 | 0 | 1/2 |

| component | pass | fail | unknown |
|---|---|---|---|
| functional | 2 | 0 | 0 |
| workflow | 2 | 0 | 0 |
| evidence | 1 | 0 | 1 |
| completion | 2 | 0 | 0 |

| resource | known_sum | known_count | unknown_count | complete_total |
|---|---|---|---|---|
| model_responses | 3 | 2 | 0 | 3 |
| tool_actions | 3 | 1 | 1 | null |
| input_tokens | 180 | 2 | 0 | 180 |
| output_tokens | 50 | 1 | 1 | null |
| elapsed_ms | 1000 | 1 | 1 | null |
| estimated_cost_micro_usd | 10 | 1 | 1 | null |
| reserved_micro_usd | 50 | 2 | 0 | 50 |

## R2 × ordinary (development, synthetic_development)

Attempted 1; eligible 0; excluded 1.

| completed | partial | limit_reached | provider_error | infrastructure_error | invalid |
|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 1 | 0 |

| strict pass | strict fail | strict unknown | success fraction |
|---|---|---|---|
| 0 | 0 | 0 | N/A (denominator 0) |

| component | pass | fail | unknown |
|---|---|---|---|
| functional | 0 | 0 | 0 |
| workflow | 0 | 0 | 0 |
| evidence | 0 | 0 | 0 |
| completion | 0 | 0 | 0 |

| resource | known_sum | known_count | unknown_count | complete_total |
|---|---|---|---|---|
| model_responses | 0 | 0 | 1 | null |
| tool_actions | 0 | 0 | 1 | null |
| input_tokens | 0 | 0 | 1 | null |
| output_tokens | 0 | 0 | 1 | null |
| elapsed_ms | 0 | 0 | 1 | null |
| estimated_cost_micro_usd | 0 | 0 | 1 | null |
| reserved_micro_usd | 25 | 1 | 0 | 25 |

## Limitations

- Small authored study; scenario configurations are not independent domains.
- Workflow/evidence/completion predicates depend on supplied independent judgments.
- Unknown values are not failures and are never imputed as zero.
- No statistical significance or model-ranking claim is supported.
