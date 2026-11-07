"""Correct preview-cache reference (authored fixture, not a live model repair).

This is the final v2 scope described in tasks/preview-cache/SPEC.md: cache
successful renderer output under the complete semantic request identity
(project_id, document_id, revision, normalized_mode). Validate every argument
before any cache access or renderer call.

Contract: docs/project/CACHE-CONTRACT.md.
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

    key = (project_id, document_id, revision, mode)
    if key not in cache:
        cache[key] = renderer(project_id, document_id, revision, mode)
    return cache[key]
