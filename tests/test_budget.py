"""Offline tests for the durable SQLite campaign spending ledger.

All project roots, databases and operations are synthetic and live under the
candidate overlay's ``tmp`` directory.
"""

from decimal import Decimal
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from adapters import budget


OVERLAY_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = OVERLAY_ROOT / "tmp"
CAMPAIGN = "synthetic-campaign"
SOURCE_KEY_MARKER = "synthetic-ledger-marker-EEEE4444"

CHILD_SCRIPT = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from adapters.budget import BudgetError, CampaignLedger\n"
    "root, operation, ready, go, result = sys.argv[1:6]\n"
    "ledger = CampaignLedger(root, campaign_id='synthetic-race', cap_usd='1')\n"
    "Path(ready).write_text('ready', encoding='ascii')\n"
    "deadline = time.time() + 25\n"
    "while not Path(go).exists():\n"
    "    if time.time() > deadline:\n"
    "        Path(result).write_text('timeout', encoding='ascii')\n"
    "        raise SystemExit(3)\n"
    "    time.sleep(0.01)\n"
    "try:\n"
    "    ledger.reserve(operation, '1')\n"
    "except BudgetError as failure:\n"
    "    Path(result).write_text('error:' + failure.code, encoding='ascii')\n"
    "else:\n"
    "    Path(result).write_text('reserved', encoding='ascii')\n"
)

CHILD_INIT_SCRIPT = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from adapters.budget import BudgetError, CampaignLedger\n"
    "root, campaign, ready, go, result = sys.argv[1:6]\n"
    "Path(ready).write_text('ready', encoding='ascii')\n"
    "deadline = time.time() + 25\n"
    "while not Path(go).exists():\n"
    "    if time.time() > deadline:\n"
    "        Path(result).write_text('timeout', encoding='ascii')\n"
    "        raise SystemExit(3)\n"
    "    time.sleep(0.01)\n"
    "try:\n"
    "    CampaignLedger(root, campaign_id=campaign, cap_usd='1')\n"
    "except BudgetError as failure:\n"
    "    Path(result).write_text('error:' + failure.code, encoding='ascii')\n"
    "else:\n"
    "    Path(result).write_text('success', encoding='ascii')\n"
)

# Deliberately abandons an uncommitted transaction with os._exit to leave a hot
# rollback journal.  This is a synthetic fixture action, not a product crash claim.
HOT_CHILD_SCRIPT = (
    "import os, sqlite3, sys\n"
    "connection = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
    "connection.execute('PRAGMA cache_size = 5')\n"
    "connection.execute('BEGIN IMMEDIATE')\n"
    "connection.execute(sys.argv[2])\n"
    "os._exit(0)\n"
)

TRIGGER_SQL = (
    "CREATE TRIGGER incompatible_operation_policy"
    " BEFORE INSERT ON operations BEGIN SELECT RAISE(IGNORE); END;"
)


def storage_fingerprint(database):
    hashes = {}
    for suffix in ("", "-journal", "-wal", "-shm"):
        path = database.with_name(database.name + suffix)
        try:
            hashes[suffix] = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            hashes[suffix] = None
    return hashes


class CampaignLedgerTest(unittest.TestCase):
    def setUp(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = SCRATCH_ROOT / ("budget-" + uuid.uuid4().hex[:12])
        os.makedirs(self.root, exist_ok=False)
        self.addCleanup(shutil.rmtree, self.root, True)

    def make_project(self, name, *, campaign_id=CAMPAIGN, cap_usd="20"):
        project = self.root / name
        project.mkdir(parents=True, exist_ok=True)
        return project, budget.CampaignLedger(
            project, campaign_id=campaign_id, cap_usd=cap_usd
        )

    def assert_error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(budget.BudgetError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def test_exact_cap_boundary_and_full_envelope_reservation(self):
        project, ledger = self.make_project("boundary", cap_usd="0.000002")
        self.assertEqual(ledger.reserve("op-one", "0.000001"), 1)
        self.assertEqual(ledger.reserve("op-two", "0.000001"), 1)
        self.assert_error("budget_cap_exceeded", ledger.reserve, "op-three", "0.000001")
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 2)
        self.assertEqual(snapshot["remaining_microdollars"], 0)
        self.assertEqual(snapshot["cap_microdollars"], 2)
        self.assertEqual(len(snapshot["operations"]), 2)

        project, ledger = self.make_project("full-cap")
        self.assertEqual(ledger.reserve("op-all", "20"), 20_000_000)
        self.assert_error("budget_cap_exceeded", ledger.reserve, "op-extra", "0.000001")

    def test_request_amounts_round_up_to_whole_microdollars(self):
        project, ledger = self.make_project("rounding", cap_usd="0.000003")
        self.assertEqual(ledger.reserve("op-tiny", "0.0000001"), 1)
        self.assertEqual(ledger.reserve("op-nineteen", "0.0000019"), 2)
        snapshot = ledger.snapshot()
        by_id = {item["operation_id"]: item for item in snapshot["operations"]}
        self.assertEqual(by_id["op-tiny"]["amount_microdollars"], 1)
        self.assertEqual(by_id["op-nineteen"]["amount_microdollars"], 2)
        self.assertEqual(by_id["op-tiny"]["amount_usd"], "0.000001")
        self.assertEqual(by_id["op-nineteen"]["amount_usd"], "0.000002")

    def test_cap_rounds_down_to_whole_microdollars(self):
        project, ledger = self.make_project("cap-round", cap_usd="0.0000019")
        self.assertEqual(ledger.cap_microdollars, 1)
        self.assertEqual(ledger.reserve("op-one", "0.0000001"), 1)
        self.assert_error("budget_cap_exceeded", ledger.reserve, "op-two", "0.0000001")

    def test_invalid_caps_are_rejected_without_creating_a_ledger(self):
        cases = (
            "0", "-1", "0.0000001", "20.000001", "NaN", "Infinity",
            "1e100000", 20.0, True, None, "1_0", " 20 ", "9" * 100,
        )
        for index, value in enumerate(cases):
            project = self.root / f"bad-cap-{index}"
            project.mkdir()
            with self.subTest(index=index):
                self.assert_error(
                    "invalid_cap", budget.CampaignLedger, project,
                    campaign_id=CAMPAIGN, cap_usd=value,
                )
                self.assertFalse(
                    (project / ".coordination" / "provider-budget.sqlite3").exists(),
                    "invalid cap created a ledger",
                )

    def test_invalid_campaign_ids_are_rejected(self):
        for value in ("", "has space", "semi;colon", "x" * 129, None, 123, b"bytes"):
            with self.subTest(kind=type(value).__name__):
                self.assert_error(
                    "invalid_campaign_id", budget.CampaignLedger, self.root,
                    campaign_id=value,
                )

    def test_invalid_operation_ids_are_rejected_before_any_write(self):
        project, ledger = self.make_project("ids")
        for value in ("", "has space", "semi;colon", "slash/inside", "x" * 129, None, 123):
            with self.subTest(kind=type(value).__name__):
                self.assert_error("invalid_operation_id", ledger.reserve, value, "1")
        self.assertEqual(ledger.snapshot()["operations"], [])

    def test_invalid_amounts_are_rejected(self):
        project, ledger = self.make_project("amounts")
        cases = (
            0, -1, 1, 0.1, True, None, [], Decimal("NaN"), Decimal("Infinity"),
            "", " ", "1_0", "0x10", "nan", "-0", "1e100000", "9" * 100,
        )
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                self.assert_error("invalid_amount", ledger.reserve, f"op-{index}", value)
        self.assertEqual(ledger.snapshot()["reserved_microdollars"], 0)

    def test_immutable_metadata_and_reopening(self):
        project, ledger = self.make_project("reopen")
        ledger.reserve("op-keep", "1")
        first = ledger.snapshot()
        reopened = budget.CampaignLedger(project, campaign_id=CAMPAIGN, cap_usd="20")
        self.assertTrue(reopened.snapshot() == first, "reopen changed ledger state")

        self.assert_error(
            "budget_campaign_mismatch", budget.CampaignLedger, project,
            campaign_id="other-campaign",
        )
        self.assert_error(
            "budget_cap_mismatch", budget.CampaignLedger, project,
            campaign_id=CAMPAIGN, cap_usd="19",
        )
        again = budget.CampaignLedger(project, campaign_id=CAMPAIGN, cap_usd="20")
        self.assertEqual(again.snapshot()["reserved_microdollars"], 1_000_000)
        self.assertEqual(again.snapshot()["operations"][0]["status"], "pending")
        self.assertEqual(again.snapshot()["schema_version"], 1)
        self.assertEqual(again.snapshot()["campaign_id"], CAMPAIGN)

    def test_unknown_schema_and_corrupt_metadata_fail_closed(self):
        project, ledger = self.make_project("schema")
        connection = sqlite3.connect(ledger.ledger_path)
        try:
            connection.execute("UPDATE ledger_meta SET value = '2' WHERE key = 'schema_version'")
            connection.commit()
        finally:
            connection.close()
        self.assert_error(
            "budget_schema_mismatch", budget.CampaignLedger, project, campaign_id=CAMPAIGN
        )

        project, ledger = self.make_project("extra-meta")
        connection = sqlite3.connect(ledger.ledger_path)
        try:
            connection.execute("INSERT INTO ledger_meta (key, value) VALUES ('extra', 'x')")
            connection.commit()
        finally:
            connection.close()
        self.assert_error(
            "budget_ledger_corrupt", budget.CampaignLedger, project, campaign_id=CAMPAIGN
        )

        project, ledger = self.make_project("missing-table")
        connection = sqlite3.connect(ledger.ledger_path)
        try:
            connection.execute("DROP TABLE operations")
            connection.commit()
        finally:
            connection.close()
        self.assert_error(
            "budget_ledger_corrupt", budget.CampaignLedger, project, campaign_id=CAMPAIGN
        )

    def test_corrupt_and_unreadable_storage_are_not_replaced(self):
        project = self.root / "corrupt"
        ledger_dir = project / ".coordination"
        ledger_dir.mkdir(parents=True)
        database = ledger_dir / "provider-budget.sqlite3"
        database.write_bytes(b"this is not a sqlite database" * 20)
        before = database.read_bytes()
        self.assert_error(
            "budget_storage_corrupt", budget.CampaignLedger, project, campaign_id=CAMPAIGN
        )
        self.assertEqual(database.read_bytes(), before, "corrupt ledger was replaced")

        project = self.root / "db-directory"
        (project / ".coordination" / "provider-budget.sqlite3").mkdir(parents=True)
        self.assert_error(
            "budget_storage_unavailable", budget.CampaignLedger, project, campaign_id=CAMPAIGN
        )

        missing = self.root / "does-not-exist"
        self.assert_error(
            "budget_storage_unavailable", budget.CampaignLedger, missing, campaign_id=CAMPAIGN
        )
        self.assertFalse(missing.exists(), "ledger created a missing project root")

    def test_locked_storage_fails_closed_then_recovers(self):
        project, ledger = self.make_project("locked")
        blocker = sqlite3.connect(ledger.ledger_path, timeout=1.0, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with patch.object(budget, "LOCK_TIMEOUT_SECONDS", 0.2):
                self.assert_error(
                    "budget_storage_locked", ledger.reserve, "op-locked", "1"
                )
                try:
                    budget.CampaignLedger(project, campaign_id=CAMPAIGN)
                except budget.BudgetError as failure:
                    self.assertIn(
                        failure.code,
                        ("budget_storage_locked", "budget_storage_not_ready"),
                    )
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        self.assertEqual(ledger.reserve("op-after-unlock", "1"), 1_000_000)
        self.assertEqual(ledger.snapshot()["reserved_microdollars"], 1_000_000)

    def test_duplicate_operations_never_redispatch(self):
        project, ledger = self.make_project("duplicates")
        ledger.reserve("op-success", "1")
        ledger.finish("op-success", "success")
        self.assert_error("duplicate_operation", ledger.reserve, "op-success", "1")

        ledger.reserve("op-failure", "1")
        ledger.finish("op-failure", "uncertain")
        self.assert_error("duplicate_operation", ledger.reserve, "op-failure", "1")

        reopened = budget.CampaignLedger(project, campaign_id=CAMPAIGN)
        self.assert_error("duplicate_operation", reopened.reserve, "op-success", "1")
        self.assert_error("duplicate_operation", reopened.reserve, "op-failure", "1")

    def test_pending_reservation_survives_reopening_and_consumes_allowance(self):
        project, ledger = self.make_project("pending", cap_usd="1")
        ledger.reserve("op-pending", "0.75")
        reopened = budget.CampaignLedger(project, campaign_id=CAMPAIGN, cap_usd="1")
        snapshot = reopened.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 750_000)
        self.assertEqual(snapshot["operations"][0]["status"], "pending")
        self.assert_error("duplicate_operation", reopened.reserve, "op-pending", "0.1")
        self.assert_error("budget_cap_exceeded", reopened.reserve, "op-new", "0.250001")

    def test_every_terminal_outcome_retains_the_reservation(self):
        project, ledger = self.make_project("outcomes", cap_usd="10")
        for outcome in budget.OPERATION_OUTCOMES:
            operation = f"op-{outcome}"
            ledger.reserve(operation, "1")
            before = ledger.snapshot()["reserved_microdollars"]
            ledger.finish(operation, outcome)
            after = ledger.snapshot()
            self.assertEqual(after["reserved_microdollars"], before)
            entry = next(item for item in after["operations"] if item["operation_id"] == operation)
            self.assertEqual(entry["status"], outcome)
            self.assertIsNone(entry["estimated_cost_microdollars"])
        self.assertEqual(ledger.snapshot()["reserved_microdollars"], 4_000_000)

    def test_estimates_are_recorded_without_refunding(self):
        project, ledger = self.make_project("estimates")
        ledger.reserve("op-estimate", "1")
        ledger.finish("op-estimate", "success", estimated_cost_usd="0.001")
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        entry = {item["operation_id"]: item for item in snapshot["operations"]}["op-estimate"]
        self.assertEqual(entry["estimated_cost_microdollars"], 1_000)
        self.assertEqual(entry["estimated_cost_usd"], "0.001000")

        ledger.reserve("op-zero", "1")
        ledger.finish("op-zero", "success", estimated_cost_usd="0")
        entries = {item["operation_id"]: item for item in ledger.snapshot()["operations"]}
        self.assertEqual(entries["op-zero"]["estimated_cost_microdollars"], 0)

        ledger.reserve("op-tiny", "1")
        ledger.finish("op-tiny", "success", estimated_cost_usd="0.0000001")
        entries = {item["operation_id"]: item for item in ledger.snapshot()["operations"]}
        self.assertEqual(entries["op-tiny"]["estimated_cost_microdollars"], 1)

    def test_finish_validation_and_double_finish(self):
        project, ledger = self.make_project("finish")
        self.assert_error("unknown_operation", ledger.finish, "op-absent", "success")
        ledger.reserve("op-once", "1")
        for outcome in ("settled", "", None, 1):
            with self.subTest(outcome=outcome):
                self.assert_error("invalid_outcome", ledger.finish, "op-once", outcome)
        for value in (-1, "NaN", "Infinity", 1.5, True, "1_0", "1e100000"):
            with self.subTest(estimate=type(value).__name__):
                self.assert_error(
                    "invalid_estimate", ledger.finish, "op-once", "success",
                    estimated_cost_usd=value,
                )
        ledger.finish("op-once", "success")
        self.assert_error("operation_already_finished", ledger.finish, "op-once", "success")
        self.assert_error("operation_already_finished", ledger.finish, "op-once", "uncertain")

    def test_snapshot_is_exact_json_compatible_metadata_only(self):
        project, ledger = self.make_project("snapshot", cap_usd="2")
        ledger.reserve("op-metadata", "0.5")
        snapshot = ledger.snapshot()
        expected_keys = {
            "schema_version", "campaign_id", "cap_microdollars", "cap_usd",
            "reserved_microdollars", "reserved_usd", "remaining_microdollars",
            "remaining_usd", "operations",
        }
        self.assertEqual(set(snapshot), expected_keys)
        self.assertEqual(snapshot["cap_usd"], "2.000000")
        self.assertEqual(snapshot["reserved_usd"], "0.500000")
        self.assertEqual(snapshot["remaining_usd"], "1.500000")
        entry = snapshot["operations"][0]
        self.assertEqual(
            set(entry),
            {
                "operation_id", "amount_microdollars", "amount_usd", "status",
                "estimated_cost_microdollars", "estimated_cost_usd",
            },
        )
        self.assertEqual(entry["amount_microdollars"], 500_000)
        self.assertTrue(
            SOURCE_KEY_MARKER not in repr(snapshot) and "request" not in repr(snapshot).lower(),
            "snapshot exposed non-budget data",
        )

    def test_no_release_reset_or_top_up_api_exists(self):
        project, ledger = self.make_project("no-api")
        for name in ("release", "refund", "reset", "top_up", "increase_cap", "retry"):
            with self.subTest(name=name):
                self.assertFalse(
                    hasattr(ledger, name), f"unexpected budget mutation API: {name}"
                )
        ledger.reserve("op-no-refund", "1")
        ledger.finish("op-no-refund", "provider_error")
        self.assertEqual(ledger.snapshot()["reserved_microdollars"], 1_000_000)

    def test_repeated_serial_reservations_never_exceed_the_cap(self):
        project, ledger = self.make_project("serial", cap_usd="0.000010")
        accepted = 0
        rejected = 0
        for index in range(30):
            try:
                ledger.reserve(f"op-serial-{index:02d}", "0.000001")
            except budget.BudgetError as failure:
                self.assertEqual(failure.code, "budget_cap_exceeded")
                rejected += 1
            else:
                accepted += 1
        self.assertEqual((accepted, rejected), (10, 20))
        self.assertEqual(ledger.snapshot()["reserved_microdollars"], 10)

    def test_two_overlapping_connections_admit_only_one_request(self):
        project, ledger_one = self.make_project("thread-race", cap_usd="1")
        ledger_two = budget.CampaignLedger(project, campaign_id=CAMPAIGN, cap_usd="1")
        barrier = threading.Barrier(2, timeout=10)
        results = []
        results_lock = threading.Lock()

        def attempt(ledger, operation):
            try:
                barrier.wait()
                ledger.reserve(operation, "1")
            except budget.BudgetError as failure:
                outcome = failure.code
            except BaseException as failure:  # pragma: no cover - diagnostic path
                outcome = "unexpected:" + type(failure).__name__
            else:
                outcome = "reserved"
            with results_lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=attempt, args=(ledger_one, "thread-a")),
            threading.Thread(target=attempt, args=(ledger_two, "thread-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "race thread did not settle")
        self.assertEqual(len(results), 2)
        self.assertEqual(results.count("reserved"), 1, "race admitted more than one request")
        loser = next(result for result in results if result != "reserved")
        self.assertIn(loser, ("budget_cap_exceeded", "budget_storage_locked"))
        snapshot = ledger_one.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        self.assertEqual(len(snapshot["operations"]), 1)

    def test_two_overlapping_processes_admit_only_one_request(self):
        project = self.root / "process-race"
        control = project / "control"
        control.mkdir(parents=True)
        # Both children must take the existing-ledger path; first-create racing is
        # covered separately.
        budget.CampaignLedger(project, campaign_id="synthetic-race", cap_usd="1")
        go = control / "go"
        processes = []
        try:
            for operation in ("process-a", "process-b"):
                ready = control / f"ready-{operation}"
                result = control / f"result-{operation}"
                process = subprocess.Popen(
                    [
                        sys.executable, "-B", "-c", CHILD_SCRIPT,
                        str(project), operation, str(ready), str(go), str(result),
                    ],
                    cwd=str(OVERLAY_ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                processes.append((process, ready, result, operation))
            deadline = time.time() + 25
            while not all(ready.exists() for _, ready, _, _ in processes):
                if time.time() > deadline:
                    self.fail("child processes did not reach the start barrier")
                time.sleep(0.01)
            go.write_text("go", encoding="ascii")
            for process, _, _, _ in processes:
                try:
                    process.wait(timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            self.assertTrue(
                all(process.returncode == 0 for process, _, _, _ in processes),
                "a race child exited unsuccessfully",
            )
            results = []
            for _, _, result, operation in processes:
                self.assertTrue(result.exists(), f"race child {operation} left no result")
                results.append(result.read_text(encoding="ascii").strip())
            self.assertEqual(results.count("reserved"), 1, "process race admitted more than one request")
            loser = next(result for result in results if result != "reserved")
            self.assertEqual(loser, "error:budget_cap_exceeded")
        finally:
            for process, _, _, _ in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        ledger = budget.CampaignLedger(project, campaign_id="synthetic-race", cap_usd="1")
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot["reserved_microdollars"], 1_000_000)
        self.assertEqual(len(snapshot["operations"]), 1)
        self.assertEqual(snapshot["remaining_microdollars"], 0)


    # -- r2 correction regressions (F1/F2) --------------------------------

    def tamper(self, ledger, sql, params=()):
        connection = sqlite3.connect(ledger.ledger_path)
        try:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def operation_count(self, ledger):
        connection = sqlite3.connect(ledger.ledger_path)
        try:
            return connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
        finally:
            connection.close()

    def test_new_project_initializes_one_bound_absolute_ledger(self):
        project = self.root / "fresh"
        project.mkdir()
        ledger = budget.CampaignLedger(project, campaign_id=CAMPAIGN, cap_usd="1")
        self.assertTrue(os.path.isabs(ledger.ledger_path), "ledger path is not absolute")
        self.assertTrue(
            os.path.isabs(ledger.project_root), "project root is not bound absolute"
        )
        expected = project / ".coordination" / "provider-budget.sqlite3"
        self.assertEqual(
            os.path.normcase(ledger.ledger_path), os.path.normcase(str(expected.resolve()))
        )
        self.assertEqual(ledger.reserve("op-new", "1"), 1_000_000)
        self.assertEqual(ledger.snapshot()["reserved_microdollars"], 1_000_000)

    def test_preexisting_invalid_files_are_refused_unchanged(self):
        cases = []

        blank = self.root / "blank"
        (blank / ".coordination").mkdir(parents=True)
        (blank / ".coordination" / "provider-budget.sqlite3").write_bytes(b"")
        cases.append((blank, "budget_storage_not_ready"))

        unrelated = self.root / "unrelated"
        (unrelated / ".coordination").mkdir(parents=True)
        connection = sqlite3.connect(
            unrelated / ".coordination" / "provider-budget.sqlite3"
        )
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('synthetic')")
        connection.commit()
        connection.close()
        cases.append((unrelated, "budget_ledger_unrecognized"))

        no_schema = self.root / "no-schema"
        no_schema.mkdir()
        budget.CampaignLedger(no_schema, campaign_id=CAMPAIGN, cap_usd="1")
        connection = sqlite3.connect(
            no_schema / ".coordination" / "provider-budget.sqlite3"
        )
        connection.execute("DROP TABLE operations")
        connection.execute("DROP TABLE ledger_meta")
        connection.commit()
        connection.close()
        cases.append((no_schema, "budget_storage_not_ready"))

        damaged = self.root / "damaged"
        (damaged / ".coordination").mkdir(parents=True)
        (damaged / ".coordination" / "provider-budget.sqlite3").write_bytes(
            b"this is not sqlite" * 40
        )
        cases.append((damaged, "budget_storage_corrupt"))

        for project, code in cases:
            path = project / ".coordination" / "provider-budget.sqlite3"
            before = path.read_bytes()
            with self.subTest(code=code):
                self.assert_error(
                    code, budget.CampaignLedger, project,
                    campaign_id=CAMPAIGN, cap_usd="1",
                )
                self.assertEqual(
                    path.read_bytes(), before,
                    "existing unrecognized file was changed on refusal",
                )

    def test_simultaneous_first_constructors_initialize_once(self):
        project = self.root / "init-race"
        project.mkdir()
        barrier = threading.Barrier(2, timeout=10)
        results = []
        results_lock = threading.Lock()
        campaigns = ("campaign-alpha", "campaign-beta")

        def attempt(campaign):
            try:
                barrier.wait()
                budget.CampaignLedger(project, campaign_id=campaign, cap_usd="1")
            except budget.BudgetError as failure:
                outcome = "error:" + failure.code
            except BaseException as failure:  # pragma: no cover - diagnostic path
                outcome = "unexpected:" + type(failure).__name__
            else:
                outcome = "success:" + campaign
            with results_lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=attempt, args=(campaign,))
            for campaign in campaigns
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(
            any(thread.is_alive() for thread in threads), "init race did not settle"
        )
        successes = [item for item in results if item.startswith("success:")]
        self.assertEqual(
            len(successes), 1,
            "simultaneous constructors initialized more than one ledger identity",
        )
        loser = next(item for item in results if not item.startswith("success:"))
        self.assertIn(
            loser,
            (
                "error:budget_storage_not_ready",
                "error:budget_storage_locked",
                "error:budget_campaign_mismatch",
            ),
        )
        winner = successes[0].split(":", 1)[1]
        ledger = budget.CampaignLedger(project, campaign_id=winner, cap_usd="1")
        ledger.reserve("op-winner", "1")
        self.assertEqual(ledger.snapshot()["campaign_id"], winner)
        for other in campaigns:
            if other != winner:
                self.assert_error(
                    "budget_campaign_mismatch", budget.CampaignLedger, project,
                    campaign_id=other, cap_usd="1",
                )

    def test_two_processes_racing_first_initialization_are_bounded(self):
        project = self.root / "init-process"
        project.mkdir()
        control = project / "control"
        control.mkdir()
        go = control / "go"
        campaigns = ("process-init-alpha", "process-init-beta")
        processes = []
        try:
            for campaign in campaigns:
                ready = control / f"ready-{campaign}"
                result = control / f"result-{campaign}"
                process = subprocess.Popen(
                    [
                        sys.executable, "-B", "-c", CHILD_INIT_SCRIPT,
                        str(project), campaign, str(ready), str(go), str(result),
                    ],
                    cwd=str(OVERLAY_ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                processes.append((process, ready, result, campaign))
            deadline = time.time() + 25
            while not all(ready.exists() for _, ready, _, _ in processes):
                if time.time() > deadline:
                    self.fail("initializing children did not reach the barrier")
                time.sleep(0.01)
            go.write_text("go", encoding="ascii")
            for process, _, _, _ in processes:
                try:
                    process.wait(timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            self.assertTrue(
                all(process.returncode == 0 for process, _, _, _ in processes),
                "an initialization child exited unsuccessfully",
            )
            results = []
            for _, _, result, campaign in processes:
                self.assertTrue(
                    result.exists(), f"initialization child {campaign} left no result"
                )
                results.append(result.read_text(encoding="ascii").strip())
            successes = [item for item in results if item == "success"]
            self.assertEqual(
                len(successes), 1,
                "process race initialized more than one ledger identity",
            )
            loser = next(item for item in results if item != "success")
            self.assertIn(
                loser,
                (
                    "error:budget_storage_not_ready",
                    "error:budget_storage_locked",
                    "error:budget_campaign_mismatch",
                ),
            )
            winner = next(
                campaign
                for _, _, result, campaign in processes
                if result.read_text(encoding="ascii").strip() == "success"
            )
            ledger = budget.CampaignLedger(project, campaign_id=winner, cap_usd="1")
            ledger.reserve("op-winner", "1")
            self.assertEqual(ledger.snapshot()["campaign_id"], winner)
            for campaign in campaigns:
                if campaign != winner:
                    self.assert_error(
                        "budget_campaign_mismatch", budget.CampaignLedger, project,
                        campaign_id=campaign, cap_usd="1",
                    )
        finally:
            for process, _, _, _ in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_existing_handle_never_recreates_deleted_database(self):
        project, ledger = self.make_project("deleted")
        ledger.reserve("op-before", "1")
        path = Path(ledger.ledger_path)
        path.unlink()
        self.assert_error("budget_storage_unavailable", ledger.reserve, "op-after", "1")
        self.assertFalse(path.exists(), "existing handle recreated the deleted database")
        self.assert_error(
            "budget_storage_unavailable", ledger.finish, "op-before", "success"
        )
        self.assert_error("budget_storage_unavailable", ledger.snapshot)
        self.assertFalse(path.exists(), "read/finish recreated the deleted database")

    def test_ledger_path_is_bound_against_chdir(self):
        base = self.root / "chdir"
        first = base / "first" / "project"
        second = base / "second" / "project"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        previous = os.getcwd()
        try:
            os.chdir(first.parent)
            ledger = budget.CampaignLedger("project", campaign_id=CAMPAIGN, cap_usd="2")
            ledger.reserve("op-first", "1")
            os.chdir(second.parent)
            ledger.reserve("op-second", "1")
        finally:
            os.chdir(previous)
        self.assertTrue(os.path.isabs(ledger.ledger_path))
        self.assertEqual(
            ledger.snapshot()["reserved_microdollars"], 2_000_000,
            "chdir redirected the ledger",
        )
        self.assertFalse(
            (second / ".coordination").exists(), "chdir created a second ledger"
        )

    def test_metadata_change_after_construction_is_revalidated(self):
        cases = (
            ("cap_micro", "1000000", "budget_cap_mismatch"),
            ("campaign_id", "other-campaign", "budget_campaign_mismatch"),
            ("schema_version", "2", "budget_schema_mismatch"),
        )
        for key, value, code in cases:
            project, ledger = self.make_project(f"meta-{key}", cap_usd="2")
            ledger.reserve("op-valid", "1")
            connection = sqlite3.connect(ledger.ledger_path)
            connection.execute(
                "UPDATE ledger_meta SET value = ? WHERE key = ?", (value, key)
            )
            connection.commit()
            connection.close()
            with self.subTest(key=key):
                self.assert_error(code, ledger.reserve, "op-new", "1")
                self.assertEqual(
                    self.operation_count(ledger), 1, "tampered metadata still authorized a row"
                )
                self.assert_error(code, ledger.snapshot)
                self.assert_error(
                    code, budget.CampaignLedger, project,
                    campaign_id=CAMPAIGN, cap_usd="2",
                )

    def test_invalid_metadata_storage_type_fails_closed(self):
        project, ledger = self.make_project("meta-blob")
        ledger.reserve("op-valid", "1")
        self.tamper(
            ledger,
            "UPDATE ledger_meta SET value = x'00ff' WHERE key = 'cap_micro'",
        )
        self.assert_error("budget_ledger_corrupt", ledger.reserve, "op-new", "1")
        self.assert_error("budget_ledger_corrupt", ledger.snapshot)

    def test_new_schema_rejects_non_integer_storage(self):
        project, ledger = self.make_project("schema-types")
        ledger.reserve("op-valid", "1")
        for value in ("'not-a-number'", "1.5", "0", "-1"):
            with self.subTest(value=value):
                connection = sqlite3.connect(ledger.ledger_path)
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "UPDATE operations SET amount_micro = %s"
                            " WHERE operation_id = 'op-valid'" % value
                        )
                finally:
                    connection.rollback()
                    connection.close()

    def test_invalid_persisted_amounts_fail_closed_without_coercion(self):
        cases = (
            (
                "UPDATE operations SET amount_micro = 'not-a-number'"
                " WHERE operation_id = 'op-full'",
                "text",
            ),
            (
                "UPDATE operations SET amount_micro = 1.5"
                " WHERE operation_id = 'op-full'",
                "real",
            ),
            (
                "UPDATE operations SET amount_micro = 0"
                " WHERE operation_id = 'op-full'",
                "zero",
            ),
            (
                "UPDATE operations SET amount_micro = -1"
                " WHERE operation_id = 'op-full'",
                "negative",
            ),
        )
        for sql, label in cases:
            project, ledger = self.make_project(f"bad-{label}", cap_usd="1")
            ledger.reserve("op-full", "1")
            self.tamper(ledger, sql)
            with self.subTest(label=label):
                self.assert_error(
                    "budget_ledger_corrupt", ledger.reserve, "op-second", "1"
                )
                self.assertEqual(
                    self.operation_count(ledger), 1,
                    "invalid history was repaired or extended",
                )
                self.assert_error("budget_ledger_corrupt", ledger.snapshot)
                self.assert_error(
                    "budget_ledger_corrupt", budget.CampaignLedger, project,
                    campaign_id=CAMPAIGN, cap_usd="1",
                )

    def test_invalid_persisted_estimate_status_or_total_fails_closed(self):
        project, ledger = self.make_project("bad-estimate", cap_usd="1")
        ledger.reserve("op-valid", "1")
        ledger.finish("op-valid", "success", estimated_cost_usd="0.1")
        self.tamper(
            ledger,
            "UPDATE operations SET estimated_cost_micro = 'text'"
            " WHERE operation_id = 'op-valid'",
        )
        self.assert_error("budget_ledger_corrupt", ledger.snapshot)
        self.assert_error("budget_ledger_corrupt", ledger.reserve, "op-new", "1")
        self.assert_error(
            "budget_ledger_corrupt", budget.CampaignLedger, project,
            campaign_id=CAMPAIGN, cap_usd="1",
        )

        project, ledger = self.make_project("bad-status", cap_usd="1")
        ledger.reserve("op-valid", "1")
        self.tamper(
            ledger,
            "UPDATE operations SET status = 'bogus' WHERE operation_id = 'op-valid'",
        )
        self.assert_error("budget_ledger_corrupt", ledger.snapshot)

        project, ledger = self.make_project("bad-total", cap_usd="1")
        ledger.reserve("op-valid", "1")
        self.tamper(
            ledger,
            "INSERT INTO operations (operation_id, amount_micro, status,"
            " estimated_cost_micro, created_at, finished_at)"
            " VALUES ('op-extra', 2000000, 'pending', NULL, 'x', NULL)",
        )
        self.assert_error("budget_ledger_corrupt", ledger.snapshot)
        self.assert_error("budget_ledger_corrupt", ledger.reserve, "op-new", "0.000001")


    # -- r3 correction regressions (R2-F1/R2-F2) -------------------------

    def execute_schema(self, ledger, sql):
        connection = sqlite3.connect(ledger.ledger_path)
        try:
            connection.executescript(sql)
            connection.commit()
        finally:
            connection.close()

    def make_hot_unrecognized_database(self, project):
        database = project / ".coordination" / "provider-budget.sqlite3"
        database.parent.mkdir(parents=True)
        return self.make_hot_journal(database)

    def make_hot_journal(self, database):
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE unrelated (payload BLOB)")
        connection.executemany(
            "INSERT INTO unrelated VALUES (zeroblob(3000))", [()] * 100
        )
        connection.commit()
        connection.close()
        clean = database.read_bytes()
        process = subprocess.run(
            [
                sys.executable, "-B", "-c", HOT_CHILD_SCRIPT, str(database),
                "UPDATE unrelated SET payload = randomblob(3000)",
            ],
            cwd=str(OVERLAY_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, "hot-journal fixture child failed")
        journal = database.with_name(database.name + "-journal")
        self.assertTrue(journal.exists(), "hot journal was not created")
        before_database = database.read_bytes()
        before_journal = journal.read_bytes()
        self.assertNotEqual(
            before_database, clean, "fixture did not dirty the database"
        )
        self.assertGreater(len(before_journal), 512)
        self.assertTrue(any(before_journal[:8]), "journal header is not hot")
        return database, journal, before_database, before_journal

    def test_hot_journal_unrecognized_file_is_refused_without_recovery(self):
        from adapters.budgeted_deepseek import BudgetedDeepSeekClient

        project = self.root / "hot-journal"
        project.mkdir()
        database, journal, before_database, before_journal = (
            self.make_hot_unrecognized_database(project)
        )
        self.assert_error(
            "budget_storage_unavailable", budget.CampaignLedger, project,
            campaign_id=CAMPAIGN, cap_usd="1",
        )
        self.assertEqual(
            database.read_bytes(), before_database,
            "database bytes changed during read-only refusal",
        )
        self.assertEqual(
            journal.read_bytes(), before_journal,
            "hot journal bytes changed during read-only refusal",
        )
        self.assertTrue(journal.exists(), "hot journal was recovered or deleted")

        (project / ".env.local").write_bytes(
            b"DEEPSEEK_API_KEY=synthetic-hot-journal-key\n"
        )
        calls = []

        def unexpected_transport(*args, **kwargs):
            calls.append(True)
            raise AssertionError("fake transport reached for refused storage")

        with self.assertRaises(budget.BudgetError) as raised:
            BudgetedDeepSeekClient(
                project, campaign_id=CAMPAIGN, cap_usd="1", environ={},
                transport=unexpected_transport,
            )
        self.assertEqual(raised.exception.code, "budget_storage_unavailable")
        self.assertEqual(calls, [], "fake transport was called")
        self.assertEqual(database.read_bytes(), before_database)
        self.assertEqual(journal.read_bytes(), before_journal)

    def test_read_only_validation_and_snapshot_use_actual_mode_ro(self):
        project, ledger = self.make_project("mode-ro")
        recorded = []
        real_connect = sqlite3.connect

        def capturing(database_uri, *args, **kwargs):
            if isinstance(database_uri, str) and "?mode=" in database_uri:
                recorded.append(database_uri.rsplit("?mode=", 1)[1])
            return real_connect(database_uri, *args, **kwargs)

        with patch.object(budget.sqlite3, "connect", new=capturing):
            reopened = budget.CampaignLedger(project, campaign_id=CAMPAIGN)
            reopened.snapshot()
            reopened.reserve("op-mode", "0.1")
        self.assertEqual(
            recorded, ["ro", "ro", "ro", "rw"], "unexpected SQLite open modes"
        )

    def test_incompatible_trigger_refused_on_reopen_and_active_handle(self):
        project, ledger = self.make_project("schema-trigger", cap_usd="1")
        ledger.reserve("op-pending", "0.1")
        self.execute_schema(ledger, TRIGGER_SQL)
        database = Path(ledger.ledger_path)
        before = database.read_bytes()
        self.assert_error(
            "budget_schema_unsupported", budget.CampaignLedger, project,
            campaign_id=CAMPAIGN, cap_usd="1",
        )
        self.assertEqual(
            database.read_bytes(), before,
            "refusal modified the unsupported schema",
        )
        self.assert_error("budget_schema_unsupported", ledger.reserve, "op-new", "0.1")
        self.assert_error("budget_schema_unsupported", ledger.snapshot)
        self.assert_error(
            "budget_schema_unsupported", ledger.finish, "op-pending", "success"
        )
        self.assertEqual(database.read_bytes(), before)
        connection = sqlite3.connect(database)
        try:
            rows = connection.execute(
                "SELECT operation_id, status FROM operations"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [("op-pending", "pending")])

    def test_incompatible_table_shape_and_extra_table_are_refused(self):
        project, ledger = self.make_project("shape-rename")
        self.execute_schema(
            ledger,
            "ALTER TABLE operations RENAME COLUMN amount_micro TO amount_other;",
        )
        database = Path(ledger.ledger_path)
        before = database.read_bytes()
        self.assert_error(
            "budget_schema_unsupported", budget.CampaignLedger, project,
            campaign_id=CAMPAIGN,
        )
        self.assert_error("budget_schema_unsupported", ledger.snapshot)
        self.assert_error("budget_schema_unsupported", ledger.reserve, "op-x", "0.1")
        self.assertEqual(database.read_bytes(), before)

        project, ledger = self.make_project("shape-extra")
        self.execute_schema(ledger, "CREATE TABLE extra_table (value TEXT);")
        self.assert_error(
            "budget_schema_unsupported", budget.CampaignLedger, project,
            campaign_id=CAMPAIGN,
        )

    def test_insert_that_creates_no_row_is_refused(self):
        project, ledger = self.make_project("suppressed", cap_usd="1")
        self.execute_schema(ledger, TRIGGER_SQL)
        with patch.object(
            budget.CampaignLedger,
            "_validate_supported_schema",
            lambda self, connection: None,
        ):
            self.assert_error(
                "budget_reservation_not_persisted", ledger.reserve,
                "op-suppressed", "1",
            )
        self.assertEqual(
            self.operation_count(ledger), 0, "suppressed insert left a row"
        )
        self.assert_error("budget_schema_unsupported", ledger.snapshot)

    # -- r4 correction regressions (R3-F1/R3-F2) -------------------------

    def test_schema_drift_after_preflight_is_revalidated_in_write_transaction(self):
        _, ledger = self.make_project("schema-between-connections")
        ledger.reserve("op-pending", "0.1")
        database = Path(ledger.ledger_path)
        real_connect = ledger._connect
        drift_fingerprints = []

        def connect_after_drift(*, read_only=False):
            if not read_only:
                self.execute_schema(ledger, TRIGGER_SQL)
                drift_fingerprints.append(storage_fingerprint(database))
            return real_connect(read_only=read_only)

        with patch.object(ledger, "_connect", side_effect=connect_after_drift):
            self.assert_error(
                "budget_schema_unsupported", ledger.reserve, "op-blocked", "0.1"
            )
        self.assertEqual(drift_fingerprints, [storage_fingerprint(database)])
        self.assertEqual(self.operation_count(ledger), 1)

    def test_unrelated_persistent_wal_refused_before_any_sqlite_open(self):
        project = self.root / "unrelated-wal"
        database = project / budget.LEDGER_RELATIVE_PATH
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            connection.execute("CREATE TABLE unrelated (payload INTEGER)")
            connection.execute("INSERT INTO unrelated VALUES (1)")
            connection.commit()
        finally:
            connection.close()
        before = storage_fingerprint(database)
        self.assertTrue(all(before[suffix] is None for suffix in ("-journal", "-wal", "-shm")))
        with patch.object(sqlite3, "connect", wraps=sqlite3.connect) as connect:
            self.assert_error(
                "budget_storage_unavailable", budget.CampaignLedger, project,
                campaign_id=CAMPAIGN,
            )
        connect.assert_not_called()
        self.assertEqual(storage_fingerprint(database), before)

    def test_active_wal_refused_by_constructor_snapshot_reserve_and_finish(self):
        project, ledger = self.make_project("active-wal")
        ledger.reserve("op-pending", "0.1")
        database = Path(ledger.ledger_path)
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            # Activate normal WAL/SHM sidecars on an existing WAL connection.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations").fetchone(), (1,))
            for sidecars_present in (True, False):
                with self.subTest(sidecars_present=sidecars_present):
                    if not sidecars_present:
                        connection.close()
                    before = storage_fingerprint(database)
                    for suffix in ("-wal", "-shm"):
                        self.assertEqual(before[suffix] is not None, sidecars_present)
                    with patch.object(sqlite3, "connect", wraps=sqlite3.connect) as connect:
                        for method, args, kwargs in (
                            (budget.CampaignLedger, (project,), {"campaign_id": CAMPAIGN}),
                            (ledger.snapshot, (), {}),
                            (ledger.reserve, ("op-blocked", "0.1"), {}),
                            (ledger.finish, ("op-pending", "success"), {}),
                        ):
                            self.assert_error("budget_storage_unavailable", method, *args, **kwargs)
                            self.assertEqual(storage_fingerprint(database), before)
                    connect.assert_not_called()
        finally:
            connection.close()

    def test_orphaned_sidecars_are_refused_without_claiming_database(self):
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                project = self.root / ("orphan" + suffix)
                database = project / budget.LEDGER_RELATIVE_PATH
                database.parent.mkdir(parents=True)
                database.with_name(database.name + suffix).write_bytes(b"synthetic orphan")
                before = storage_fingerprint(database)
                self.assert_error(
                    "budget_storage_unavailable", budget.CampaignLedger, project,
                    campaign_id=CAMPAIGN,
                )
                self.assertEqual(storage_fingerprint(database), before)

    def test_active_incompatible_hot_rollback_storage_never_recovered(self):
        from adapters.budgeted_deepseek import BudgetedDeepSeekClient

        project, ledger = self.make_project("active-incompatible-hot", cap_usd="1")
        ledger.reserve("op-pending", "0.1")
        fake_calls = []

        def unexpected_transport(*args, **kwargs):
            fake_calls.append(True)
            raise AssertionError("fake transport reached for refused storage")

        client = BudgetedDeepSeekClient(
            project, campaign_id=CAMPAIGN, cap_usd="1", transport=unexpected_transport,
            environ={"DEEPSEEK_API_KEY": "synthetic-hot-active-key"},
        )
        database = Path(ledger.ledger_path)
        self.make_hot_journal(database)
        before = storage_fingerprint(database)
        self.assertIsNotNone(before["-journal"])
        self.assertIsNone(before["-wal"])
        self.assertIsNone(before["-shm"])

        opened_modes = []
        real_connect = sqlite3.connect

        def capture_mode(database_uri, *args, **kwargs):
            opened_modes.append(database_uri.rsplit("?mode=", 1)[1])
            return real_connect(database_uri, *args, **kwargs)

        with patch.object(sqlite3, "connect", new=capture_mode):
            for method, args, kwargs in (
                (budget.CampaignLedger, (project,), {"campaign_id": CAMPAIGN, "cap_usd": "1"}),
                (client.snapshot, (), {}),
                (
                    client.complete, ("op-blocked", [{"role": "user", "content": "fixture"}]),
                    {"max_cost_usd": "0.5", "dispatch": True},
                ),
                (ledger.finish, ("op-pending", "success"), {}),
            ):
                self.assert_error("budget_storage_unavailable", method, *args, **kwargs)
                self.assertEqual(storage_fingerprint(database), before)
        self.assertEqual(opened_modes, ["ro"] * 4)
        self.assertEqual(fake_calls, [])


if __name__ == "__main__":
    unittest.main()
