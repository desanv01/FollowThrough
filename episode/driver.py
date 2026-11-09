"""Offline scenario-to-episode driver.

Composes the accepted scenario preparer, package-to-config adapter and
synthetic role loop into one reusable entry point. This is an offline wiring
seam only: it never contacts a provider, runs candidate Python, invokes Docker,
grades, or claims a functional pass.

Importing this module performs no I/O.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys


from episode import EpisodeConfigError, run_episode
from episode.workspace import WorkspaceError, assert_no_reparse_chain
from scenarios.episode_config import ScenarioConfigError, build_episode_config


SCHEMA_VERSION = "episode-driver-0.1"
EVIDENCE_KIND = "synthetic_episode_control"
ENVELOPE_KEYS = {
    "schema_version",
    "evidence_kind",
    "status",
    "result",
    "paths",
    "error",
}
SMOKE_LIMITS = {
    "max_model_responses": 4,
    "max_tool_actions": 4,
    "max_elapsed_ms": 30000,
    "max_context_bytes": 60000,
}
SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
ARMS = ("ordinary", "structured_progress")
CONTROLLER_FAILURE_STATUSES = (
    "protocol_error",
    "infrastructure_error",
    "provider_error",
)


class DriverError(Exception):
    """Internal fixed-code driver failure."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _error(code):
    raise DriverError(code)


def _path_value(raw, code):
    if isinstance(raw, bool) or not isinstance(raw, (str, os.PathLike)):
        _error(code)
    try:
        value = os.fspath(raw)
    except TypeError:
        _error(code)
    if isinstance(value, bytes) or not value or "\x00" in value:
        _error(code)
    if os.name == "nt":
        normalized = value.replace("/", "\\")
        if (
            normalized.startswith("\\\\?\\")
            or normalized.startswith("\\\\.\\")
            or normalized.startswith("\\??\\")
        ):
            _error("unsupported_path_alias")
    path = Path(value)
    if not path.is_absolute():
        _error(code)
    return path


def _same_or_inside(path, root):
    normalized_path = os.path.normcase(os.path.abspath(os.fspath(path)))
    normalized_root = os.path.normcase(os.path.abspath(os.fspath(root)))
    try:
        common = os.path.commonpath([normalized_path, normalized_root])
    except ValueError:
        return False
    return common == normalized_root


def _load_prepare_module():
    # Deferred: scenarios.prepare probes its package path at import time. The
    # driver module itself must remain import-I/O free.
    from scenarios import prepare as prepare_module

    return prepare_module


def _validate_run_root(project_root, run_root):
    project = _path_value(project_root, "invalid_project_root")
    run = _path_value(run_root, "invalid_run_root")
    try:
        assert_no_reparse_chain(project)
        assert_no_reparse_chain(run.parent)
    except WorkspaceError:
        _error("run_root_unsafe")
    if not project.is_dir():
        _error("invalid_project_root")
    project_resolved = Path(os.path.realpath(project))
    parent = run.parent
    if not parent.is_dir():
        _error("run_root_parent_missing")
    parent_resolved = Path(os.path.realpath(parent))
    run_resolved = parent_resolved / run.name
    if not run.name or run.name in (".", ".."):
        _error("invalid_run_root")
    if run_resolved.exists() or run_resolved.is_symlink():
        _error("run_root_exists")
    try:
        prepare_module = _load_prepare_module()
    except Exception:
        _error("preparer_import_failed")
    # Reuse the accepted preparer's complete protected-output policy, including
    # its actual loaded PACKAGE_ROOT independently of the supplied fixture root.
    for protected_root in prepare_module._protected_output_roots(project_resolved):
        if prepare_module._same_or_inside(run_resolved, protected_root):
            _error("run_root_protected")
    if _same_or_inside(project_resolved, run_resolved):
        _error("run_root_overlaps_project")
    return project_resolved, run_resolved, prepare_module


def _envelope(status, result, package, episode, error):
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": EVIDENCE_KIND,
        "status": status,
        "result": result,
        "paths": {
            "package": None if package is None else str(package),
            "episode": None if episode is None else str(episode),
        },
        "error": error,
    }


def _bounded_error(exc, prepare_module=None):
    if isinstance(exc, DriverError):
        return exc.code
    if isinstance(exc, ScenarioConfigError):
        return str(exc)
    if isinstance(exc, EpisodeConfigError):
        return str(exc)
    if isinstance(exc, WorkspaceError):
        return exc.code
    if prepare_module is not None and isinstance(exc, prepare_module.ScenarioError):
        return "preparation_failed"
    return "driver_internal_error"


def _write_driver_record(run_root, envelope):
    try:
        target = Path(run_root) / "driver.json"
        target.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def run_scenario(
    project_root,
    scenario_id,
    *,
    run_root,
    episode_id,
    arm,
    limits,
    model_port,
    check_port,
    check_evidence_kind="synthetic_check",
    evidence_kind=EVIDENCE_KIND,
    cancelled=None,
    monotonic=None,
):
    """Prepare and run injected host ports; default CLI evidence stays synthetic.

    The trusted live composition supplies its provenance explicitly. This label
    records the caller's execution context; it does not authenticate a provider.
    """
    if evidence_kind not in (EVIDENCE_KIND, "real_provider_episode"):
        return _envelope("preparation_error", None, None, None, "invalid_evidence_kind")
    def execution_envelope(*args):
        envelope = _envelope(*args)
        envelope["evidence_kind"] = evidence_kind
        return envelope

    if scenario_id not in SCENARIOS:
        return execution_envelope("preparation_error", None, None, None, "invalid_scenario")
    if arm not in ARMS:
        return execution_envelope("preparation_error", None, None, None, "invalid_arm")
    if not callable(model_port) or not callable(check_port):
        return execution_envelope("preparation_error", None, None, None, "ports_not_callable")
    if cancelled is not None and not callable(cancelled):
        return execution_envelope("preparation_error", None, None, None, "cancelled_not_callable")
    if monotonic is not None and not callable(monotonic):
        return execution_envelope("preparation_error", None, None, None, "monotonic_not_callable")

    try:
        project_resolved, run_resolved, prepare_module = _validate_run_root(
            project_root, run_root
        )
    except DriverError as exc:
        return execution_envelope("preparation_error", None, None, None, exc.code)

    try:
        os.mkdir(run_resolved)
    except OSError:
        return execution_envelope("preparation_error", None, None, None, "run_root_create_failed")

    package_path = run_resolved / "package"
    episode_path = run_resolved / "episode"
    try:
        prepare_module.prepare_scenario(project_resolved, scenario_id, package_path)
    except Exception as exc:
        code = _bounded_error(exc, prepare_module)
        package = package_path if package_path.exists() else None
        envelope = execution_envelope("preparation_error", None, package, None, code)
        _write_driver_record(run_resolved, envelope)
        return envelope

    try:
        config = build_episode_config(
            package_path,
            episode_id=episode_id,
            arm=arm,
            output_dir=episode_path,
            limits=limits,
        )
        config["check_evidence_kind"] = check_evidence_kind
    except Exception as exc:
        code = _bounded_error(exc)
        envelope = execution_envelope("preparation_error", None, package_path, None, code)
        _write_driver_record(run_resolved, envelope)
        return envelope

    try:
        result = run_episode(
            config,
            model_port=model_port,
            check_port=check_port,
            cancelled=cancelled,
            monotonic=monotonic,
        )
    except Exception as exc:
        code = _bounded_error(exc)
        episode = episode_path if episode_path.exists() else None
        envelope = execution_envelope("preparation_error", None, package_path, episode, code)
        _write_driver_record(run_resolved, envelope)
        return envelope

    envelope = execution_envelope(
        "episode_returned", result, package_path, episode_path, None
    )
    envelope["evidence_kind"] = evidence_kind
    if not _write_driver_record(run_resolved, envelope):
        return execution_envelope(
            "preparation_error", None, package_path, episode_path, "driver_record_failed"
        )
    return envelope


class _SingleFinishModelPort:
    """Exactly one scripted O1 finish(partial); no model/provider is involved."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if self.calls != 1:
            raise RuntimeError("synthetic model port has no further action")
        return {
            "action": {
                "name": "finish",
                "claimed_status": "partial",
                "summary": (
                    "Synthetic wiring smoke: O1 returned a partial claim after one "
                    "scripted action. This is not a functional pass or benchmark result."
                ),
                "evidence_ids": [],
                "remaining": ["Synthetic wiring smoke only; no task evaluation was performed."],
            },
            "usage": {"input_tokens": None, "output_tokens": None},
            "requested_model": None,
            "returned_model": None,
        }


def _refuse_check(request):
    raise RuntimeError("synthetic check port refuses unexpected use")


def _smoke_exit(envelope):
    if envelope["status"] != "episode_returned":
        return 1
    result = envelope.get("result")
    if not isinstance(result, dict):
        return 1
    if result.get("status") in CONTROLLER_FAILURE_STATUSES:
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run one offline synthetic episode driver smoke.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--synthetic-smoke", action="store_true")
    args = parser.parse_args(argv)
    if not args.synthetic_smoke:
        print(
            "refusing to run: --synthetic-smoke is required; this command only "
            "executes a one-action synthetic wiring smoke and does not contact a provider.",
            file=sys.stderr,
        )
        return 2

    model_port = _SingleFinishModelPort()
    envelope = run_scenario(
        args.project_root,
        args.scenario,
        run_root=args.run_root,
        episode_id=args.episode_id,
        arm=args.arm,
        limits=dict(SMOKE_LIMITS),
        model_port=model_port,
        check_port=_refuse_check,
    )
    print(json.dumps({
        "notice": (
            "Synthetic wiring smoke only. No provider was contacted, no candidate "
            "Python was executed, and a partial claim is not a functional pass."
        ),
        "driver": envelope,
    }, ensure_ascii=False, sort_keys=True))
    return _smoke_exit(envelope)


if __name__ == "__main__":
    sys.exit(main())
