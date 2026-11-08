"""P2 focused tests for protected Cache host authority and v1/v2 scope."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from episode import protected_cache as pc  # noqa: E402
from episode.protected_grade import ProtectedGradeError  # noqa: E402


class CacheSim:
    """Test-only protocol speaker implementing correct v2 or partial v1 logic."""

    qualified = False

    def __init__(self, behavior="v2", forged=False):
        self.behavior = behavior
        self.forged = forged
        self.frames = []
        self.index = None
        self.args = None
        self.key = None
        self.value = None
        self.phase = None
        self.cleanup = "settled"
        self.witness = None

    def start(self):
        pass

    def send_frame(self, frame):
        kind = frame.get("type")
        if kind == "call":
            self.index = frame["index"]
            self.args = frame["args"]
            if self.forged:
                self.frames = [{"type": "result", "index": self.index,
                                "status": "ok", "value": "forged"}]
                return
            project, document, revision, mode = self._validated()
            if mode is None:
                self.frames = [{"type": "result", "index": self.index,
                                "status": "exception", "class": "ValueError",
                                "message": "invalid"}]
                return
            if self.behavior == "v1":
                self.key = (project, document, revision)
            else:
                self.key = (project, document, revision, mode)
            self.mode = mode
            self.phase = "contains"
            self.frames = [{"type": "cache", "index": self.index,
                            "op": "contains", "args": [list(self.key)]}]
            return
        if kind == "cache_result":
            if not frame.get("ok"):
                self.frames = [{"type": "result", "index": self.index,
                                "status": "exception", "class": "RuntimeError",
                                "message": "cache"}]
                return
            if self.phase == "contains":
                if frame.get("result"):
                    self.phase = "get"
                    self.frames = [{"type": "cache", "index": self.index,
                                    "op": "getitem", "args": [list(self.key)]}]
                else:
                    self.phase = "render"
                    self.frames = [{"type": "renderer", "index": self.index,
                                    "args": list(self.key[:3]) + [self.mode]}]
                return
            if self.phase == "get":
                self.frames = [{"type": "result", "index": self.index,
                                "status": "ok", "value": frame.get("result")}]
                return
            if self.phase == "set":
                self.frames = [{"type": "result", "index": self.index,
                                "status": "ok", "value": self.value}]
                return
        if kind == "renderer_result":
            if not frame.get("ok"):
                error = frame.get("error") or {}
                self.frames = [{"type": "result", "index": self.index,
                                "status": "exception",
                                "class": error.get("class", "RendererFailure"),
                                "message": error.get("message", "boom")}]
                return
            self.value = frame.get("result")
            self.phase = "set"
            self.frames = [{"type": "cache", "index": self.index,
                            "op": "setitem", "args": [list(self.key), self.value]}]
            return

    def _validated(self):
        args = self.args
        if len(args) not in (3, 4):
            return None, None, None, None
        project, document, revision = args[:3]
        mode = args[3] if len(args) == 4 else "full"
        if not isinstance(project, str) or project == "":
            return None, None, None, None
        if not isinstance(document, str) or document == "":
            return None, None, None, None
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            return None, None, None, None
        if not isinstance(mode, str) or mode not in ("full", "compact"):
            return None, None, None, None
        return project, document, revision, mode

    def receive(self, timeout):
        if not self.frames:
            raise pc.ProtectedGradeError("no_frame")
        return self.frames.pop(0)

    def send_store_result(self, ok, value):
        pass

    def close(self):
        pass


class CacheShimBoundaryTests(unittest.TestCase):
    def test_shim_reads_error_class_from_error_object(self):
        import protected.cache_shim as shim
        recorded = []
        old_write, old_read = shim._write, shim._read
        try:
            shim._write = lambda frame: recorded.append(frame)
            shim._read = lambda: {"type": "renderer_result", "ok": False,
                                  "error": {"class": "RendererBoom",
                                            "message": "boom"}}
            with self.assertRaises(Exception) as ctx:
                shim.RemoteRenderer(0)("p", "d", 1, "full")
            self.assertEqual(type(ctx.exception).__name__, "RendererBoom")
            self.assertEqual(recorded[0]["type"], "renderer")
        finally:
            shim._write, shim._read = old_write, old_read

    def test_host_accepts_scalar_candidate_cache_keys(self):
        host = pc.CacheHost()
        ok, value = host.handle_cache("contains", ["doc"])
        self.assertTrue(ok)
        self.assertFalse(value)
        ok, value = host.handle_cache("setitem", ["doc", "text"])
        self.assertTrue(ok)
        self.assertEqual(host.snapshot(), [[["scalar", "doc"], "text"]])


class CacheProtectedTests(unittest.TestCase):
    def test_qualified_cache_witness_is_checked_after_start(self):
        class Qualified(CacheSim):
            qualified = True

            def start(self):
                self.witness = {"verified": True, "id": "a" * 64}

            def isolation_witness(self):
                return self.witness

        receipt = pc.grade_cache(
            Qualified("v2"), "a" * 64,
            evidence_kind="protected_container_grading",
            requirements_version="v2")
        self.assertEqual(receipt["status"], "passed")

    def test_correct_v2_control_passes(self):
        receipt = pc.grade_cache(CacheSim("v2"), "a" * 64,
                                 evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(receipt["functional_pass"])
        ids = {c["id"] for c in receipt["checks"]}
        self.assertIn("authoritative_cache_state", ids)
        self.assertIn("mode_sep", ids)

    def test_v1_partial_control_passes_declared_v1_scope(self):
        receipt = pc.grade_cache(CacheSim("v1"), "b" * 64,
                                 evidence_kind="synthetic_development",
                                 requirements_version="v1")
        self.assertEqual(receipt["status"], "passed")
        self.assertTrue(receipt["functional_pass"])
        self.assertIn("mode_known_v1", {c["id"] for c in receipt["checks"]})

    def test_v1_partial_control_fails_v2_scope(self):
        receipt = pc.grade_cache(CacheSim("v1"), "b" * 64,
                                 evidence_kind="synthetic_development",
                                 requirements_version="v2")
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("mode_sep", {c["id"] for c in receipt["checks"] if not c["passed"]})

    def test_forged_value_cannot_pass_without_host_cache(self):
        receipt = pc.grade_cache(CacheSim("v2", forged=True), "c" * 64,
                                 evidence_kind="synthetic_development")
        self.assertNotEqual(receipt["status"], "passed")
        self.assertTrue(any(c["id"] == "authoritative_cache_state" and not c["passed"]
                            for c in receipt["checks"]))

    def test_checkpoint_grade_binds_id_and_is_not_final(self):
        receipt = pc.grade_cache(CacheSim("v2"), "d" * 64,
                                 evidence_kind="synthetic_development",
                                 checkpoint_id="retry-cache-checkpoint-1")
        self.assertEqual(receipt["grade_kind"], "checkpoint")
        self.assertEqual(receipt["checkpoint_id"], "retry-cache-checkpoint-1")

    def test_protected_label_requires_witness(self):
        receipt = pc.grade_cache(CacheSim("v2"), "e" * 64,
                                 evidence_kind="protected_container_grading")
        self.assertEqual(receipt["status"], "infrastructure_error")
        self.assertIsNone(receipt["functional_pass"])
        self.assertTrue(any("requires_qualified_isolation_witness" in
                            item.get("detail", "") for item in receipt["errors"]))

    def test_cache_batch_defaults_to_refusal(self):
        from episode import protected_cache_batch as batch
        run_root = ROOT / "tmp" / "test-cache-batch-refusal"
        if run_root.exists():
            for child in sorted(run_root.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            run_root.rmdir()
        self.assertEqual(batch.main(["--run-root", str(run_root)]), 2)
        self.assertFalse(run_root.exists())


if __name__ == "__main__":
    unittest.main()
