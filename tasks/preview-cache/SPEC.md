# Preview cache

Status: authored fixture for `P1-CACHE/r1`. Candidate-only overlay; not installed into the integrated public source. This task is sequential, deterministic and uses only the Python standard library. It has no network, provider, browser, database, file or timing requirement.

The task exercises cached preview rendering: a supplied cache and a supplied renderer are composed so that equal semantic requests reuse rendered output and different requests never collide. The renderer is controlled by the test harness and is authoritative.

## Candidate interface

`preview.py` is a self-contained standard-library module exporting:

```python
render_preview(cache, renderer, project_id, document_id, revision, mode="full") -> str
```

- `cache` is a supplied mutable mapping.
- `renderer` is a supplied callable invoked as `renderer(project_id, document_id, revision, normalized_mode)` and returns the preview string.
- The candidate returns the renderer's content unchanged. It must not synthesize, trim or reformat preview text.
- No other entrypoint or dependency is required.

## Validation

Every argument is validated before any cache lookup, cache write or renderer call.

| Argument | Valid values |
|---|---|
| `project_id` | nonempty string |
| `document_id` | nonempty string |
| `revision` | Python integer `>= 1`; booleans are not integers here and are invalid |
| `mode` | an actual string equal to exactly `"full"` or `"compact"` |

An omitted `mode` means `"full"`. Explicit `None` is invalid. The mode test is a string-type test followed by allowed-value membership: a non-string object that compares equal to a valid mode (for example `collections.UserString("full")`) is invalid, while an ordinary `str` subclass is accepted. Any invalid argument raises `ValueError`, causes zero renderer calls and leaves `cache` unchanged. A caller must be able to pass a prepopulated cache and an inaccessible or throwing renderer to an invalid request without observable effect. The cache is a supplied mutable mapping, not necessarily a `dict`; a minimal `MutableMapping` wrapper or `collections.UserDict` must work. Nonempty whitespace strings such as `" \t"` are valid opaque IDs and are never trimmed or normalized.

## Caching identity

A successful semantic request is the tuple `(project_id, document_id, revision, normalized_mode)`, where an omitted `mode` normalizes to `"full"`.

- Each successful request caches the returned string.
- A repeated equivalent request returns the cached string with no renderer call.
- Requests that differ in project, document, revision or mode are distinct and must not collide.
- Cached entries persist: after rendering document A then document B in one project, a replay of A still reuses A's cached output without another renderer call and without any mapping change.
- Omitted `mode` and explicit `"full"` share one identity, in either arrival order.
- When the renderer raises, the exact same exception object propagates with its custom metadata intact. No partial output is cached and existing cache contents remain unchanged. A later retry may succeed.
- Cache key layout is not part of the public interface; behavior is observed through outputs, renderer call counters and cache preservation.

## Fixture renderer originals

The grader owns these authored originals. Each entry is `project/document/revision -> title, body`.

| Project | Document | Revision | Title | Body |
|---|---|---|---|---|
| Alpha | report | 1 | Launch | Alpha body |
| Beta | report | 1 | Launch | Beta body |
| Alpha | report | 2 | Launch | Updated alpha body |
| Alpha | notes | 1 | Notes | Alpha notes |

A second original fixture set covers the same identities elsewhere:

| Project | Document | Revision | Title | Body |
|---|---|---|---|---|
| Gamma | memo | 3 | Draft | Gamma body |
| Delta | memo | 3 | Draft | Delta body |
| Gamma | memo | 4 | Draft | Gamma revision two |

The renderer output formats are exactly:

- `full`: `TITLE: {title}\nBODY: {body}`
- `compact`: `TITLE: {title}`

The grader also uses a literal renderer whose output does not follow those formats, to verify that candidate code returns renderer content verbatim rather than manufacturing an expected preview.

## Candidate files and scope

All three are authored fixtures, not live model repairs and not benchmark data.

| Path | Scope |
|---|---|
| `tasks/preview-cache/preview.py` | Original candidate. Validates the interface but keys the cache by document ID alone, so projects, revisions and modes collide. |
| `references/preview-cache/v1/preview.py` | Intermediate v1 candidate. Fixes the original document-only collision for projects and revisions, but still omits mode from the cache identity. |
| `references/preview-cache/correct/preview.py` | Final v2 reference. Caches by the full semantic identity `(project, document, revision, normalized_mode)` and satisfies this specification. |

### v1 versus final v2

v1 is a partial repair: its key is `(project_id, document_id, revision)`. It makes project and revision separation correct and still reuses repeated requests, but a compact request that follows a full request for the same project, document and revision returns the cached full preview. The final v2 reference adds the normalized mode to the key, so all four identity dimensions are distinct. The fixture therefore demonstrates a complete repair rather than only a first defect fix.

## Grader

```text
python grader/preview_cache.py --candidate <preview.py> --output <new JSON path>
python grader/preview_cache.py --self-test
```

The grader loads the candidate source from actual bytes, records the resolved candidate path and SHA-256, and runs independent checks with fresh caches and grader-owned renderer spies/counters. It never trusts candidate self-reports.

JSON output has `schema_version` `"0.1"`, task family `preview-cache`, candidate `{path, sha256}`, `evidence_kind` `"real_local_control"`, a boolean `functional_pass`, requirement checks `[{id, passed, detail}]`, and execution errors `[{origin, phase, type, detail}]`. Candidate execution errors are attributed to origin `candidate`; grader reading or setup problems are attributed to origin `grader`.

The process exits 0 whenever a complete functional grade was produced, including a failing grade. It exits nonzero for candidate execution errors or grader errors, with the origin recorded in JSON. The `--output` path must be new; an existing path is refused and left unmodified. `--self-test` runs the correct reference and both negative controls and succeeds only when the correct reference passes and each negative candidate fails exactly its specified checks.

This grader executes only trusted authored local fixtures. It provides no sandbox, isolation, resource-limit or security guarantee, and it makes no live-agent or provider claim.

## Checks exercised

- cold full render and caching of successful output
- repeated equivalent request reuse with no renderer call
- omitted/full equivalence in both arrival orders
- project, document, revision and mode separation
- a second original fixture set
- authoritative renderer content, including text outside the fixture formats
- invalid arguments: IDs, revision, and mode including explicit `None`; zero renderer calls and no cache access or mutation
- renderer failure propagation with preserved existing cache, and successful retry afterwards

## Tests and out of scope

`tests/test_preview_cache_grader.py` covers the positive and negative controls, the CLI, provenance from actual bytes, isolated-mode execution from an unrelated working directory, output non-overwrite, and the candidate-versus-grader error distinction.

Out of scope: agent messages, scenario delivery, repair trajectories, model scoring, protected execution, formal results and main acceptance.
