"""Candidate-container shim for host-owned Retry Store RPC.

The shim has no database, expected results, references or grade authority. It
loads the candidate and presents a bounded Store proxy whose operations are
requested from the trusted host over line-framed JSON. The host owns durable state,
transactions, faults and snapshots; candidate statuses are just replies.

Protocol:
  host -> shim: {"type":"event","index":N,"kind":"event"|"batch","payload":...}
  shim -> host: {"type":"store","index":N,"method":...,"args":[...]}
  host -> shim: {"type":"store_result","ok":true,"result":...} |
                {"type":"store_result","ok":false,"error":"injected_after_credit"}
  shim -> host: {"type":"result","index":N,"kind":"event"|"batch",
                 "status":...|"results":...,"fault_propagated":bool}
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from types import ModuleType
from uuid import uuid4

MAX_LINE = 65536


class InjectedFailure(RuntimeError):
    pass


class CandidateLoadError(Exception):
    pass


def _read_frame():
    line = sys.stdin.readline(MAX_LINE + 1)
    if not line:
        raise EOFError
    if len(line) > MAX_LINE:
        raise ValueError("frame too large")
    frame = json.loads(line)
    if type(frame) is not dict:
        raise ValueError("frame not object")
    return frame


def _write_frame(frame):
    sys.stdout.write(json.dumps(frame, sort_keys=True) + "\n")
    sys.stdout.flush()


class RemoteStore:
    def __init__(self, index):
        self.index = index

    def _call(self, method, *args):
        _write_frame({"type": "store", "index": self.index,
                      "method": method, "args": list(args)})
        frame = _read_frame()
        if frame.get("type") != "store_result":
            raise ValueError("invalid store response")
        if frame.get("ok") is not True:
            raise InjectedFailure(str(frame.get("error", "store_error")))
        return frame.get("result")

    @contextlib.contextmanager
    def transaction(self):
        self._call("begin")
        try:
            yield
        except BaseException:
            self._call("rollback")
            raise
        self._call("commit")

    def lookup_units(self, tenant, event_id):
        return self._call("lookup_units", tenant, event_id)

    def add_units(self, tenant, units):
        return self._call("add_units", tenant, units)

    def mark_completed(self, tenant, event_id, units):
        return self._call("mark_completed", tenant, event_id, units)


def _load_candidate(path, source):
    module = ModuleType(f"_protected_candidate_{uuid4().hex}")
    module.__file__ = str(path)
    try:
        exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
        for name in ("process_event", "process_batch"):
            if not callable(getattr(module, name, None)):
                raise TypeError(f"candidate must export callable {name}")
    except (Exception, SystemExit) as exc:
        raise CandidateLoadError(type(exc).__name__) from None
    return module


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        _write_frame({"type": "fatal", "error": "invalid_arguments"})
        return 2
    candidate_path = Path(argv[0])
    try:
        source = candidate_path.read_bytes()
        module = _load_candidate(candidate_path, source)
    except (OSError, CandidateLoadError):
        _write_frame({"type": "fatal", "error": "candidate_load_failed"})
        return 0
    while True:
        try:
            operation = _read_frame()
        except EOFError:
            return 0
        except (ValueError, json.JSONDecodeError):
            _write_frame({"type": "fatal", "error": "invalid_operation"})
            return 2
        if operation.get("type") != "event":
            _write_frame({"type": "fatal", "error": "invalid_operation"})
            return 2
        index = operation.get("index")
        kind = operation.get("kind")
        payload = operation.get("payload")
        if type(index) is not int or kind not in ("event", "batch"):
            _write_frame({"type": "fatal", "error": "invalid_operation"})
            return 2
        store = RemoteStore(index)
        try:
            if kind == "event":
                status = module.process_event(store, payload)
                frame = {"type": "result", "index": index, "kind": "event",
                         "status": status, "fault_propagated": False}
            else:
                if type(payload) is not list:
                    raise TypeError("batch payload must be a list")
                results = module.process_batch(store, iter(payload))
                frame = {"type": "result", "index": index, "kind": "batch",
                         "results": results, "fault_propagated": False}
        except InjectedFailure:
            frame = {"type": "result", "index": index, "kind": kind,
                     "fault_propagated": True}
        except (Exception, SystemExit):
            frame = {"type": "result", "index": index, "kind": kind,
                     "candidate_error": True, "fault_propagated": False}
        _write_frame(frame)


if __name__ == "__main__":
    sys.exit(main())
