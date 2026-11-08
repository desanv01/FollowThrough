# Deadline-focused completion policy

## Required approach for every Codex and DeepSeek task

User-confirmed instruction, 8 November 2026:

1. Complete the entire approved master checklist: every phase, subphase, task and subtask.
2. Keep tests and reviews minimal but meaningful; verify that the required behavior works.
3. Fix bugs that block required functionality or make the resulting evaluation invalid.
4. Record and defer nonblocking bugs, deeper testing, refactoring and hardening until the full planned implementation is complete.
5. Advance once the required behavior works; reuse valid evidence instead of repeating broad checks.
6. Do not drop features, phases or deliverables without the user's explicit instruction. Do not mark unfinished or untested work as completed or verified.

Codex Main must include this policy in every new task packet, DeepSeek launch prompt and reviewer assignment. Each worker must read it before task work. Record deferred findings against their checklist IDs. Existing ownership and credential/spending protections remain in force.

User instruction, 8 November 2026: minimize implementation verification and review effort on both DeepSeek and Codex. Get the intended task working, integrate it, and advance through the remaining plan. Defer deeper bug hunting, edge-case coverage, refactoring and hardening until the core project is complete.

This latest user instruction supersedes earlier exhaustive review/test expectations. Main applies it immediately to future acceptance decisions; new DeepSeek packets and Codex reviewer instructions must include it. It does not retroactively turn a failed or untested result into a pass.

- Implement only the concrete assigned behavior needed for the working end-to-end evaluation.
- Use the smallest meaningful smoke check for the intended path. For a known blocker, add or reuse one targeted reproduction. Reuse trustworthy existing results; do not repeat broad passing suites or require a second layout run by default.
- Independent review remains brief and focused on actual changes and the main path. Do not open speculative adversarial/edge-case audits or expand a finite correction into a general hardening project.
- Block acceptance only for a demonstrated issue that prevents the intended workflow, materially corrupts evaluation/results, loses required work, exposes credentials, defeats the authorized spending bound, or makes the intended execution boundary unsafe. Fix those with the smallest check. Log other findings as deferred and proceed.
- Never change assertions, fabricate evidence, suppress relevant failures or call something tested when it was not. Mark source accepted for the demonstrated scope with deferred limitations; qualification not performed remains pending.
- Keep ownership, credential isolation, spending limits, evidence provenance and honest outcomes. Preserve frozen deliveries and concise existing delivery records. Reduce paperwork rather than generating extra preparation/review rounds.
- Sequence work around an integrated working episode, while retaining every checklist component as required scope. Continue independent ready work in parallel. Track scoped GitHub publication and required current-head CI; do not invent extra CI gates.
- Existing released packets may be running. Do not silently rewrite pinned inputs or worker scopes. Apply this policy to their reviews immediately; give a concise user-relayed addendum when changing an active worker's verification instructions. All newly issued prompts use this policy.

Deferred work stays visible in the master checklist or a concise findings list with its affected task; it is not erased or described as fixed. Revisit deeper testing and hardening after the full planned work is implemented, within remaining time.

Scope clarification from the user, 8 November 2026: complete the ENTIRE approved master checklist, including all phases, subphases, tasks and subtasks. Deadline-focused mode reduces verification depth and nonblocking bug-fix effort only; it does not authorize a smaller feature set, omitted phases, reduced deliverables or marking unfinished work complete. Any future scope reduction requires a separate explicit user decision. Implement every listed requirement with minimal meaningful checks and brief reviews; fix blockers, record nonblocking issues, and revisit deeper testing/hardening after the full planned work is implemented. Normal dependencies still apply.
