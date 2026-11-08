"""Trusted host for protected Cache v1/v2 and checkpoint grading.

Host owns cache contents, renderer fixtures/authority, call sequence, snapshots and
grade. Candidate sees a remote MutableMapping and renderer; it cannot supply cache
state, renderer output or a passing result.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import time
import uuid

from episode.protected_grade import ProtectedGradeError
from episode.protected_session import DockerSession, Session

IMAGE = "python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3"
SCHEMA = "protected-cache-grade-0.1"
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


def _full(title, body):
    return f"TITLE: {title}\nBODY: {body}"


def _compact(title):
    return f"TITLE: {title}"


def _value(key, mode):
    title, body = FIXTURES[key]
    return _full(title, body) if mode == "full" else _compact(title)


class CacheHost:
    def __init__(self):
        self.cache = {}
        self.renderer_calls = 0
        self.total_renderer_calls = 0
        self.cache_ops = 0
        self.failures = set()
        self.literal = set()

    def reset_op_counts(self):
        self.cache_ops = 0
        self.renderer_calls = 0

    def snapshot(self):
        return sorted([[list(k), v] for k, v in self.cache.items()],
                      key=lambda item: item[0])

    def _normalize(self, key):
        if type(key) is list:
            return tuple(key)
        if type(key) in (str, int, float, bool) or key is None:
            return ("scalar", key)
        raise ProtectedGradeError("invalid_cache_key")

    def handle_cache(self, op, args):
        self.cache_ops += 1
        if op == "contains":
            key = self._normalize(args[0])
            return True, key in self.cache
        if op == "getitem":
            key = self._normalize(args[0])
            if key not in self.cache:
                return False, "key_error"
            return True, self.cache[key]
        if op == "setitem":
            key = self._normalize(args[0])
            value = args[1]
            if type(value) is not str:
                raise ProtectedGradeError("invalid_cache_value")
            self.cache[key] = value
            return True, None
        if op == "delitem":
            key = self._normalize(args[0])
            self.cache.pop(key, None)
            return True, None
        if op == "iter":
            return True, [list(key) for key in self.cache]
        if op == "len":
            return True, len(self.cache)
        raise ProtectedGradeError("invalid_cache_op")

    def handle_renderer(self, args):
        if type(args) is not list or len(args) != 4:
            raise ProtectedGradeError("invalid_renderer_args")
        project, document, revision, mode = args
        if not all(type(x) is str for x in (project, document, mode)) or type(revision) is not int:
            raise ProtectedGradeError("invalid_renderer_args")
        key = (project, document, revision)
        if key not in FIXTURES:
            raise ProtectedGradeError("unknown_renderer_fixture")
        self.renderer_calls += 1
        self.total_renderer_calls += 1
        if key in self.literal:
            return True, "LITERAL RENDERER OUTPUT"
        if key in self.failures:
            self.failures.discard(key)
            return False, {"class": "RendererBoom", "message": "boom",
                           "metadata": {"key": key}}
        return True, _value(key, mode)

    def _run_call(self, session, index, args, total_deadline=None):
        session.send_frame({"type": "call", "index": index, "args": args})
        deadline = time.monotonic() + 30
        frames = 0
        while True:
            limit = deadline if total_deadline is None else min(deadline, total_deadline)
            remaining = limit - time.monotonic()
            if remaining <= 0:
                raise ProtectedGradeError("cache_session_timeout")
            frame = session.receive(min(30.0, remaining))
            frames += 1
            if frames > 128:
                raise ProtectedGradeError("cache_frame_limit")
            if frame.get("index") != index:
                continue
            if frame.get("type") == "cache":
                op = frame.get("op")
                args = frame.get("args")
                if type(op) is not str or type(args) is not list:
                    raise ProtectedGradeError("invalid_cache_request")
                try:
                    ok, value = self.handle_cache(op, args)
                except ProtectedGradeError:
                    raise
                session.send_frame({"type": "cache_result", "ok": ok,
                                    "result": value, "error": None if ok else value})
                continue
            if frame.get("type") == "renderer":
                args = frame.get("args")
                try:
                    ok, value = self.handle_renderer(args)
                except ProtectedGradeError:
                    raise
                session.send_frame({"type": "renderer_result", "ok": ok,
                                    "result": value if ok else None,
                                    "error": None if ok else value})
                continue
            if frame.get("type") == "result":
                if getattr(session, "_reader_error", None):
                    raise ProtectedGradeError(session._reader_error)
                return frame
            # Unknown/forged frame is ignored, never authority.
        return None


def grade_cache(session, candidate_sha256, *, evidence_kind,
                requirements_version="v2", checkpoint_id=None, cleanup="settled"):
    if evidence_kind not in ("protected_container_grading", "synthetic_development"):
        raise ProtectedGradeError("invalid_evidence_kind")
    host = CacheHost()
    checks = []
    errors = []
    total_deadline = time.monotonic() + float(getattr(session, "timeout", 30))
    try:
        session.start()
        if evidence_kind == "protected_container_grading":
            witness = session.isolation_witness() if session.qualified else None
            if not witness or witness.get("verified") is not True:
                raise ProtectedGradeError("requires_qualified_isolation_witness")
        v1 = requirements_version == "v1"
        mode_check = ("mode_known_v1" if v1 else "mode_sep")
        mode_expected = (_value(("Alpha", "report", 1), "full")
                         if v1 else _value(("Alpha", "report", 1), "compact"))
        cases = [
            ("cold_full", ["Alpha", "report", 1],
             _value(("Alpha", "report", 1), "full"), None),
            ("repeat", ["Alpha", "report", 1, "full"],
             _value(("Alpha", "report", 1), "full"), None),
            ("explicit_full", ["Alpha", "report", 1, "full"],
             _value(("Alpha", "report", 1), "full"), None),
            ("project_sep", ["Beta", "report", 1, "full"],
             _value(("Beta", "report", 1), "full"), None),
            ("revision_sep", ["Alpha", "report", 2, "full"],
             _value(("Alpha", "report", 2), "full"), None),
            ("document_sep", ["Alpha", "notes", 1, "compact"],
             _value(("Alpha", "notes", 1), "compact"), None),
            (mode_check, ["Alpha", "report", 1, "compact"], mode_expected, None),
            ("second_set", ["Gamma", "memo", 3, "full"],
             _value(("Gamma", "memo", 3), "full"), None),
            ("second_set_revision", ["Gamma", "memo", 4, "full"],
             _value(("Gamma", "memo", 4), "full"), None),
            ("authoritative", ["Delta", "memo", 3, "full"],
             "LITERAL RENDERER OUTPUT", None),
        ]
        host.literal.add(("Delta", "memo", 3))
        for index, (check_id, args, expected, _) in enumerate(cases):
            if time.monotonic() > total_deadline:
                raise ProtectedGradeError("cache_total_timeout")
            host.reset_op_counts()
            frame = host._run_call(session, index, args, total_deadline)
            passed = (frame.get("status") == "ok" and frame.get("value") == expected)
            checks.append({"id": check_id, "passed": passed,
                           "detail": "expected renderer content" if passed
                           else "candidate result mismatch"})
        invalid_cases = [
            (["", "doc", 1, "full"], ValueError),
            (["p", "", 1, "full"], ValueError),
            (["p", "doc", 0, "full"], ValueError),
            (["p", "doc", True, "full"], ValueError),
            (["p", "doc", 1, "weird"], ValueError),
            (["p", "doc", 1, None], ValueError),
        ]
        base = len(cases)
        invalid_passed = True
        for offset, (args, expected_error) in enumerate(invalid_cases):
            if time.monotonic() > total_deadline:
                raise ProtectedGradeError("cache_total_timeout")
            host.reset_op_counts()
            frame = host._run_call(session, base + offset, args, total_deadline)
            if frame.get("status") != "exception" or frame.get("class") != expected_error.__name__:
                invalid_passed = False
            if host.renderer_calls or host.cache_ops:
                invalid_passed = False
        checks.append({"id": "invalid_arguments", "passed": invalid_passed,
                       "detail": "ValueError before any cache/renderer access"})
        fail_index = base + len(invalid_cases)
        host.failures.add(("Beta", "notes", 1))
        frame = host._run_call(session, fail_index, ["Beta", "notes", 1, "compact"], total_deadline)
        failure_key = (("Beta", "notes", 1) if v1 else
                       ("Beta", "notes", 1, "compact"))
        failure_passed = (frame.get("status") == "exception"
                          and frame.get("class") == "RendererBoom"
                          and failure_key not in host.cache)
        retry = host._run_call(session, fail_index + 1, ["Beta", "notes", 1, "compact"], total_deadline)
        retry_passed = retry.get("status") == "ok" and retry.get("value") == (
            "TITLE: Notes")
        checks.append({"id": "renderer_failure", "passed": failure_passed,
                       "detail": "renderer exception preserved; no cache write"})
        checks.append({"id": "retry_after_failure", "passed": retry_passed,
                       "detail": "successful retry caches output"})
        if v1:
            expected_keys = {
                ("Alpha", "report", 1),
                ("Beta", "report", 1),
                ("Alpha", "report", 2),
                ("Alpha", "notes", 1),
                ("Gamma", "memo", 3),
                ("Gamma", "memo", 4),
                ("Delta", "memo", 3),
                ("Beta", "notes", 1),
            }
            expected_calls = 9
        else:
            expected_keys = {
                ("Alpha", "report", 1, "full"),
                ("Beta", "report", 1, "full"),
                ("Alpha", "report", 2, "full"),
                ("Alpha", "notes", 1, "compact"),
                ("Alpha", "report", 1, "compact"),
                ("Gamma", "memo", 3, "full"),
                ("Gamma", "memo", 4, "full"),
                ("Delta", "memo", 3, "full"),
                ("Beta", "notes", 1, "compact"),
            }
            expected_calls = 10
        checks.append({"id": "authoritative_cache_state",
                       "passed": set(host.cache) == expected_keys,
                       "detail": "host cache state is the only authority"})
        checks.append({"id": "renderer_call_count",
                       "passed": host.total_renderer_calls == expected_calls,
                       "detail": f"expected {expected_calls} host renderer calls, observed {host.total_renderer_calls}"})
        if getattr(session, "_reader_error", None):
            raise ProtectedGradeError(session._reader_error)
    except ProtectedGradeError as exc:
        errors.append({"origin": "grader", "phase": "cache_session",
                       "type": "InfrastructureError", "detail": exc.code})
    finally:
        session.close()
        cleanup = getattr(session, "cleanup", cleanup)
    if getattr(session, "_reader_error", None):
        errors.append({"origin": "executor", "phase": "output",
                       "type": "OutputLimit", "detail": session._reader_error})
    if cleanup == "unknown" and not errors:
        errors.append({"origin": "executor", "phase": "cleanup",
                       "type": "CleanupUnknown",
                       "detail": "cleanup unknown forbids semantic outcome"})
    status = "infrastructure_error" if (cleanup == "unknown" or errors) else (
        "passed" if all(c["passed"] for c in checks) else "failed")
    receipt = {
        "schema_version": SCHEMA,
        "task_family": "preview-cache",
        "candidate_sha256": candidate_sha256,
        "run_id": getattr(session, "run_id", None),
        "requirements_version": requirements_version,
        "grade_kind": "checkpoint" if checkpoint_id else "final",
        "checkpoint_id": checkpoint_id,
        "evidence_kind": evidence_kind,
        "status": status,
        "functional_pass": True if status == "passed" else (False if status == "failed" else None),
        "origin": "grader" if status in ("passed", "failed") else "executor",
        "checks": checks,
        "errors": errors,
        "cleanup": cleanup,
        "settlement": "unknown" if cleanup == "unknown" else "known",
        "limitations": ["host_authoritative_cache_and_renderer"],
    }
    if cleanup == "unknown":
        receipt["status"] = "infrastructure_error"
        receipt["functional_pass"] = None
        receipt["limitations"].append("cleanup_unknown_forbids_semantic_outcome")
    return receipt


def cache_session(docker, candidate, shim_dir, run_id):
    return DockerSession(docker, candidate, shim_dir, run_id,
                         shim_name="cache_shim.py")


__all__ = ["CacheHost", "FIXTURES", "cache_session", "grade_cache"]
