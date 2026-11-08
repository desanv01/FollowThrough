"""Candidate-only Cache shim. Host owns cache contents, renderer fixtures and grade.

The candidate sees only a remote MutableMapping and remote renderer. Every cache or
renderer operation is a bounded request to the trusted host; the candidate cannot
supply cache state or a passing result.
"""

from __future__ import annotations

import json
import sys
from collections.abc import MutableMapping
from pathlib import Path
from types import ModuleType
from uuid import uuid4

MAX_LINE = 65536


class RendererFailure(RuntimeError):
    def __init__(self, message, metadata=None):
        super().__init__(message)
        self.metadata = metadata or {}


class RemoteCache(MutableMapping):
    def __init__(self, index):
        self.index = index

    def _call(self, op, *args):
        frame = {"type": "cache", "index": self.index, "op": op, "args": list(args)}
        _write(frame)
        response = _read()
        if response.get("type") != "cache_result":
            raise RuntimeError("invalid cache response")
        if response.get("ok") is not True:
            if response.get("error") == "key_error":
                raise KeyError(response.get("key"))
            raise RuntimeError("cache_error")
        return response.get("result")

    def __getitem__(self, key):
        return self._call("getitem", key)

    def __setitem__(self, key, value):
        self._call("setitem", key, value)
        return None

    def __delitem__(self, key):
        self._call("delitem", key)
        return None

    def __iter__(self):
        keys = self._call("iter")
        return iter(keys)

    def __len__(self):
        return self._call("len")

    def __contains__(self, key):
        return self._call("contains", key)


class RemoteRenderer:
    def __init__(self, index):
        self.index = index

    def __call__(self, *args):
        _write({"type": "renderer", "index": self.index, "args": list(args)})
        response = _read()
        if response.get("type") != "renderer_result":
            raise RuntimeError("invalid renderer response")
        if response.get("ok") is not True:
            error = response.get("error")
            if not isinstance(error, dict):
                error = {}
            error_cls = type(str(error.get("class", "RendererFailure")),
                             (RendererFailure,), {})
            raise error_cls(str(error.get("message", "renderer failure")),
                            error.get("metadata"))
        return response.get("result")


def _read():
    line = sys.stdin.readline(MAX_LINE + 1)
    if not line:
        raise EOFError
    if len(line) > MAX_LINE:
        raise ValueError("frame too large")
    frame = json.loads(line)
    if type(frame) is not dict:
        raise ValueError("frame not object")
    return frame


def _write(frame):
    sys.stdout.write(json.dumps(frame, sort_keys=True) + "\n")
    sys.stdout.flush()


def _load(path, source):
    module = ModuleType(f"_cache_candidate_{uuid4().hex}")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
    entry = getattr(module, "render_preview", None)
    if not callable(entry):
        raise TypeError("candidate must export render_preview")
    return entry


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        _write({"type": "fatal", "error": "invalid_arguments"})
        return 2
    try:
        entry = _load(Path(argv[0]), Path(argv[0]).read_bytes())
    except (OSError, Exception, SystemExit):
        _write({"type": "fatal", "error": "candidate_load_failed"})
        return 0
    while True:
        try:
            call = _read()
        except EOFError:
            return 0
        except (ValueError, json.JSONDecodeError):
            _write({"type": "fatal", "error": "invalid_call"})
            return 2
        if call.get("type") != "call":
            _write({"type": "fatal", "error": "invalid_call"})
            return 2
        index = call.get("index")
        args = call.get("args")
        if type(index) is not int or type(args) is not list:
            _write({"type": "fatal", "error": "invalid_call"})
            return 2
        cache = RemoteCache(index)
        renderer = RemoteRenderer(index)
        try:
            value = entry(cache, renderer, *args)
            frame = {"type": "result", "index": index, "status": "ok",
                     "value": value}
        except Exception as exc:  # candidate/renderer exception is an observation
            frame = {"type": "result", "index": index, "status": "exception",
                     "class": type(exc).__name__, "message": str(exc)}
        _write(frame)


if __name__ == "__main__":
    sys.exit(main())
