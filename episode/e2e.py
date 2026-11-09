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


def _report_events(run_root, receipts, result, live):
    """Use the bounded actual event stream in live mode; synthetic stays authored."""
    if live:
        return _safe_progress_events(run_root)
    return _events_from_checks(receipts, result)


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
                            "evidence_id", "cleanup", "candidate_sha256",
                            "requirements_version"):
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
                    source, operation, status_override=None, cleanup="unknown"):
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
        "cleanup": cleanup,
        "status": status_override or (
            result.get("status") if isinstance(result, dict) else None
        ),
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


def run_scenario_smoke(run_root, *, episode_id, scenario_id="R1",
                       arm="ordinary", check_port=None, model_port=None,
                       grade_fn=None, observations=None, evidence_kind=None,
                       stage=None, trial=1, operation=None,
                       dataset_id="ds-end-to-end-r1", live=False,
                       check_evidence_kind=None):
    """Run one scenario/arm episode with required ports and explicit identity.

    ``run_r1_smoke`` is the preserved R1/ordinary entry point. This generic form
    is for the six-scenario/two-arm composition; it never selects an authored
    port or grade and never fills a missing outcome. Live mode requires an
    explicit trusted real-container check provenance; synthetic mode rejects it.
    """
    episode_id = _valid_episode_id(episode_id)
    if scenario_id not in runtime_projection.SCENARIOS:
        raise E2EError("invalid_scenario")
    if arm not in runtime_projection.ARMS:
        raise E2EError("invalid_arm")
    if grade_fn is None or not callable(grade_fn):
        raise E2EError("grade_required")
    if model_port is None or not callable(model_port):
        raise E2EError("actual_model_port_required")
    if check_port is None or not callable(check_port):
        raise E2EError("actual_check_port_required")
    if check_evidence_kind is None:
        check_evidence_kind = "synthetic_check"
    if check_evidence_kind not in ("synthetic_check", "real_container_check"):
        raise E2EError("invalid_check_evidence_kind")
    if live and check_evidence_kind != "real_container_check":
        raise E2EError("live_check_provenance_required")
    if not live and check_evidence_kind != "synthetic_check":
        raise E2EError("synthetic_check_provenance_required")
    operation = operation if isinstance(operation, dict) else {}
    if live:
        if operation.get("evidence_kind") != "real_provider":
            raise E2EError("live_operation_provenance_required")
        if not isinstance(operation.get("operation_id"), str) or not operation["operation_id"].strip():
            raise E2EError("live_operation_id_required")
        if not isinstance(operation.get("run_id"), str) or not operation["run_id"].strip():
            raise E2EError("live_run_id_required")
    if evidence_kind == "real_provider" and not live:
        raise E2EError("live_mode_required_for_real_provider")
    run_root = Path(run_root)
    receipts = []

    def capture_check(request):
        receipt = check_port(request)
        if isinstance(receipt, dict):
            receipts.append(receipt)
        return receipt

    envelope = driver_module.run_scenario(
        SNAPSHOT_ROOT,
        scenario_id,
        run_root=run_root,
        episode_id=episode_id,
        arm=arm,
        limits=dict(LIMITS),
        model_port=model_port,
        check_port=capture_check,
        check_evidence_kind=check_evidence_kind,
        evidence_kind=("real_provider_episode" if live else "synthetic_episode_control"),
    )
    if envelope.get("status") != "episode_returned":
        return {"envelope": envelope, "episode_id": episode_id,
                "grade": None, "report": None, "row": None, "binding": None,
                "grade_started": False}
    result = envelope["result"]
    if result.get("episode_id") != episode_id:
        raise E2EError("episode_identity_not_preserved")
    filename = ("preview.py" if scenario_id in ("C1", "C2", "C3")
                else "processor.py")
    candidate = run_root / "package" / "agent" / filename
    candidate_sha = _sha(candidate.read_bytes())
    source = {
        "candidate_sha256": result.get("current_candidate_sha256"),
        "requirements_version": result.get("current_requirements_version"),
    }
    events = _report_events(run_root, receipts, result, live)
    reason = None
    if not receipts:
        reason = "check_receipt_missing"
    elif any(
        not isinstance(receipt, dict) or receipt.get("cleanup") != "settled"
        for receipt in receipts
    ):
        reason = "check_cleanup_unknown"
    if reason is not None:
        provenance = operation.get("evidence_kind")
        if provenance not in runtime_projection.EVIDENCE_KINDS:
            provenance = "synthetic_development"
        stage_name = operation.get("stage") or stage or "development"
        row, binding = runtime_projection.project_unscored_episode(
            result, events, source=source, evidence_kind=provenance,
            stage=stage_name, trial=trial, operation=operation, reason=reason,
        )
        report_input = runtime_projection.project_report_input(dataset_id, [row])
        report = build_report(report_input)
        for name, payload in (
            ("report-input.json", report_input),
            ("report.json", report),
            ("episode-binding.json", binding),
            ("grade-skipped.json", {"reason": reason, "grade_started": False}),
        ):
            (run_root / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2,
                           sort_keys=True) + "\n", encoding="utf-8")
        (run_root / "report.md").write_text(render_markdown(report), encoding="utf-8")
        progress_state = _write_progress(
            run_root, result, episode_id, stage_name, provenance, source,
            operation, status_override="infrastructure_error",
        )
        return {
            "envelope": envelope, "episode_id": episode_id, "grade": None,
            "grade_started": False, "grade_skipped_reason": reason,
            "report": report, "row": row, "binding": binding, "result": result,
            "candidate_sha256": candidate_sha, "progress": progress_state,
        }
    grade_started = True
    grade = grade_fn(candidate_sha, candidate)
    if not isinstance(grade, dict):
        raise E2EError("invalid_grade")
    grade_is_protected = (
        grade.get("evidence_kind") == "protected_container_grading"
    )
    if live and not grade_is_protected:
        raise E2EError("live_grade_provenance_mismatch")
    if not live and grade_is_protected:
        raise E2EError("live_mode_required_for_real_provider")
    if evidence_kind is None:
        operation_provenance = operation.get("evidence_kind")
        if operation_provenance in runtime_projection.EVIDENCE_KINDS:
            evidence_kind = operation_provenance
        elif grade_is_protected:
            evidence_kind = "real_provider"
        else:
            evidence_kind = "synthetic_development"
    if evidence_kind == "real_provider" and not grade_is_protected:
        raise E2EError("real_provider_grade_required")
    if stage is None:
        stage = operation.get("stage") or "development"
    if live and evidence_kind != "real_provider":
        raise E2EError("live_grade_provenance_mismatch")
    events = _report_events(run_root, receipts, result, live)
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
        run_root, result, episode_id, stage, evidence_kind, source, operation,
        cleanup=("settled" if grade.get("cleanup") == "settled" else "unknown"),
    )
    return {
        "envelope": envelope,
        "episode_id": episode_id,
        "grade": grade,
        "grade_started": True,
        "report": report,
        "row": row,
        "binding": binding,
        "result": result,
        "candidate_sha256": candidate_sha,
        "progress": progress_state,
    }


def run_r1_smoke(run_root, *, episode_id, check_port=None, model_port=None,
                 grade_fn=None, observations=None, evidence_kind=None,
                 stage=None, trial=1, operation=None,
                 dataset_id="ds-end-to-end-r1", live=False,
                 check_evidence_kind=None):
    """Preserved R1/ordinary entry point backed by the generic composition."""
    return run_scenario_smoke(
        run_root, episode_id=episode_id, scenario_id="R1", arm="ordinary",
        check_port=check_port, model_port=model_port, grade_fn=grade_fn,
        observations=observations, evidence_kind=evidence_kind, stage=stage,
        trial=trial, operation=operation, dataset_id=dataset_id, live=live,
        check_evidence_kind=check_evidence_kind,
    )


__all__ = ["E2EError", "LIMITS", "run_r1_smoke", "run_scenario_smoke",
           "scripted_r1_model_port"]
