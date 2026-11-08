# Preview cache — v2 requirements

This workspace uses the final v2 requirements of a cached preview-rendering task. Processing is sequential and uses only the Python standard library. A supplied cache and a supplied renderer are composed so that equal semantic requests reuse rendered output and different requests do not collide.

## Candidate interface

`preview.py` is a self-contained standard-library module exporting:

```python
render_preview(cache, renderer, project_id, document_id, revision, mode="full") -> str
```

- `cache` is a supplied mutable mapping, not necessarily a `dict`; a minimal `MutableMapping` wrapper or `collections.UserDict` must work.
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

An omitted `mode` means `"full"`. Explicit `None` is invalid. The mode test is a string-type test followed by allowed-value membership: a non-string object that compares equal to a valid mode, such as `collections.UserString("full")`, is invalid, while an ordinary `str` subclass is accepted. Any invalid argument raises `ValueError`, causes zero renderer calls and leaves the cache unchanged. Nonempty whitespace strings such as `" \t"` are valid opaque IDs and are never trimmed or normalized.

## Semantic identity

A successful semantic request is `(project_id, document_id, revision, normalized_mode)`, where an omitted mode normalizes to `"full"`.

- Each successful request caches the returned string.
- A repeated equivalent request returns the cached string with no renderer call.
- Requests that differ in project, document, revision or mode are distinct and must not collide.
- Cached entries persist: after rendering document A then document B in one project, a replay of A still reuses A's cached output without another renderer call and without any mapping change.
- Omitted mode and explicit `"full"` share one identity, in either arrival order.
- Full and compact are distinct identities. A compact request that follows a full request for the same project, document and revision must render compact output rather than reuse the full result.
- When the renderer raises, the exact same exception object propagates with its custom metadata intact. No partial output is cached and existing cache contents remain unchanged. A later retry may succeed.
- Cache key layout is not part of the public interface; behavior is observed through outputs, renderer call counters and cache preservation.

## Scope

This is the final requirement version for the cache task family. No later requirement update supersedes it.
