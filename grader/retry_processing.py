"""Functional grader for trusted local retry-processing candidate modules."""

import argparse
from contextlib import closing, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
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


class InjectedFailure(RuntimeError):
    pass


class CheckFailure(Exception):
    pass


class CandidateExecutionError(Exception):
    def __init__(self, phase, cause):
        self.phase = phase
        self.cause = cause
        super().__init__(str(cause))


class StoreExecutionError(RuntimeError):
    pass


def _key(value):
    return value.encode("utf-8", errors="surrogatepass")


class Store:
    """Owns durable state and raises a one-shot fault at a selected SQL boundary."""

    def __init__(self, path, fault=None, occurrence=1):
        self._db = sqlite3.connect(path, isolation_level=None)
        self._fault = fault
        self._remaining = occurrence
        self._lookup_count = 0

    def close(self):
        self._db.close()

    def _execute(self, statement, parameters=()):
        try:
            return self._db.execute(statement, parameters)
        except sqlite3.OperationalError as exc:
            raise StoreExecutionError(str(exc)) from exc

    def _interrupt(self, stage):
        if stage == self._fault:
            self._remaining -= 1
            if self._remaining == 0:
                raise InjectedFailure(stage)

    @contextmanager
    def transaction(self):
        if self._db.in_transaction:
            raise RuntimeError("nested transactions are unsupported")
        self._execute("BEGIN")
        try:
            yield
            self._execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._execute("ROLLBACK")
            raise
        self._interrupt("after_commit")

    def lookup_units(self, tenant, event_id):
        self._lookup_count += 1
        row = self._execute(
            "SELECT units FROM completed WHERE tenant = ? AND event_id = ?",
            (_key(tenant), _key(event_id)),
        ).fetchone()
        return int(row[0], 16) if row is not None else None

    def add_units(self, tenant, units):
        tenant_key = _key(tenant)
        row = self._execute(
            "SELECT units FROM totals WHERE tenant = ?", (tenant_key,)
        ).fetchone()
        total = (int(row[0], 16) if row is not None else 0) + units
        self._execute(
            "INSERT INTO totals VALUES (?, ?) "
            "ON CONFLICT(tenant) DO UPDATE SET units = excluded.units",
            (tenant_key, format(total, "x")),
        )
        self._interrupt("after_credit")

    def mark_completed(self, tenant, event_id, units):
        self._execute(
            "INSERT INTO completed VALUES (?, ?, ?)",
            (_key(tenant), _key(event_id), format(units, "x")),
        )
        self._interrupt("after_mark")


@contextmanager
def _candidate_module(path, source):
    # Execute the exact hashed bytes. Import caches cannot replace the snapshot.
    module = ModuleType(f"_retry_candidate_{uuid4().hex}")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    try:
        try:
            exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
            for name in ("process_event", "process_batch"):
                if not callable(getattr(module, name, None)):
                    raise TypeError(f"candidate must export callable {name}")
        except (Exception, SystemExit) as exc:
            raise CandidateExecutionError("load", exc) from exc
        yield module
    finally:
        sys.modules.pop(module.__name__, None)


def _invoke(process, store, value):
    try:
        return process(store, value)
    except (InjectedFailure, StoreExecutionError):
        raise
    except (Exception, SystemExit) as exc:
        raise CandidateExecutionError("invoke", exc) from exc


def _event(tenant, event_id, units):
    return {"tenant": tenant, "event_id": event_id, "units": units}


def _deliver(processor, database, item):
    with closing(Store(database)) as store:
        return _invoke(processor.process_event, store, item)


def _expect(actual, expected, label):
    if actual != expected:
        raise CheckFailure(f"{label}: expected {expected!r}, observed {actual!r}")


def _expect_fault(process, store, value, stage):
    try:
        _invoke(process, store, value)
    except InjectedFailure as exc:
        _expect(exc.args, (stage,), "propagated fault")
    else:
        raise CheckFailure(f"{stage}: store fault was not propagated")


def _expect_state(database, totals, completed):
    # Open a fresh SQLite connection and read both tables independently of Store.
    with closing(sqlite3.connect(database)) as db:
        actual_totals = {
            tenant: int(units, 16)
            for tenant, units in db.execute("SELECT tenant, units FROM totals")
        }
        actual_completed = {
            (tenant, event_id): int(units, 16)
            for tenant, event_id, units in db.execute(
                "SELECT tenant, event_id, units FROM completed"
            )
        }
    _expect(
        actual_totals,
        {_key(tenant): units for tenant, units in totals.items()},
        "durable totals",
    )
    _expect(
        actual_completed,
        {(_key(tenant), _key(event_id)): units for (tenant, event_id), units in completed.items()},
        "durable completions",
    )


def _check_new(processor, database):
    large = (1 << 80) + 53
    first = _event("ledger", "open", large)
    second = _event("ledger", "next", 1)
    _expect(_deliver(processor, database, first), "applied", "new delivery")
    _expect(_deliver(processor, database, second), "applied", "second delivery")
    _expect_state(
        database, {"ledger": large + 1}, {("ledger", "open"): large, ("ledger", "next"): 1}
    )


def _check_duplicate(processor, database):
    item = _event("amber", "r-17", 23)
    with closing(Store(database)) as store:
        _expect(_invoke(processor.process_event, store, item), "applied", "first delivery")
        _expect(_invoke(processor.process_event, store, item.copy()), "duplicate", "second delivery")
    _expect_state(database, {"amber": 23}, {("amber", "r-17"): 23})


def _check_conflict(processor, database):
    original = _event("fern", "f-08", 31)
    collision = _event("fern", "f-08", 29)
    _expect(_deliver(processor, database, original), "applied", "original delivery")
    _expect(_deliver(processor, database, collision), "conflict", "collision")
    _expect(_deliver(processor, database, original), "duplicate", "original replay")
    _expect_state(database, {"fern": 31}, {("fern", "f-08"): 31})


def _check_tenants(processor, database):
    deliveries = {
        ("Ångström", "k\0-2"): 4,
        ("ångström", "k\0-2"): 19,
        ("Ångström", "K\0-2"): 6,
        (" ", " "): 3,
    }
    for (tenant, event_id), units in deliveries.items():
        item = _event(tenant, event_id, units)
        _expect(_deliver(processor, database, item), "applied", "distinct identity")
        _expect(_deliver(processor, database, item), "duplicate", "same identity")
    _expect_state(database, {"Ångström": 10, "ångström": 19, " ": 3}, deliveries)


def _check_invalid(processor, database):
    original = _event("spruce", "pre", 5)
    _expect(_deliver(processor, database, original), "applied", "existing delivery")
    invalid_items = [None, [], "spruce", 1, {}]
    for field in original:
        missing = original.copy()
        del missing[field]
        invalid_items.append(missing)
    for field, values in {
        "tenant": ("", None, 9, []),
        "event_id": ("", None, 7, {}),
        "units": (None, True, False, 0, -1, 5.0, "5", []),
    }.items():
        invalid_items.extend(dict(original, **{field: value}) for value in values)
    for item in invalid_items:
        label = f"malformed input {item!r}"
        with closing(Store(database)) as store:
            try:
                status = _invoke(processor.process_event, store, item)
            finally:
                # A lookup attempt violates the contract even if the store rejects its arguments.
                _expect(store._lookup_count, 0, f"{label} lookup count")
        _expect(status, "invalid", label)
    _expect_state(database, {"spruce": 5}, {("spruce", "pre"): 5})


def _check_batch(processor, database):
    first = _event("jade", "x", 13)
    items = [
        first,
        _event("jade", "malformed", 0),
        _event("jade", "x", 27),
        None,
        dict(first),
        dict(_event("jade", "y", 7), ignored=True),
        _event("opal", "x", 3),
    ]
    with closing(Store(database)) as store:
        _expect(_invoke(processor.process_batch, store, iter(())), [], "empty batch")
        _expect(
            _invoke(processor.process_batch, store, iter(items)),
            ["applied", "invalid", "conflict", "invalid", "duplicate", "applied", "applied"],
            "mixed batch results",
        )
    _expect_state(
        database, {"jade": 20, "opal": 3}, {("jade", "x"): 13, ("jade", "y"): 7, ("opal", "x"): 3}
    )


def _check_precommit(processor, database, stage):
    prior = _event("moss", "settled", 17)
    item = _event("moss", "pending", 41)
    _expect(_deliver(processor, database, prior), "applied", "previous delivery")
    with closing(Store(database, fault=stage)) as store:
        _expect_fault(processor.process_event, store, item, stage)
        _expect_state(database, {"moss": 17}, {("moss", "settled"): 17})
    _expect(_deliver(processor, database, item), "applied", "retry")
    _expect(_deliver(processor, database, item), "duplicate", "subsequent replay")
    _expect_state(database, {"moss": 58}, {("moss", "settled"): 17, ("moss", "pending"): 41})


def _check_batch_interruption(processor, database):
    items = [_event("lichen", "a", 8), _event("lichen", "b", 21), _event("lichen", "c", 16)]
    with closing(Store(database, fault="after_credit", occurrence=2)) as store:
        _expect_fault(processor.process_batch, store, iter(items), "after_credit")
        _expect_state(database, {"lichen": 8}, {("lichen", "a"): 8})
    with closing(Store(database)) as store:
        _expect(
            _invoke(processor.process_batch, store, iter(items)),
            ["duplicate", "applied", "applied"],
            "batch retry",
        )
    _expect_state(
        database, {"lichen": 45}, {("lichen", "a"): 8, ("lichen", "b"): 21, ("lichen", "c"): 16}
    )


def _check_ack_loss(processor, database, reload_candidate):
    item = _event("coral", "seen-but-unacknowledged", 37)
    with closing(Store(database, fault="after_commit")) as store:
        _expect_fault(processor.process_event, store, item, "after_commit")
        _expect_state(database, {"coral": 37}, {("coral", "seen-but-unacknowledged"): 37})
    with reload_candidate() as reloaded:
        _expect(_deliver(reloaded, database, item), "duplicate", "retry after module and store reopen")
    _expect_state(database, {"coral": 37}, {("coral", "seen-but-unacknowledged"): 37})


def _add_error(result, origin, phase, exc, **context):
    result["errors"].append({
        "origin": origin,
        "phase": phase,
        "type": type(exc).__name__,
        "detail": str(exc),
        **context,
    })


def grade(candidate):
    path = Path(candidate).absolute()
    result = {
        "schema_version": "0.1",
        "task_family": "retry-processing",
        "candidate": {"path": str(path), "sha256": None},
        "evidence_kind": "real_local_control",
        "functional_pass": False,
        "checks": [],
        "errors": [],
    }
    try:
        path = path.resolve(strict=True)
        result["candidate"]["path"] = str(path)
        source = path.read_bytes()
    except (OSError, RuntimeError) as exc:
        _add_error(result, "grader", "candidate_read", exc)
        return result
    result["candidate"]["sha256"] = sha256(source).hexdigest()

    def reload_candidate():
        return _candidate_module(path, source)

    checks = (
        ("new_delivery", _check_new, ()),
        ("duplicate_replay", _check_duplicate, ()),
        ("conflicting_units", _check_conflict, ()),
        ("tenant_and_event_identity", _check_tenants, ()),
        ("malformed_items", _check_invalid, ()),
        ("mixed_batch", _check_batch, ()),
        ("rollback_after_credit", _check_precommit, ("after_credit",)),
        ("rollback_after_mark", _check_precommit, ("after_mark",)),
        ("interrupted_batch_retry", _check_batch_interruption, ()),
        ("ack_loss_reopen_retry", _check_ack_loss, (reload_candidate,)),
    )

    try:
        with reload_candidate() as processor, TemporaryDirectory(prefix="followthrough-retry-") as temp:
            for check_id, check, args in checks:
                database = Path(temp) / f"{check_id}.sqlite"
                with closing(sqlite3.connect(database)) as db:
                    db.executescript(SCHEMA)
                entry = {"id": check_id, "passed": False, "detail": "execution error"}
                result["checks"].append(entry)
                try:
                    check(processor, database, *args)
                except CheckFailure as exc:
                    entry["detail"] = str(exc)
                except CandidateExecutionError as exc:
                    _add_error(result, "candidate", exc.phase, exc.cause, check_id=check_id)
                except Exception as exc:
                    _add_error(result, "grader", "check", exc, check_id=check_id)
                else:
                    entry.update(passed=True, detail="observed specified statuses and durable SQLite state")
    except CandidateExecutionError as exc:
        _add_error(result, "candidate", exc.phase, exc.cause)
    except Exception as exc:
        _add_error(result, "grader", "setup_or_cleanup", exc)
    result["functional_pass"] = not result["errors"] and all(
        entry["passed"] for entry in result["checks"]
    )
    return result


def self_test():
    expected_fault_checks = {
        "rollback_after_credit",
        "rollback_after_mark",
        "interrupted_batch_retry",
        "ack_loss_reopen_retry",
    }
    success = True
    for relative_path, expected_failures in (
        ("references/retry-processing/correct/processor.py", set()),
        ("tasks/retry-processing/processor.py", expected_fault_checks),
        ("references/retry-processing/mark-first/processor.py", expected_fault_checks),
    ):
        result = grade(ROOT / relative_path)
        failures = {entry["id"] for entry in result["checks"] if not entry["passed"]}
        matched = (
            not result["errors"]
            and failures == expected_failures
            and result["functional_pass"] == (not expected_failures)
        )
        print(json.dumps({
            "candidate": result["candidate"],
            "functional_pass": result["functional_pass"],
            "failed_checks": sorted(failures),
            "errors": result["errors"],
            "matched_expectation": matched,
        }))
        success = success and matched
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--candidate", type=Path)
    mode.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        if args.output is not None:
            parser.error("--output is only valid with --candidate")
        return self_test()
    if args.output is None:
        parser.error("--candidate requires --output")

    try:
        with args.output.open("x", encoding="utf-8") as output:
            result = grade(args.candidate)
            json.dump(result, output, indent=2)
            output.write("\n")
    except OSError as exc:
        print(f"grader output error: {exc}", file=sys.stderr)
        return 1
    if result["errors"]:
        print(f"grader recorded {len(result['errors'])} execution error(s) in {args.output}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
