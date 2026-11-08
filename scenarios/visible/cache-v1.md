# Preview cache — v1 checkpoint

This workspace is at the v1 checkpoint of a cached preview-rendering task. Processing is sequential and uses only the Python standard library. A supplied cache and a supplied renderer are composed so that equal full-preview requests reuse rendered output and different requests do not collide.

The supplied interface already accepts a `mode` argument, but this checkpoint's requirements and evidence cover full previews only. Compact-mode obligations are not part of the v1 checkpoint.

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
| `revision` | Python integer at least 1; booleans are not integers here and are invalid |
| `mode` | an actual string equal to exactly `"full"` or `"compact"` |

An omitted `mode` means `"full"`. Explicit `None` is invalid. A non-string object that compares equal to a valid mode is invalid. Any invalid argument raises `ValueError`, causes zero renderer calls and leaves the cache unchanged. The cache is a supplied mutable mapping, not necessarily a `dict`. Nonempty whitespace strings are valid opaque IDs and are not trimmed or normalized.

## Full-preview caching

A successful full request is identified by its project, document and revision. Within this checkpoint, every successful request uses the full mode.

- Each successful request caches the returned string.
- A repeated equivalent request returns the cached string with no renderer call.
- Requests that differ in project, document or revision are distinct and must not collide.
- Cached entries persist: after rendering document A then document B in one project, a replay of A still reuses A's cached output without another renderer call and without any mapping change.
- When the renderer raises, the exact same exception object propagates with its custom metadata intact. No partial output is cached and existing cache contents remain unchanged. A later retry may succeed.
- Cache key layout is not part of the public interface; behavior is observed through outputs, renderer call counters and cache preservation.

## Current scope

This checkpoint covers full-preview behavior only; apply the requirements above as written.
