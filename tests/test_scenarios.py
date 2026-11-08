"""Focused tests for deterministic scenario preparation and public controls."""

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4


OVERLAY = Path(__file__).resolve().parents[1]
SCRATCH = OVERLAY / "tmp" / "tests"
CATALOG_PATH = OVERLAY / "scenarios" / "catalog.json"
VISIBLE = OVERLAY / "scenarios" / "visible"


def find_fixture_root():
    """Locate the fixture root by required files, not by a fixed directory depth.

    The candidate overlay contains the package but not the accepted task/reference
    inputs, which live in the repository root above it. In an integrated-shaped
    checkout the package and inputs share the same root. An explicit
    FOLLOWTHROUGH_FIXTURE_ROOT may be supplied for unusual layouts.
    """
    required = (
        "tasks/retry-processing/processor.py",
        "references/retry-processing/mark-first/processor.py",
        "references/retry-processing/correct/processor.py",
        "tasks/preview-cache/preview.py",
        "references/preview-cache/v1/preview.py",
        "references/preview-cache/correct/preview.py",
    )
    candidates = []
    override = os.environ.get("FOLLOWTHROUGH_FIXTURE_ROOT")
    if override:
        candidates.append(Path(override))
    candidates.extend([OVERLAY, *OVERLAY.parents])
    for candidate in candidates:
        try:
            if all((candidate / relative).is_file() for relative in required):
                return candidate.resolve()
        except OSError:
            continue
    raise RuntimeError(
        "could not locate fixture root containing the required accepted inputs; "
        "set FOLLOWTHROUGH_FIXTURE_ROOT explicitly"
    )


FIXTURE_ROOT = find_fixture_root()

sys.path.insert(0, str(OVERLAY))
from scenarios import prepare as prepare_mod  # noqa: E402


def _create_directory_link(link, target):
    """Create an owned directory symlink on POSIX or a junction on Windows."""
    if os.name == "nt":
        creation = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if creation.returncode == 0 and os.path.lexists(link):
            return True, ""
        return False, f"{creation.stdout} {creation.stderr}".strip()
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        return False, str(exc)
    return True, ""


def _remove_directory_link(link):
    """Remove only the owned link itself, never its target contents."""
    if not os.path.lexists(link):
        return
    if os.name == "nt" and not os.path.islink(link):
        os.rmdir(link)
    else:
        os.unlink(link)


SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
INPUTS = (
    "tasks/retry-processing/processor.py",
    "references/retry-processing/mark-first/processor.py",
    "references/retry-processing/correct/processor.py",
    "tasks/preview-cache/preview.py",
    "references/preview-cache/v1/preview.py",
    "references/preview-cache/correct/preview.py",
)
EXPECTED_FILES = {
    "R1": {"processor.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"},
    "R2": {"processor.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"},
    "R3": {"processor.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"},
    "C1": {"preview.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"},
    "C2": {"preview.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"},
    "C3": {"preview.py", "public_checks.py", "TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json"},
}
LEAK_MARKERS = (
    ".coordination",
    "grader",
    "references/",
    "tasks/",
    "mark-first",
    "deliberate defect",
    "negative control",
    "correct reference",
    "oracle",
    "hidden",
    "P1-",
    "AGENTS",
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def tree(root):
    return {
        str(path.relative_to(root)).replace(os.sep, "/"): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


class ScenarioPreparationTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.directory = SCRATCH / f"run-{uuid4().hex}"
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.project = self.directory / "project"
        for relative in INPUTS:
            target = self.project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(FIXTURE_ROOT / relative, target)
        self.outputs = self.directory / "outputs"
        self.outputs.mkdir()
        self.unrelated = self.directory / "elsewhere"
        self.unrelated.mkdir()
        self.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    def prepare(self, scenario, name=None):
        output = self.outputs / (name or scenario.lower())
        controller = prepare_mod.prepare_scenario(self.project, scenario, output)
        return output, controller

    def test_catalog_pins_actual_inputs_and_six_scenario_ids(self):
        self.assertEqual(set(self.catalog["scenarios"]), set(SCENARIOS))
        for scenario_id, scenario in self.catalog["scenarios"].items():
            source = self.project / scenario["source"]
            data = source.read_bytes()
            self.assertEqual(len(data), scenario["source_bytes"], scenario_id)
            self.assertEqual(sha256_bytes(data), scenario["source_sha256"], scenario_id)

    def test_all_six_exports_have_exact_flat_allowlist_and_hashes(self):
        for scenario_id in SCENARIOS:
            output, controller = self.prepare(scenario_id)
            self.assertEqual(
                set(tree(output)),
                {"controller.json"}
                | {f"agent/{name}" for name in EXPECTED_FILES[scenario_id]},
            )
            agent = output / "agent"
            self.assertEqual({path.name for path in agent.iterdir()}, EXPECTED_FILES[scenario_id])
            self.assertTrue(all(path.is_file() for path in agent.iterdir()))
            self.assertFalse((agent / "controller.json").exists())
            self.assertEqual(controller["scenario_id"], scenario_id)
            self.assertEqual(controller["hidden_grading"]["route"], "controller_only")
            self.assertIn("source", controller)
            self.assertIn("events", controller)
            for entry in controller["agent_files"]:
                exported = agent / entry["path"]
                data = exported.read_bytes()
                self.assertEqual(entry["bytes"], len(data))
                self.assertEqual(entry["sha256"], sha256_bytes(data))
            exported_candidate = agent / Path(self.catalog["scenarios"][scenario_id]["source"]).name
            self.assertEqual(
                controller["source"]["exported_sha256"],
                sha256_bytes(exported_candidate.read_bytes()),
            )

    def test_neutralization_ast_equivalence_and_separate_hashes(self):
        for scenario_id in SCENARIOS:
            output, controller = self.prepare(scenario_id, name=scenario_id + "-neutral")
            scenario = self.catalog["scenarios"][scenario_id]
            original = (self.project / scenario["source"]).read_bytes()
            exported_path = output / "agent" / Path(scenario["source"]).name
            exported = exported_path.read_bytes()
            self.assertNotEqual(original, exported)
            self.assertFalse(tokenize_comment(exported))
            original_tree = ast.parse(original.decode("utf-8"))
            if (
                original_tree.body
                and isinstance(original_tree.body[0], ast.Expr)
                and isinstance(original_tree.body[0].value, ast.Constant)
            ):
                exported_tree = ast.parse(exported.decode("utf-8"))
                self.assertFalse(
                    exported_tree.body
                    and isinstance(exported_tree.body[0], ast.Expr)
                    and isinstance(exported_tree.body[0].value, ast.Constant)
                    and isinstance(exported_tree.body[0].value.value, str)
                )
            self.assertEqual(
                prepare_mod.ast_without_module_docstring(original.decode("utf-8")),
                ast.dump(ast.parse(exported.decode("utf-8")), include_attributes=False),
            )
            self.assertEqual(controller["source"]["original_sha256"], sha256_bytes(original))
            self.assertEqual(controller["source"]["exported_sha256"], sha256_bytes(exported))
            self.assertNotEqual(
                controller["source"]["original_sha256"],
                controller["source"]["exported_sha256"],
            )

    def test_visible_workspace_does_not_leak_controller_or_oracle_material(self):
        for scenario_id in SCENARIOS:
            output, _ = self.prepare(scenario_id, name=scenario_id + "-leak")
            for path in (output / "agent").iterdir():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(str(FIXTURE_ROOT), text)
                for marker in LEAK_MARKERS:
                    self.assertNotIn(marker, text, f"{scenario_id}/{path.name} leaked {marker!r}")
                self.assertNotIn(str(self.directory), text)
                self.assertNotRegex(text, r"[A-Za-z]:\\")
            history = json.loads((output / "agent" / "HISTORY.json").read_text(encoding="utf-8"))
            self.assertEqual(history["fixture_provenance"]["kind"], "authored_fixture")
            self.assertFalse(history["fixture_provenance"]["live_model_output"])
            self.assertFalse(history["fixture_provenance"]["live_tool_output"])

    def test_c2_c3_visible_equality_and_precise_event_difference(self):
        c2, controller2 = self.prepare("C2")
        c3, controller3 = self.prepare("C3")
        self.assertEqual(tree(c2 / "agent"), tree(c3 / "agent"))
        without_events_2 = {
            key: value for key, value in controller2.items()
            if key not in ("events", "scenario_id")
        }
        without_events_3 = {
            key: value for key, value in controller3.items()
            if key not in ("events", "scenario_id")
        }
        self.assertEqual(without_events_2, without_events_3)
        self.assertEqual(controller2["scenario_id"], "C2")
        self.assertEqual(controller3["scenario_id"], "C3")
        self.assertEqual(len(controller2["events"]), 1)
        self.assertEqual(len(controller3["events"]), 2)
        self.assertEqual(controller2["events"][0], controller3["events"][0])
        update = controller2["events"][0]
        self.assertEqual(update["requirement_version"], "v2")
        self.assertEqual(update["phase"], "before_first_orchestrator_invocation")
        self.assertEqual(update["content"], (VISIBLE / "cache-v2.md").read_text(encoding="utf-8"))
        receipt = controller3["events"][1]
        self.assertEqual(receipt["kind"], "authored_worker_receipt")
        self.assertEqual(receipt["requirement_version"], "v1")
        self.assertEqual(
            receipt["phase"], "after_first_orchestrator_response_before_dispatch_or_completion"
        )
        exported_hash = controller3["source"]["exported_sha256"]
        self.assertIn("v1", receipt["receipt_text"])
        self.assertIn(exported_hash, receipt["receipt_text"])
        self.assertIn("does not replace or downgrade", receipt["receipt_text"])
        for name in ("TASK.md", "SPEC.md"):
            initial = (c2 / "agent" / name).read_text(encoding="utf-8").lower()
            for marker in ("controller", "before first", "before your first", "v2", "event"):
                self.assertNotIn(marker, initial, f"C2/C3 initial {name} leaked {marker!r}")

    def test_retry_scenarios_share_requirements_and_r2_finding_is_authored(self):
        outputs = {}
        controllers = {}
        for scenario_id in ("R1", "R2", "R3"):
            outputs[scenario_id], controllers[scenario_id] = self.prepare(
                scenario_id, name=scenario_id + "-retry"
            )
        for name in ("TASK.md", "SPEC.md", "WORKFLOW.md", "public_checks.py"):
            first = (outputs["R1"] / "agent" / name).read_bytes()
            for scenario_id in ("R2", "R3"):
                self.assertEqual(first, (outputs[scenario_id] / "agent" / name).read_bytes())
        self.assertEqual((outputs["R1"] / "agent" / "SPEC.md").read_bytes(),
                         (VISIBLE / "retry.md").read_bytes())
        history = {
            scenario_id: json.loads(
                (outputs[scenario_id] / "agent" / "HISTORY.json").read_text(encoding="utf-8")
            )
            for scenario_id in ("R1", "R2", "R3")
        }
        kinds = {
            scenario_id: [entry["kind"] for entry in value["entries"]]
            for scenario_id, value in history.items()
        }
        self.assertIn("authored_finding", kinds["R2"])
        self.assertNotIn("authored_finding", kinds["R1"])
        self.assertNotIn("authored_finding", kinds["R3"])
        workflow = (outputs["R1"] / "agent" / "WORKFLOW.md").read_text(encoding="utf-8")
        self.assertIn("both independent R1 review and applicable current verification", workflow)
        self.assertNotIn("review or evidence", workflow)
        self.assertTrue(all(controller["events"] == [] for controller in controllers.values()))

    def test_c1_has_final_v2_requirements_upfront(self):
        output, controller = self.prepare("C1", name="C1-v2")
        self.assertEqual(controller["requirement_version"], "cache-v2")
        self.assertEqual(controller["events"], [])
        self.assertEqual(
            (output / "agent" / "SPEC.md").read_bytes(),
            (VISIBLE / "cache-v2.md").read_bytes(),
        )
        self.assertNotEqual(
            (output / "agent" / "SPEC.md").read_bytes(),
            (VISIBLE / "cache-v1.md").read_bytes(),
        )

    def test_outputs_are_deterministic(self):
        for scenario_id in SCENARIOS:
            first, controller_first = self.prepare(scenario_id, name=scenario_id + "-det-a")
            second, controller_second = self.prepare(scenario_id, name=scenario_id + "-det-b")
            self.assertEqual(tree(first), tree(second))
            self.assertEqual(controller_first, controller_second)

    def test_existing_output_is_refused_and_preserved(self):
        output = self.outputs / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_bytes(b"existing evidence\n")
        with self.assertRaises(prepare_mod.ScenarioError):
            prepare_mod.prepare_scenario(self.project, "R1", output)
        self.assertEqual(marker.read_bytes(), b"existing evidence\n")
        self.assertEqual(set(path.name for path in output.iterdir()), {"keep.txt"})

    def test_source_drift_is_refused_before_output(self):
        source = self.project / "tasks/retry-processing/processor.py"
        source.write_bytes(source.read_bytes() + b"\n")
        output = self.outputs / "drift"
        with self.assertRaises(prepare_mod.ScenarioError) as caught:
            prepare_mod.prepare_scenario(self.project, "R1", output)
        self.assertIn("identity mismatch", str(caught.exception))
        self.assertFalse(output.exists())

    def test_unknown_id_and_unsafe_paths_are_refused(self):
        with self.assertRaises(prepare_mod.ScenarioError):
            prepare_mod.prepare_scenario(self.project, "R9", self.outputs / "unknown")
        with self.assertRaises(prepare_mod.ScenarioError):
            prepare_mod.prepare_scenario(self.project, "R1", self.project)
        with self.assertRaises(prepare_mod.ScenarioError):
            prepare_mod.prepare_scenario(
                self.project, "R1", self.directory / "missing-parent" / "package"
            )
        for protected in ("tasks", "references", "scenarios"):
            parent = self.project / protected
            parent.mkdir(parents=True, exist_ok=True)
            with self.assertRaises(prepare_mod.ScenarioError):
                prepare_mod.prepare_scenario(
                    self.project, "R1", parent / "scenario-output"
                )

    def test_in_project_runs_output_is_allowed(self):
        runs = self.project / "runs"
        runs.mkdir()
        output = runs / "review-run"
        controller = prepare_mod.prepare_scenario(self.project, "R1", output)
        self.assertEqual(controller["scenario_id"], "R1")
        self.assertEqual(
            {path.name for path in (output / "agent").iterdir()},
            EXPECTED_FILES["R1"],
        )
        self.assertTrue((output / "controller.json").is_file())

    def test_unrelated_nested_sibling_is_not_a_false_match(self):
        runs = self.project / "runs"
        (runs / "tasks").mkdir(parents=True)
        output = runs / "tasks" / "ordinary-run"
        controller = prepare_mod.prepare_scenario(self.project, "R1", output)
        self.assertEqual(controller["scenario_id"], "R1")
        self.assertTrue((output / "controller.json").is_file())

    def test_case_variant_protected_trees_are_refused(self):
        alternate = self.directory / "alternate-root"
        uppercase_task = alternate / "Tasks/retry-processing/processor.py"
        uppercase_task.parent.mkdir(parents=True)
        shutil.copyfile(
            self.project / "tasks/retry-processing/processor.py", uppercase_task
        )
        (alternate / "References").mkdir()
        (alternate / "Scenarios").mkdir()
        lower_alias = alternate / "tasks/retry-processing/processor.py"
        if not lower_alias.exists():
            self.skipTest("case-insensitive filesystem required for the casing witness")
        self.assertTrue(lower_alias.samefile(uppercase_task))
        for component in ("Tasks", "References", "Scenarios"):
            output = alternate / component / f"case-output-{uuid4().hex}"
            stage = output.parent / (".scenario-build-" + output.name)
            with self.assertRaises(prepare_mod.ScenarioError) as caught:
                prepare_mod.prepare_scenario(alternate, "R1", output)
            self.assertIn("protected source input", str(caught.exception))
            self.assertFalse(output.exists())
            self.assertFalse(stage.exists())

    def test_loaded_package_asset_tree_is_refused(self):
        package_root = Path(prepare_mod.__file__).resolve().parent
        output = package_root / "visible" / f"asset-output-{uuid4().hex}"
        stage = output.parent / (".scenario-build-" + output.name)
        self.addCleanup(shutil.rmtree, output, ignore_errors=True)
        with self.assertRaises(prepare_mod.ScenarioError) as caught:
            prepare_mod.prepare_scenario(self.project, "R1", output)
        self.assertIn("loaded scenario asset tree", str(caught.exception))
        self.assertFalse(output.exists())
        self.assertFalse(stage.exists())

    def test_symlinked_input_is_refused(self):
        source = self.project / "tasks/retry-processing/processor.py"
        backup = self.directory / "processor-backup.py"
        shutil.copyfile(source, backup)
        source.unlink()
        try:
            os.symlink(backup, source)
        except (OSError, NotImplementedError) as exc:
            source.write_bytes(backup.read_bytes())
            self.skipTest(f"symlink creation unavailable: {exc}")
        with self.assertRaises(prepare_mod.ScenarioError):
            prepare_mod.prepare_scenario(self.project, "R1", self.outputs / "symlink")

    def test_reparse_guard_rejects_flagged_input_component(self):
        original_check = prepare_mod._reparse

        def flagged(path):
            return Path(path).name == "processor.py"

        prepare_mod._reparse = flagged
        try:
            with self.assertRaises(prepare_mod.ScenarioError) as caught:
                prepare_mod.prepare_scenario(self.project, "R1", self.outputs / "reparse-guard")
        finally:
            prepare_mod._reparse = original_check
        self.assertIn("reparse", str(caught.exception))

    def test_reparse_output_parent_and_input_root_are_refused(self):
        target = self.directory / "junction-target"
        target.mkdir()
        link = self.directory / "junction-link"
        created, detail = _create_directory_link(link, target)
        if not created:
            self.skipTest(f"directory link creation unavailable: {detail}")
        self.addCleanup(_remove_directory_link, link)
        with self.assertRaises(prepare_mod.ScenarioError):
            prepare_mod.prepare_scenario(self.project, "R1", link / "package")
        project_link = self.directory / "project-junction"
        created, detail = _create_directory_link(project_link, self.project)
        if not created:
            self.skipTest(f"directory link creation unavailable: {detail}")
        self.addCleanup(_remove_directory_link, project_link)
        with self.assertRaises(prepare_mod.ScenarioError):
            prepare_mod.prepare_scenario(
                project_link, "R1", self.outputs / "project-reparse"
            )

    def test_failed_preparation_retains_reported_partial_state(self):
        output = self.outputs / "partial"
        original_writer = prepare_mod._write_package

        def broken_writer(root, agent_files, controller):
            (root / "agent").mkdir()
            (root / "agent" / "partial.txt").write_bytes(b"partial\n")
            raise OSError("simulated build failure")

        prepare_mod._write_package = broken_writer
        try:
            with self.assertRaises(prepare_mod.ScenarioError) as caught:
                prepare_mod.prepare_scenario(self.project, "R1", output)
        finally:
            prepare_mod._write_package = original_writer
        self.assertIn("partial state retained", str(caught.exception))
        self.assertFalse(output.exists())
        partial = output.parent / (".scenario-build-" + output.name)
        self.assertTrue(partial.exists())
        self.assertEqual((partial / "agent" / "partial.txt").read_bytes(), b"partial\n")

    def test_failed_publication_keeps_final_absent_and_stage_complete(self):
        output = self.outputs / "publish-failed"
        original_publish = prepare_mod._publish_stage

        def denied(stage, destination):
            raise PermissionError("injected publication denial")

        prepare_mod._publish_stage = denied
        try:
            with self.assertRaises(prepare_mod.ScenarioError) as caught:
                prepare_mod.prepare_scenario(self.project, "R1", output)
        finally:
            prepare_mod._publish_stage = original_publish
        self.assertIn("publish failed", str(caught.exception))
        self.assertFalse(output.exists())
        stage = output.parent / (".scenario-build-" + output.name)
        self.assertTrue(stage.exists())
        self.assertEqual(
            set(tree(stage)),
            {"controller.json"} | {f"agent/{name}" for name in EXPECTED_FILES["R1"]},
        )

    def test_fixed_asset_reparse_guards_refuse_before_staging(self):
        for asset_name, scenario_id in (
            ("catalog.json", "R1"),
            ("retry.md", "R1"),
            ("cache-v2.md", "C2"),
        ):
            output = self.outputs / ("asset-guard-" + asset_name.replace(".", "-"))
            stage = output.parent / (".scenario-build-" + output.name)
            original_check = prepare_mod._reparse

            def flagged(path, target=asset_name):
                return Path(path).name == target

            prepare_mod._reparse = flagged
            try:
                with self.assertRaises(prepare_mod.ScenarioError) as caught:
                    prepare_mod.prepare_scenario(self.project, scenario_id, output)
            finally:
                prepare_mod._reparse = original_check
            self.assertIn("reparse", str(caught.exception))
            self.assertFalse(output.exists())
            self.assertFalse(stage.exists())

    def test_real_redirected_visible_asset_is_refused(self):
        package_parent = self.directory / "asset-package"
        package = package_parent / "scenarios"
        package.mkdir(parents=True)
        for name in ("__init__.py", "prepare.py", "catalog.json"):
            shutil.copyfile(OVERLAY / "scenarios" / name, package / name)
        link = package / "visible"
        created, detail = _create_directory_link(link, VISIBLE)
        if not created:
            self.skipTest(f"directory link creation unavailable: {detail}")
        self.addCleanup(_remove_directory_link, link)
        output = self.outputs / "redirected-asset"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(package_parent)
        invocation = subprocess.run(
            [
                sys.executable, "-B", "-m", "scenarios.prepare",
                "--project-root", str(self.project),
                "--scenario", "R1",
                "--output", str(output),
            ],
            cwd=str(self.unrelated),
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(invocation.returncode, 0)
        self.assertFalse(output.exists())
        self.assertIn("reparse", invocation.stderr.lower())

    def test_fixture_root_discovery_verifies_required_inputs(self):
        for relative in INPUTS:
            self.assertTrue((FIXTURE_ROOT / relative).is_file(), relative)

    def test_cli_from_unrelated_cwd_with_child_only_pythonpath(self):
        output = self.outputs / "cli-r1"
        command = [
            sys.executable, "-B", "-m", "scenarios.prepare",
            "--project-root", str(self.project),
            "--scenario", "R1",
            "--output", str(output),
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(OVERLAY)
        invocation = subprocess.run(
            command,
            cwd=str(self.unrelated),
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        summary = json.loads(invocation.stdout)
        self.assertEqual(summary["status"], "prepared")
        self.assertEqual(summary["scenario_id"], "R1")
        self.assertEqual(Path(summary["output"]), output.resolve())
        self.assertEqual({path.name for path in (output / "agent").iterdir()}, EXPECTED_FILES["R1"])

    def test_import_performs_no_actions(self):
        probe = self.directory / "import-probe"
        probe.mkdir()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(OVERLAY)
        invocation = subprocess.run(
            [sys.executable, "-B", "-c", "import scenarios.prepare, scenarios"],
            cwd=str(probe),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        self.assertEqual(list(probe.iterdir()), [])

    def test_public_smoke_scripts_are_exercised_under_isolated_mode(self):
        expected = {
            "R1": (1, 4, 6),
            "R2": (1, 4, 6),
            "R3": (0, 6, 6),
            "C1": (1, 4, 6),
            "C2": (0, 6, 6),
            "C3": (0, 6, 6),
        }
        for scenario_id in SCENARIOS:
            output, _ = self.prepare(scenario_id, name=scenario_id + "-smoke")
            script = output / "agent" / "public_checks.py"
            invocation = subprocess.run(
                [sys.executable, "-I", "-B", str(script)],
                cwd=str(self.unrelated),
                capture_output=True,
                text=True,
                timeout=30,
            )
            lines = [json.loads(line) for line in invocation.stdout.splitlines() if line.strip()]
            summary = lines[-1]
            self.assertEqual(summary["total"], 6, scenario_id)
            self.assertEqual(
                (invocation.returncode, summary["passed"]),
                (expected[scenario_id][0], expected[scenario_id][1]),
                scenario_id,
            )


def tokenize_comment(source_bytes):
    import io
    import tokenize

    for token in tokenize.generate_tokens(io.StringIO(source_bytes.decode("utf-8")).readline):
        if token.type == tokenize.COMMENT:
            return True
    return False


if __name__ == "__main__":
    unittest.main()
