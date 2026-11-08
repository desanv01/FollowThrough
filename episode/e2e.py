"""P4 R1 end-to-end runner with an explicit reviewed episode identity.

Composes the accepted episode driver, an injected check port and an injected
host grade callback, then projects the result through the reviewed runtime row
reporting.  It performs no provider, credential, Docker, candidate-host or
network operation itself; ``episode.live`` supplies the bounded ports.

P4-owned seam for P3: ``run_r1_smoke(..., episode_id=...)`` preserves the
reviewed ID instead of hardcoding the scripted smoke identity.  Offline
synthetic/default checks are labelled; a real grade must bind candidate and
requirements version or the row is excluded as invalid.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from episode import driver as driver_module
from reporting.report import build_report, render_markdown
from reporting import progress as progress_module
from reporting import runtime_projection

SNAPSHOT_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCHEMA = "check-port-0.1"
_EID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
LIMITS = {
    "max_model_responses": 16,
    "max_tool_actions": 16,
    "max_elapsed_ms": 600000,
    "max_context_bytes": 60000,
}


class E2EError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _valid_episode_id(value):
    if type(value) is not str or _EID.fullmatch(value) is None:
        raise E2EError("invalid_episode_id")
    return value


def _synthetic_check(request):
    return {
        "schema_version": CHECK_SCHEMA,
        "request_id": request["request_id"],
        "evidence_kind": "synthetic_check",
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": "passed",
        "exit_code": 0,
        "stdout": "authored public checks passed\n",
        "stderr": "",
        "cleanup": "settled",
    }


def scripted_r1_model_port(package_agent, correct_source):
    """Authored R1 model script; never used as provider evidence."""
    state = {"initial": None}
    correct = _sha(correct_source)
    sequence = []

    def build():
        initial = state["initial"]
        return [
            {"name": "delegate", "target": "W1", "task": "Implement the fix.",
             "claimed_source_sha256": initial,
             "claimed_requirements_version": "v1"},
            {"name": "read", "filename": "processor.py"},
            {"name": "write", "filename": "processor.py",
             "expected_sha256": initial, "content": correct_source},
            {"name": "check"},
            {"name": "report", "summary": "Implementation complete.",
             "evidence_ids": ["authored-check"]},
            {"name": "request_review", "target": "R1",
             "task": "Review the current candidate.",
             "claimed_source_sha256": correct,
             "claimed_requirements_version": "v1"},
            {"name": "check"},
            {"name": "review", "disposition": "approve",
             "summary": "Authored review approves the fix.",
             "findings": [], "evidence_ids": ["authored-check"],
             "claimed_source_sha256": correct,
             "claimed_requirements_version": "v1"},
            {"name": "finish", "claimed_status": "complete",
             "summary": "Implemented, checked and reviewed.",
             "evidence_ids": ["authored-check"], "remaining": []},
        ]

    def port(request):
        nonlocal sequence
        if state["initial"] is None:
            state["initial"] = _sha(Path(package_agent, "processor.py").read_bytes())
            sequence = build()
        action = sequence.pop(0)
        return {
            "action": action,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "requested_model": "stub-requested",
            "returned_model": "stub-returned",
        }

    return port


def _events_from_checks(receipts, result):
    events = [{"type": "episode_start", "role": "controller", "elapsed_ms": 0}]
    for receipt in receipts:
        events.append({
            "type": "check_result",
            "role": "W1",
            "status": receipt.get("status") if isinstance(receipt, dict) else None,
            "cleanup": receipt.get("cleanup") if isinstance(receipt, dict) else None,
            "candidate_sha256": receipt.get("candidate_sha256")
            if isinstance(receipt, dict) else None,
            "requirements_version": receipt.get("requirements_version")
            if isinstance(receipt, dict) else None,
        })
    if result.get("final_claim"):
        events.append({"type": "finish_claim", "role": "O1",
                       "elapsed_ms": result.get("elapsed_ms") or 0})
    return events


def _safe_progress_events(run_root):
    """Project driver events to bounded display fields only; never raw text."""
    path = Path(run_root) / "episode" / "events.jsonl"
    if not path.is_file():
        return []
    events = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(event, dict):
                    continue
                safe = {}
                for key in ("type", "role", "status", "action", "elapsed_ms",
                            "evidence_id"):
                    value = event.get(key)
                    if isinstance(value, (str, int)) and not isinstance(value, bool):
                        safe[key] = value
                if isinstance(safe.get("type"), str):
                    events.append(safe)
                if len(events) >= 100000:
                    break
    except OSError:
        return []
    return events


def _write_progress(run_root, result, episode_id, stage, evidence_kind,
                    source, operation):
    events = _safe_progress_events(run_root)
    event_path = Path(run_root) / "progress-events.jsonl"
    with event_path.open("w", encoding="utf-8", newline="") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    operation = operation if isinstance(operation, dict) else {}
    meta = {
        "episode_id": episode_id,
        "run_id": operation.get("run_id"),
        "scenario_id": result.get("scenario_id"),
        "arm": result.get("arm"),
        "stage": stage,
        "evidence_kind": evidence_kind,
        "source_sha256": source.get("candidate_sha256"),
        "requirements_version": source.get("requirements_version"),
        "events_path": str(event_path),
        "result_path": str(Path(run_root) / "report-input.json"),
        "report_path": str(Path(run_root) / "report.md"),
        "estimated_cost_micro_usd": operation.get("estimated_cost_micro_usd"),
        "reserved_micro_usd": operation.get("reserved_micro_usd"),
        "cleanup": (result.get("cleanup") if isinstance(result, dict) else None),
        "status": (result.get("status") if isinstance(result, dict) else None),
    }
    (Path(run_root) / "progress-meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state = progress_module.summarize_progress(meta, events, result)
    (Path(run_root) / "progress.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (Path(run_root) / "progress.txt").write_text(
        progress_module.render_progress(state) + "\n", encoding="utf-8"
    )
    return state


def run_r1_smoke(run_root, *, episode_id, check_port=None, observations=None,
                 evidence_kind=None, model_port=None,
                 grade_fn=None, stage=None, trial=1, operation=None,
                 dataset_id="ds-end-to-end-r1"):
    """Run one R1 episode with a reviewed identity and build a report row.

    ``grade_fn(candidate_sha256, candidate_path)`` is required; this seam never
    invents a protected grade.  ``check_port`` may be synthetic or the trusted
    bridge.  When labels are omitted, a protected grade is reported as
    ``real_provider`` at development stage; a synthetic grade as
    ``synthetic_development``/development.  The returned report row binds
    result/source/grade identities and is invalid/excluded on mismatch.
    """
    episode_id = _valid_episode_id(episode_id)
    if grade_fn is None or not callable(grade_fn):
        raise E2EError("grade_required")
    run_root = Path(run_root)
    package = run_root / "package"
    agent = package / "agent"
    correct_source = (SNAPSHOT_ROOT / "references" / "retry-processing" /
                      "correct" / "processor.py").read_text(encoding="utf-8")
    if model_port is None:
        model_port = scripted_r1_model_port(agent, correct_source)
    receipts = []

    def capture_check(request):
        receipt = check_port(request) if check_port is not None else _synthetic_check(request)
        if isinstance(receipt, dict):
            receipts.append(receipt)
        return receipt

    envelope = driver_module.run_scenario(
        SNAPSHOT_ROOT,
        "R1",
        run_root=run_root,
        episode_id=episode_id,
        arm="ordinary",
        limits=dict(LIMITS),
        model_port=model_port,
        check_port=capture_check,
    )
    if envelope.get("status") != "episode_returned":
        return {"envelope": envelope, "episode_id": episode_id,
                "grade": None, "report": None, "row": None, "binding": None}
    result = envelope["result"]
    if result.get("episode_id") != episode_id:
        raise E2EError("episode_identity_not_preserved")
    candidate = package / "agent" / "processor.py"
    candidate_sha = _sha(candidate.read_bytes())
    grade = grade_fn(candidate_sha, candidate)
    if not isinstance(grade, dict):
        raise E2EError("invalid_grade")
    if evidence_kind is None:
        evidence_kind = (
            "real_provider"
            if grade.get("evidence_kind") == "protected_container_grading"
            else "synthetic_development"
        )
    if stage is None:
        stage = "development"
    source = {
        "candidate_sha256": result.get("current_candidate_sha256"),
        "requirements_version": result.get("current_requirements_version"),
    }
    events = _events_from_checks(receipts, result)
    row, binding = runtime_projection.project_episode(
        result,
        events,
        grade,
        source=source,
        evidence_kind=evidence_kind,
        stage=stage,
        trial=trial,
        operation=operation,
    )
    report_input = runtime_projection.project_report_input(dataset_id, [row])
    report = build_report(report_input)
    binding["episode_id"] = episode_id
    binding["run_id"] = (operation or {}).get("run_id") or result.get("run_id")
    binding["operation_id"] = (operation or {}).get("operation_id")
    for name, payload in (
        ("report-input.json", report_input),
        ("report.json", report),
        ("protected-grade.json", grade),
        ("episode-binding.json", binding),
    ):
        (run_root / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    (run_root / "report.md").write_text(render_markdown(report), encoding="utf-8")
    progress_state = _write_progress(
        run_root, result, episode_id, stage, evidence_kind, source, operation
    )
    return {
        "envelope": envelope,
        "episode_id": episode_id,
        "grade": grade,
        "report": report,
        "row": row,
        "binding": binding,
        "result": result,
        "candidate_sha256": candidate_sha,
        "progress": progress_state,
    }


__all__ = ["E2EError", "LIMITS", "run_r1_smoke", "scripted_r1_model_port"]
