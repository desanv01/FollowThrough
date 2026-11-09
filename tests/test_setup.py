"""Original offline setup controls; Git identity is injected, no runtime calls."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from adapters.budget import CampaignLedger
from episode import live, setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "tmp" / "setup-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "trusted"
        self.source = self.root / "source"
        self.project.mkdir()
        self.source.mkdir()
        self.args = argparse.Namespace(
            prepare=True, initialize_ledger=True,
            project_root=str(self.project), source_root=str(self.source),
            campaign_root=str(self.project / "campaign"),
            run_root=str(self.project / "runs" / "fresh"),
            campaign_id="fixture-campaign", episode_id="ep:1", operation_id="op:1",
            run_id="run:1", stage="pilot", scenario_id="C2", arm="structured_progress",
            trial=2, dataset_id="original-setup-fixture",
        )
        self.identity = mock.patch.object(setup, "_source_identity", return_value={
            "commit": "a" * 40, "tree": "b" * 40})
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_fresh_explicit_setup_derives_bounds_and_draft_refuses_paid_gate(self):
        result = setup.prepare(self.args)
        self.assertFalse(result["runtime_qualified"])
        self.assertEqual(result["ledger"], str(live.check_ledger_path(self.project)))
        self.assertFalse(Path(self.args.run_root).exists())
        self.assertTrue(Path(self.args.run_root).parent.is_dir())
        gate_path = Path(result["gate"])
        gate = json.loads(gate_path.read_text())
        self.assertEqual(gate["limits"]["max_operation_reservation_micro_usd"], 600000)
        self.assertEqual(set(gate["limits"]), set(live.G4_BOUND_KEYS))
        self.assertEqual(gate["source"], {"commit": "a" * 40, "tree": "b" * 40})
        self.assertEqual(gate["operation"]["scenario_id"], "C2")
        released = json.loads((Path(self.args.campaign_root) / "leases/docker.json").read_text())
        self.assertFalse(released["unknown_settlement"])
        self.assertEqual(released["active_docker_operations"], 0)
        with self.assertRaises(live.LiveGateError) as error:
            live.verify_g4_gate(gate_path, self.source, {})
        self.assertEqual(error.exception.code, "g4_not_ready")

    def test_existing_ledger_is_read_only_and_existing_campaign_never_overwritten(self):
        live.initialize_ledger(self.project, self.args.campaign_id)
        ledger = live.check_ledger_path(self.project)
        before = hashlib.sha256(ledger.read_bytes()).hexdigest()
        self.args.initialize_ledger = False
        setup.prepare(self.args)
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), before)
        sentinel = Path(self.args.campaign_root) / "retained.txt"
        sentinel.write_text("keep")
        with self.assertRaises(setup.SetupError):
            setup.prepare(self.args)
        self.assertEqual(sentinel.read_text(), "keep")
        self.args.campaign_root = str(self.project / "other-campaign")
        self.args.initialize_ledger = True
        with self.assertRaises(setup.SetupError):
            setup.prepare(self.args)
        self.assertFalse(Path(self.args.campaign_root).exists())
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), before)

    def test_protected_output_and_existing_run_refused_before_any_write(self):
        for kind in ("protected", "existing_run"):
            with self.subTest(kind=kind):
                if kind == "protected":
                    self.args.campaign_root = str(self.source / "tasks" / "campaign")
                else:
                    self.args.campaign_root = str(self.project / "campaign")
                    Path(self.args.run_root).mkdir(parents=True)
                with self.assertRaises(setup.SetupError):
                    setup.prepare(self.args)
                self.assertFalse(live.check_ledger_path(self.project).exists())
                self.assertFalse(Path(self.args.campaign_root).exists())

    def test_source_readiness_and_explicit_prepare_are_required(self):
        self.args.prepare = False
        with self.assertRaises(setup.SetupError):
            setup.prepare(self.args)
        self.args.prepare = True
        with mock.patch.object(setup, "_source_identity", side_effect=setup.SetupError("source_not_clean")):
            with self.assertRaises(setup.SetupError):
                setup.prepare(self.args)
        self.assertFalse(Path(self.args.campaign_root).exists())
        self.assertFalse(live.check_ledger_path(self.project).exists())


if __name__ == "__main__":
    unittest.main()
