"""Trusted host session for protected Retry grading.

The host owns the retry test inputs, SQLite store, fault schedule, snapshots,
observation framing and grade. The candidate receives one event at a time and may
only issue Store method requests. Candidate result/status frames are validated
replies, never authority for durable state or a pass.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid

from episode.protected_grade import (
    CHECK_IDS,
    IMAGE,
    PROTECTED_SCHEMA,
    ProtectedGradeError,
)

# Kept local to avoid importing candidate-facing code or grader modules.
SCHEMA = """
CREATE TABLE totals (
    tenant BLOB PRIMARY KEY,
    units TEXT NOT NULL
);
CREATE TABLE completed (
    tenant BLOB NOT NULL,
    event_id BLOB NOT NULL,
    units TEXT NOT NULL,
    PRIMARY KEY (tenant, event_id)
);
"""

OPERATIONS = [
    {"kind": "event", "payload": {"tenant": "ledger", "event_id": "open",
                                  "units": (1 << 80) + 53}},
    {"kind": "event", "payload": {"tenant": "ledger", "event_id": "next", "units": 1}},
    {"kind": "event", "payload": {"tenant": "ledger", "event_id": "open",
                                  "units": (1 << 80) + 53}},
    {"kind": "event", "payload": {"tenant": "ledger", "event_id": "open", "units": 29}},
    {"kind": "event", "payload": None},
    {"kind": "batch", "payload": [
        {"tenant": "jade", "event_id": "x", "units": 13},
        {"tenant": "jade", "event_id": "malformed", "units": 0},
        {"tenant": "jade", "event_id": "x", "units": 27},
        None,
        {"tenant": "jade", "event_id": "x", "units": 13},
    ]},
    {"kind": "event", "payload": {"tenant": "moss", "event_id": "pending", "units": 41},
     "fault": "after_credit"},
    {"kind": "event", "payload": {"tenant": "moss", "event_id": "pending", "units": 41}},
]
LARGE = (1 << 80) + 53


def _key(value):
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="surrogatepass")


def _hex(value):
    return _key(value).hex()


def _db(totals, completed):
    return {
        "totals": sorted([[_hex(t), format(u, "x")] for t, u in totals.items()]),
        "completed": sorted([[_hex(t), _hex(e), format(u, "x")]
                             for (t, e), u in completed.items()]),
    }


EXPECTED = [
    {"status": "applied", "fault": False, "db": _db({"ledger": LARGE}, {("ledger", "open"): LARGE})},
    {"status": "applied", "fault": False, "db": _db({"ledger": LARGE + 1}, {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"status": "duplicate", "fault": False, "db": _db({"ledger": LARGE + 1}, {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"status": "conflict", "fault": False, "db": _db({"ledger": LARGE + 1}, {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"status": "invalid", "fault": False, "db": _db({"ledger": LARGE + 1}, {("ledger", "open"): LARGE, ("ledger", "next"): 1})},
    {"results": ["applied", "invalid", "conflict", "invalid", "duplicate"], "fault": False,
     "db": _db({"ledger": LARGE + 1, "jade": 13}, {("ledger", "open"): LARGE, ("ledger", "next"): 1, ("jade", "x"): 13})},
    {"status": None, "fault": True, "db": _db({"ledger": LARGE + 1, "jade": 13}, {("ledger", "open"): LARGE, ("ledger", "next"): 1, ("jade", "x"): 13})},
    {"status": "applied", "fault": False,
     "db": _db({"ledger": LARGE + 1, "jade": 13, "moss": 41}, {("ledger", "open"): LARGE, ("ledger", "next"): 1, ("jade", "x"): 13, ("moss", "pending"): 41})},
]


class HostStore:
    """Host-authoritative durable store, transactions, faults and snapshots."""

    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        self.db.executescript(SCHEMA)
        self.in_transaction = False
        self.fault = None
        self.faulted = False

    def close(self):
        self.db.close()

    def reopen(self):
        self.db.close()
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        self.in_transaction = False

    def snapshot(self):
        totals = {t: int(u, 16) for t, u in self.db.execute(
            "SELECT tenant, units FROM totals")}
        completed = {(t, e): int(u, 16) for t, e, u in self.db.execute(
            "SELECT tenant, event_id, units FROM completed")}
        return _db(totals, completed)

    def _execute(self, sql, args=()):
        try:
            return self.db.execute(sql, args)
        except sqlite3.OperationalError as exc:
            raise ProtectedGradeError("host_store_error") from None

    def _maybe_fault(self, stage):
        if self.fault == stage and not self.faulted:
            self.faulted = True
            return False
        return True

    def handle(self, method, args):
        if method == "begin":
            if self.in_transaction:
                raise ProtectedGradeError("nested_transaction")
            self._execute("BEGIN")
            self.in_transaction = True
            return True
        if method == "lookup_units":
            tenant, event_id = args
            row = self._execute(
                "SELECT units FROM completed WHERE tenant=? AND event_id=?",
                (_key(tenant), _key(event_id))).fetchone()
            return int(row[0], 16) if row is not None else None
        if method == "add_units":
            tenant, units = args
            if type(units) is not int or isinstance(units, bool) or units <= 0:
                return False
            current = self._execute(
                "SELECT units FROM totals WHERE tenant=?", (_key(tenant),)
            ).fetchone()
            total = (int(current[0], 16) if current else 0) + units
            self._execute(
                "INSERT INTO totals VALUES (?,?) ON CONFLICT(tenant) "
                "DO UPDATE SET units=excluded.units",
                (_key(tenant), format(total, "x")))
            if not self._maybe_fault("after_credit"):
                raise ProtectedGradeError("injected_after_credit")
            return None
        if method == "mark_completed":
            tenant, event_id, units = args
            self._execute(
                "INSERT INTO completed VALUES (?,?,?)",
                (_key(tenant), _key(event_id), format(units, "x")))
            if not self._maybe_fault("after_mark"):
                raise ProtectedGradeError("injected_after_mark")
            return None
        if method == "commit":
            if not self.in_transaction:
                raise ProtectedGradeError("no_transaction")
            self._execute("COMMIT")
            self.in_transaction = False
            if not self._maybe_fault("after_commit"):
                raise ProtectedGradeError("injected_after_commit")
            return True
        if method == "rollback":
            if self.in_transaction:
                self._execute("ROLLBACK")
                self.in_transaction = False
            return True
        raise ProtectedGradeError("invalid_store_method")


class Session:
    qualified = False

    def start(self):
        raise NotImplementedError

    def send_operation(self, index, kind, payload):
        raise NotImplementedError

    def receive(self, timeout):
        raise NotImplementedError

    def send_store_result(self, ok, value):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def isolation_witness(self):
        return None


class DockerSession(Session):
    """Actual create/inspect/start/remove session with host-owned store.

    Restrictions and identity are verified with a real inspect before start; cleanup
    is exact-name only and absence is read back. No candidate/reference/control
    directory is mounted.
    """

    qualified = True

    def __init__(self, docker, candidate, shim_dir, run_id, *, image=IMAGE,
                 timeout=30, shim_name="retry_shim.py"):
        self.docker = str(docker)
        self.candidate = str(Path(candidate).resolve())
        self.shim_dir = str(Path(shim_dir).resolve())
        self.run_id = run_id
        self.name = "ft-grade-" + run_id + "-" + uuid.uuid4().hex[:8]
        self.image = image
        self.timeout = timeout
        self.shim_name = shim_name
        self.proc = None
        self.queue = None
        self.witness = None
        self.cleanup = "unknown"
        self._id = None
        self._frames = 0
        self._max_frames = 128

    def _run(self, argv, timeout=10, input_bytes=None):
        try:
            return subprocess.run(
                argv, input=input_bytes,
                stdin=subprocess.PIPE if input_bytes is None else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False, env=_docker_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ProtectedGradeError("docker_command_failed") from None

    def start(self):
        create = self._run([
            self.docker, "create", "--pull=never", "--name", self.name,
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=256m", "--cpus=1", "--user=65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--workdir", "/tmp",
            "--mount", f"type=bind,src={self.candidate},dst=/candidate/processor.py,readonly",
            "--mount", f"type=bind,src={Path(self.shim_dir) / self.shim_name},dst=/opt/shim/{self.shim_name},readonly",
            "--entrypoint", "python", self.image,
            "-I", "-B", "/opt/shim/" + self.shim_name, "/candidate/processor.py",
        ])
        if create.returncode != 0:
            raise ProtectedGradeError("docker_create_refused")
        self._id = create.stdout.decode("utf-8", "replace").strip()
        if len(self._id) != 64:
            raise ProtectedGradeError("docker_create_id_invalid")
        inspect = self._run([
            self.docker, "inspect", "--format", "{{json .}}", self._id,
        ])
        if inspect.returncode != 0:
            raise ProtectedGradeError("docker_inspect_failed")
        try:
            facts = json.loads(inspect.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProtectedGradeError("docker_inspect_malformed") from None
        if not self._witness_ok(facts):
            raise ProtectedGradeError("docker_isolation_mismatch")
        start = subprocess.Popen(
            [self.docker, "start", "-a", "-i", self.name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
        self.proc = start
        self.queue = queue.Queue()

        def reader():
            try:
                for line in start.stdout:
                    self.queue.put(line)
            except Exception:
                pass

        threading.Thread(target=reader, daemon=True).start()
        self.witness = {
            "verified": True,
            "network": facts["HostConfig"]["NetworkMode"],
            "read_only": facts["HostConfig"]["ReadonlyRootfs"],
            "cap_drop": facts["HostConfig"]["CapDrop"],
            "user": facts["Config"]["User"],
            "mounts": [m["Destination"] for m in facts["Mounts"]],
            "name": facts["Name"],
            "id": self._id,
            "image_id": facts.get("Image"),
            "memory": facts["HostConfig"]["Memory"],
            "nano_cpus": facts["HostConfig"]["NanoCpus"],
        }

    def _witness_ok(self, facts):
        try:
            expected_mounts = {
                "/candidate/processor.py": os.path.normcase(os.path.abspath(self.candidate)),
                "/opt/shim/" + self.shim_name: os.path.normcase(os.path.abspath(
                    os.path.join(self.shim_dir, self.shim_name))),
            }
            mounts = facts["Mounts"]
            if type(mounts) is not list or len(mounts) != 2:
                return False
            seen = {}
            for mount in mounts:
                destination = mount["Destination"]
                if destination not in expected_mounts or mount.get("RW") is not False:
                    return False
                source = os.path.normcase(os.path.abspath(str(mount.get("Source", ""))))
                if source != expected_mounts[destination]:
                    return False
                seen[destination] = True
            if set(seen) != set(expected_mounts):
                return False
            tmpfs = facts["HostConfig"]["Tmpfs"]
            options = tmpfs.get("/tmp") if type(tmpfs) is dict else None
            if type(options) is not str:
                return False
            tokens = {token.strip() for token in options.split(",")}
            if not {"rw", "noexec", "nosuid", "size=16m"}.issubset(tokens):
                return False
            image_id = facts.get("Image")
            return (
                facts["Id"] == self._id
                and facts["Name"] == "/" + self.name
                and facts["Config"]["Image"] == IMAGE
                and facts["Config"]["User"] == "65534:65534"
                and type(image_id) is str and image_id.startswith("sha256:")
                and len(image_id) == 71
                and facts["HostConfig"]["NetworkMode"] == "none"
                and facts["HostConfig"]["ReadonlyRootfs"] is True
                and facts["HostConfig"]["CapDrop"] == ["ALL"]
                and any(str(x).startswith("no-new-privileges")
                        for x in facts["HostConfig"]["SecurityOpt"])
                and facts["HostConfig"]["Privileged"] is False
                and facts["HostConfig"]["PidsLimit"] == 64
                and facts["HostConfig"]["Memory"] == 256 * 1024 * 1024
                and facts["HostConfig"]["NanoCpus"] == 1_000_000_000
            )
        except (KeyError, TypeError, ValueError):
            return False

    def send_operation(self, index, kind, payload):
        self._frames = 0
        self.send_frame({"type": "event", "index": index, "kind": kind,
                         "payload": payload})

    def send_frame(self, frame):
        self._write(frame)

    def _write(self, frame):
        try:
            self.proc.stdin.write(json.dumps(frame, sort_keys=True) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise ProtectedGradeError("candidate_pipe_closed") from None

    def receive(self, timeout):
        self._frames += 1
        if self._frames > self._max_frames:
            raise ProtectedGradeError("candidate_frame_limit")
        try:
            line = self.queue.get(timeout=timeout)
        except queue.Empty:
            raise ProtectedGradeError("candidate_session_timeout") from None
        if len(line) > 65536:
            raise ProtectedGradeError("candidate_frame_too_large")
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            raise ProtectedGradeError("candidate_frame_malformed") from None
        if type(frame) is not dict:
            raise ProtectedGradeError("candidate_frame_malformed")
        return frame

    def send_store_result(self, ok, value):
        self._write({"type": "store_result", "ok": bool(ok), "result": value,
                     "error": None if ok else value})

    def close(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        removed = self._run([self.docker, "rm", "--force", self.name])
        listed = self._run([self.docker, "ps", "-a", "--quiet", "--filter",
                            "name=^/?" + self.name + "$"])
        if removed.returncode == 0 and listed.returncode == 0 and not listed.stdout.strip():
            self.cleanup = "settled"
        else:
            self.cleanup = "unknown"


def _docker_env():
    """Do not leak credentials, provider keys or project env into Docker CLI."""
    allowed = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR",
               "ProgramData", "ProgramFiles")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def native_batch_command(docker, candidate, shim_dir, run_root):
    """One native PowerShell-compatible authored batch command (review-gated)."""
    return [
        str(docker), "run", "--rm", "--pull=never", "--network=none",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--pids-limit=64", "--memory=256m", "--cpus=1", "--user=65534:65534",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--workdir", "/tmp",
        "--mount", f"type=bind,src={candidate},dst=/candidate/processor.py,readonly",
        "--mount", f"type=bind,src={Path(shim_dir) / 'retry_shim.py'},dst=/opt/shim/retry_shim.py,readonly",
        "--entrypoint", "python", IMAGE, "-I", "-B",
        "/opt/shim/retry_shim.py", "/candidate/processor.py",
    ]


def _validate_result(frame, index, kind):
    if type(frame) is not dict or frame.get("type") != "result":
        raise ProtectedGradeError("invalid_result_frame")
    if frame.get("index") != index or frame.get("kind") != kind:
        raise ProtectedGradeError("result_index_mismatch")
    keys = set(frame)
    if "candidate_error" in keys:
        return {"candidate_error": True, "fault": False}
    required = {"type", "index", "kind", "fault_propagated"}
    if not required.issubset(keys) or type(frame["fault_propagated"]) is not bool:
        raise ProtectedGradeError("result_schema_mismatch")
    if kind == "event":
        if "status" in keys:
            if type(frame["status"]) is not str:
                raise ProtectedGradeError("result_status_type")
            return {"status": frame["status"], "fault": frame["fault_propagated"]}
        if frame["fault_propagated"] is True and keys == required:
            return {"status": None, "fault": True}
        raise ProtectedGradeError("result_status_missing")
    if "results" in keys:
        if type(frame["results"]) is not list:
            raise ProtectedGradeError("result_results_type")
        return {"results": frame["results"], "fault": frame["fault_propagated"]}
    if frame["fault_propagated"] is True and keys == required:
        return {"results": None, "fault": True}
    raise ProtectedGradeError("result_results_missing")


def grade_session(session, candidate_sha256, *, evidence_kind, cleanup="settled",
                  checkpoint_id=None, requirements_version="v1"):
    """Drive one host-owned session and adjudicate. Candidate cannot supply DB state."""
    if evidence_kind == "protected_container_grading":
        witness = session.isolation_witness() if session.qualified else None
        if not witness or witness.get("verified") is not True:
            raise ProtectedGradeError("requires_qualified_isolation_witness")
    elif evidence_kind != "synthetic_development":
        raise ProtectedGradeError("invalid_evidence_kind")
    work = Path(tempfile.gettempdir()) / ("protected-session-" + uuid.uuid4().hex)
    work.mkdir()
    store = HostStore(work / "store.sqlite")
    checks = []
    errors = []
    try:
        session.start()
        for index, operation in enumerate(OPERATIONS):
            if index == 7:
                store.reopen()  # reopen semantics: durable state must survive
            store.fault = operation.get("fault")
            store.faulted = False
            session.send_operation(index, operation["kind"], operation["payload"])
            result = None
            deadline = time.monotonic() + 30
            while result is None:
                if time.monotonic() > deadline:
                    raise ProtectedGradeError("candidate_session_timeout")
                frame = session.receive(max(0.1, deadline - time.monotonic()))
                if frame.get("type") == "store":
                    if frame.get("index") != index:
                        raise ProtectedGradeError("store_index_mismatch")
                    method = frame.get("method")
                    args = frame.get("args")
                    if type(method) is not str or type(args) is not list:
                        raise ProtectedGradeError("invalid_store_request")
                    try:
                        value = store.handle(method, args)
                    except ProtectedGradeError as exc:
                        session.send_store_result(False, exc.code)
                    else:
                        session.send_store_result(True, value)
                    continue
                if frame.get("type") == "result":
                    if frame.get("index") != index or frame.get("kind") != operation["kind"]:
                        continue  # unsolicited/forged frame is ignored
                    result = _validate_result(frame, index, operation["kind"])
                    continue
                # Unknown/forged frame: ignored, never accepted as authority.
            observed = store.snapshot()
            expected = EXPECTED[index]
            check = {"id": CHECK_IDS[index], "passed": False,
                     "detail": "observation mismatch"}
            if result.get("candidate_error"):
                check["detail"] = "candidate error frame"
                errors.append({"origin": "candidate", "phase": "invoke",
                               "type": "CandidateError", "detail": "candidate error"})
            elif result.get("fault") != expected["fault"]:
                check["detail"] = "fault observation mismatch"
            elif "status" in expected and result.get("status") != expected["status"]:
                check["detail"] = "status mismatch"
            elif "results" in expected and result.get("results") != expected["results"]:
                check["detail"] = "results mismatch"
            elif observed != expected["db"]:
                check["detail"] = "host durable state mismatch"
            else:
                check["passed"] = True
                check["detail"] = "expected host state"
            checks.append(check)
    finally:
        try:
            session.close()
        finally:
            cleanup = getattr(session, "cleanup", cleanup)
            store.close()
    status = "passed" if all(c["passed"] for c in checks) and not errors else (
        "candidate_error" if errors else "failed")
    receipt = {
        "schema_version": PROTECTED_SCHEMA,
        "task_family": "retry-processing",
        "candidate_sha256": candidate_sha256,
        "run_id": getattr(session, "run_id", None),
        "requirements_version": requirements_version,
        "grade_kind": "checkpoint" if checkpoint_id else "final",
        "checkpoint_id": checkpoint_id,
        "evidence_kind": evidence_kind,
        "origin": "grader" if status in ("passed", "failed") else "candidate",
        "status": status,
        "functional_pass": True if status == "passed" else (False if status == "failed" else None),
        "exit_code": None,
        "checks": checks,
        "errors": errors,
        "stdout": "",
        "stderr": "",
        "cleanup": cleanup,
        "settlement": "unknown" if cleanup == "unknown" else "known",
        "limitations": ["host_authoritative_store_snapshots"],
    }
    if cleanup == "unknown":
        receipt["status"] = "infrastructure_error"
        receipt["functional_pass"] = None
        receipt["limitations"].append("cleanup_unknown_forbids_semantic_outcome")
    return receipt


__all__ = ["EXPECTED", "HostStore", "OPERATIONS", "Session", "grade_session"]
