"""Trusted host bridge from real ``container-check-0.1`` receipts to check-port.

This module is host-only. It invokes the reviewed ``container-check`` executable
with a fresh run ID and a private output root, binds the run ID, candidate SHA-256
and requirements version, bounds the visible output, and projects only a
``real_container_check`` check-port response. Synthetic receipts remain handled by
``episode.check_receipt`` with their original label.

No evaluated agent imports or calls this module. A candidate stdout claim is data.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .check_receipt import (
    CHECK_PORT_SCHEMA,
    OUTPUT_LIMIT_BYTES,
    RECEIPT_SCHEMA,
    CheckReceiptProjectionError,
    _cleanup_state,
    _commands_settled,
    _display_text,
    _pass_fail_consistent,
    _validated_request,
)

REAL_EVIDENCE = "real_container_check"
RECEIPT_LIMIT_BYTES = 1024 * 1024
SUPPORTED_STATUSES = {
    "check_passed": "passed",
    "check_failed": "failed",
    "timeout": "timeout",
    "output_limit": "output_limit",
    "cancelled": "cancelled",
    "refused": "infrastructure_error",
    "infrastructure_error": "infrastructure_error",
}
_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_RUN_RE = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


class CheckBridgeError(Exception):
    """Fixed bounded host-bridge failure (no host paths or raw diagnostics)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass
class BridgeProcessResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    spawn_failed: bool = False


def _infrastructure(request):
    return {
        "schema_version": CHECK_PORT_SCHEMA,
        "request_id": request["request_id"],
        "evidence_kind": REAL_EVIDENCE,
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": "infrastructure_error",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "cleanup": "unknown",
    }


def project_real_receipt(
    request,
    receipt,
    *,
    expected_run_id,
    expected_requirements_version,
    public_stdout="",
    public_stderr="",
    expected_inventory=None,
    process_returncode=None,
):
    """Bind a real receipt to a validated check request.

    Only ``real_container_check`` evidence is accepted. Run ID, candidate filename
    and SHA-256, requirements version, request-time inventory, cleanup, command
    settlement, status/exit correlation, executor process exit and bounded display
    output must all match.
    """
    request = _validated_request(request)
    if type(expected_run_id) is not str or not _RUN_RE.fullmatch(expected_run_id):
        raise CheckBridgeError("invalid_expected_run_id")
    if expected_requirements_version not in ("v1", "v2"):
        raise CheckBridgeError("invalid_expected_version")
    if expected_requirements_version != request["requirements_version"]:
        return _infrastructure(request)
    stdout = _display_text(public_stdout)
    stderr = _display_text(public_stderr)
    if stdout is None or stderr is None:
        return _infrastructure(request)
    if type(receipt) is not dict:
        return _infrastructure(request)
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        return _infrastructure(request)
    if receipt.get("evidence_kind") != REAL_EVIDENCE:
        return _infrastructure(request)
    if receipt.get("run_id") != expected_run_id:
        return _infrastructure(request)
    workspace = receipt.get("workspace")
    if type(workspace) is not dict:
        return _infrastructure(request)
    if workspace.get("candidate_file") != request["candidate_filename"]:
        return _infrastructure(request)
    if workspace.get("candidate_sha256") != request["candidate_sha256"]:
        return _infrastructure(request)
    if expected_inventory is not None:
        actual = workspace.get("inventory")
        if type(actual) is not list or type(expected_inventory) is not list:
            return _infrastructure(request)
        normalized = [
            {"name": item.get("name"), "bytes": item.get("bytes"),
             "sha256": item.get("sha256")}
            for item in actual if type(item) is dict
        ]
        if normalized != expected_inventory:
            return _infrastructure(request)
    status = receipt.get("status")
    if type(status) is not str or status not in SUPPORTED_STATUSES:
        return _infrastructure(request)
    exit_code = receipt.get("public_check_exit")
    if exit_code is not None and type(exit_code) is not int:
        return _infrastructure(request)
    cleanup = _cleanup_state(receipt)
    if cleanup is None:
        return _infrastructure(request)
    if not _commands_settled(receipt.get("commands")):
        return _infrastructure(request)
    mapped = SUPPORTED_STATUSES[status]
    if mapped in ("passed", "failed"):
        if not _pass_fail_consistent(receipt, status, exit_code):
            return _infrastructure(request)
        if cleanup != "settled":
            return _infrastructure(request)
        if process_returncode is not None:
            expected_code = 0 if mapped == "passed" else 1
            if process_returncode != expected_code:
                return _infrastructure(request)
    elif process_returncode is not None and process_returncode != 2:
        return _infrastructure(request)
    return {
        "schema_version": CHECK_PORT_SCHEMA,
        "request_id": request["request_id"],
        "evidence_kind": REAL_EVIDENCE,
        "candidate_sha256": request["candidate_sha256"],
        "requirements_version": request["requirements_version"],
        "status": mapped,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "cleanup": cleanup,
    }


def _read_bounded(path, limit):
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return b""
    return data[:limit]


def _workspace_inventory(workspace):
    entries = []
    for path in sorted(Path(workspace).iterdir(), key=lambda item: item.name):
        try:
            metadata = path.lstat()
        except OSError:
            return None
        if path.is_symlink() or not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        entries.append({"name": path.name, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    return entries


def _sanitized_environment():
    allowed = ["PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR"]
    env = {}
    for key in allowed:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _default_runner(argv, timeout):
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=_sanitized_environment(),
            check=False,
        )
        return BridgeProcessResult(
            completed.returncode,
            completed.stdout[-OUTPUT_LIMIT_BYTES:],
            completed.stderr[-OUTPUT_LIMIT_BYTES:],
        )
    except subprocess.TimeoutExpired as exc:
        return BridgeProcessResult(
            None,
            (exc.stdout or b"")[-OUTPUT_LIMIT_BYTES:],
            (exc.stderr or b"")[-OUTPUT_LIMIT_BYTES:],
            timed_out=True,
        )
    except OSError:
        return BridgeProcessResult(None, b"", b"", spawn_failed=True)


class LiveCheckBridge:
    """Callable check-port backed by the reviewed native executor.

    ``runner`` is injectable for offline tests; production uses the bounded
    subprocess runner above. Each request gets a fresh output directory, run ID and
    host binding record.
    """

    def __init__(
        self,
        executable,
        docker,
        output_root,
        *,
        timeout=150,
        runner=None,
        counter_start=0,
    ):
        self.executable = Path(executable)
        self.docker = Path(docker)
        self.output_root = Path(output_root)
        self.timeout = int(timeout)
        self.runner = runner or _default_runner
        self._counter = int(counter_start)

    def __call__(self, request):
        try:
            request = _validated_request(request)
        except CheckReceiptProjectionError as exc:
            raise CheckBridgeError(exc.code) from None
        workspace = Path(request["workspace"])
        candidate = workspace / request["candidate_filename"]
        try:
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            return _infrastructure(request)
        if actual != request["candidate_sha256"]:
            return _infrastructure(request)
        inventory = _workspace_inventory(workspace)
        if inventory is None:
            return _infrastructure(request)
        self._counter += 1
        seed = ("%s:%s:%s" % (
            request["request_id"], request["candidate_sha256"], self._counter
        )).encode("utf-8")
        run_id = "live-" + hashlib.sha256(seed).hexdigest()[:32]
        if not _RUN_RE.fullmatch(run_id):
            return _infrastructure(request)
        output_dir = self.output_root / run_id
        try:
            if output_dir.exists():
                return _infrastructure(request)
            output_dir.parent.mkdir(parents=True, exist_ok=True)
            binding = {
                "schema_version": "live-check-binding-0.1",
                "request_id": request["request_id"],
                "run_id": run_id,
                "candidate_filename": request["candidate_filename"],
                "candidate_sha256": request["candidate_sha256"],
                "requirements_version": request["requirements_version"],
                "check_contract": CHECK_PORT_SCHEMA,
                "inventory": inventory,
            }
            binding_path = self.output_root / (run_id + ".binding.json")
            with binding_path.open("x", encoding="utf-8") as handle:
                json.dump(binding, handle, sort_keys=True)
                handle.write("\n")
        except OSError:
            return _infrastructure(request)
        argv = [
            str(self.executable),
            "--workspace", str(workspace),
            "--run-id", run_id,
            "--output-dir", str(output_dir),
            "--docker", str(self.docker),
            "--execute",
        ]
        result = self.runner(argv, self.timeout)
        if result.timed_out or result.spawn_failed or result.returncode is None:
            return _infrastructure(request)
        # Read bounded logs before loading the receipt.
        stdout = _read_bounded(output_dir / "stdout.txt", OUTPUT_LIMIT_BYTES).decode(
            "utf-8", errors="replace")[:OUTPUT_LIMIT_BYTES]
        stderr = _read_bounded(output_dir / "stderr.txt", OUTPUT_LIMIT_BYTES).decode(
            "utf-8", errors="replace")[:OUTPUT_LIMIT_BYTES]
        raw = _read_bounded(output_dir / "receipt.json", RECEIPT_LIMIT_BYTES)
        if not raw:
            return _infrastructure(request)
        try:
            receipt = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _infrastructure(request)
        return project_real_receipt(
            request,
            receipt,
            expected_run_id=run_id,
            expected_requirements_version=request["requirements_version"],
            public_stdout=stdout,
            public_stderr=stderr,
            expected_inventory=inventory,
            process_returncode=result.returncode,
        )


__all__ = [
    "BridgeProcessResult",
    "CheckBridgeError",
    "LiveCheckBridge",
    "project_real_receipt",
]
