"""In-memory tests for synthetic executor receipt projection (no I/O)."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest import mock
import uuid

from episode.check_receipt import (
    CheckReceiptProjectionError,
    project_synthetic_receipt,
)


OVERLAY = Path(__file__).resolve().parents[1]
SCRATCH = OVERLAY / "tmp"
WORKSPACE = str(SCRATCH / ("workspace-" + uuid.uuid4().hex[:8]))
SHA = "a" * 64
RUN_ID = "run:check:0001"


def request(**overrides):
    value = {
        "request_id": "ep-1:check:1",
        "workspace": WORKSPACE,
        "candidate_filename": "processor.py",
        "candidate_sha256": SHA,
        "requirements_version": "v1",
    }
    value.update(overrides)
    return value


def command(*, spawn_failed=False, reaped=True, reader=True, exit_code=0):
    return {
        "name": "attach",
        "argv": ["docker", "exec", "private-container"],
        "pid": 1234,
        "exit_code": exit_code,
        "stdout_bytes": 1,
        "stderr_bytes": 0,
        "timed_out": False,
        "output_limit": False,
        "cancelled": False,
        "spawn_failed": spawn_failed,
        "direct_child_reaped": reaped,
        "reader_settled": reader,
        "elapsed_ms": 5,
        "issue": None,
    }


def container(exit_code=0):
    return {
        "id": "container-id-private",
        "name": "followthrough-private",
        "owner_token": "private-owner-token",
        "attached": {
            "exit_code": exit_code,
            "timed_out": False,
            "output_limit": False,
            "cancelled": False,
            "direct_child_reaped": True,
        },
    }


def receipt(status="check_passed", *, exit_code=0, cleanup=None, commands=None,
            container_value=None, evidence="synthetic_executor_control",
            run_id=RUN_ID, candidate_sha=SHA,
            candidate_file="processor.py"):
    if cleanup is None:
        cleanup = {
            "state": "settled",
            "method": "removed_verified",
            "removal_verified": True,
            "detail": None,
        }
    if container_value is None and status in ("check_passed", "check_failed"):
        container_value = container(exit_code if exit_code is not None else 0)
    if commands is None:
        commands = [command(exit_code=exit_code or 0)]
    return {
        "schema_version": "container-check-0.1",
        "evidence_kind": evidence,
        "run_id": run_id,
        "status": status,
        "diagnostic": "private-diagnostic-text",
        "workspace": {
            "path": "/host/private/workspace",
            "candidate_file": candidate_file,
            "candidate_sha256": candidate_sha,
            "inventory": [{"path": "/host/private/inventory"}],
            "inventory_file_count": 6,
            "inventory_total_bytes": 100,
        },
        "image": {"reference": "private-image"},
        "container": container_value,
        "public_check_exit": exit_code,
        "commands": commands,
        "cleanup": cleanup,
        "stdout_file": "/host/private/stdout.txt",
        "stderr_file": "/host/private/stderr.txt",
        "elapsed_ms": 12,
        "observed": {"setup_elapsed_ms": 1},
        "limitations": ["private-limitation"],
    }


class CheckReceiptProjectionTest(unittest.TestCase):
    def assert_malformed(self, callable_, *args, **kwargs):
        with self.assertRaises(CheckReceiptProjectionError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, "invalid_check_request")
        return raised.exception

    def test_passed_projection_is_bounded_check_port_data(self):
        result = project_synthetic_receipt(
            request(), receipt("check_passed", exit_code=0),
            expected_run_id=RUN_ID,
            public_stdout="public pass\n", public_stderr="",
        )
        self.assertEqual(
            set(result),
            {"schema_version", "request_id", "evidence_kind",
             "candidate_sha256", "requirements_version", "status", "exit_code",
             "stdout", "stderr", "cleanup"},
        )
        self.assertEqual(result["schema_version"], "check-port-0.1")
        self.assertEqual(result["request_id"], "ep-1:check:1")
        self.assertEqual(result["evidence_kind"], "synthetic_check")
        self.assertEqual(result["candidate_sha256"], SHA)
        self.assertEqual(result["requirements_version"], "v1")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "public pass\n")
        self.assertEqual(result["stderr"], "")
        self.assertEqual(result["cleanup"], "settled")
        serialized = json.dumps(result)
        for private in ("/host/private", "docker", "private-owner-token",
                        "container-id-private", "private-diagnostic-text",
                        "private-limitation"):
            self.assertNotIn(private, serialized)

    def test_failed_check_requires_nonzero_exit(self):
        result = project_synthetic_receipt(
            request(), receipt("check_failed", exit_code=1),
            expected_run_id=RUN_ID,
            public_stdout="", public_stderr="failure output\n",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["cleanup"], "settled")
        self.assertEqual(result["stderr"], "failure output\n")

    def test_pre_create_failure_not_created_is_settled(self):
        cleanup = {
            "state": "settled", "method": "not_created",
            "removal_verified": False, "detail": "create_failed_no_id",
        }
        result = project_synthetic_receipt(
            request(), receipt(
                "infrastructure_error", exit_code=2, cleanup=cleanup,
                commands=[], container_value=None,
            ),
            expected_run_id=RUN_ID,
        )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["cleanup"], "settled")
        self.assertEqual(result["exit_code"], 2)

    def test_unknown_cleanup_retains_failure_but_cannot_pass(self):
        cleanup = {
            "state": "unknown", "method": "none",
            "removal_verified": False, "detail": "host-only-detail",
        }
        timeout = project_synthetic_receipt(
            request(), receipt(
                "timeout", exit_code=None, cleanup=cleanup,
                commands=[command(exit_code=2)],
            ),
            expected_run_id=RUN_ID,
            public_stdout="public timeout\n",
        )
        self.assertEqual(timeout["status"], "timeout")
        self.assertEqual(timeout["cleanup"], "unknown")
        self.assertEqual(timeout["stdout"], "public timeout\n")
        self.assertIsNone(timeout["exit_code"])

        for status in ("check_passed", "check_failed"):
            with self.subTest(status=status):
                result = project_synthetic_receipt(
                    request(), receipt(status, exit_code=0, cleanup=cleanup),
                    expected_run_id=RUN_ID,
                    public_stdout="must not leak\n",
                )
                self.assertEqual(result["status"], "infrastructure_error")
                self.assertEqual(result["cleanup"], "unknown")
                self.assertEqual(result["stdout"], "")
                self.assertEqual(result["stderr"], "")
                self.assertIsNone(result["exit_code"])

    def test_identity_mismatch_and_production_evidence_fail_closed(self):
        cases = (
            receipt("check_passed", run_id="other-run"),
            receipt("check_passed", candidate_sha="b" * 64),
            receipt("check_passed", candidate_file="preview.py"),
            receipt("check_passed", evidence="real_container_check"),
            {"schema_version": "wrong", "evidence_kind": "synthetic_executor_control"},
            None,
            [],
        )
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                result = project_synthetic_receipt(
                    request(), value, expected_run_id=RUN_ID,
                    public_stdout="should be dropped\n",
                )
                self.assertEqual(result["status"], "infrastructure_error")
                self.assertEqual(result["cleanup"], "unknown")
                self.assertEqual(result["stdout"], "")
                self.assertEqual(result["request_id"], "ep-1:check:1")
                self.assertEqual(result["candidate_sha256"], SHA)
                self.assertEqual(result["requirements_version"], "v1")

    def test_unsettled_commands_and_inconsistent_exits_fail_closed(self):
        unsettled = [command(reaped=False)]
        cases = (
            receipt("check_passed", commands=unsettled),
            receipt("check_passed", exit_code=1),
            receipt("check_failed", exit_code=0),
            receipt("check_passed", container_value=container(3)),
            receipt("check_passed", cleanup={
                "state": "settled", "method": "removed_verified",
                "removal_verified": False, "detail": None,
            }),
            receipt("check_passed", cleanup={
                "state": "settled", "method": "not_created",
                "removal_verified": False, "detail": "not_created",
            }),
            receipt("check_passed", cleanup={
                "state": "unknown", "method": "none",
                "removal_verified": False, "detail": None,
            }),
        )
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                result = project_synthetic_receipt(
                    request(), value, expected_run_id=RUN_ID
                )
                self.assertEqual(result["status"], "infrastructure_error")
                self.assertEqual(result["cleanup"], "unknown")
                self.assertEqual(result["stdout"], "")

    def test_not_created_requires_non_pass_container_null_and_settled(self):
        not_created = {
            "state": "settled", "method": "not_created",
            "removal_verified": False, "detail": "create_failed_no_id",
        }
        passed = project_synthetic_receipt(
            request(), receipt(
                "check_passed", exit_code=0, cleanup=not_created,
                container_value=None,
            ),
            expected_run_id=RUN_ID,
        )
        self.assertEqual(passed["status"], "infrastructure_error")
        with_container = project_synthetic_receipt(
            request(), receipt(
                "refused", exit_code=2, cleanup=not_created,
                container_value=container(),
            ),
            expected_run_id=RUN_ID,
        )
        self.assertEqual(with_container["status"], "infrastructure_error")
        self.assertEqual(with_container["cleanup"], "unknown")

    def test_display_output_bounds_and_utf8(self):
        exact = "x" * 32768
        result = project_synthetic_receipt(
            request(), receipt("check_passed"),
            expected_run_id=RUN_ID, public_stdout=exact,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["stdout"], exact)
        for index, (stdout, stderr) in enumerate((
            ("x" * 32769, ""),
            ("", "x" * 32769),
            (123, ""),
            ("", None),
            ("\ud800", ""),
        )):
            with self.subTest(case=index):
                failed = project_synthetic_receipt(
                    request(), receipt("check_passed"),
                    expected_run_id=RUN_ID,
                    public_stdout=stdout, public_stderr=stderr,
                )
                self.assertEqual(failed["status"], "infrastructure_error")
                self.assertEqual(failed["cleanup"], "unknown")
                self.assertEqual(failed["stdout"], "")
                self.assertEqual(failed["stderr"], "")

    def test_malformed_request_and_expected_run_id_are_fixed_errors(self):
        cases = (
            {},
            request(extra="x"),
            request(workspace="relative/path"),
            request(request_id="bad id"),
            request(candidate_sha256="A" * 64),
            request(candidate_filename="other.py"),
            request(requirements_version="v3"),
            request(workspace=123),
        )
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises(CheckReceiptProjectionError) as raised:
                    project_synthetic_receipt(
                        value, receipt("check_passed"),
                        expected_run_id=RUN_ID,
                    )
                self.assertEqual(
                    raised.exception.code, "invalid_check_request"
                )
        for run_id in ("", 123, "run\nid"):
            with self.subTest(run_id=repr(run_id)):
                with self.assertRaises(CheckReceiptProjectionError) as raised:
                    project_synthetic_receipt(
                        request(), receipt("check_passed"),
                        expected_run_id=run_id,
                    )
                self.assertEqual(
                    raised.exception.code, "invalid_expected_run_id"
                )

    def test_projection_reads_no_files(self):
        with mock.patch("builtins.open", side_effect=AssertionError("file read")):
            result = project_synthetic_receipt(
                request(), receipt("check_passed"),
                expected_run_id=RUN_ID,
            )
        self.assertEqual(result["status"], "passed")


    def test_failed_check_preserves_attach_zero_with_inspected_exit_seven(self):
        # Pinned Rust classifier accepts attach CLI exit 0 or the inspected exit.
        result = project_synthetic_receipt(
            request(), receipt(
                "check_failed", exit_code=7,
                container_value=container(0),
            ),
            expected_run_id=RUN_ID,
            public_stdout="public failure\n",
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["cleanup"], "settled")
        self.assertEqual(result["stdout"], "public failure\n")

        mismatch = project_synthetic_receipt(
            request(), receipt(
                "check_failed", exit_code=7,
                container_value=container(3),
            ),
            expected_run_id=RUN_ID,
        )
        self.assertEqual(mismatch["status"], "infrastructure_error")
        self.assertEqual(mismatch["cleanup"], "unknown")
        self.assertEqual(mismatch["stdout"], "")

    def test_malformed_status_returns_structured_fallback(self):
        cleanup = {
            "state": "settled", "method": "not_created",
            "removal_verified": False, "detail": "create_failed_no_id",
        }
        malformed = receipt(
            [], exit_code=None, cleanup=cleanup, commands=[],
            container_value=None,
        )
        malformed["status"] = []
        result = project_synthetic_receipt(
            request(), malformed, expected_run_id=RUN_ID,
            public_stdout="must be dropped\n",
        )
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["cleanup"], "unknown")
        self.assertEqual(result["stdout"], "")
        self.assertEqual(result["stderr"], "")
        self.assertIsNone(result["exit_code"])
        self.assertEqual(result["request_id"], "ep-1:check:1")
        self.assertEqual(result["candidate_sha256"], SHA)
        self.assertEqual(result["requirements_version"], "v1")


if __name__ == "__main__":
    unittest.main()
