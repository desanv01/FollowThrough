#!/usr/bin/env python3
"""Original public smoke checks for the preview-cache v1 checkpoint workspace.

These are small full-preview examples derived from the visible v1 checkpoint.
They are not exhaustive, are not a grading implementation, and a pass is not
proof of complete correctness. Run from any working directory with:

    python -I -B <absolute path to public_checks.py>
"""

import importlib.util
import json
from pathlib import Path
import sys


def load_candidate():
    path = Path(__file__).with_name("preview.py")
    spec = importlib.util.spec_from_file_location("public_preview_cache", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sibling preview.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "render_preview", None)):
        raise RuntimeError("preview.py must export callable render_preview")
    return module


class Renderer:
    def __init__(self):
        self.calls = []

    def __call__(self, project_id, document_id, revision, mode):
        self.calls.append((project_id, document_id, revision, mode))
        return f"TITLE: {project_id}/{document_id}/{revision}\nBODY: {mode}"


class Boom(RuntimeError):
    pass


def _full(project_id, document_id, revision):
    return f"TITLE: {project_id}/{document_id}/{revision}\nBODY: full"


def check_validation(processor):
    cache = {"sentinel": "kept"}
    renderer = Renderer()
    invalid = [
        ("", "document", 1, "full"),
        ("project", "", 1, "full"),
        ("project", "document", 0, "full"),
        ("project", "document", True, "full"),
        ("project", "document", 1, "Full"),
        ("project", "document", 1, None),
    ]
    for args in invalid:
        try:
            processor.render_preview(cache, renderer, *args)
        except ValueError:
            continue
        except BaseException as exc:
            raise AssertionError(f"expected ValueError for {args!r}, observed {type(exc).__name__}")
        raise AssertionError(f"expected ValueError for {args!r}, observed a return value")
    if renderer.calls:
        raise AssertionError("invalid arguments reached the renderer")
    if cache != {"sentinel": "kept"}:
        raise AssertionError("invalid arguments changed the cache")
    return f"{len(invalid)} invalid requests raised ValueError before rendering"


def check_cold_full_and_reuse(processor):
    cache = {}
    renderer = Renderer()
    output = processor.render_preview(cache, renderer, "alpha", "report", 1)
    if output != _full("alpha", "report", 1):
        raise AssertionError(f"unexpected full output: {output!r}")
    if renderer.calls != [("alpha", "report", 1, "full")]:
        raise AssertionError(f"unexpected renderer calls: {renderer.calls!r}")
    again = processor.render_preview(cache, renderer, "alpha", "report", 1, "full")
    if again != output:
        raise AssertionError("replayed output differed")
    if len(renderer.calls) != 1:
        raise AssertionError("equivalent request rendered again")
    return "cold full request rendered once and reused"


def check_project_separation(processor):
    cache = {}
    renderer = Renderer()
    first = processor.render_preview(cache, renderer, "alpha", "report", 1)
    second = processor.render_preview(cache, renderer, "beta", "report", 1)
    if first == second:
        raise AssertionError("two projects shared one cached preview")
    if len(renderer.calls) != 2:
        raise AssertionError(f"expected two renders, observed {renderer.calls!r}")
    return "the same document and revision stayed distinct across projects"


def check_revision_separation(processor):
    cache = {}
    renderer = Renderer()
    first = processor.render_preview(cache, renderer, "alpha", "report", 1)
    second = processor.render_preview(cache, renderer, "alpha", "report", 2)
    if first == second:
        raise AssertionError("two revisions shared one cached preview")
    if len(renderer.calls) != 2:
        raise AssertionError(f"expected two renders, observed {renderer.calls!r}")
    return "two revisions of one document stayed distinct"


def check_interleaved_document_reuse(processor):
    cache = {}
    renderer = Renderer()
    first = processor.render_preview(cache, renderer, "alpha", "report", 1)
    processor.render_preview(cache, renderer, "alpha", "notes", 1)
    replay = processor.render_preview(cache, renderer, "alpha", "report", 1)
    if replay != first:
        raise AssertionError("interleaved replay returned a different preview")
    if len(renderer.calls) != 2:
        raise AssertionError(f"interleaved replay rendered again: {renderer.calls!r}")
    return "first document was reused after a second document was rendered"


def check_renderer_failure_preserved(processor):
    cache = {}
    renderer = Renderer()
    processor.render_preview(cache, renderer, "alpha", "report", 1)
    snapshot = dict(cache)
    failure = Boom("renderer unavailable")
    failure.retryable = True

    def broken(*args):
        raise failure

    try:
        processor.render_preview(cache, broken, "beta", "notes", 1)
    except Boom as observed:
        if observed is not failure:
            raise AssertionError("renderer exception object was replaced")
        if getattr(observed, "retryable", None) is not True:
            raise AssertionError("renderer exception metadata was lost")
    except BaseException as exc:
        raise AssertionError(f"expected the injected exception, observed {type(exc).__name__}")
    else:
        raise AssertionError("renderer failure was swallowed")
    if cache != snapshot:
        raise AssertionError("failed render changed the cache")
    retry = processor.render_preview(cache, renderer, "beta", "notes", 1)
    if retry != _full("beta", "notes", 1):
        raise AssertionError("retry after failure did not render")
    return "renderer failure propagated unchanged and a later retry succeeded"


CHECKS = (
    ("validation", check_validation),
    ("cold_full_and_reuse", check_cold_full_and_reuse),
    ("project_separation", check_project_separation),
    ("revision_separation", check_revision_separation),
    ("interleaved_document_reuse", check_interleaved_document_reuse),
    ("renderer_failure_preserved", check_renderer_failure_preserved),
)


def main():
    try:
        processor = load_candidate()
    except BaseException as exc:
        print(json.dumps({"check": "load", "passed": False,
                          "detail": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        return 1
    passed = 0
    for name, check in CHECKS:
        try:
            detail = check(processor)
        except BaseException as exc:
            print(json.dumps({"check": name, "passed": False,
                              "detail": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        else:
            passed += 1
            print(json.dumps({"check": name, "passed": True, "detail": detail}, sort_keys=True))
    print(json.dumps({"summary": "public smoke", "passed": passed, "total": len(CHECKS)},
                     sort_keys=True))
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
