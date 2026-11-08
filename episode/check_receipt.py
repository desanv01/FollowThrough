"""Project trusted synthetic executor receipts to bounded check-port-0.1 data.

This is the offline portion of P3.6.3.  The caller (future Main-owned integration)
launches a qualified executor, binds a fresh trusted run ID to the check request and
its requirements version, and supplies separately approved public checker output.
This module reads no files, invokes nothing, accepts only
``synthetic_executor_control`` evidence and emits only ``synthetic_check`` data with
no host paths, command records, tokens or raw diagnostics.
"""

from __future__ import annotations

import os
import re


CHECK_PORT_SCHEMA = "check-port-0.1"
RECEIPT_SCHEMA = "container-check-0.1"
SYNTHETIC_EVIDENCE = "synthetic_executor_control"
OUTPUT_LIMIT_BYTES = 32768
CANDIDATE_FILENAMES = ("processor.py", "preview.py")
CHECK_STATUSES = (
    "passed", "failed", "timeout", "output_limit", "cancelled",
    "infrastructure_error",
)
_RECEIPT_STATUS_MAP = {
    "check_passed": "passed",
    "check_failed": "failed",
    "timeout": "timeout",
    "output_limit": "output_limit",
    "cancelled": "cancelled",
    "refused": "infrastructure_error",
    "infrastructure_error": "infrastructure_error",
}
_PASS_FAIL_RECEIPT_STATUSES = ("check_passed", "check_failed")
_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SHA_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_KEYS = frozenset({
    "request_id", "workspace", "candidate_filename", "candidate_sha256",
    "requirements_version",
})


class CheckReceiptProjectionError(Exception):
    """Fixed malformed-request error with no raw receipt or host metadata."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _valid_run_id(value):
    return (
        type(value) is str
        and 0 < len(value) <= 128
        and all(33 <= ord(char) <= 126 for char in value)
    )


def _validated_request(request):
    if type(request) is not dict or set(request) != _REQUEST_KEYS:
        raise CheckReceiptProjectionError("invalid_check_request")
    request_id = request["request_id"]
    if type(request_id) is not str or not _ID_PATTERN.fullmatch(request_id):
        raise CheckReceiptProjectionError("invalid_check_request")
    workspace = request["workspace"]
    if (
        type(workspace) is not str
        or not workspace
        or "\x00" in workspace
        or not os.path.isabs(workspace)
    ):
        raise CheckReceiptProjectionError("invalid_check_request")
    if request["candidate_filename"] not in CANDIDATE_FILENAMES:
        raise CheckReceiptProjectionError("invalid_check_request")
    if (
        type(request["candidate_sha256"]) is not str
        or not _SHA_PATTERN.fullmatch(request["candidate_sha256"])
    ):
        raise CheckReceiptProjectionError("invalid_check_request")
    if request["requirements_version"] not in ("v1", "v2"):
        raise CheckReceiptProjectionError("invalid_check_request")
    return request


def _display_text(value):
    if type(value) is not str:
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(encoded) > OUTPUT_LIMIT_BYTES:
        return None
    return value


def _infrastructure_response(request):
    return {
        "schema_version": CHECK_PORT_SCHEMA,
        "request_id": request["request_id"],
        "evidence_kind": "synthetic_check",
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": "infrastructure_error",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "cleanup": "unknown",
    }


def _commands_settled(commands):
    if type(commands) is not list:
        return False
    for command in commands:
        if type(command) is not dict:
            return False
        spawned = command.get("spawn_failed")
        reaped = command.get("direct_child_reaped")
        reader = command.get("reader_settled")
        if type(spawned) is not bool or type(reaped) is not bool or type(reader) is not bool:
            return False
        if not spawned and (not reaped or not reader):
            return False
    return True


def _cleanup_state(receipt):
    cleanup = receipt.get("cleanup")
    if type(cleanup) is not dict:
        return None
    state = cleanup.get("state")
    method = cleanup.get("method")
    verified = cleanup.get("removal_verified")
    if type(state) is not str or type(method) is not str or type(verified) is not bool:
        return None
    container = receipt.get("container")
    if state == "unknown":
        return "unknown"
    if state != "settled":
        return None
    if method == "not_created":
        return "settled" if container is None else None
    if not verified:
        return None
    if type(container) is not dict or not container.get("id") or type(
        container.get("id")
    ) is not str:
        return None
    return "settled"


def _pass_fail_consistent(receipt, status, exit_code):
    if exit_code is None:
        return False
    if status == "check_passed" and exit_code != 0:
        return False
    if status == "check_failed" and (type(exit_code) is not int or exit_code == 0):
        return False
    container = receipt.get("container")
    attached = container.get("attached") if type(container) is dict else None
    if type(attached) is not dict:
        return False
    attached_exit = attached.get("exit_code")
    if type(attached_exit) is not int:
        return False
    # The pinned executor classifier accepts the attach CLI exit 0 or the
    # inspected checker exit. Preserve that rule; other mismatches are refused.
    if attached_exit not in (0, exit_code):
        return False
    if attached.get("direct_child_reaped") is not True:
        return False
    if type(receipt.get("commands")) is not list or not receipt["commands"]:
        return False
    return True


def _valid_receipt(request, receipt, expected_run_id):
    """Return mapped status/cleanup/exit or None for an invalid/mismatched receipt."""
    if type(receipt) is not dict:
        return None
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        return None
    if receipt.get("evidence_kind") != SYNTHETIC_EVIDENCE:
        return None
    if receipt.get("run_id") != expected_run_id:
        return None
    workspace = receipt.get("workspace")
    if type(workspace) is not dict:
        return None
    if workspace.get("candidate_file") != request["candidate_filename"]:
        return None
    if workspace.get("candidate_sha256") != request["candidate_sha256"]:
        return None
    status = receipt.get("status")
    if type(status) is not str or status not in _RECEIPT_STATUS_MAP:
        return None
    exit_code = receipt.get("public_check_exit")
    if exit_code is not None and type(exit_code) is not int:
        return None
    cleanup = _cleanup_state(receipt)
    if cleanup is None:
        return None
    if not _commands_settled(receipt.get("commands")):
        return None
    mapped = _RECEIPT_STATUS_MAP[status]
    if mapped in ("passed", "failed"):
        passed_or_failed = _pass_fail_consistent(receipt, status, exit_code)
        if not passed_or_failed or cleanup != "settled":
            return None
        return (mapped, cleanup, exit_code)
    return (mapped, cleanup, exit_code)


def project_synthetic_receipt(
    request,
    receipt,
    *,
    expected_run_id,
    public_stdout="",
    public_stderr="",
):
    """Bind a trusted synthetic executor receipt to a check-port request.

    Malformed requests raise a fixed ``CheckReceiptProjectionError``. Invalid,
    mismatched, production-labelled or unbounded-display receipts fail closed as an
    ``infrastructure_error`` with unknown cleanup and empty output.
    """
    request = _validated_request(request)
    if not _valid_run_id(expected_run_id):
        raise CheckReceiptProjectionError("invalid_expected_run_id")
    stdout = _display_text(public_stdout)
    stderr = _display_text(public_stderr)
    if stdout is None or stderr is None:
        return _infrastructure_response(request)
    projected = _valid_receipt(request, receipt, expected_run_id)
    if projected is None:
        return _infrastructure_response(request)
    status, cleanup, exit_code = projected
    return {
        "schema_version": CHECK_PORT_SCHEMA,
        "request_id": request["request_id"],
        "evidence_kind": "synthetic_check",
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "cleanup": cleanup,
    }


__all__ = [
    "CHECK_PORT_SCHEMA",
    "CheckReceiptProjectionError",
    "OUTPUT_LIMIT_BYTES",
    "RECEIPT_SCHEMA",
    "SYNTHETIC_EVIDENCE",
    "project_synthetic_receipt",
]
