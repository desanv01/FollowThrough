"""Synthetic regressions for the native receipt verifier normalization."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT.parents[2] / "tools" / "P2-verify-native.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_tool():
    if not TOOL.exists():
        raise unittest.SkipTest("campaign receipt verifier not present in this checkout")
    spec = importlib.util.spec_from_file_location("p2_verify_native", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def witness():
    return {"verified": True, "id": "a" * 64}


class ReceiptVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.temp = ROOT / "tmp" / "p2-verifier-regression"
        if self.temp.exists():
            for child in sorted(self.temp.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            self.temp.rmdir()
        self.retry = self.temp / "retry"
        self.cache = self.temp / "cache"
        self.retry.mkdir(parents=True)
        self.cache.mkdir()

    def tearDown(self):
        if self.temp.exists():
            for child in sorted(self.temp.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                else:
                    child.rmdir()
            self.temp.rmdir()

    def write(self, directory, name, data):
        (directory / name).write_text(json.dumps(data), encoding="utf-8")

    def retry_fixture(self):
        for name, status in (("correct.receipt.json", "passed"),
                             ("incorrect.receipt.json", "failed"),
                             ("forged.receipt.json", "failed"),
                             ("boundary_canary.receipt.json", "failed")):
            self.write(self.retry, name, {"status": status, "cleanup": "settled",
                                          "isolation": witness()})
        self.write(self.retry, "failing_public.receipt.json", {
            "status": "check_failed", "cleanup": {
                "state": "settled", "removal_verified": True},
            "public_check_exit": 1, "process_exit": 1})

    def test_public_object_cleanup_and_canary_are_normalized(self):
        self.retry_fixture()
        failures, summary = self.tool.verify_retry(self.retry)
        self.assertEqual(failures, [])
        self.assertIn("failing_public.receipt.json", summary)

    def test_infrastructure_canary_is_not_success(self):
        self.retry_fixture()
        self.write(self.retry, "boundary_canary.receipt.json", {
            "status": "infrastructure_error", "cleanup": "unknown",
            "isolation": witness()})
        failures, _ = self.tool.verify_retry(self.retry)
        self.assertTrue(any("boundary_canary" in failure for failure in failures))

    def test_public_cleanup_removal_and_exit_must_match(self):
        self.retry_fixture()
        self.write(self.retry, "failing_public.receipt.json", {
            "status": "check_failed", "cleanup": {
                "state": "settled", "removal_verified": False},
            "public_check_exit": 0, "process_exit": 1})
        failures, _ = self.tool.verify_retry(self.retry)
        self.assertTrue(any("removal" in failure for failure in failures))
        self.assertTrue(any("public check did not fail" in failure for failure in failures))

    def test_cache_v1_control_requires_v1_binding_and_failure(self):
        fixtures = {
            "correct_v2.receipt.json": ("passed", "v2"),
            "v1_partial.receipt.json": ("failed", "v1"),
            "original.receipt.json": ("failed", "v2"),
        }
        for name, (status, version) in fixtures.items():
            self.write(self.cache, name, {"status": status, "cleanup": "settled",
                                          "requirements_version": version,
                                          "isolation": witness()})
        failures, _ = self.tool.verify_cache(self.cache)
        self.assertEqual(failures, [])
        self.write(self.cache, "v1_partial.receipt.json", {
            "status": "failed", "cleanup": "settled", "requirements_version": "v2",
            "isolation": witness()})
        failures, _ = self.tool.verify_cache(self.cache)
        self.assertTrue(any("v1 control" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()

