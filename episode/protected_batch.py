"""Authored-only protected batch for Main review.

Default is rehearsal/refusal. With ``--execute`` and Docker access, runs the
correct control, known-incorrect control, forged-observation canary and boundary
canary as isolated candidate containers, then one authored failing public check with
the reviewed executor. Writes only sanitized receipt summaries under the private run
root. Never contacts a provider, ledger or credential.

Exact native command:
  python -m episode.protected_batch --execute --docker "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe" --run-root "C:\\...\\r2\\tmp\\authored-batch"
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from episode.protected_session import DockerSession, grade_session

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3"
CONTROLS = [
    ("correct", ROOT / "references/retry-processing/correct/processor.py", "passed"),
    ("incorrect", ROOT / "protected/controls/incorrect_processor.py", "failed"),
    ("forged", ROOT / "protected/controls/forged_processor.py", "not_passed"),
    ("boundary_canary", ROOT / "protected/controls/canary_processor.py", "not_passed"),
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def run_batch(docker, run_root):
    import subprocess
    probe = subprocess.run(
        [str(docker), "version", "--format", "{{json .Server}}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=20, check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError("docker_gate_refused")
    run_root = Path(run_root)
    if run_root.exists():
        raise SystemExit("run root exists; refusing")
    run_root.mkdir(parents=True)
    shim = ROOT / "protected"
    summaries = []
    for name, candidate, expected in CONTROLS:
        digest = _sha(candidate.read_bytes())
        session = DockerSession(docker, candidate, shim, f"batch-{name}")
        try:
            receipt = grade_session(
                session, digest, evidence_kind="protected_container_grading")
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            if str(code).startswith("docker_"):
                raise  # stop after the first Docker denial/failure
            receipt = {"schema_version": "protected-grade-receipt-0.1",
                       "status": "infrastructure_error", "origin": "executor",
                       "candidate_sha256": digest, "cleanup": "unknown",
                       "error": code}
        summary = {
            "control": name,
            "expected": expected,
            "status": receipt.get("status"),
            "origin": receipt.get("origin"),
            "cleanup": receipt.get("cleanup"),
            "functional_pass": receipt.get("functional_pass"),
            "candidate_sha256": digest,
            "checks_passed": sum(1 for c in receipt.get("checks", []) if c.get("passed")),
            "isolation": receipt.get("isolation") or getattr(session, "witness", None),
        }
        path = run_root / f"{name}.receipt.json"
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        summaries.append(summary)
    summaries.append(_public_failure(docker, run_root))
    return summaries


def _public_failure(docker, run_root):
    """One authored public-check lifecycle using the accepted executor."""
    from scenarios.prepare import prepare_scenario
    import subprocess

    package = run_root / "public-package"
    prepare_scenario(ROOT, "R1", package)
    executor = Path(r"C:\Users\Dv\Desktop\FollowThrough\.coordination\runtime\group05\build\debug\container-check.exe")
    output = run_root / "public-check"
    completed = subprocess.run([
        str(executor), "--workspace", str(package / "agent"),
        "--run-id", "batch-public-fail", "--output-dir", str(output),
        "--docker", str(docker), "--execute",
    ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
       stderr=subprocess.PIPE, timeout=150, check=False)
    receipt = {}
    try:
        receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    summary = {
        "control": "failing_public", "expected": "check_failed",
        "status": receipt.get("status"), "cleanup": receipt.get("cleanup"),
        "public_check_exit": receipt.get("public_check_exit"),
        "process_exit": completed.returncode,
        "candidate_sha256": receipt.get("workspace", {}).get("candidate_sha256"),
    }
    (run_root / "failing_public.receipt.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--docker", default=r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args(argv)
    if not args.execute:
        print("protected batch refused: rehearsal/default refusal; pass --execute only after review", file=sys.stderr)
        return 2
    try:
        summaries = run_batch(args.docker, args.run_root)
    except SystemExit as exc:
        print(f"protected batch refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("protected batch refused: batch_setup_error", file=sys.stderr)
        return 2
    print(json.dumps(summaries, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
