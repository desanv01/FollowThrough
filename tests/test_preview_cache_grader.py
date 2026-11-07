"""Focused tests for the preview-cache fixture grader and its controls."""

import hashlib
import json
from collections import UserString
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4


OVERLAY = Path(__file__).resolve().parents[1]
SCRATCH = OVERLAY / "tmp" / "tests"
GRADER = OVERLAY / "grader/preview_cache.py"
TASKS_CANDIDATE = OVERLAY / "tasks/preview-cache/preview.py"
V1_CANDIDATE = OVERLAY / "references/preview-cache/v1/preview.py"
CORRECT_REFERENCE = OVERLAY / "references/preview-cache/correct/preview.py"

CHECK_IDS = {
    "cold_render_full",
    "repeat_reuse_no_rerender",
    "omitted_then_explicit_full",
    "explicit_then_omitted_full",
    "project_separation",
    "document_separation",
    "revision_separation",
    "mode_separation",
    "second_fixture_set",
    "authoritative_renderer_content",
    "invalid_arguments",
    "renderer_failure_preserves_cache",
    "retry_after_failure",
}


class PreviewCacheGraderTests(unittest.TestCase):
    def setUp(self):
        # The sandboxed default TEMP and mode-0700 directories are not writable
        # here, so use an owned overlay scratch directory with default mode.
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.directory = SCRATCH / f"run-{uuid4().hex}"
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.output = self.directory / "grade.json"

    def run_cli(self, candidate, output=None, cwd=None, isolated=False, *, grader=GRADER):
        command = [sys.executable]
        if isolated:
            command.append("-I")
        command.extend([
            "-B",
            str(grader),
            "--candidate",
            str(candidate),
            "--output",
            str(self.output if output is None else output),
        ])
        return subprocess.run(
            command,
            cwd=str(self.directory if cwd is None else cwd),
            capture_output=True,
            text=True,
            timeout=60,
        )

    def result(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def write_candidate(self, source, name="candidate.py"):
        candidate = self.directory / name
        candidate.write_text(source, encoding="utf-8")
        return candidate

    def mutation(self, anchor, replacement, name):
        source = CORRECT_REFERENCE.read_text(encoding="utf-8")
        self.assertEqual(source.count(anchor), 1, anchor)
        return self.write_candidate(source.replace(anchor, replacement), name)

    def test_correct_reference_passes_all_checks(self):
        invocation = self.run_cli(CORRECT_REFERENCE)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertEqual(result["schema_version"], "0.1")
        self.assertEqual(result["task_family"], "preview-cache")
        self.assertEqual(result["evidence_kind"], "real_local_control")
        self.assertEqual(result["candidate"]["path"], str(CORRECT_REFERENCE.resolve()))
        self.assertEqual(
            result["candidate"]["sha256"],
            hashlib.sha256(CORRECT_REFERENCE.read_bytes()).hexdigest(),
        )
        self.assertTrue(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        self.assertEqual({check["id"] for check in result["checks"]}, CHECK_IDS)
        self.assertTrue(all(check["passed"] for check in result["checks"]))
        self.assertTrue(all(check["detail"] for check in result["checks"]))

    def test_original_candidate_fails_document_only_collisions(self):
        invocation = self.run_cli(TASKS_CANDIDATE)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        failed = {check["id"] for check in result["checks"] if not check["passed"]}
        self.assertEqual(
            failed,
            {"project_separation", "revision_separation", "mode_separation", "second_fixture_set"},
        )
        failures = {check["id"]: check["detail"] for check in result["checks"]}
        self.assertIn("Beta body", failures["project_separation"])
        self.assertIn("Updated alpha body", failures["revision_separation"])
        self.assertIn("TITLE: Launch", failures["mode_separation"])

    def test_v1_candidate_fails_missing_mode_identity(self):
        invocation = self.run_cli(V1_CANDIDATE)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        failed = {check["id"] for check in result["checks"] if not check["passed"]}
        self.assertEqual(failed, {"mode_separation", "second_fixture_set"})

    def test_self_test_validates_positive_and_negative_controls(self):
        invocation = subprocess.run(
            [sys.executable, "-B", str(GRADER), "--self-test"],
            cwd=str(self.directory),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        lines = [json.loads(line) for line in invocation.stdout.splitlines() if line.strip()]
        self.assertEqual(lines[-1], {"self_test_passed": True, "controls": 3})
        controls = {record["candidate"]["path"]: record for record in lines[:-1]}
        self.assertEqual(len(controls), 3)
        correct = controls[str(CORRECT_REFERENCE.resolve())]
        self.assertTrue(correct["functional_pass"])
        self.assertEqual(correct["failed_checks"], [])
        original = controls[str(TASKS_CANDIDATE.resolve())]
        self.assertEqual(
            original["failed_checks"],
            ["mode_separation", "project_separation", "revision_separation", "second_fixture_set"],
        )
        intermediate = controls[str(V1_CANDIDATE.resolve())]
        self.assertEqual(intermediate["failed_checks"], ["mode_separation", "second_fixture_set"])
        self.assertTrue(all(record["matched_expectation"] for record in lines[:-1]))
        self.assertTrue(all(record["errors"] == [] for record in lines[:-1]))
        self.assertTrue(all(record["check_count"] == len(CHECK_IDS) for record in lines[:-1]))

    def test_cli_records_actual_identity_from_unrelated_cwd_in_isolated_mode(self):
        candidate = self.directory / "candidate.py"
        candidate.write_bytes(CORRECT_REFERENCE.read_bytes())
        output = Path("grade.json")
        invocation = self.run_cli(candidate.name, output=output, cwd=self.directory, isolated=True)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = json.loads((self.directory / "grade.json").read_text(encoding="utf-8"))
        self.assertEqual(result["candidate"]["path"], str(candidate.resolve()))
        self.assertEqual(
            result["candidate"]["sha256"],
            hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )
        self.assertTrue(result["functional_pass"])

    def test_existing_output_is_refused_and_unmodified(self):
        self.output.write_bytes(b"existing evidence\n")
        invocation = self.run_cli(CORRECT_REFERENCE)
        self.assertNotEqual(invocation.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b"existing evidence\n")
        self.assertIn("refusing to overwrite", invocation.stderr)

    def test_missing_candidate_is_grader_read_error(self):
        missing = self.directory / "missing.py"
        invocation = self.run_cli(missing)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertIsNone(result["candidate"]["sha256"])
        self.assertEqual(result["checks"], [])
        error, = result["errors"]
        self.assertEqual((error["origin"], error["phase"]), ("grader", "candidate_read"))

    def test_syntax_error_is_candidate_load_error(self):
        candidate = self.write_candidate("def render_preview(:\n")
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["checks"], [])
        error, = result["errors"]
        self.assertEqual(
            (error["origin"], error["phase"], error["type"]),
            ("candidate", "load", "SyntaxError"),
        )

    def test_missing_entrypoint_is_candidate_load_error(self):
        candidate = self.write_candidate("VALUE = 1\n")
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        error, = result["errors"]
        self.assertEqual(
            (error["origin"], error["phase"], error["type"]),
            ("candidate", "load", "TypeError"),
        )
        self.assertIn("render_preview", error["detail"])

    def test_runtime_error_is_candidate_execution_error_not_check_failure(self):
        candidate = self.write_candidate(
            "def render_preview(cache, renderer, project_id, document_id, revision, mode='full'):\n"
            "    raise RuntimeError('candidate-origin boom')\n"
        )
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertTrue(result["errors"])
        self.assertTrue(all(error["origin"] == "candidate" for error in result["errors"]))
        self.assertTrue(all(error["phase"] == "invoke" for error in result["errors"]))
        self.assertEqual(result["errors"][0]["check_id"], "cold_render_full")

    def test_swallowed_renderer_failure_is_functional_failure_not_execution_error(self):
        candidate = self.write_candidate(
            "def render_preview(cache, renderer, project_id, document_id, revision, mode='full'):\n"
            "    if not isinstance(project_id, str) or project_id == '':\n"
            "        raise ValueError('project_id')\n"
            "    if not isinstance(document_id, str) or document_id == '':\n"
            "        raise ValueError('document_id')\n"
            "    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:\n"
            "        raise ValueError('revision')\n"
            "    if mode not in ('full', 'compact'):\n"
            "        raise ValueError('mode')\n"
            "    key = (project_id, document_id, revision, mode)\n"
            "    if key not in cache:\n"
            "        try:\n"
            "            cache[key] = renderer(project_id, document_id, revision, mode)\n"
            "        except Exception:\n"
            "            return ''\n"
            "    return cache[key]\n"
        )
        invocation = self.run_cli(candidate)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        failed = {check["id"] for check in result["checks"] if not check["passed"]}
        self.assertEqual(
            failed,
            {
                "invalid_arguments",
                "renderer_failure_preserves_cache",
                "retry_after_failure",
            },
        )

    def test_non_string_mode_rejected_and_plain_str_subclass_valid(self):
        class Mode(str):
            pass

        for path in (TASKS_CANDIDATE, V1_CANDIDATE, CORRECT_REFERENCE):
            render = runpy.run_path(str(path))["render_preview"]
            cache = {"sentinel": "kept"}
            calls = []
            with self.assertRaises(ValueError):
                render(cache, lambda *args: calls.append(args), "p", "d", 1, UserString("full"))
            self.assertEqual(calls, [])
            self.assertEqual(cache, {"sentinel": "kept"})
            output = render(
                {}, lambda *args: calls.append(args) or "opaque", "p", "d", 1, Mode("compact")
            )
            self.assertEqual(output, "opaque")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][3], "compact")

    def test_dict_only_mutation_cannot_pass(self):
        candidate = self.mutation(
            "    key = (project_id, document_id, revision, mode)",
            "    if not isinstance(cache, dict):\n"
            "        raise TypeError('cache must be a dictionary')\n\n"
            "    key = (project_id, document_id, revision, mode)",
            "dict_only.py",
        )
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        error, = result["errors"]
        self.assertEqual((error["origin"], error["phase"], error["type"]),
                         ("candidate", "invoke", "TypeError"))
        self.assertEqual(error["check_id"], "authoritative_renderer_content")

    def test_blank_id_rejected_mutation_cannot_pass(self):
        candidate = self.mutation(
            'or project_id == "":',
            "or not project_id.strip():",
            "blank_id_rejected.py",
        )
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        error, = result["errors"]
        self.assertEqual((error["origin"], error["type"]), ("candidate", "ValueError"))
        self.assertEqual(error["check_id"], "authoritative_renderer_content")

    def test_lookup_first_mutation_cannot_pass(self):
        candidate = self.mutation(
            "    if not isinstance(project_id, str)",
            "    next(iter(cache), None)\n    if not isinstance(project_id, str)",
            "lookup_first.py",
        )
        invocation = self.run_cli(candidate)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        failed = {check["id"] for check in result["checks"] if not check["passed"]}
        self.assertEqual(failed, {"invalid_arguments"})
        detail = next(check["detail"] for check in result["checks"]
                      if check["id"] == "invalid_arguments")
        self.assertIn("cache mapping activity", detail)

    def test_document_eviction_mutation_cannot_pass(self):
        candidate = self.mutation(
            "        cache[key] = renderer(project_id, document_id, revision, mode)",
            "        output = renderer(project_id, document_id, revision, mode)\n"
            "        for previous in list(cache):\n"
            "            if previous[0] == project_id and previous[1] != document_id:\n"
            "                del cache[previous]\n"
            "        cache[key] = output",
            "document_eviction.py",
        )
        invocation = self.run_cli(candidate)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        failed = {check["id"] for check in result["checks"] if not check["passed"]}
        self.assertEqual(failed, {"document_separation"})

    def test_recreated_exception_mutation_cannot_pass(self):
        candidate = self.mutation(
            "        cache[key] = renderer(project_id, document_id, revision, mode)",
            "        try:\n"
            "            cache[key] = renderer(project_id, document_id, revision, mode)\n"
            "        except Exception as error:\n"
            "            raise type(error)(*error.args) from error",
            "recreated_exception.py",
        )
        invocation = self.run_cli(candidate)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        failed = {check["id"] for check in result["checks"] if not check["passed"]}
        self.assertEqual(failed, {"renderer_failure_preserves_cache", "retry_after_failure"})
        detail = next(check["detail"] for check in result["checks"]
                      if check["id"] == "renderer_failure_preserves_cache")
        self.assertIn("identity", detail)

    def test_changed_exception_args_mutation_cannot_pass(self):
        candidate = self.mutation(
            "        cache[key] = renderer(project_id, document_id, revision, mode)\n",
            "        try:\n"
            "            cache[key] = renderer(project_id, document_id, revision, mode)\n"
            "        except RuntimeError as exc:\n"
            '            exc.args = ("changed by candidate",)\n'
            "            raise\n",
            "changed_exception_args.py",
        )
        invocation = self.run_cli(candidate, isolated=True)
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["errors"], [])
        failures = {check["id"]: check["detail"] for check in result["checks"]
                    if not check["passed"]}
        self.assertEqual(set(failures), {"renderer_failure_preserves_cache", "retry_after_failure"})
        for detail in failures.values():
            self.assertIn("exception arguments", detail)
            self.assertIn("changed by candidate", detail)

    def test_candidate_system_exit_on_invalid_is_execution_error(self):
        candidate = self.mutation(
            "    if not isinstance(mode, str) or mode not in VALID_MODES:",
            "    if mode is None:\n        raise SystemExit('invalid mode')\n"
            "    if not isinstance(mode, str) or mode not in VALID_MODES:",
            "exit_on_invalid.py",
        )
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        error, = result["errors"]
        self.assertEqual((error["origin"], error["phase"], error["type"]),
                         ("candidate", "invoke", "SystemExit"))
        self.assertEqual(error["check_id"], "invalid_arguments")

    def test_candidate_system_exit_on_renderer_path_is_execution_error(self):
        candidate = self.mutation(
            "        cache[key] = renderer(project_id, document_id, revision, mode)",
            "        if project_id == 'Beta' and document_id == 'notes':\n"
            "            raise SystemExit('renderer path terminated')\n"
            "        cache[key] = renderer(project_id, document_id, revision, mode)",
            "exit_on_renderer.py",
        )
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        errors = [error for error in result["errors"]
                  if error["origin"] == "candidate" and error["phase"] == "invoke"]
        self.assertEqual(errors[0]["check_id"], "renderer_failure_preserves_cache")
        self.assertEqual(errors[0]["type"], "SystemExit")

    def test_missing_declared_fixture_is_grader_error(self):
        source = GRADER.read_text(encoding="utf-8")
        marker = "def _full(title, body):\n"
        self.assertEqual(source.count(marker), 1)
        # Remove the fixture at runtime, after the declaration has been built.
        broken_source = source.replace(
            marker, 'del FIXTURES[("Alpha", "report", 1)]\n\n\n' + marker
        )
        self.assert_fixture_preflight_error(broken_source)

    def test_source_omission_of_required_fixture_is_grader_error(self):
        source = GRADER.read_text(encoding="utf-8")
        fixture = '    ("Alpha", "report", 1): ("Launch", "Alpha body"),\n'
        self.assertEqual(source.count(fixture), 1)
        self.assert_fixture_preflight_error(source.replace(fixture, ""))

    def assert_fixture_preflight_error(self, broken_source):
        broken_grader = self.write_candidate(broken_source, "broken_grader.py")
        invocation = self.run_cli(CORRECT_REFERENCE, isolated=True, grader=broken_grader)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        self.assertEqual(result["checks"], [])
        error, = result["errors"]
        self.assertEqual((error["origin"], error["phase"], error["type"]),
                         ("grader", "fixture_preflight", "MissingFixtureError"))
        self.assertIn("Alpha", error["detail"])

    def test_fixture_loss_during_invalid_arguments_is_grader_error(self):
        source = GRADER.read_text(encoding="utf-8")
        anchor = '    cache = MappingSpy({"sentinel": "kept"})\n    spy = RendererSpy()\n'
        self.assertEqual(source.count(anchor), 1)
        broken_grader = self.write_candidate(
            source.replace(anchor, anchor + '    del spy.fixtures[("Alpha", "report", 1)]\n'),
            "invalid_probe_fixture_loss.py",
        )
        validation = '    if not isinstance(project_id, str) or project_id == "":\n'
        candidate = self.mutation(
            validation,
            '    if project_id == "":\n        return renderer("Alpha", "report", 1, "full")\n'
            + validation,
            "invalid_probe_fixture_call.py",
        )
        invocation = self.run_cli(candidate, isolated=True, grader=broken_grader)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        error, = result["errors"]
        self.assertEqual(
            (error["origin"], error["phase"], error["type"], error["check_id"]),
            ("grader", "renderer_fixture", "MissingFixtureError", "invalid_arguments"),
        )
        self.assertEqual(
            {check["id"] for check in result["checks"] if not check["passed"]},
            {"invalid_arguments"},
        )

    def test_wrong_forwarded_request_is_candidate_error_not_grader(self):
        candidate = self.mutation(
            "        cache[key] = renderer(project_id, document_id, revision, mode)",
            "        cache[key] = renderer(project_id, document_id, revision + 1000, mode)",
            "wrong_request.py",
        )
        invocation = self.run_cli(candidate)
        self.assertNotEqual(invocation.returncode, 0)
        result = self.result()
        self.assertFalse(result["functional_pass"])
        error = result["errors"][0]
        self.assertEqual((error["origin"], error["phase"], error["type"]),
                         ("candidate", "renderer_request", "MissingFixtureError"))
        self.assertEqual(error["check_id"], "cold_render_full")
        self.assertFalse(any(e.get("phase") == "fixture_preflight" for e in result["errors"]))

    def test_output_directory_missing_is_grader_output_error(self):
        output = self.directory / "missing" / "grade.json"
        invocation = self.run_cli(CORRECT_REFERENCE, output=output)
        self.assertNotEqual(invocation.returncode, 0)
        self.assertFalse(output.exists())
        self.assertIn("grader output error", invocation.stderr)

    def test_cli_mode_arguments_are_consistent(self):
        self_test = subprocess.run(
            [sys.executable, "-B", str(GRADER), "--self-test", "--output", str(self.output)],
            cwd=str(self.directory),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(self_test.returncode, 2)
        candidate_without_output = subprocess.run(
            [sys.executable, "-B", str(GRADER), "--candidate", str(CORRECT_REFERENCE)],
            cwd=str(self.directory),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(candidate_without_output.returncode, 2)


if __name__ == "__main__":
    unittest.main()
