"""Original preview-cache candidate (authored fixture, deliberate defect).

This is the starting candidate supplied with the task, not a live model
repair. It validates the interface but keys the cache by document ID alone,
so requests that share a document collide across projects, revisions and
modes. See tasks/preview-cache/SPEC.md and docs/project/CACHE-CONTRACT.md.
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

    key = document_id
    if key not in cache:
        cache[key] = renderer(project_id, document_id, revision, mode)
    return cache[key]
