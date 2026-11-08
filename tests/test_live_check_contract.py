"""Focused live check-provenance/bridge/driver/loop witnesses. No provider."""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from episode import check_bridge, driver, e2e

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / "tmp" / "live-check-contract"


def _raw_receipt(request, *, setup_error, run_id="r6-check-contract"):
    commands = [{
        "spawn_failed": False,
        "direct_child_reaped": True,
        "reader_settled": True,
    }]
    workspace = {
        "candidate_file": request["candidate_filename"],
        "candidate_sha256": request["candidate_sha256"],
        "inventory": [],
    }
    if setup_error:
        return {
            "schema_version": "container-check-0.1",
            "evidence_kind": "real_container_check",
            "run_id": run_id,
            "workspace": workspace,
            "status": "infrastructure_error",
            "public_check_exit": None,
            "cleanup": {
                "state": "settled", "method": "not_created",
                "removal_verified": False, "detail": "create_not_attempted",
            },
            "container": None,
            "commands": commands,
        }
    return {
        "schema_version": "container-check-0.1",
        "evidence_kind": "real_container_check",
        "run_id": run_id,
        "workspace": workspace,
        "status": "check_passed",
        "public_check_exit": 0,
        "cleanup": {
            "state": "settled", "method": "removed",
            "removal_verified": True, "detail": "container_removed",
        },
        "container": {
            "id": "container-r6", "attached": {
                "exit_code": 0, "direct_child_reaped": True,
            },
        },
        "commands": commands,
    }


def _real_port(*, setup_error):
    def port(request):
        raw = _raw_receipt(request, setup_error=setup_error)
        return check_bridge.project_real_receipt(
            request, raw, expected_run_id="r6-check-contract",
            expected_requirements_version="v1",
        )
    return port


def _model_port(run_root):
    source = (ROOT / "references" / "retry-processing" / "correct" /
              "processor.py").read_text(encoding="utf-8")
    return e2e.scripted_r1_model_port(
        run_root / "package" / "agent", source,
    )


class LiveCheckContractTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)

    def _run(self, *, setup_error, check_evidence_kind):
        run_root = SCRATCH / ("run-" + uuid.uuid4().hex)
        try:
            envelope = driver.run_scenario(
                ROOT, "R1", run_root=run_root,
                episode_id="ep-check-contract", arm="ordinary",
                limits=dict(driver.SMOKE_LIMITS),
                model_port=_model_port(run_root),
                check_port=_real_port(setup_error=setup_error),
                check_evidence_kind=check_evidence_kind,
            )
            return envelope, run_root
        except Exception:
            shutil.rmtree(run_root, ignore_errors=True)
            raise

    def test_settled_not_created_real_setup_error_is_infrastructure(self):
        envelope, run_root = self._run(
            setup_error=True, check_evidence_kind="real_container_check",
        )
        self.assertEqual(envelope["status"], "episode_returned")
        result = envelope["result"]
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "check_infrastructure_error")
        events = [
            json.loads(line) for line in
            (run_root / "episode" / "events.jsonl").read_text(
                encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertFalse(any(
            event.get("type") == "check_error"
            and event.get("reason") == "check_evidence_kind"
            for event in events
        ))
        shutil.rmtree(run_root, ignore_errors=True)

    def test_valid_live_shaped_receipt_is_accepted(self):
        envelope, run_root = self._run(
            setup_error=False, check_evidence_kind="real_container_check",
        )
        self.assertEqual(envelope["status"], "episode_returned")
        result = envelope["result"]
        self.assertNotEqual(result["status"], "infrastructure_error")
        self.assertNotEqual(result["stop_reason"], "check_invalid_receipt")
        shutil.rmtree(run_root, ignore_errors=True)

    def test_synthetic_mode_rejects_real_provenance(self):
        envelope, run_root = self._run(
            setup_error=False, check_evidence_kind="synthetic_check",
        )
        self.assertEqual(envelope["status"], "episode_returned")
        result = envelope["result"]
        self.assertEqual(result["status"], "infrastructure_error")
        self.assertEqual(result["stop_reason"], "check_invalid_receipt")
        shutil.rmtree(run_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
