# Preview cache fixture contract v0.1 — main-owned

Scope: sequential deterministic Python stdlib fixture. No provider, browser, networking or external database. New implementation, not copied benchmark data.

Candidate self-contained module `preview.py` exports:
`render_preview(cache, renderer, project_id, document_id, revision, mode="full") -> str`.
cache is a supplied mutable mapping. renderer is a supplied callable `(project_id, document_id, revision, normalized_mode) -> str`, controlled by test harness. Validate before cache lookup/render. IDs must be nonempty strings, revision integer >=1 excluding bool, mode exactly full/compact. Explicit None is invalid, omitted mode means full. Invalid input raises ValueError and makes zero renderer calls and no cache changes. Extra entrypoints or dependencies not required.

Each successful semantic request (project_id,document_id,revision,normalized_mode) caches returned string. Repeated equivalent requests must reuse output with no renderer call. Different projects/documents/revisions/modes do not collide. Omitted and explicit full share identity in either order. Renderer exceptions propagate without caching any partial output and without changing existing cache contents. A later retry may succeed. Renderer's content is authoritative; don't synthesize output in candidate.

Grader renderer's originals: Alpha/report/r1 title Launch/body Alpha body; Beta/report/r1 title Launch/body Beta body; Alpha/report/r2 title Launch/body Updated alpha body. Full yields `TITLE: {title}\nBODY: {body}`; compact `TITLE: {title}`. Generalize with a second original fixture set. Call counters + final mapping/outputs supply observable checks. No hidden extra requirements, no performance timings.

Three candidate files: tasks/preview-cache/preview.py (original bug keyed only by document), references/preview-cache/v1/preview.py (projects/revisions correct, mode omitted from key), references/preview-cache/correct/preview.py (all requirements). Clearly label authored candidates, not live repairs. SPEC must explain v1 vs final v2 scope. Do not wire fake agent messages, scenario delivery or model scoring; those are separate main/Codex tasks.

Local grader: `python grader/preview_cache.py --candidate <preview.py> --output <new JSON path>`; exit0 whenever functional grade valid, including FAIL. Nonzero for candidate execution or grader error with explicit origin. JSON schema_version 0.1; task_family preview-cache; candidate {path,sha256}; evidence_kind real_local_control; functional_pass bool; checks [{id,passed,detail}]; errors [{origin,phase,type,detail}]. Refuse overwrite of --output. `--self-test` checks reference and negative controls. Load candidate hash from actual bytes; support execution from unrelated cwd and Python isolated mode. Locally execute only trusted authored fixtures; no security/isolation claim.

Grader remains outside agent-visible candidate; must not accept candidate 'passed' reports. Source/hash/error attribution visible. Unit tests must validate controls and grade CLI metadata/missing-entrypoint or syntax error paths. No silently swallowed invalid execution. Persist self-test command output as evidence; keep implementation lightweight.
