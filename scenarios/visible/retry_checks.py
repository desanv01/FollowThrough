#!/usr/bin/env python3
"""Original public smoke checks for the retry-processing workspace.

These are small examples derived from the visible requirements. They are not
exhaustive, are not a grading implementation, and a pass is not proof of
complete correctness. Run from any working directory with:

    python -I -B <absolute path to public_checks.py>
"""

import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
import sys


def load_candidate():
    path = Path(__file__).with_name("processor.py")
    spec = importlib.util.spec_from_file_location("public_retry_processor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sibling processor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("process_event", "process_batch"):
        if not callable(getattr(module, name, None)):
            raise RuntimeError(f"processor.py must export callable {name}")
    return module


class Store:
    """Small in-memory store with the complete processor-facing API and one-shot faults."""

    def __init__(self):
        self.totals = {}
        self.completed = {}
        self.fault = None

    def lookup_units(self, tenant, event_id):
        return self.completed.get((tenant, event_id))

    def add_units(self, tenant, units):
        self.totals[tenant] = self.totals.get(tenant, 0) + units
        self._maybe_fail("after_add")

    def mark_completed(self, tenant, event_id, units):
        self.completed[(tenant, event_id)] = units
        self._maybe_fail("after_mark")

    @contextmanager
    def transaction(self):
        totals = dict(self.totals)
        completed = dict(self.completed)
        try:
            yield
        except BaseException:
            self.totals = totals
            self.completed = completed
            raise

    def _maybe_fail(self, stage):
        if self.fault == stage:
            self.fault = None
            raise RuntimeError(f"injected store failure {stage}")


def _item(tenant, event_id, units):
    return {"tenant": tenant, "event_id": event_id, "units": units}


def check_validation(processor):
    store = Store()
    cases = [
        None,
        [],
        "not-an-item",
        {},
        _item("", "e", 1),
        _item("t", "", 1),
        _item("t", "e", 0),
        _item("t", "e", -1),
        _item("t", "e", True),
        _item("t", "e", 1.0),
    ]
    for item in cases:
        status = processor.process_event(store, item)
        if status != "invalid":
            raise AssertionError(f"expected invalid for {item!r}, observed {status!r}")
    if store.totals or store.completed:
        raise AssertionError("invalid items changed durable state")
    return f"{len(cases)} malformed items returned invalid without state changes"


def check_new_duplicate_conflict(processor):
    store = Store()
    item = _item("harbor", "m1", 2)
    if processor.process_event(store, dict(item)) != "applied":
        raise AssertionError("new identity was not applied")
    if store.totals != {"harbor": 2} or store.completed != {("harbor", "m1"): 2}:
        raise AssertionError("applied identity produced unexpected durable state")
    if processor.process_event(store, dict(item)) != "duplicate":
        raise AssertionError("identical replay was not duplicate")
    if processor.process_event(store, _item("harbor", "m1", 3)) != "conflict":
        raise AssertionError("different units were not conflict")
    if store.totals != {"harbor": 2} or store.completed != {("harbor", "m1"): 2}:
        raise AssertionError("duplicate or conflict changed durable state")
    return "applied, duplicate and conflict kept the original units exactly once"


def check_tenant_identity(processor):
    store = Store()
    first = _item("amber", "shared", 4)
    second = _item("birch", "shared", 7)
    if processor.process_event(store, first) != "applied":
        raise AssertionError("first tenant was not applied")
    if processor.process_event(store, second) != "applied":
        raise AssertionError("second tenant was not applied independently")
    if store.totals != {"amber": 4, "birch": 7}:
        raise AssertionError("tenants collided")
    return "the same event ID applied independently in two tenants"


def check_batch_order(processor):
    store = Store()
    first = _item("jade", "x", 13)
    items = [
        first,
        _item("jade", "bad", 0),
        dict(first),
        _item("jade", "x", 27),
        _item("opal", "x", 3),
    ]
    statuses = processor.process_batch(store, iter(items))
    expected = ["applied", "invalid", "duplicate", "conflict", "applied"]
    if statuses != expected:
        raise AssertionError(f"expected {expected!r}, observed {statuses!r}")
    if processor.process_batch(store, iter(())) != []:
        raise AssertionError("empty batch did not return an empty list")
    if store.totals != {"jade": 13, "opal": 3}:
        raise AssertionError("batch produced unexpected totals")
    return "batch preserved order, continued after invalid/conflict, and handled empty input"


def check_rollback_after_add(processor):
    store = Store()
    store.fault = "after_add"
    try:
        processor.process_event(store, _item("moss", "pending", 5))
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected post-credit failure was not propagated")
    if store.totals or store.completed:
        raise AssertionError("post-credit failure left a durable effect")
    return "post-credit failure rolled back both effects"


def check_rollback_after_mark(processor):
    store = Store()
    store.fault = "after_mark"
    try:
        processor.process_event(store, _item("moss", "pending", 5))
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected post-completion failure was not propagated")
    if store.totals or store.completed:
        raise AssertionError("post-completion failure left a durable effect")
    return "post-completion failure rolled back both effects"


CHECKS = (
    ("validation", check_validation),
    ("new_duplicate_conflict", check_new_duplicate_conflict),
    ("tenant_identity", check_tenant_identity),
    ("batch_order", check_batch_order),
    ("rollback_after_add", check_rollback_after_add),
    ("rollback_after_mark", check_rollback_after_mark),
)


def main():
    try:
        processor = load_candidate()
    except BaseException as exc:
        print(json.dumps({"check": "load", "passed": False,
                          "detail": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        return 1
    passed = 0
    for name, check in CHECKS:
        try:
            detail = check(processor)
        except BaseException as exc:
            print(json.dumps({"check": name, "passed": False,
                              "detail": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        else:
            passed += 1
            print(json.dumps({"check": name, "passed": True, "detail": detail}, sort_keys=True))
    print(json.dumps({"summary": "public smoke", "passed": passed, "total": len(CHECKS)},
                     sort_keys=True))
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
