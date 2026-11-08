"""Focused tests for prepared-package to offline episode-config translation."""

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
PROJECT = OVERLAY
sys.path.insert(0, str(OVERLAY))

from scenarios import episode_config as config_mod  # noqa: E402
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
LIMITS = {
    "max_model_responses": 12,
    "max_tool_actions": 24,
    "max_elapsed_ms": 60000,
    "max_context_bytes": 32000,
}
EXPECTED = {
    "R1": ("retry-processing", "processor.py", "retry-v1", "v1", []),
    "R2": ("retry-processing", "processor.py", "retry-v1", "v1", []),
    "R3": ("retry-processing", "processor.py", "retry-v1", "v1", []),
    "C1": ("preview-cache", "preview.py", "cache-v2", "v2", []),
    "C2": ("preview-cache", "preview.py", "cache-v1", "v1",
           [("before_first_O1", "requirements_update", "v2")]),
    "C3": ("preview-cache", "preview.py", "cache-v1", "v1",
           [("before_first_O1", "requirements_update", "v2"),
            ("after_first_O1_response", "supplied_receipt", "v1")]),
}
CONFIG_KEYS = {
    "episode_id", "scenario_id", "arm", "workspace", "output_dir",
    "requirements_version", "scheduled_events", "max_model_responses",
    "max_tool_actions", "max_elapsed_ms", "max_context_bytes", "evidence_kind",
}
EVENT_KEYS = {"boundary", "kind", "version", "text", "source_sha256"}
RECEIPT_TEMPLATE = (
    "Authored fixture receipt: earlier v1 work was reported complete under the v1-only "
    "checkpoint scope for exported candidate sha256 {candidate_sha256}. This receipt is "
    "authored fixture data, is not evidence of current v2 completion or of a live provider "
    "response, and does not replace or downgrade the current v2 requirements."
)
PRIVATE_MARKERS = (
    "hidden_grading", "fixture_provenance", "source_expectation", "catalog_path",
    "controller.json", "workflow_roles", "visible_requirements_version",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(root):
    return {
        str(path.relative_to(root)).replace(os.sep, "/"): path.read_bytes()
        for path in Path(root).rglob("*")
        if path.is_file()
    }


class EpisodeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        cls.run_root = SCRATCH / f"run-{uuid4().hex}"
        cls.packages = cls.run_root / "packages"
        cls.packages.mkdir(parents=True)
        cls.package_paths = {}
        for scenario_id in SCENARIOS:
            output = cls.packages / scenario_id
            prepare_mod.prepare_scenario(PROJECT, scenario_id, output)
            cls.package_paths[scenario_id] = output
        cls.addClassCleanup(shutil.rmtree, cls.run_root, ignore_errors=True)

    def setUp(self):
        self.case_root = self.run_root / f"case-{uuid4().hex}"
        self.outputs = self.case_root / "outputs"
        self.outputs.mkdir(parents=True)

    def package(self, scenario_id):
        return self.package_paths[scenario_id]

    def copy_package(self, scenario_id):
        destination = self.case_root / f"pkg-{uuid4().hex}"
        shutil.copytree(self.package(scenario_id), destination)
        return destination

    def output(self, name="new-output"):
        return self.outputs / name

    def build(self, scenario_id="R1", *, package_root=None, arm="ordinary",
              episode_id="episode-1", output_dir=None, limits=None):
        return config_mod.build_episode_config(
            package_root or self.package(scenario_id),
            episode_id=episode_id,
            arm=arm,
            output_dir=output_dir or self.output(),
            limits=dict(LIMITS if limits is None else limits),
        )

    def assert_code(self, code, **kwargs):
        with self.assertRaises(config_mod.ScenarioConfigError) as caught:
            self.build(**kwargs)
        error = caught.exception
        self.assertEqual(str(error), code)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        return error

    def write_json(self, path, value):
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    def test_exact_mapping_for_all_six_ids_and_both_arms(self):
        for scenario_id in SCENARIOS:
            family, candidate, requirement, normalized, events = EXPECTED[scenario_id]
            for arm in ("ordinary", "structured_progress"):
                config = self.build(
                    scenario_id,
                    arm=arm,
                    output_dir=self.outputs / f"{scenario_id}-{arm}",
                )
                self.assertEqual(set(config), CONFIG_KEYS)
                self.assertEqual(config["episode_id"], "episode-1")
                self.assertEqual(config["scenario_id"], scenario_id)
                self.assertEqual(config["arm"], arm)
                self.assertEqual(config["requirements_version"], normalized)
                self.assertEqual(config["evidence_kind"], "synthetic_episode_control")
                self.assertEqual(config["max_model_responses"], LIMITS["max_model_responses"])
                self.assertEqual(config["max_tool_actions"], LIMITS["max_tool_actions"])
                self.assertEqual(config["max_elapsed_ms"], LIMITS["max_elapsed_ms"])
                self.assertEqual(config["max_context_bytes"], LIMITS["max_context_bytes"])
                self.assertEqual(
                    Path(config["workspace"]),
                    Path(os.path.realpath(self.package(scenario_id) / "agent")),
                )
                self.assertEqual(
                    Path(config["output_dir"]),
                    self.outputs / f"{scenario_id}-{arm}",
                )
                self.assertEqual(len(config["scheduled_events"]), len(events))
                for event, (boundary, kind, version) in zip(config["scheduled_events"], events):
                    self.assertEqual(set(event), EVENT_KEYS)
                    self.assertEqual((event["boundary"], event["kind"], event["version"]),
                                     (boundary, kind, version))
                serialized = json.dumps(config, sort_keys=True)
                for marker in PRIVATE_MARKERS:
                    self.assertNotIn(marker, serialized)

    def test_no_package_or_output_mutation(self):
        package = self.package("C3")
        before_package = tree(package)
        before_outputs = tree(self.outputs)
        config = self.build("C3", output_dir=self.outputs / "never-created")
        self.assertEqual(tree(package), before_package)
        self.assertEqual(tree(self.outputs), before_outputs)
        self.assertFalse((self.outputs / "never-created").exists())
        self.assertIsInstance(config, dict)

    def test_c2_c3_initial_agent_equality_and_precise_schedule_difference(self):
        c2 = self.package("C2")
        c3 = self.package("C3")
        self.assertEqual(tree(c2 / "agent"), tree(c3 / "agent"))
        config2 = self.build("C2", output_dir=self.outputs / "c2")
        config3 = self.build("C3", output_dir=self.outputs / "c3")
        self.assertEqual(len(config2["scheduled_events"]), 1)
        self.assertEqual(len(config3["scheduled_events"]), 2)
        self.assertEqual(config2["scheduled_events"][0]["text"],
                         (OVERLAY / "scenarios/visible/cache-v2.md").read_text(encoding="utf-8"))
        self.assertEqual(config2["scheduled_events"][0], config3["scheduled_events"][0])
        receipt = config3["scheduled_events"][1]
        candidate_hash = sha((c3 / "agent/preview.py").read_bytes())
        self.assertEqual(receipt["source_sha256"], candidate_hash)
        self.assertEqual(receipt["text"], RECEIPT_TEMPLATE.format(candidate_sha256=candidate_hash))
        self.assertNotEqual(config2["scheduled_events"], config3["scheduled_events"])

    def test_invalid_limits_episode_and_arm(self):
        invalid_limits = [
            {k: v for k, v in LIMITS.items() if k != "max_model_responses"},
            dict(LIMITS, extra=1),
            dict(LIMITS, max_model_responses=True),
            dict(LIMITS, max_tool_actions=0),
            dict(LIMITS, max_elapsed_ms=600001),
            dict(LIMITS, max_context_bytes="1"),
        ]
        for limits in invalid_limits:
            self.assert_code("invalid_limits", limits=limits)
        for bad in ("", "bad id", "x" * 65, 7, "é"):
            self.assert_code("invalid_episode_id", episode_id=bad)
        for bad in ("ordinary ", "structured", 1, None):
            self.assert_code("invalid_arm", arm=bad)

    def test_relative_nul_and_namespace_paths(self):
        self.assert_code("invalid_package_root", package_root="relative/pkg")
        self.assert_code("invalid_output_dir", output_dir=Path("relative/out"))
        self.assert_code("invalid_package_root", package_root=str(self.package("R1")) + "\x00x")
        if os.name == "nt":
            alias = "\\\\?\\" + str(self.package("R1"))
            self.assert_code("unsupported_path_alias", package_root=alias)

    def test_existing_inside_missing_and_ancestor_outputs(self):
        existing = self.outputs / "existing"
        existing.mkdir()
        (existing / "marker").write_bytes(b"keep")
        self.assert_code("output_exists", output_dir=existing)
        self.assertEqual((existing / "marker").read_bytes(), b"keep")
        self.assert_code("output_overlaps_package",
                         output_dir=self.package("R1") / "agent" / "new")
        self.assert_code("output_exists", output_dir=self.package("R1"))
        self.assert_code("unsafe_output_path",
                         output_dir=self.case_root / "missing" / "child")

    def test_inventory_negatives(self):
        package = self.copy_package("R1")
        (package / "agent" / "extra.txt").write_bytes(b"extra")
        self.assert_code("agent_inventory_invalid", package_root=package)
        package = self.copy_package("R2")
        (package / "extra-root.txt").write_bytes(b"extra")
        self.assert_code("package_inventory_invalid", package_root=package)
        package = self.copy_package("R3")
        (package / "agent" / "subdir").mkdir()
        self.assert_code("agent_inventory_invalid", package_root=package)

    def test_controller_json_schema_and_size_negatives(self):
        package = self.copy_package("R1")
        (package / "controller.json").write_bytes(b"{not json")
        self.assert_code("controller_json_invalid", package_root=package)

        package = self.copy_package("R1")
        text = (package / "controller.json").read_text(encoding="utf-8")
        text = text.replace('"schema_version": "0.1"',
                            '"schema_version": "0.1",\n  "schema_version": "0.1"')
        (package / "controller.json").write_text(text, encoding="utf-8")
        self.assert_code("controller_json_invalid", package_root=package)

        package = self.copy_package("R1")
        controller = json.loads((package / "controller.json").read_text(encoding="utf-8"))
        controller["unknown"] = True
        self.write_json(package / "controller.json", controller)
        self.assert_code("controller_schema_invalid", package_root=package)

        package = self.copy_package("R1")
        controller["unknown"] = True
        controller.pop("unknown")
        controller["scenario_id"] = "RX"
        self.write_json(package / "controller.json", controller)
        self.assert_code("scenario_mapping_invalid", package_root=package)

        package = self.copy_package("R1")
        (package / "controller.json").write_bytes(b"x" * (262144 + 1))
        self.assert_code("controller_too_large", package_root=package)

    def test_agent_size_aggregate_and_source_identity_negatives(self):
        package = self.copy_package("R1")
        (package / "agent" / "TASK.md").write_bytes(b"x" * (1024 * 1024 + 1))
        self.assert_code("agent_size_invalid", package_root=package)

        package = self.copy_package("R2")
        (package / "agent" / "TASK.md").write_bytes(b"x" * (1024 * 1024))
        (package / "agent" / "WORKFLOW.md").write_bytes(b"x" * (1024 * 1024))
        self.assert_code("agent_size_invalid", package_root=package)

        package = self.copy_package("R3")
        controller = json.loads((package / "controller.json").read_text(encoding="utf-8"))
        controller["source"]["exported_sha256"] = "0" * 64
        self.write_json(package / "controller.json", controller)
        self.assert_code("source_identity_mismatch", package_root=package)

        package = self.copy_package("R1")
        controller = json.loads((package / "controller.json").read_text(encoding="utf-8"))
        controller["agent_files"][0]["sha256"] = "0" * 64
        self.write_json(package / "controller.json", controller)
        self.assert_code("agent_inventory_invalid", package_root=package)

        package = self.copy_package("R1")
        (package / "agent" / "TASK.md").write_bytes(b"tampered")
        self.assert_code("agent_inventory_invalid", package_root=package)

    def test_event_negatives(self):
        def mutate(callback):
            package = self.copy_package("C3")
            controller = json.loads((package / "controller.json").read_text(encoding="utf-8"))
            callback(controller)
            self.write_json(package / "controller.json", controller)
            return package

        self.assert_code("event_schedule_invalid", package_root=mutate(
            lambda c: c["events"].append(dict(c["events"][0]))
        ))
        self.assert_code("event_schedule_invalid", package_root=mutate(
            lambda c: c["events"].pop()
        ))
        self.assert_code("event_schedule_invalid", package_root=mutate(
            lambda c: c["events"][0].__setitem__("phase", "after_first_orchestrator_response")
        ))
        self.assert_code("event_schedule_invalid", package_root=mutate(
            lambda c: c["events"][0].__setitem__("requirement_version", "v1")
        ))
        self.assert_code("receipt_invalid", package_root=mutate(
            lambda c: c["events"][1].__setitem__("receipt_text", "forged")
        ))
        self.assert_code("receipt_invalid", package_root=mutate(
            lambda c: c["events"][1].__setitem__("receipt_template", "forged")
        ))
        self.assert_code("receipt_invalid", package_root=mutate(
            lambda c: c["events"][1].__setitem__("receipt_text", "x" * 4097)
        ))
        self.assert_code("event_schedule_invalid", package_root=mutate(
            lambda c: c.__setitem__("events", [])
        ))

    def test_prospective_spec_aggregate_oversize(self):
        package = self.copy_package("C2")
        controller = json.loads((package / "controller.json").read_text(encoding="utf-8"))
        for name, size in (("TASK.md", 950000), ("WORKFLOW.md", 900000)):
            data = b"x" * size
            (package / "agent" / name).write_bytes(data)
            for record in controller["agent_files"]:
                if record["path"] == name:
                    record["bytes"] = len(data)
                    record["sha256"] = sha(data)
        content = "y" * 250000
        controller["events"][0]["content"] = content
        self.write_json(package / "controller.json", controller)
        self.assert_code("spec_oversize", package_root=package)

    def test_controller_agent_version_and_provenance_negatives(self):
        def mutate(callback):
            package = self.copy_package("R1")
            controller = json.loads((package / "controller.json").read_text(encoding="utf-8"))
            callback(controller)
            self.write_json(package / "controller.json", controller)
            return package

        self.assert_code("controller_schema_invalid", package_root=mutate(
            lambda c: c.__setitem__("schema_version", "0.2")
        ))
        self.assert_code("scenario_mapping_invalid", package_root=mutate(
            lambda c: c.__setitem__("requirement_version", "retry-v2")
        ))
        self.assert_code("scenario_mapping_invalid", package_root=mutate(
            lambda c: c.__setitem__("visible_requirements_version", "retry-v2")
        ))
        self.assert_code("scenario_mapping_invalid", package_root=mutate(
            lambda c: c.__setitem__("family", "preview-cache")
        ))
        self.assert_code("controller_schema_invalid", package_root=mutate(
            lambda c: c["fixture_provenance"].__setitem__("live_model_output", True)
        ))
        self.assert_code("controller_schema_invalid", package_root=mutate(
            lambda c: c["hidden_grading"].__setitem__("route", "agent_visible")
        ))

    def test_relative_and_extra_file_inventory_for_cache(self):
        package = self.copy_package("C1")
        (package / "agent" / "processor.py").write_bytes(b"extra")
        self.assert_code("agent_inventory_invalid", package_root=package)

    def test_guard_order_and_real_reparse_parent(self):
        real = self.case_root / "real-parent"
        (real / "sub").mkdir(parents=True)
        link = self.case_root / "linked-parent"
        created, detail = _create_directory_link(link, real)
        if not created:
            self.skipTest(f"directory link creation unavailable: {detail}")
        self.addCleanup(_remove_directory_link, link)
        calls = []
        original_lstat = config_mod._lstat

        def spy(path):
            calls.append(Path(path))
            return original_lstat(path)

        config_mod._lstat = spy
        try:
            self.assert_code("unsafe_output_path", output_dir=link / "sub" / "new-output")
        finally:
            config_mod._lstat = original_lstat
        link_str = str(link)
        descendants = [
            str(path) for path in calls
            if str(path).startswith(link_str) and str(path) != link_str
        ]
        self.assertEqual(descendants, [])

    def test_import_has_no_side_effects(self):
        probe = self.case_root / "import-probe"
        probe.mkdir()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(OVERLAY)
        invocation = subprocess.run(
            [sys.executable, "-B", "-c", "import scenarios.episode_config"],
            cwd=str(probe), env=environment, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(invocation.returncode, 0, invocation.stderr)
        self.assertEqual(list(probe.iterdir()), [])

    def test_returned_config_is_detached_and_errors_are_sanitized(self):
        first = self.build("R3")
        first["scheduled_events"].append({"forged": True})
        first["max_model_responses"] = 1
        second = self.build("R3")
        self.assertEqual(second["scheduled_events"], [])
        self.assertEqual(second["max_model_responses"], LIMITS["max_model_responses"])

        package = self.copy_package("R1")
        (package / "controller.json").write_bytes(b"{bad")
        with self.assertRaises(config_mod.ScenarioConfigError) as caught:
            self.build(package_root=package)
        message = str(caught.exception)
        self.assertEqual(message, "controller_json_invalid")
        self.assertNotIn(str(package), message)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


if __name__ == "__main__":
    unittest.main()
