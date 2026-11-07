# Experimental methods — development draft

No formal results yet. Two original task families, six proposed scenarios, ordinary versus structured progress, three repetitions = 36 formal episodes. Development/control runs are not part of the formal campaign.

## Separate actors
The development coordinator/workers know references and graders. Evaluated role sessions never inherit development AGENTS.md, private packets or solutions. A model acting as reviewer is not the independent functional grader. A worker's self-reported success is not ground truth.

## Outcomes
Record functional correctness, workflow adherence, verification validity and completion accuracy separately; joint success requires all four. Full workflow scoring is not implemented by the initial functional grader. An accurate partial report can fail completion without being a false completion claim.

Trajectory metrics: offline per-checkpoint requirement outcomes; first correct candidate; subsequent regressions; recurring known findings; unchanged-input check repetition; aggregate team resources. Hidden checker outcomes are never returned during formal episodes. Repetition flags need contextual inspection, not automatic penalties.

## Comparison
Hold task instance, role models/settings, tools and team budgets fixed within scenario. Structured progress can restate visible facts and agent-generated findings only. It must retain mistaken updates, not use an oracle. Additional context/maintenance tokens counted. The main effect is a workflow-package comparison; prose-recap ablation is optional. Do not assert JSON alone causes improvement.

## Data freeze and reporting
Freeze task/checker/request/settings/tool versions before formal runs. Interleave arms and preserve all attempts. Candidate syntax/runtime failures are candidate failures; provider/auth/container failures are separate. No selective rerun of poor model outcomes. Small task-family count limits generalization; report raw per-scenario outcomes and avoid treating repeats as independent domains.

## Integrity and scope
Local trusted-fixture grading does not isolate arbitrary Python. Before model-generated code executes, qualify a container with sanitized environment, no network, limited resources and no grader/secret mounts during agent actions. Grading occurs separately with protected host-side records; subprocess test outcomes alone cannot prove arbitrary malicious code resistance. Synthetic history is identified as supplied fixture context, not fabricated live tool output.
