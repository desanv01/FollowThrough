"""Forged-observation canary candidate (container-only).

It prints a fake protocol result and returns statuses without using the Store. The
host must ignore the untrusted frame as authority and reject the empty durable state.
"""

import json
import sys

print(json.dumps({
    "type": "result",
    "index": 0,
    "kind": "event",
    "status": "applied",
    "fault_propagated": False,
}))
sys.stdout.flush()


def process_event(store, item):
    return "applied"


def process_batch(store, items):
    return ["applied" for _ in items]
