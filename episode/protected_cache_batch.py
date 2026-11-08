"""Authored-only Cache protected batch; default refusal, â‰¤3 lifecycles."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from episode.protected_cache import cache_session, grade_cache
from episode.protected_session import docker_env

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = [
    ("correct_v2", ROOT / "references/preview-cache/correct/preview.py", "passed", "v2"),
    ("v1_partial", ROOT / "references/preview-cache/v1/preview.py", "passed", "v1"),
    ("v1_partial_as_v2", ROOT / "references/preview-cache/v1/preview.py", "failed", "v2"),
    ("original", ROOT / "tasks/preview-cache/preview.py", "failed", "v2"),
]


def run_batch(docker, run_root):
    probe = subprocess.run(
        [str(docker), "version", "--format", "{{json .Server}}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=20, check=False, env=docker_env(),
    )
    if probe.returncode != 0:
        raise RuntimeError("docker_gate_refused")
    run_root = Path(run_root)
    if run_root.exists():
        raise SystemExit("run root exists; refusing")
    run_root.mkdir(parents=True)
    shim = ROOT / "protected"
    summaries = []
    for name, candidate, expected, requirements_version in CONTROLS:
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        session = cache_session(docker, candidate, shim, "cache-" + name)
        try:
            receipt = grade_cache(session, digest,
                                  evidence_kind="protected_container_grading",
                                  requirements_version=requirements_version,
                                  checkpoint_id="cache-checkpoint-1")
            cleanup = getattr(session, "cleanup", "unknown")
        except Exception as exc:
            receipt = {"status": "infrastructure_error", "origin": "executor",
                       "candidate_sha256": digest,
                       "error": getattr(exc, "code", type(exc).__name__)}
            cleanup = "unknown"
        summary = {
            "control": name, "expected": expected,
            "status": receipt.get("status"), "origin": receipt.get("origin"),
            "cleanup": cleanup, "functional_pass": receipt.get("functional_pass"),
            "candidate_sha256": digest,
            "requirements_version": requirements_version,
            "checks_passed": sum(1 for c in receipt.get("checks", []) if c.get("passed")),
            "isolation": getattr(session, "witness", None),
        }
        (run_root / f"{name}.receipt.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summaries.append(summary)
        if summary.get("cleanup") != "settled" or summary.get("status") == "infrastructure_error":
            raise RuntimeError("cleanup_or_infrastructure_stop")
    return summaries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--docker", default=r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args(argv)
    if not args.execute:
        print("cache batch refused: default/rehearsal refusal", file=sys.stderr)
        return 2
    try:
        summaries = run_batch(args.docker, args.run_root)
    except SystemExit as exc:
        print(f"cache batch refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("cache batch refused: cache_batch_setup_error", file=sys.stderr)
        return 2
    print(json.dumps(summaries, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
