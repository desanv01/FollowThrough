"""Fake boundary subprocess for Rust integration tests only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


parser = argparse.ArgumentParser()
parser.add_argument("--candidate", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()

source = args.candidate.read_bytes()
mode = source.decode().strip().removeprefix("# ")
assert "FOLLOWTHROUGH_FAKE_SECRET" not in os.environ
original = Path("processor.py")
assert args.candidate.resolve() != original.resolve()

if mode == "timeout":
    time.sleep(10)
if mode in {"stdout_overflow", "stderr_overflow"}:
    stream = sys.stdout if mode == "stdout_overflow" else sys.stderr
    stream.write("x" * 65536)
    stream.flush()
if mode == "missing":
    sys.exit(0)
if mode == "no_grade_nonzero":
    sys.exit(17)
if mode == "invalid":
    args.output.write_text("{not json}", encoding="utf-8")
    sys.exit(0)
if mode == "mutate_original":
    original.write_text("# changed after snapshot", encoding="utf-8")
    assert args.candidate.read_bytes() == source
if mode == "mutate_snapshot":
    args.candidate.write_text("# changed snapshot", encoding="utf-8")

errors = []
if mode in {"candidate", "grader"}:
    errors.append({"origin": mode, "phase": "fake-boundary", "detail": "fake diagnostic"})
passed = mode != "fail" and not errors
check_ids = [
    "new_delivery",
    "duplicate_replay",
    "conflicting_units",
    "tenant_and_event_identity",
    "malformed_items",
    "mixed_batch",
    "rollback_after_credit",
    "rollback_after_mark",
    "interrupted_batch_retry",
    "ack_loss_reopen_retry",
]
if errors:
    check_ids = check_ids[:1]
elif mode == "omitted_check":
    check_ids.pop()
elif mode == "duplicate_check":
    check_ids[-1] = check_ids[0]
elif mode == "unknown_check":
    check_ids[-1] = "unknown_requirement"
check_ids.reverse()

grade = {
    "schema_version": "0.1",
    "task_family": "retry-processing",
    "candidate": {
        "path": str(original.resolve() if mode == "wrong_path" else args.candidate.resolve()),
        "sha256": "0" * 64 if mode == "wrong_hash" else hashlib.sha256(source).hexdigest(),
    },
    "evidence_kind": "real_local_control",
    "functional_pass": passed,
    "checks": [
        {"id": check_id, "passed": mode != "fail" or index != 0, "detail": "fake boundary result"}
        for index, check_id in enumerate(check_ids)
    ],
    "errors": errors,
}
with args.output.open("x", encoding="utf-8") as file:
    json.dump(grade, file)
if errors:
    sys.exit(18)
