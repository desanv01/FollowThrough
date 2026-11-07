"""Intermediate v1 preview-cache reference (authored fixture, partial repair).

v1 fixes the original document-only collision for projects and revisions but
still omits mode from the cache identity. It is intentionally incomplete; the
final v2 reference lives under references/preview-cache/correct/preview.py.
See tasks/preview-cache/SPEC.md and docs/project/CACHE-CONTRACT.md.
"""

VALID_MODES = ("full", "compact")


def render_preview(cache, renderer, project_id, document_id, revision, mode="full"):
    """Return preview text for one semantic request, reusing cached output."""
    if not isinstance(project_id, str) or project_id == "":
        raise ValueError("project_id must be a nonempty string")
    if not isinstance(document_id, str) or document_id == "":
        raise ValueError("document_id must be a nonempty string")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision must be an integer >= 1")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        raise ValueError("mode must be 'full' or 'compact'")

    key = (project_id, document_id, revision)
    if key not in cache:
        cache[key] = renderer(project_id, document_id, revision, mode)
    return cache[key]
