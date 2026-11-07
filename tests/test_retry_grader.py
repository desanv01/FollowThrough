import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GRADER = ROOT / "grader/retry_processing.py"
STARTER = ROOT / "tasks/retry-processing/processor.py"
REFERENCE = ROOT / "references/retry-processing/correct/processor.py"


class RetryGraderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.output = self.directory / "grade.json"

    def run_grade(self, candidate):
        return subprocess.run(
            [sys.executable, "-B", str(GRADER), "--candidate", str(candidate), "--output", str(self.output)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def result(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def write_candidate(self, source):
        candidate = self.directory / "processor.py"
        candidate.write_text(source, encoding="utf-8")
        return candidate

    def test_functional_failure_produces_grade_with_exact_candidate_identity(self):
        invocation = self.run_grade(STARTER.relative_to(ROOT))
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertEqual(result["schema_version"], "0.1")
        self.assertEqual(result["task_family"], "retry-processing")
        self.assertEqual(result["evidence_kind"], "real_local_control")
        self.assertEqual(result["candidate"]["path"], str(STARTER))
        self.assertEqual(result["candidate"]["sha256"], hashlib.sha256(STARTER.read_bytes()).hexdigest())
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        checks = {check["id"]: check for check in result["checks"]}
        self.assertFalse(checks["rollback_after_credit"]["passed"])
        self.assertIn("observed {b'moss': 58}", checks["rollback_after_credit"]["detail"])

    def test_lookup_before_units_validation_is_a_functional_failure(self):
        source = REFERENCE.read_text(encoding="utf-8")
        units_guard = "    if not isinstance(units, int) or isinstance(units, bool) or units <= 0:\n"
        self.assertEqual(source.count(units_guard), 1)
        premature_lookup = "    store.lookup_units(tenant, event_id)\n"
        candidate = self.write_candidate(source.replace(units_guard, premature_lookup + units_guard))

        invocation = self.run_grade(candidate)

        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["checks"]), 10)
        failures = [check for check in result["checks"] if not check["passed"]]
        self.assertEqual([check["id"] for check in failures], ["malformed_items"])
        self.assertIn("lookup count: expected 0, observed 1", failures[0]["detail"])

    def test_transactional_mark_first_is_also_valid(self):
        source = REFERENCE.read_text(encoding="utf-8")
        original = (
            "        store.add_units(tenant, units)\n"
            "        store.mark_completed(tenant, event_id, units)\n"
        )
        reversed_writes = (
            "        store.mark_completed(tenant, event_id, units)\n"
            "        store.add_units(tenant, units)\n"
        )
        self.assertIn(original, source)
        candidate = self.write_candidate(source.replace(original, reversed_writes))
        invocation = self.run_grade(candidate)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        self.assertTrue(self.result()["functional_pass"])

    def test_syntax_error_is_candidate_execution_failure(self):
        candidate = self.write_candidate("def process_event(\n")
        invocation = self.run_grade(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["checks"], [])
        error, = result["errors"]
        self.assertEqual(
            (error["origin"], error["phase"], error["type"]),
            ("candidate", "load", "SyntaxError"),
        )

    def test_runtime_error_is_candidate_execution_failure(self):
        candidate = self.write_candidate(
            "import sqlite3\n"
            "def process_event(store, item):\n"
            "    raise sqlite3.OperationalError('candidate-origin error')\n"
            "def process_batch(store, items):\n"
            "    return []\n"
        )
        invocation = self.run_grade(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        errors = self.result()["errors"]
        self.assertTrue(errors)
        self.assertTrue(all(error["origin"] == "candidate" for error in errors))
        self.assertTrue(all(error["phase"] == "invoke" for error in errors))
        self.assertEqual(errors[0]["check_id"], "new_delivery")

    def test_missing_candidate_has_separate_grader_error(self):
        invocation = self.run_grade(self.directory / "missing.py")
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertIsNone(result["candidate"]["sha256"])
        error, = result["errors"]
        self.assertEqual((error["origin"], error["phase"]), ("grader", "candidate_read"))

    def test_output_must_be_new(self):
        self.output.write_bytes(b"existing\n")
        invocation = self.run_grade(REFERENCE)
        self.assertNotEqual(invocation.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b"existing\n")
