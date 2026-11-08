"""Deterministic scenario preparation for FollowThrough.

Public interface:
    prepare_scenario(project_root, scenario_id, output_dir) -> dict

The function reads only the fixed inputs selected by scenarios/catalog.json,
constructs one complete package in an owned staging sibling, and publishes it
only by renaming that complete staging tree. If publication is refused, the
final output is not created and the owned staging tree is retained and
reported. Importing this module performs no I/O and no actions.

This is offline fixture packaging only. It implements no agent loop, provider
request, hidden grading, event engine or scoring.
"""

import argparse
import ast
import io
import json
import os
from pathlib import Path
import stat
import sys
import tokenize


CATALOG_PATH = Path(__file__).with_name("catalog.json")
VISIBLE_DIR = Path(__file__).with_name("visible")
PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = "0.1"
PROTECTED_OUTPUT_TREES = ("tasks", "references", "scenarios")
AGENT_FILE_NAMES = ("TASK.md", "SPEC.md", "WORKFLOW.md", "HISTORY.json", "public_checks.py")
FORBIDDEN_VISIBLE_MARKERS = (
    "docs/project",
    ".coordination",
    "grader",
    "references/",
    "tasks/",
    "AGENTS.md",
    "P1-CACHE",
    "P1-R1",
    "P1-SCENARIOS",
)
FIXTURE_PROVENANCE = {
    "kind": "authored_fixture",
    "live_model_output": False,
    "live_tool_output": False,
    "note": (
        "All supplied history and findings are authored fixture data. They are not records "
        "of live model or tool execution and contain no grading scores."
    ),
}


class ScenarioError(Exception):
    """A scenario preparation was refused or failed safely."""


def _load_catalog():
    _assert_no_reparse(CATALOG_PATH, "scenario catalog")
    try:
        raw = CATALOG_PATH.read_bytes()
        catalog = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioError(f"scenario catalog is unavailable or invalid: {exc}") from exc
    if not isinstance(catalog, dict) or catalog.get("schema_version") != SCHEMA_VERSION:
        raise ScenarioError("scenario catalog schema_version must be 0.1")
    if not isinstance(catalog.get("families"), dict) or not isinstance(catalog.get("scenarios"), dict):
        raise ScenarioError("scenario catalog must define families and scenarios")
    return catalog


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _text_bytes(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _reparse(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _assert_no_reparse(path, label):
    for component in (Path(path), *Path(path).parents):
        if component.exists() and _reparse(component):
            raise ScenarioError(f"{label} contains a symlink or reparse component: {component}")


def _assert_relative_source(source_path):
    candidate = Path(source_path)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise ScenarioError(f"catalog source path is unsafe: {source_path!r}")


def _same_or_inside(path, root):
    """Filesystem-aware containment, including Windows case-insensitive identity."""
    normalized_path = os.path.normcase(os.path.abspath(os.fspath(path)))
    normalized_root = os.path.normcase(os.path.abspath(os.fspath(root)))
    try:
        common = os.path.commonpath([normalized_path, normalized_root])
    except ValueError:
        return False
    return common == normalized_root


def _protected_output_roots(project_resolved):
    roots = [project_resolved / name for name in PROTECTED_OUTPUT_TREES]
    # The loaded scenario package (catalog and visible assets) is protected even
    # when it is outside the explicit fixture project root.
    roots.append(PACKAGE_ROOT)
    return roots


def _sha256(data):
    import hashlib

    return hashlib.sha256(data).hexdigest()


def ast_without_module_docstring(source_text):
    """Return an AST dump with only the module-level string docstring removed."""
    tree = ast.parse(source_text)
    if tree.body and isinstance(tree.body[0], ast.Expr):
        first = tree.body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            tree.body = tree.body[1:]
    return ast.dump(tree, include_attributes=False)


def neutralize_candidate(source_bytes):
    """Remove comments and only the module-level docstring; keep executable code.

    Comments do not affect execution. The module docstring is removed from the
    comment-stripped source using its parsed line span, so function docstrings
    and every other string are preserved.
    """
    try:
        text = source_bytes.decode("utf-8")
        tokens = []
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                continue
            tokens.append(token)
        without_comments = tokenize.untokenize(tokens)
    except (UnicodeDecodeError, tokenize.TokenError, IndentationError) as exc:
        raise ScenarioError(f"candidate source cannot be neutralized: {exc}") from exc

    try:
        tree = ast.parse(without_comments)
    except SyntaxError as exc:
        raise ScenarioError(f"candidate source does not parse: {exc}") from exc
    if tree.body and isinstance(tree.body[0], ast.Expr):
        first = tree.body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            lines = without_comments.splitlines(keepends=True)
            start = tree.body[0].lineno - 1
            end = tree.body[0].end_lineno
            without_comments = "".join(lines[:start] + lines[end:]).lstrip("\n")
    exported = without_comments.encode("utf-8")
    if ast_without_module_docstring(source_bytes.decode("utf-8")) != ast.dump(
        ast.parse(exported.decode("utf-8")), include_attributes=False
    ):
        raise ScenarioError("neutralized candidate changed executable structure")
    return exported


def _visible_template(name):
    path = VISIBLE_DIR / name
    _assert_no_reparse(path, "visible template")
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ScenarioError(f"visible template is unavailable: {name}: {exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _history_bytes(family, requirement_version, finding):
    entries = [
        {
            "kind": "fixture_origin",
            "text": (
                "This workspace is a reproducible authored fixture prepared for local "
                "FollowThrough experiments. It is not a record of a live model or tool run."
            ),
        },
        {
            "kind": "task_context",
            "text": (
                "Inspect the supplied candidate against the current visible requirements. "
                "Decide from observable behavior whether any change is needed."
            ),
        },
    ]
    if finding:
        entries.append({"kind": "authored_finding", "text": finding})
    return _json_bytes({
        "schema_version": SCHEMA_VERSION,
        "fixture_provenance": dict(FIXTURE_PROVENANCE),
        "requirement_version": requirement_version,
        "entries": entries,
    })


def _task_bytes(task_request, requirement_version, candidate_filename):
    return _text_bytes(
        "# Task handoff\n\n"
        f"{task_request}\n\n"
        "## Current requirement version\n\n"
        f"`{requirement_version}`\n\n"
        "## Supplied workspace files\n\n"
        f"- `{candidate_filename}` — the supplied candidate module\n"
        "- `SPEC.md` — current visible requirements\n"
        "- `WORKFLOW.md` — standing role and evidence rules\n"
        "- `HISTORY.json` — authored fixture history (not live model or tool output)\n"
        "- `public_checks.py` — original public smoke checks (not exhaustive)\n\n"
        "A passing smoke check is not proof of complete correctness. Report what you actually "
        "observed and do not claim checks you did not run.\n"
    )


def _controller_for(catalog, scenario_id, scenario, family, agent_files, source_info, exported_sha):
    events = []
    for event in scenario.get("events", ()):
        normalized = dict(event)
        if "requirements_template" in event:
            normalized["content"] = _visible_template(event["requirements_template"])
        if "receipt_template" in event:
            normalized["receipt_text"] = event["receipt_template"].format(
                candidate_sha256=exported_sha
            )
        events.append(normalized)
    controller = {
        "schema_version": SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "family": scenario["family"],
        "requirement_version": scenario["requirement_version"],
        "source": source_info,
        "agent_files": agent_files,
        "events": events,
        "fixture_provenance": dict(FIXTURE_PROVENANCE),
        "hidden_grading": dict(family["hidden_grading"]),
        "public_checks": {
            "file": "agent/public_checks.py",
            "evidence_kind": "trusted_local_control",
            "exhaustive": False,
        },
        "source_expectation": {
            "catalog_sha256": source_info["original_sha256"],
            "catalog_bytes": source_info["original_bytes"],
            "drift_check": "enforced_before_build",
            "oracle_label": "not_stored",
        },
        "workflow_roles": ["O1", "W1", "R1"],
        "visible_requirements_version": scenario["requirement_version"],
    }
    return controller


def _write_package(root, agent_files, controller):
    agent_dir = root / "agent"
    agent_dir.mkdir()
    for name, content in agent_files:
        (agent_dir / name).write_bytes(content)
    (root / "controller.json").write_bytes(_json_bytes(controller))


def _publish_stage(stage, output):
    """Rename a complete staging tree to the final output (no direct copies)."""
    last_error = None
    for _ in range(2):
        try:
            os.replace(stage, output)
            return
        except OSError as exc:
            last_error = exc
    raise last_error


def prepare_scenario(project_root, scenario_id, output_dir):
    """Prepare one deterministic scenario package and return its controller dict."""
    catalog = _load_catalog()
    if scenario_id not in catalog["scenarios"]:
        raise ScenarioError(f"unknown scenario ID: {scenario_id!r}")
    scenario = catalog["scenarios"][scenario_id]
    family_name = scenario.get("family")
    if family_name not in catalog["families"]:
        raise ScenarioError(f"scenario {scenario_id} names an unknown family: {family_name!r}")
    family = catalog["families"][family_name]

    root = Path(project_root)
    output = Path(output_dir)
    try:
        _assert_no_reparse(root, "project root")
        if not root.is_dir():
            raise ScenarioError(f"project root is not a directory: {root}")
        project_resolved = root.resolve(strict=True)
        source_path = scenario.get("source")
        _assert_relative_source(source_path)
        source_file = project_resolved / source_path
        _assert_no_reparse(source_file, "catalog input")
        if not source_file.is_file():
            raise ScenarioError(f"catalog input is missing or not a regular file: {source_path}")
    except (OSError, RuntimeError) as exc:
        raise ScenarioError(f"project root or catalog input is unsafe: {exc}") from exc

    try:
        output_parent = output.parent
        if not output_parent.is_dir():
            raise ScenarioError(f"output parent is not an existing directory: {output_parent}")
        _assert_no_reparse(output_parent, "output parent")
        output_resolved = output_parent.resolve(strict=True) / output.name
    except (OSError, RuntimeError) as exc:
        raise ScenarioError(f"output path is unsafe: {exc}") from exc
    if not output.name or output.name in (".", ".."):
        raise ScenarioError("output directory name is unsafe")
    if output_resolved.exists() or output_resolved.is_symlink():
        raise ScenarioError(f"output already exists: {output}")
    if output_resolved == project_resolved:
        raise ScenarioError("output directory must not be the project input root")
    for protected_root in _protected_output_roots(project_resolved):
        if _same_or_inside(output_resolved, protected_root):
            raise ScenarioError(
                "output directory must not overlap a protected source input tree "
                "or the loaded scenario asset tree"
            )

    original = source_file.read_bytes()
    if len(original) != scenario["source_bytes"] or _sha256(original) != scenario["source_sha256"]:
        raise ScenarioError(
            f"catalog source identity mismatch for {source_path}: expected bytes/sha256 "
            f"{scenario['source_bytes']}/{scenario['source_sha256']}"
        )

    exported = neutralize_candidate(original)
    exported_sha = _sha256(exported)
    exported_agent = [(family["candidate_filename"], exported)]
    exported_agent.append(("public_checks.py", _visible_template(family["public_checks"]).encode("utf-8")))
    exported_agent.append((
        "SPEC.md",
        _text_bytes(_visible_template(scenario["spec_template"])),
    ))
    exported_agent.append(("WORKFLOW.md", _text_bytes(_visible_template(family["workflow_template"]))))
    exported_agent.append((
        "TASK.md",
        _task_bytes(
            catalog["task_requests"][scenario["task_request"]],
            scenario["requirement_version"],
            family["candidate_filename"],
        ),
    ))
    exported_agent.append((
        "HISTORY.json",
        _history_bytes(
            family_name, scenario["requirement_version"], scenario.get("history_finding")
        ),
    ))
    exported_agent.sort(key=lambda item: item[0])
    agent_files = [
        {"path": name, "bytes": len(data), "sha256": _sha256(data)}
        for name, data in exported_agent
    ]
    source_info = {
        "catalog_path": source_path,
        "original_sha256": _sha256(original),
        "original_bytes": len(original),
        "exported_sha256": exported_sha,
        "exported_bytes": len(exported),
        "neutralization": "comments and module docstring removed; executable AST preserved",
    }
    controller = _controller_for(
        catalog, scenario_id, scenario, family, agent_files, source_info, exported_sha
    )

    temp = output_resolved.parent / (".scenario-build-" + output_resolved.name)
    if temp.exists() or temp.is_symlink():
        raise ScenarioError(f"owned staging path already exists and was not overwritten: {temp}")
    try:
        temp.mkdir()
        _write_package(temp, exported_agent, controller)
    except Exception as exc:
        if isinstance(exc, ScenarioError):
            raise
        raise ScenarioError(
            f"preparation failed; owned partial state retained at {temp}: {exc}"
        ) from exc
    try:
        _publish_stage(temp, output_resolved)
    except OSError as exc:
        raise ScenarioError(
            f"publish failed; complete package retained at owned staging path {temp}; "
            f"final output was not created: {exc}"
        ) from exc
    return controller


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare one FollowThrough scenario package.")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        controller = prepare_scenario(args.project_root, args.scenario, args.output)
    except ScenarioError as exc:
        print(f"scenario preparation refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "prepared",
        "scenario_id": controller["scenario_id"],
        "requirement_version": controller["requirement_version"],
        "agent_files": [entry["path"] for entry in controller["agent_files"]],
        "output": str(Path(args.output).resolve()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
