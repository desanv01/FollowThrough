"""Independent functional grader for trusted local preview-cache fixtures.

The grader executes only the trusted authored candidate modules in this
overlay. It is not a sandbox and makes no isolation, resource-limit or
security claim. Candidate reports are never accepted as evidence.

Usage:
    python grader/preview_cache.py --candidate <preview.py> --output <new JSON>
    python grader/preview_cache.py --self-test
"""

import argparse
from collections import UserDict, UserString
from collections.abc import MutableMapping
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import ModuleType
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = "render_preview"

# Authored grader-owned originals: (project, document, revision) -> title, body.
FIXTURES = {
    ("Alpha", "report", 1): ("Launch", "Alpha body"),
    ("Beta", "report", 1): ("Launch", "Beta body"),
    ("Alpha", "report", 2): ("Launch", "Updated alpha body"),
    ("Alpha", "notes", 1): ("Notes", "Alpha notes"),
    ("Beta", "notes", 1): ("Notes", "Beta notes"),
    ("Gamma", "memo", 3): ("Draft", "Gamma body"),
    ("Delta", "memo", 3): ("Draft", "Delta body"),
    ("Gamma", "memo", 4): ("Draft", "Gamma revision two"),
}

# Requests required by the checks, independent of the authored fixture data.
DECLARED_FIXTURES = frozenset({
    ("Alpha", "report", 1),
    ("Beta", "report", 1),
    ("Alpha", "report", 2),
    ("Alpha", "notes", 1),
    ("Beta", "notes", 1),
    ("Gamma", "memo", 3),
    ("Delta", "memo", 3),
    ("Gamma", "memo", 4),
})


def _full(title, body):
    return f"TITLE: {title}\nBODY: {body}"


def _compact(title):
    return f"TITLE: {title}"


class RendererBoom(RuntimeError):
    """One-shot renderer failure used by the grader controls."""


class MissingFixtureError(RuntimeError):
    """A renderer request has no grader-owned fixture."""

    def __init__(self, request=None, missing=None):
        self.request = request
        self.missing = list(missing or ())
        if request is None:
            detail = "grader fixtures are missing: " + ", ".join(repr(key) for key in self.missing)
        else:
            detail = f"no grader fixture for renderer request {request!r}"
        super().__init__(detail)


class RendererSpy:
    """Grader-owned renderer: records calls, serves fixtures, can fail once."""

    def __init__(self, fixtures=None):
        self.fixtures = dict(FIXTURES if fixtures is None else fixtures)
        self.calls = []
        self.failure = None

    def __call__(self, project_id, document_id, revision, mode):
        self.calls.append((project_id, document_id, revision, mode))
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure
        key = (project_id, document_id, revision)
        try:
            title, body = self.fixtures[key]
        except KeyError:
            raise MissingFixtureError(key) from None
        return _full(title, body) if mode == "full" else _compact(title)


class LiteralRenderer:
    """Grader-owned renderer returning fixed text outside the fixture formats."""

    def __init__(self, output):
        self.output = output
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.output


class MappingSpy(MutableMapping):
    """MutableMapping over a private backing dict with access counters.

    Counts reads, writes, deletion, iteration and length. Seed and inspect
    through the backing state helpers so grader inspection is not counted.
    """

    def __init__(self, initial=None):
        self._backing = dict(initial or {})
        self.counts = {"getitem": 0, "setitem": 0, "delitem": 0, "iter": 0, "len": 0}

    def reset_counts(self):
        for name in self.counts:
            self.counts[name] = 0

    @property
    def activity(self):
        return sum(self.counts.values())

    def snapshot(self):
        return dict(self._backing)

    def __getitem__(self, key):
        self.counts["getitem"] += 1
        return self._backing[key]

    def __setitem__(self, key, value):
        self.counts["setitem"] += 1
        self._backing[key] = value

    def __delitem__(self, key):
        self.counts["delitem"] += 1
        del self._backing[key]

    def __iter__(self):
        self.counts["iter"] += 1
        return iter(self._backing)

    def __len__(self):
        self.counts["len"] += 1
        return len(self._backing)


class CheckFailure(Exception):
    """A candidate produced a functional result that violates the specification."""


class CandidateExecutionError(Exception):
    """The candidate could not execute a requested operation."""

    def __init__(self, phase, cause):
        self.phase = phase
        self.cause = cause
        super().__init__(str(cause))


class GraderInfrastructureError(Exception):
    """A grader-owned input or step failed; not a candidate defect."""

    def __init__(self, phase, cause):
        self.phase = phase
        self.cause = cause
        super().__init__(str(cause))


@contextmanager
def _candidate_module(path, source):
    # Execute the exact hashed bytes. Import caching cannot substitute another file.
    module = ModuleType(f"_preview_candidate_{uuid4().hex}")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    try:
        try:
            exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
            entry = getattr(module, ENTRYPOINT, None)
            if not callable(entry):
                raise TypeError(f"candidate must export callable {ENTRYPOINT}")
        except (Exception, SystemExit) as exc:
            raise CandidateExecutionError("load", exc) from exc
        yield module
    finally:
        sys.modules.pop(module.__name__, None)


def _expect(actual, expected, label):
    if actual != expected:
        raise CheckFailure(f"{label}: expected {expected!r}, observed {actual!r}")


def _fixture_error(exc):
    # A request for a declared-but-absent fixture is grader infrastructure;
    # an undeclared request is the candidate forwarding the wrong arguments.
    if exc.request in DECLARED_FIXTURES:
        return GraderInfrastructureError("renderer_fixture", exc)
    return CandidateExecutionError("renderer_request", exc)


def _candidate_call(render, *args, **kwargs):
    # Single candidate boundary: termination and fixture classes are explicit;
    # all other exceptions propagate to the caller for contextual handling.
    try:
        return render(*args, **kwargs)
    except SystemExit as exc:
        raise CandidateExecutionError("invoke", exc) from exc
    except MissingFixtureError as exc:
        raise _fixture_error(exc) from exc


def _invoke(render, *args, **kwargs):
    try:
        return _candidate_call(render, *args, **kwargs)
    except (CandidateExecutionError, GraderInfrastructureError):
        raise
    except (Exception, SystemExit) as exc:
        raise CandidateExecutionError("invoke", exc) from exc


def _check_cold_render(render):
    cache, spy = {}, RendererSpy()
    output = _invoke(render, cache, spy, "Alpha", "report", 1)
    _expect(output, _full("Launch", "Alpha body"), "empty-cache full output")
    _expect(spy.calls, [("Alpha", "report", 1, "full")], "renderer calls")
    if not cache:
        raise CheckFailure("successful output was not stored in the supplied cache")
    return "one omitted-mode render normalized to full; output cached"


def _check_repeat_reuse(render):
    cache, spy = {}, RendererSpy()
    expected = _full("Launch", "Alpha body")
    first = _invoke(render, cache, spy, "Alpha", "report", 1, "full")
    snapshot = dict(cache)
    second = _invoke(render, cache, spy, "Alpha", "report", 1)
    third = _invoke(render, cache, spy, "Alpha", "report", 1, mode="full")
    _expect((first, second, third), (expected, expected, expected), "repeated outputs")
    _expect(spy.calls, [("Alpha", "report", 1, "full")], "renderer calls")
    _expect(dict(cache), snapshot, "cache after equivalent repeats")
    return "three equivalent requests produced one render and stable cache contents"


def _check_omitted_then_explicit(render):
    cache, spy = {}, RendererSpy()
    expected = _full("Launch", "Updated alpha body")
    first = _invoke(render, cache, spy, "Alpha", "report", 2)
    second = _invoke(render, cache, spy, "Alpha", "report", 2, "full")
    _expect((first, second), (expected, expected), "omitted/full outputs")
    _expect(spy.calls, [("Alpha", "report", 2, "full")], "renderer calls")
    return "omitted mode then explicit full shared one identity"


def _check_explicit_then_omitted(render):
    cache, spy = {}, RendererSpy()
    expected = _full("Launch", "Updated alpha body")
    first = _invoke(render, cache, spy, "Alpha", "report", 2, "full")
    second = _invoke(render, cache, spy, "Alpha", "report", 2)
    _expect((first, second), (expected, expected), "full/omitted outputs")
    _expect(spy.calls, [("Alpha", "report", 2, "full")], "renderer calls")
    return "explicit full then omitted mode shared one identity"


def _check_project_separation(render):
    cache, spy = {}, RendererSpy()
    alpha = _invoke(render, cache, spy, "Alpha", "report", 1)
    beta = _invoke(render, cache, spy, "Beta", "report", 1)
    _expect(alpha, _full("Launch", "Alpha body"), "Alpha output")
    _expect(beta, _full("Launch", "Beta body"), "Beta output")
    calls = [("Alpha", "report", 1, "full"), ("Beta", "report", 1, "full")]
    _expect(spy.calls, calls, "renderer calls")
    snapshot = dict(cache)
    _expect(_invoke(render, cache, spy, "Alpha", "report", 1), alpha, "Alpha replay")
    _expect(_invoke(render, cache, spy, "Beta", "report", 1), beta, "Beta replay")
    _expect(spy.calls, calls, "renderer calls after replay")
    _expect(dict(cache), snapshot, "cache after replay")
    return "same document and revision in two projects produced distinct cached outputs"


def _check_document_separation(render):
    cache, spy = {}, RendererSpy()
    report = _invoke(render, cache, spy, "Alpha", "report", 1)
    notes = _invoke(render, cache, spy, "Alpha", "notes", 1)
    _expect(report, _full("Launch", "Alpha body"), "report output")
    _expect(notes, _full("Notes", "Alpha notes"), "notes output")
    calls = [("Alpha", "report", 1, "full"), ("Alpha", "notes", 1, "full")]
    _expect(spy.calls, calls, "renderer calls")
    snapshot = dict(cache)
    replay = _invoke(render, cache, spy, "Alpha", "report", 1)
    _expect(replay, report, "interleaved report reuse")
    _expect(spy.calls, calls, "renderer calls after interleaved replay")
    _expect(dict(cache), snapshot, "cache after interleaved replay")
    return "two documents stayed distinct and the first was reused after the second"


def _check_revision_separation(render):
    cache, spy = {}, RendererSpy()
    first = _invoke(render, cache, spy, "Alpha", "report", 1)
    second = _invoke(render, cache, spy, "Alpha", "report", 2)
    _expect(first, _full("Launch", "Alpha body"), "revision 1 output")
    _expect(second, _full("Launch", "Updated alpha body"), "revision 2 output")
    _expect(
        spy.calls,
        [("Alpha", "report", 1, "full"), ("Alpha", "report", 2, "full")],
        "renderer calls",
    )
    return "two revisions of one document produced distinct outputs"


def _check_mode_separation(render):
    cache, spy = {}, RendererSpy()
    full = _invoke(render, cache, spy, "Alpha", "report", 1, "full")
    compact = _invoke(render, cache, spy, "Alpha", "report", 1, "compact")
    _expect(full, _full("Launch", "Alpha body"), "full output")
    _expect(compact, _compact("Launch"), "compact output")
    calls = [("Alpha", "report", 1, "full"), ("Alpha", "report", 1, "compact")]
    _expect(spy.calls, calls, "renderer calls")
    snapshot = dict(cache)
    _expect(_invoke(render, cache, spy, "Alpha", "report", 1, "compact"), compact, "compact replay")
    _expect(_invoke(render, cache, spy, "Alpha", "report", 1, "full"), full, "full replay")
    _expect(spy.calls, calls, "renderer calls after replay")
    _expect(dict(cache), snapshot, "cache after replay")
    return "full and compact modes produced distinct cached outputs"


def _check_second_fixture_set(render):
    requests = (
        (("Gamma", "memo", 3, "full"), _full("Draft", "Gamma body")),
        (("Gamma", "memo", 3, "compact"), _compact("Draft")),
        (("Delta", "memo", 3, "full"), _full("Draft", "Delta body")),
        (("Gamma", "memo", 4, "full"), _full("Draft", "Gamma revision two")),
    )
    cache, spy = {}, RendererSpy()
    for args, expected in requests:
        _expect(_invoke(render, cache, spy, *args), expected, f"second-set output {args}")
    calls = [args for args, _ in requests]
    _expect(spy.calls, calls, "second-set renderer calls")
    snapshot = dict(cache)
    for args, expected in requests:
        _expect(_invoke(render, cache, spy, *args), expected, f"second-set replay {args}")
    _expect(spy.calls, calls, "second-set renderer calls after replay")
    _expect(dict(cache), snapshot, "second-set cache after replay")
    return "second fixture set kept project, revision and mode identities distinct"


def _check_authoritative_content(render):
    literal = "TITLE: \u00c5NGSTR\u00d6M\nBODY:  spaced body \nunchanged\n"
    renderer = LiteralRenderer(literal)
    cache = UserDict()
    project_id = " \t"
    document_id = "doc\t "
    call = (project_id, document_id, 9, "compact")
    output = _invoke(render, cache, renderer, project_id, document_id, 9, "compact")
    _expect(output, literal, "verbatim renderer output")
    _expect(renderer.calls, [call], "renderer calls with an opaque whitespace ID")
    _expect(
        _invoke(render, cache, renderer, project_id, document_id, 9, "compact"),
        literal,
        "verbatim cached output",
    )
    _expect(renderer.calls, [call], "renderer calls after replay")
    if not cache:
        raise CheckFailure("successful literal output was not cached in the supplied mapping")
    return "non-dict mapping and opaque whitespace IDs cached verbatim renderer output"


def _check_invalid_arguments(render):
    cache = MappingSpy({"sentinel": "kept"})
    spy = RendererSpy()
    cases = (
        ("empty project ID", ("", "report", 1, "full"), {}),
        ("None project ID", (None, "report", 1, "full"), {}),
        ("non-string project ID", (7, "report", 1, "full"), {}),
        ("list project ID", (["Alpha"], "report", 1, "full"), {}),
        ("empty document ID", ("Alpha", "", 1, "full"), {}),
        ("None document ID", ("Alpha", None, 1, "full"), {}),
        ("non-string document ID", ("Alpha", 3.5, 1, "full"), {}),
        ("revision zero", ("Alpha", "report", 0, "full"), {}),
        ("negative revision", ("Alpha", "report", -2, "full"), {}),
        ("boolean revision", ("Alpha", "report", True, "full"), {}),
        ("float revision", ("Alpha", "report", 1.0, "full"), {}),
        ("string revision", ("Alpha", "report", "1", "full"), {}),
        ("None revision", ("Alpha", "report", None, "full"), {}),
        ("explicit None mode", ("Alpha", "report", 1), {"mode": None}),
        ("empty mode", ("Alpha", "report", 1, ""), {}),
        ("case-variant mode", ("Alpha", "report", 1, "Full"), {}),
        ("padded mode", ("Alpha", "report", 1, " full"), {}),
        ("numeric mode", ("Alpha", "report", 1, 1), {}),
        ("list mode", ("Alpha", "report", 1, ["full"]), {}),
        ("UserString mode", ("Alpha", "report", 1, UserString("full")), {}),
    )
    for label, args, kwargs in cases:
        try:
            output = _candidate_call(render, cache, spy, *args, **kwargs)
        except ValueError:
            pass
        except (CandidateExecutionError, GraderInfrastructureError):
            raise
        except (Exception, SystemExit) as exc:
            raise CheckFailure(
                f"{label}: expected ValueError, observed {type(exc).__name__}: {exc}"
            ) from exc
        else:
            raise CheckFailure(f"{label}: expected ValueError, observed return {output!r}")
        _expect(cache.activity, 0, f"{label}: cache mapping activity {cache.counts}")
        _expect(cache.snapshot(), {"sentinel": "kept"}, f"{label}: cache contents")
        _expect(spy.calls, [], f"{label}: renderer calls")
    return f"{len(cases)} invalid requests rejected before cache access or rendering"


def _check_renderer_failure(render):
    cache, spy = {}, RendererSpy()
    _expect(
        _invoke(render, cache, spy, "Alpha", "report", 1),
        _full("Launch", "Alpha body"),
        "existing cached output",
    )
    snapshot = dict(cache)
    expected_args = ("renderer unavailable",)
    failure = RendererBoom(*expected_args)
    failure.retryable = True
    failure.request_token = "p1-cache"
    spy.failure = failure
    try:
        _candidate_call(render, cache, spy, "Beta", "notes", 1)
    except RendererBoom as exc:
        _expect(exc is failure, True, "propagated renderer exception identity")
        _expect(exc.args, expected_args, "preserved renderer exception arguments")
        _expect(getattr(exc, "retryable", None), True, "preserved retryable metadata")
        _expect(getattr(exc, "request_token", None), "p1-cache", "preserved custom metadata")
    except (CandidateExecutionError, GraderInfrastructureError):
        raise
    except (Exception, SystemExit) as exc:
        raise CheckFailure(
            f"renderer exception was transformed into {type(exc).__name__}: {exc}"
        ) from exc
    else:
        raise CheckFailure("renderer exception was swallowed")
    _expect(dict(cache), snapshot, "cache after renderer failure")
    _expect(
        spy.calls,
        [("Alpha", "report", 1, "full"), ("Beta", "notes", 1, "full")],
        "renderer calls",
    )
    return "renderer exception object preserved unchanged; existing cache preserved"


def _check_retry_after_failure(render):
    cache, spy = {}, RendererSpy()
    alpha = _invoke(render, cache, spy, "Alpha", "report", 1)
    _expect(alpha, _full("Launch", "Alpha body"), "pre-failure output")
    snapshot = dict(cache)
    expected_args = ("one-shot renderer failure",)
    failure = RendererBoom(*expected_args)
    failure.retryable = True
    spy.failure = failure
    try:
        _candidate_call(render, cache, spy, "Beta", "notes", 1)
    except RendererBoom as exc:
        _expect(exc is failure, True, "propagated retry exception identity")
        _expect(exc.args, expected_args, "preserved retry exception arguments")
        _expect(getattr(exc, "retryable", None), True, "preserved retry metadata")
    except (CandidateExecutionError, GraderInfrastructureError):
        raise
    except (Exception, SystemExit) as exc:
        raise CheckFailure(
            f"renderer exception was transformed into {type(exc).__name__}: {exc}"
        ) from exc
    else:
        raise CheckFailure("renderer exception was swallowed")
    _expect(dict(cache), snapshot, "cache after failed render")
    beta = _invoke(render, cache, spy, "Beta", "notes", 1)
    _expect(beta, _full("Notes", "Beta notes"), "retry output")
    calls = [
        ("Alpha", "report", 1, "full"),
        ("Beta", "notes", 1, "full"),
        ("Beta", "notes", 1, "full"),
    ]
    _expect(spy.calls, calls, "renderer calls after retry")
    _expect(_invoke(render, cache, spy, "Alpha", "report", 1), alpha, "Alpha replay")
    _expect(_invoke(render, cache, spy, "Beta", "notes", 1), beta, "Beta replay")
    _expect(spy.calls, calls, "renderer calls after replay")
    return "failed request retried successfully and both cached entries were reused"


CHECKS = (
    ("cold_render_full", _check_cold_render),
    ("repeat_reuse_no_rerender", _check_repeat_reuse),
    ("omitted_then_explicit_full", _check_omitted_then_explicit),
    ("explicit_then_omitted_full", _check_explicit_then_omitted),
    ("project_separation", _check_project_separation),
    ("document_separation", _check_document_separation),
    ("revision_separation", _check_revision_separation),
    ("mode_separation", _check_mode_separation),
    ("second_fixture_set", _check_second_fixture_set),
    ("authoritative_renderer_content", _check_authoritative_content),
    ("invalid_arguments", _check_invalid_arguments),
    ("renderer_failure_preserves_cache", _check_renderer_failure),
    ("retry_after_failure", _check_retry_after_failure),
)


def _add_error(result, origin, phase, exc, **context):
    result["errors"].append({
        "origin": origin,
        "phase": phase,
        "type": type(exc).__name__,
        "detail": str(exc),
        **context,
    })


def grade(candidate):
    raw = Path(candidate)
    result = {
        "schema_version": "0.1",
        "task_family": "preview-cache",
        "candidate": {"path": str(raw.absolute()), "sha256": None},
        "evidence_kind": "real_local_control",
        "functional_pass": False,
        "checks": [],
        "errors": [],
    }
    try:
        path = raw.resolve(strict=True)
        result["candidate"]["path"] = str(path)
        source = path.read_bytes()
    except (OSError, RuntimeError) as exc:
        _add_error(result, "grader", "candidate_read", exc)
        return result
    result["candidate"]["sha256"] = sha256(source).hexdigest()

    missing = [key for key in DECLARED_FIXTURES if key not in FIXTURES]
    if missing:
        # Preflight: a broken harness is grader infrastructure, never candidate fault.
        _add_error(result, "grader", "fixture_preflight", MissingFixtureError(None, missing))
        return result

    try:
        with _candidate_module(path, source) as module:
            render = module.render_preview
            for check_id, check in CHECKS:
                entry = {"id": check_id, "passed": False, "detail": "execution error"}
                result["checks"].append(entry)
                try:
                    detail = check(render)
                except CheckFailure as exc:
                    entry["detail"] = str(exc)
                except CandidateExecutionError as exc:
                    entry["detail"] = (
                        f"candidate execution error: {type(exc.cause).__name__}: {exc.cause}"
                    )
                    _add_error(result, "candidate", exc.phase, exc.cause, check_id=check_id)
                except GraderInfrastructureError as exc:
                    entry["detail"] = (
                        f"grader infrastructure error: {type(exc.cause).__name__}: {exc.cause}"
                    )
                    _add_error(result, "grader", exc.phase, exc.cause, check_id=check_id)
                except Exception as exc:
                    entry["detail"] = f"grader check error: {type(exc).__name__}: {exc}"
                    _add_error(result, "grader", "check", exc, check_id=check_id)
                else:
                    entry.update(passed=True, detail=detail)
    except CandidateExecutionError as exc:
        _add_error(result, "candidate", exc.phase, exc.cause)
    except GraderInfrastructureError as exc:
        _add_error(result, "grader", exc.phase, exc.cause)
    except Exception as exc:
        _add_error(result, "grader", "setup_or_cleanup", exc)

    result["functional_pass"] = (
        bool(result["checks"])
        and not result["errors"]
        and all(entry["passed"] for entry in result["checks"])
    )
    return result


def self_test():
    expectations = (
        ("references/preview-cache/correct/preview.py", set()),
        (
            "tasks/preview-cache/preview.py",
            {"project_separation", "revision_separation", "mode_separation", "second_fixture_set"},
        ),
        (
            "references/preview-cache/v1/preview.py",
            {"mode_separation", "second_fixture_set"},
        ),
    )
    success = True
    for relative_path, expected_failures in expectations:
        result = grade(ROOT / relative_path)
        failed = {entry["id"] for entry in result["checks"] if not entry["passed"]}
        matched = (
            not result["errors"]
            and len(result["checks"]) == len(CHECKS)
            and failed == expected_failures
            and result["functional_pass"] == (not expected_failures)
        )
        print(json.dumps({
            "candidate": result["candidate"],
            "functional_pass": result["functional_pass"],
            "failed_checks": sorted(failed),
            "expected_failures": sorted(expected_failures),
            "check_count": len(result["checks"]),
            "errors": result["errors"],
            "matched_expectation": matched,
        }))
        success = success and matched
    print(json.dumps({"self_test_passed": success, "controls": len(expectations)}))
    return 0 if success else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--candidate", type=Path)
    mode.add_argument("--self-test", action="store_true", dest="self_test")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.self_test:
        if args.output is not None:
            parser.error("--output is only valid with --candidate")
        return self_test()
    if args.output is None:
        parser.error("--candidate requires --output")
    if args.output.exists():
        print(
            f"grader output error: refusing to overwrite existing output {args.output}",
            file=sys.stderr,
        )
        return 1
    result = grade(args.candidate)
    try:
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, indent=2)
            output.write("\n")
    except OSError as exc:
        print(f"grader output error: {exc}", file=sys.stderr)
        return 1
    if result["errors"]:
        print(
            f"grader recorded {len(result['errors'])} execution error(s) in {args.output}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
