"""Focused P2 protected/bridge tests. Synthetic/scripted evidence only.

No provider, credential, Docker or candidate-host execution is performed.
"""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import os
import queue
from pathlib import Path
import sys
import shutil
import uuid
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(__file__).resolve().parents[3] / "tmp"
SCRATCH.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

from episode import check_bridge  # noqa: E402
from episode import protected_grade as pg  # noqa: E402
from episode import protected_session as ps  # noqa: E402
from episode.check_receipt import project_synthetic_receipt  # noqa: E402


class SimulatedSession(ps.Session):
    """Test-only protocol speaker; never imports candidate code."""

    qualified = False

    def __init__(self, behavior="correct", forged=False):
        self.behavior = behavior
        self.forged = forged
        self.frames = []
        self.index = None
        self.kind = None
        self.awaiting = None
        self.witness = None

    def start(self):
        pass

    def _store(self, method, *args):
        return ("store", {"type": "store", "index": self.index,
                          "method": method, "args": list(args)})

    def _result(self, status=None, results=None, fault=False, error=False):
        frame = {"type": "result", "index": self.index, "kind": self.kind,
                 "fault_propagated": fault}
        if error:
            frame = {"type": "result", "index": self.index, "kind": self.kind,
                     "candidate_error": True, "fault_propagated": False}
        elif results is not None:
            frame["results"] = results
        elif status is not None:
            frame["status"] = status
        return ("result", frame)

    def _event(self, status):
        return [self._result(status=status)]

    def send_operation(self, index, kind, payload):
        self.index, self.kind = index, kind
        if self.behavior == "error":
            self.frames = [self._result(error=True)]
            return
        if self.forged:
            status = "applied" if kind == "event" else []
            self.frames = [self._result(status=status if kind == "event" else None,
                                        results=[] if kind == "batch" else None)]
            return
        if index == 0:
            self.frames = [self._store("begin"), self._store("lookup_units", "ledger", "open"),
                           self._store("add_units", "ledger", (1 << 80) + 53),
                           self._store("mark_completed", "ledger", "open", (1 << 80) + 53),
                           self._store("commit"), self._result(status="applied")]
        elif index == 1:
            self.frames = [self._store("begin"), self._store("lookup_units", "ledger", "next"),
                           self._store("add_units", "ledger", 1),
                           self._store("mark_completed", "ledger", "next", 1),
                           self._store("commit"), self._result(status="applied")]
        elif index == 2:
            self.frames = [self._store("begin"), self._store("lookup_units", "ledger", "open"),
                           self._store("commit"), self._result(status="duplicate")]
        elif index == 3:
            self.frames = [self._store("begin"), self._store("lookup_units", "ledger", "open"),
                           self._store("commit"), self._result(status="conflict")]
        elif index == 4:
            self.frames = [self._result(status="invalid")]
        elif index == 5:
            self.frames = []
            for item, result in zip(payload, ["applied", "invalid", "conflict", "invalid", "duplicate"]):
                if result == "invalid":
                    continue
                self.frames += [self._store("begin"), self._store("lookup_units", item["tenant"], item["event_id"])]
                if result == "applied":
                    self.frames += [self._store("add_units", item["tenant"], item["units"]),
                                    self._store("mark_completed", item["tenant"], item["event_id"], item["units"])]
                self.frames.append(self._store("commit"))
            self.frames.append(self._result(results=["applied", "invalid", "conflict", "invalid", "duplicate"]))
        elif index == 6:
            self.frames = [self._store("begin"), self._store("lookup_units", "moss", "pending"),
                           self._store("add_units", "moss", 41)]
        elif index == 7:
            self.frames = [self._store("begin"), self._store("lookup_units", "moss", "pending"),
                           self._store("add_units", "moss", 41),
                           self._store("mark_completed", "moss", "pending", 41),
                           self._store("commit"), self._result(status="applied")]

    def receive(self, timeout):
        if not self.frames:
            raise ps.ProtectedGradeError("no_frame")
        kind, frame = self.frames.pop(0)
        if kind == "store":
            self.awaiting = (frame["method"], frame["args"])
        return frame

    def send_store_result(self, ok, value):
        if not ok:
            method = self.awaiting[0] if self.awaiting else ""
            if method in ("add_units", "mark_completed"):
                # Correct class rolls back; known-incorrect class returns a status
                # but the host durable state remains authoritative.
                self.frames = [self._store("rollback"),
                               self._result(status="applied" if self.behavior == "incorrect" else None,
                                            fault=self.behavior != "incorrect")]
            elif method == "commit":
                self.frames = [self._result(fault=True)]
            else:
                self.frames = [self._result(error=True)]

    def close(self):
        pass

    def isolation_witness(self):
        return self.witness

PROJECT = Path(os.environ.get("FOLLOWTHROUGH_PROJECT_ROOT",
                              r"C:\Users\Dv\Desktop\FollowThrough"))
NATIVE = PROJECT / ".coordination" / "runtime" / "group08-info" / \
    "native-r3-1d9b733085af" / "receipt.json"


@contextlib.contextmanager
def temp_scratch():
    path = SCRATCH / ("scratch-" + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory_for(workspace):
    entries = []
    for path in sorted(Path(workspace).iterdir(), key=lambda item: item.name):
        data = path.read_bytes()
        entries.append({"name": path.name, "bytes": len(data),
                        "sha256": sha(data)})
    return entries


def request_for(candidate_sha):
    return {
        "request_id": "ep-1:check:1",
        "workspace": str(ROOT / "tmp" / "workspace"),
        "candidate_filename": "processor.py",
        "candidate_sha256": candidate_sha,
        "requirements_version": "v1",
    }


class CheckBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(NATIVE.read_text(encoding="utf-8"))

    def test_native_r3_real_receipt_projects_passed(self):
        receipt = self.receipt
        request = {
            "request_id": "native:check:1",
            "workspace": "C:\\native\\agent",
            "candidate_filename": "processor.py",
            "candidate_sha256": receipt["workspace"]["candidate_sha256"],
            "requirements_version": "v1",
        }
        response = check_bridge.project_real_receipt(
            request, receipt,
            expected_run_id=receipt["run_id"],
            expected_requirements_version="v1",
            public_stdout="public checks passed\n",
        )
        self.assertEqual(response["schema_version"], "check-port-0.1")
        self.assertEqual(response["evidence_kind"], "real_container_check")
        self.assertEqual(response["status"], "passed")
        self.assertEqual(response["exit_code"], 0)
        self.assertEqual(response["cleanup"], "settled")

    def test_authored_failing_public_control_maps_failed(self):
        receipt = copy.deepcopy(self.receipt)
        receipt["status"] = "check_failed"
        receipt["public_check_exit"] = 2
        receipt["container"]["attached"]["exit_code"] = 2
        request = {
            "request_id": "native:check:2",
            "workspace": "C:\\native\\agent",
            "candidate_filename": "processor.py",
            "candidate_sha256": receipt["workspace"]["candidate_sha256"],
            "requirements_version": "v1",
        }
        response = check_bridge.project_real_receipt(
            request, receipt, expected_run_id=receipt["run_id"],
            expected_requirements_version="v1", public_stderr="checks failed\n")
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["exit_code"], 2)
        self.assertEqual(response["cleanup"], "settled")

    def test_bridge_binds_inventory_and_process_exit(self):
        receipt = copy.deepcopy(self.receipt)
        request = {
            "request_id": "native:check:inventory",
            "workspace": "C:\\native\\agent",
            "candidate_filename": "processor.py",
            "candidate_sha256": receipt["workspace"]["candidate_sha256"],
            "requirements_version": "v1",
        }
        inventory = [
            {"name": i["name"], "bytes": i["bytes"], "sha256": i["sha256"]}
            for i in receipt["workspace"]["inventory"]
        ]
        response = check_bridge.project_real_receipt(
            request, receipt, expected_run_id=receipt["run_id"],
            expected_requirements_version="v1", expected_inventory=inventory,
            process_returncode=0)
        self.assertEqual(response["status"], "passed")
        bad = copy.deepcopy(inventory)
        bad[0]["sha256"] = "0" * 64
        response = check_bridge.project_real_receipt(
            request, receipt, expected_run_id=receipt["run_id"],
            expected_requirements_version="v1", expected_inventory=bad,
            process_returncode=1)
        self.assertEqual(response["status"], "infrastructure_error")

    def test_real_bridge_refuses_wrong_hash_version_and_unknown_cleanup(self):
        receipt = copy.deepcopy(self.receipt)
        request = {
            "request_id": "native:check:3",
            "workspace": "C:\\native\\agent",
            "candidate_filename": "processor.py",
            "candidate_sha256": "0" * 64,
            "requirements_version": "v1",
        }
        response = check_bridge.project_real_receipt(
            request, receipt, expected_run_id=receipt["run_id"],
            expected_requirements_version="v2")
        self.assertEqual(response["status"], "infrastructure_error")
        receipt["cleanup"] = {"state": "unknown", "method": "none",
                              "removal_verified": False, "detail": "late"}
        request["candidate_sha256"] = receipt["workspace"]["candidate_sha256"]
        response = check_bridge.project_real_receipt(
            request, receipt, expected_run_id=receipt["run_id"],
            expected_requirements_version="v1")
        self.assertEqual(response["cleanup"], "unknown")
        self.assertEqual(response["status"], "infrastructure_error")

    def test_synthetic_route_is_not_upgraded(self):
        receipt = copy.deepcopy(self.receipt)
        receipt["evidence_kind"] = "synthetic_executor_control"
        request = {
            "request_id": "native:check:4",
            "workspace": "C:\\native\\agent",
            "candidate_filename": "processor.py",
            "candidate_sha256": receipt["workspace"]["candidate_sha256"],
            "requirements_version": "v1",
        }
        response = project_synthetic_receipt(
            request, receipt, expected_run_id=receipt["run_id"])
        self.assertEqual(response["evidence_kind"], "synthetic_check")
        self.assertEqual(response["status"], "passed")

    def test_live_bridge_runs_bounded_executor_and_binds_binding(self):
        with temp_scratch() as temp:
            temp = Path(temp)
            workspace = temp / "workspace"
            workspace.mkdir()
            (workspace / "processor.py").write_bytes(b"VALUE = 1\n")
            digest = sha(b"VALUE = 1\n")
            output_root = temp / "outputs"

            def runner(argv, timeout):
                out = Path(argv[argv.index("--output-dir") + 1])
                out.mkdir()
                receipt = copy.deepcopy(self.receipt)
                receipt["workspace"]["candidate_sha256"] = digest
                receipt["workspace"]["inventory"] = inventory_for(workspace)
                receipt["run_id"] = argv[argv.index("--run-id") + 1]
                (out / "receipt.json").write_text(json.dumps(receipt),
                                                  encoding="utf-8")
                (out / "stdout.txt").write_text("ok\n", encoding="utf-8")
                return check_bridge.BridgeProcessResult(0, b"", b"")

            bridge = check_bridge.LiveCheckBridge(
                temp / "container-check.exe", temp / "docker.exe",
                output_root, runner=runner)
            request = {
                "request_id": "live:check:1",
                "workspace": str(workspace),
                "candidate_filename": "processor.py",
                "candidate_sha256": digest,
                "requirements_version": "v1",
            }
            response = bridge(request)
            self.assertEqual(response["evidence_kind"], "real_container_check")
            self.assertEqual(response["status"], "passed")
            self.assertEqual(len(list(output_root.glob("*.binding.json"))), 1)


class ProtectedGradeTests(unittest.TestCase):
    def test_correct_control_passes_and_incorrect_fails(self):
        digest = "a" * 64
        correct = pg.expected_observations(digest)
        receipt = pg.adjudicate(digest, correct,
                                evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["functional_pass"], True)
        self.assertEqual(pg.trajectory_outcome(receipt), "pass")
        wrong = copy.deepcopy(correct)
        wrong["operations"][0]["status"] = "conflict"
        receipt = pg.adjudicate(digest, wrong,
                                evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["functional_pass"], False)
        self.assertEqual(pg.trajectory_outcome(receipt), "fail")

    def test_candidate_error_cleanup_unknown_and_malformed_are_not_pass(self):
        digest = "b" * 64
        observations = pg.expected_observations(digest)
        observations["operations"][0] = {"candidate_error": "RuntimeError"}
        receipt = pg.adjudicate(digest, observations,
                                evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "candidate_error")
        self.assertEqual(pg.trajectory_outcome(receipt), "unknown")

        receipt = pg.adjudicate(digest, pg.expected_observations(digest),
                                evidence_kind="synthetic_development",
                                cleanup="unknown")
        self.assertNotIn(receipt["status"], ("passed", "failed"))
        self.assertEqual(pg.trajectory_outcome(receipt), "unknown")

        receipt = pg.adjudicate(digest, {"bad": "shape"},
                                evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "infrastructure_error")

    def test_docker_witness_checks_memory_cpu_tmpfs_and_mount_authority(self):
        session = ps.DockerSession("docker", ROOT / "candidate.py",
                                   ROOT / "protected", "p2-witness")
        session._id = "a" * 64
        facts = {
            "Id": session._id,
            "Name": "/" + session.name,
            "Image": "sha256:" + "b" * 64,
            "Config": {"Image": ps.IMAGE, "User": "65534:65534"},
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True,
                "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                "Privileged": False, "PidsLimit": 64,
                "Memory": 256 * 1024 * 1024, "NanoCpus": 1_000_000_000,
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"},
            },
            "Mounts": [
                {"Destination": "/candidate/processor.py", "RW": False,
                 "Source": str((ROOT / "candidate.py").resolve())},
                {"Destination": "/opt/shim/retry_shim.py", "RW": False,
                 "Source": str((ROOT / "protected" / "retry_shim.py").resolve())},
            ],
        }
        self.assertTrue(session._witness_ok(facts))
        broken = copy.deepcopy(facts)
        broken["HostConfig"]["Memory"] = 128 * 1024 * 1024
        self.assertFalse(session._witness_ok(broken))
        broken = copy.deepcopy(facts)
        broken["HostConfig"]["Tmpfs"]["/tmp"] = "rw,noexec,nosuid,size=8m"
        self.assertFalse(session._witness_ok(broken))
        broken = copy.deepcopy(facts)
        broken["Mounts"][0]["RW"] = True
        self.assertFalse(session._witness_ok(broken))

    def test_docker_session_timeout_and_frame_limit_are_classified(self):
        session = ps.DockerSession("docker", ROOT / "candidate.py", ROOT / "protected", "p2-timeout")
        session.queue = queue.Queue()
        with self.assertRaises(pg.ProtectedGradeError):
            session.receive(0.01)
        session._frames = 128
        with self.assertRaises(pg.ProtectedGradeError):
            session.receive(0.01)

    def test_docker_runner_is_denied_in_this_shell_gate(self):
        # Docker is not invoked here. The gate is recorded as blocked separately.
        self.assertTrue(str(pg.IMAGE).startswith("python@sha256:"))

    def test_known_incorrect_authored_control_fails_rollback(self):
        digest = "d" * 64
        observations = pg.expected_observations(digest)
        # Mark-first/incorrect class: after-credit fault leaves the credit durable.
        observations["operations"][6]["db"] = {
            "totals": observations["operations"][6]["db"]["totals"] + [
                ["6d6f7373", format(41, "x")]],
            "completed": observations["operations"][6]["db"]["completed"],
        }
        receipt = pg.adjudicate(digest, observations,
                                evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "failed")
        failed = {check["id"] for check in receipt["checks"] if not check["passed"]}
        self.assertIn("rollback_after_credit", failed)

    def test_protected_label_requires_qualified_runner(self):
        with self.assertRaises(pg.ProtectedGradeError):
            ps.grade_session(
                SimulatedSession("correct"), "e" * 64,
                evidence_kind="protected_container_grading")

    def test_retry_checkpoint_receipt_binds_id(self):
        receipt = ps.grade_session(
            SimulatedSession("correct"), "f" * 64,
            evidence_kind="synthetic_development",
            checkpoint_id="retry-checkpoint-1")
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["grade_kind"], "checkpoint")
        self.assertEqual(receipt["checkpoint_id"], "retry-checkpoint-1")

    def test_host_session_correct_control_passes(self):
        receipt = ps.grade_session(
            SimulatedSession("correct"), "a" * 64,
            evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["functional_pass"], True)
        self.assertEqual(len(receipt["checks"]), len(ps.OPERATIONS))
        self.assertTrue(all(c["passed"] for c in receipt["checks"]))

    def test_known_incorrect_session_fails_host_adjudication(self):
        receipt = ps.grade_session(
            SimulatedSession("incorrect"), "b" * 64,
            evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["functional_pass"], False)

    def test_forged_observation_frame_cannot_pass(self):
        receipt = ps.grade_session(
            SimulatedSession("correct", forged=True), "c" * 64,
            evidence_kind="synthetic_development")
        self.assertNotEqual(receipt["status"], "passed")
        self.assertTrue(any("host durable state" in c["detail"]
                            for c in receipt["checks"]))

    def test_candidate_error_frame_is_not_semantic_failure(self):
        receipt = ps.grade_session(
            SimulatedSession("error"), "d" * 64,
            evidence_kind="synthetic_development")
        self.assertEqual(receipt["status"], "candidate_error")
        self.assertIsNone(receipt["functional_pass"])

    def test_host_store_transaction_rollback_and_reopen(self):
        with temp_scratch() as temp:
            store = ps.HostStore(Path(temp) / "store.sqlite")
            store.fault = "after_credit"
            self.assertTrue(store.handle("begin", []))
            with self.assertRaises(pg.ProtectedGradeError):
                store.handle("add_units", ["t", 5])
            store.handle("rollback", [])
            store.reopen()
            store.fault = None
            self.assertEqual(store.snapshot()["totals"], [])

    def test_host_lost_ack_after_commit_is_durable_after_reopen(self):
        with temp_scratch() as temp:
            store = ps.HostStore(Path(temp) / "store.sqlite")
            store.fault = "after_commit"
            store.handle("begin", [])
            store.handle("add_units", ["t", 7])
            with self.assertRaises(pg.ProtectedGradeError):
                store.handle("commit", [])
            store.reopen()
            self.assertEqual(store.snapshot()["totals"],
                             [[b"t".hex(), format(7, "x")]])

    def test_canary_control_exists_with_forbidden_probes(self):
        text = (ROOT / "protected" / "controls" / "canary_processor.py").read_text(
            encoding="utf-8")
        for probe in ("grader", "references", "docker.sock", ".env.local"):
            self.assertIn(probe, text)


