"""Boundary canary candidate (container-only; never host-imported).

The shim loads this file as candidate code. Its module import attempts only
forbidden paths that should not exist in the candidate container: grader source,
references, host project, credentials and the Docker socket. The host records the
actual candidate-container witness; this file itself grants no authority.
"""

from pathlib import Path

PROBES = (
    "/grader/retry_processing.py",
    "/references/retry-processing/correct/processor.py",
    "/host",
    "/project",
    "/.env.local",
    "/var/run/docker.sock",
    "/protected/exchange.json",
)

RESULTS = {}
for probe in PROBES:
    try:
        Path(probe).read_bytes()
        RESULTS[probe] = "accessible"
    except (OSError, ValueError):
        RESULTS[probe] = "denied"


def process_event(store, item):
    return "invalid"


def process_batch(store, items):
    return []
