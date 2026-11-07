# FollowThrough master execution board

Updated 7 November 2026. User explicitly requires phase -> subphase/workstream -> task -> subtask ownership including human-launched DeepSeek Harness work. This board refines the approved plan without restarting accepted work. Source delivery is not acceptance. Private activation and source pins are under .coordination. The adopted [hybrid workflow](HYBRID-WORKFLOW.md) incorporates inspected CueMender and VerifForge practices.

## Owners and gates
MAIN = coordinator/integration/GitHub acceptance. CM = Codex Sol Medium implementer. CH = independent Codex Sol High reviewer; a separate explicitly transferred High implementation scope may resolve a blocker. DS = human-launched DeepSeek Harness execution worker. CU = Terra High computer-use worker. HUMAN = task meaning, credential entry, DSH launch/relay and personal submission choices.

Gates: I implemented, G integrated, R independently reviewed, A actual acceptance. State vocabulary: QUEUED, READY, ACTIVE, DELIVERED, CHANGES_REQUIRED, BLOCKED, ACCEPTED. A missing or failed check never becomes PASS by implication.

## P0 — project and experiment contracts
| ID / workstream | Tasks and subtasks | Execution owner | Review / acceptance | Dependencies | Current state |
|---|---|---|---|---|---|
| P0.1 baseline | 1 inspect empty root/instructions; 2 initialize local Git; 3 save approved plan/process; 4 create private controls and ignore secrets | MAIN | MAIN | user implementation instruction | Local bootstrap done; public repository created; initial accepted baseline being published |
| P0.2 ownership | 1 map all work; 2 record path owners; 3 register live workers; 4 reconcile gates after deliveries | MAIN | MAIN | P0.1 | ACTIVE; this board |
| P0.3 experiment | 1 specify six scenarios/two families; 2 define ordinary/progress conditions; 3 define error categories; 4 set primary outcomes | MAIN | CH + HUMAN semantic review | approved task design | Draft documented; formal freeze pending |
| P0.4 access | 1 record official DeepSeek route; 2 verify model alias/docs; 3 create ignored .env.local; 4 load key without logging; 5 establish aggregate US$20 ledger | MAIN; CM for loader/ledger | CH; MAIN | user provider/cap choice | $20 approved; alias verified; key value not inspected; loader/ledger pending |

## P1 — original tasks and graders
| ID / workstream | Tasks and subtasks | Execution owner | Review / acceptance | Dependencies | Current state |
|---|---|---|---|---|---|
| P1.1 retry fixture | 1 API/spec; 2 deterministic store faults; 3 original/mark-first/correct candidates; 4 positive and negative grader controls | CM retry_fixture | CH reviewer_wave1; MAIN | P0.3 + contracts | r2 ACCEPTED_LOCAL_FIXTURE after independent PASS and corrected real runner control; earlier failures preserved |
| P1.2 cache fixture | 1 implement original/v1/correct candidates; 2 grader-owned renderer spy; 3 spec-driven controls; 4 CLI/README; 5 candidate delivery | DS-CACHE-01 r1/r2; Codex cache_correction r3 takeover | CH reviewer_cache; MAIN integrates | main cache contract, pins | r3 ACCEPTED_LOCAL_FIXTURE; six files integrated; 26 cache tests; independent PASS; r1/r2 retained |
| P1.3 scenario wiring | 1 R1/R2/R3 manifests; 2 C1/C2/C3 manifests; 3 initial histories; 4 deterministic old-result delivery | CM integration owner | CH scenario audit; MAIN | P1.1/P1.2 accepted interfaces + P2/P3 | QUEUED |
| P1.4 grader qualification | 1 examine every public requirement; 2 mutation checks; 3 fresh state verification; 4 check candidate-error vs infrastructure labels; 5 human examples | CH; HUMAN review | MAIN | P1.1/P1.2 | QUEUED |

## P2 — runner and protected execution
| ID / workstream | Tasks and subtasks | Execution owner | Review / acceptance | Dependencies | Current state |
|---|---|---|---|---|---|
| P2.1 trusted controller | 1 Rust CLI; 2 new run directories; 3 snapshots/events; 4 subprocess limits; 5 grade validation | CM runner_core | CH reviewer_corrections; MAIN | contracts | r2 ACCEPTED_SOURCE; seven fake-boundary tests; independent F2 PASS; isolation not included |
| P2.2 real fixture integration | 1 invoke correct and wrong candidates; 2 examine actual grade; 3 preserve failure records; 4 handle Windows temp permission through supported mechanism | MAIN coordinates CM correction if needed | CH; MAIN | P1.1 + P2.1 | r2 actual trusted controls matched after approved escalation: correct passes, both wrong fail; r1 temp failures retained; retry F1 closed; corrected positive control passed with owned TEMP/TMP |
| P2.3 isolation | 1 pin existing Python image; 2 sandbox resource/network controls; 3 workspace mount exclusions; 4 bounded execution/cleanup; 5 read/write/network negative probes | CM isolation owner | CH security-focused review; MAIN | P2.1; Docker | Image/detached probe available; executor not implemented |
| P2.4 protected grading | 1 copy only candidate output; 2 separate grade execution; 3 protect grader/records; 4 limits and abnormal execution handling | CM isolation owner | CH; MAIN | P2.3 + functional graders | QUEUED |

## P3 — provider, evaluated roles, workflow conditions
| ID / workstream | Tasks and subtasks | Execution owner | Review / acceptance | Dependencies | Current state |
|---|---|---|---|---|---|
| P3.1 DeepSeek protocol | 1 bounded HTTPS request; 2 thinking/tool history; 3 usage normalization; 4 mocked transport errors | CM adapter_correction (Codex worker, not DSH) | CH reviewer_corrections; MAIN | official docs | r2 ACCEPTED_SOURCE; 17 offline tests; independent F3/F4 PASS; aggregate ledger and live unverified |
| P3.2 budget/auth | 1 explicit project .env.local loader; 2 pre-dispatch campaign reservation; 3 retain uncertain reservations; 4 aggregate <=US$20; 5 redacted logging | CM adapter owner | CH; MAIN | P0.4 + P3.1 | QUEUED; no paid requests made |
| P3.3 evaluated tools | 1 scoped read/edit/test; 2 isolated role contexts; 3 delegation/review event protocol; 4 completion/partial state | CM agent-loop owner | CH; MAIN | P2.3 + P3.1/P3.2 | QUEUED |
| P3.4 workflow arms | 1 ordinary message history; 2 persistent progress record; 3 same underlying facts/tools; 4 token/accounting differences | CM agent-loop owner | CH methodological review; MAIN | P3.3 | QUEUED |
| P3.5 event/task integration | 1 fixed checkpoint initialization; 2 requirements update event; 3 delayed supplied result; 4 distinguish supplied and live evidence | CM integration owner | CH; MAIN | P1.3 + P3.3 | QUEUED |

## P4 — reporting and qualification
| ID / workstream | Tasks and subtasks | Execution owner | Review / acceptance | Dependencies | Current state |
|---|---|---|---|---|---|
| P4.1 offline reporting | 1 parse frozen run records; 2 per-scenario tables; 3 resource summaries; 4 incomplete/unknown labels; 5 recorded replay text | DS-REPORT-01, future full packet P4-REPORT/r1 | CH reporting reviewer; MAIN | stable event/result schema plus representative sanitized samples | QUEUED; do not launch yet |
| P4.2 first live episode | 1 bounded API readiness; 2 actual tool turn; 3 retry repair; 4 independent live-role review; 5 separate grading | MAIN campaign operator / CM fixes | CH checks trace and acceptance | key + budget ledger + isolation + P3 | QUEUED |
| P4.3 pilot qualification | 1 both families/arms; 2 positive/negative grader controls; 3 measure cost/runtime; 4 verify no hidden answer leakage | MAIN operation owner; DS finite qualification/diagnostic packet when ready; CH audit | HUMAN task review; MAIN | P4.2 + accepted cache + exact operation scope | QUEUED; no launch or paid permission inferred from this row |
| P4.4 experiment freeze | 1 pin source/tasks/prompts/settings; 2 fix limits and retry policy; 3 register run order/outcomes; 4 separate development from formal results | MAIN | CH | P4.3 | QUEUED |

## P5 — campaign and evidence
| ID / workstream | Tasks and subtasks | Execution owner | Review / acceptance | Dependencies | Current state |
|---|---|---|---|---|---|
| P5.1 formal runs | 1 6 scenarios x 2 arms x 3 trials; 2 controlled concurrency; 3 budget enforcement; 4 retain every attempt | MAIN campaign owner | CH integrity review | P4.4 | QUEUED; reduce breadth explicitly if necessary |
| P5.2 analysis | 1 functional/process outcomes; 2 offline checkpoint trajectories; 3 review repeated-check flags; 4 limitations and denominators | MAIN; DS-REPORT implements report engine only | CH + HUMAN case review | P5.1 + P4.1 | QUEUED |
| P5.3 reproducibility | 1 clean package grading; 2 manifest/source identity; 3 sanitized traces; 4 representative independent recomputation | DS finite reproduction packet; CH independent review | MAIN | P5.2 | QUEUED; detailed prompt prepared only when inputs exist |

## P6 — GitHub, release and submission
| ID / workstream | Tasks and subtasks | Execution owner | Review / acceptance | Dependencies | Current state |
|---|---|---|---|---|---|
| P6.1 repository delivery | 1 create public desanv01/FollowThrough; 2 accepted clean diffs; 3 scoped commits/branches/PRs; 4 current-head CI; 5 guarded merge and readback | MAIN | CH where relevant; MAIN merge | user authorized target/visibility; relevant review must pass | ACTIVE; public repository created; accepted baseline publication |
| P6.2 docs/provenance | 1 setup; 2 methods; 3 actual results; 4 limitations; 5 contribution/license attribution | MAIN + documentation owner | CH + HUMAN task expertise | begins P0, completes P5 | Basic drafts present |
| P6.3 demonstration | 1 real useful trace; 2 story; 3 recorded/live labels; 4 <=5-minute video; 5 factual/playback checks | release worker; CU for UI; MAIN | CH/HUMAN factual check | first accepted real episode | QUEUED |
| P6.4 submission | 1 ZIP <50MB; 2 clean extraction; 3 video accessibility; 4 description/personal fields; 5 authorized final submission and receipt | MAIN + CU; HUMAN personal choices | MAIN/HUMAN | P5.3/P6.2/P6.3 | QUEUED |
| P6.5 parking/handoff | 1 settled workers/processes; 2 final references; 3 dirty-file preservation; 4 next actions and single coordinator transfer | MAIN | MAIN | completion or user pause | QUEUED |

## Wave 1 review corrections
Historical first report: .coordination/reviews/WAVE1/r1/REVIEW.md. That revision required corrections in all three source scopes. Current correction review: .coordination/reviews/CORRECTIONS/r2/REVIEW.md; runner F2 and adapter F3/F4 PASS with narrow source acceptance. Retry F1 also closed in independent RETRY/r2 review.
- P1.1.F1, CM retry_fixture: detect malformed-input lookup-before-validation; preserve correct reference and add the reproduced mutation to grader controls.
- P2.1.F2, CM runner_core: require complete, unique expected grade check IDs before accepting an error-free functional result; retain partial error receipts.
- P3.1.F3/F4, CM adapter owner: disable HTTP diagnostic output on actual connection; treat gateway/provider 5xx outcomes as uncertain for budget settlement. No real key leaked in review; synthetic-key mock only.
- CH verifies affected corrections; MAIN integrates actual controls after correcting supported temp path/permission handling. Reuse unaffected successful evidence.

## DeepSeek launch/return gates
P1-CACHE/r1 and r2 were delivered, verified and independently reviewed. Remaining r2 F3/F4b findings were transferred to a stopped-owner Codex r3 correction, independently passed and integrated. No further cache DeepSeek launch is pending. P4-REPORT is reserved for DS but depends on schema and samples; its prompt will be fully populated and posted when ready. Later finite qualification/reproduction packets extend DS participation when prerequisites exist. Main checks artifacts, CH reviews actual candidate, DS fixes finite findings, MAIN integrates; accepted coherent source can proceed through GitHub independently of unrelated live gates. Explicit Codex takeover is available after stopped-owner transfer; no endless correction relays. No DS self-acceptance, Git/PR/merge, paid API campaigns or future-task self-launch.

## Evidence and scope policy
The development process must avoid repeated successful checks; this does not artificially suppress loops in evaluated episodes. One writer per path. No candidates silently integrated. No model-generated code executed on host before qualified isolation. Local trusted controls are explicitly labeled. Routine GitHub source work is within user's latest instruction, but repo identity/visibility must be concrete; final event submission/personal consent remains separate.

## Schedule
Previously verified deadline 9 November 2026 13:15 Malaysia. Follow deliverable gates, not a literal four-day schedule. Prioritize complete real retry episode, cache parallel work, freeze, formal run evidence and release buffer. No new optional provider/UI work on the critical path. US$20 is the cumulative API ceiling, not a per-worker allowance.
