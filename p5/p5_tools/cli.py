"""Command-line entry point for the P5 offline tooling.

Usage from the prep directory:
    python -B -m p5_tools.cli <subcommand> ...
All operations are offline and local. No provider, Docker or credential access.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analysis, design, ledger, receipts, recompute, reproduce, trajectory


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="p5_tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("design")
    p.add_argument("--repetitions", type=int, default=3)
    p.add_argument("--json", required=True)

    p = sub.add_parser("affordability")
    p.add_argument("--design", required=True)
    p.add_argument("--pilot", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("run-request")
    p.add_argument("--design", required=True)
    p.add_argument("--stage", choices=("pilot", "formal"), required=True)
    p.add_argument("--bounds", required=True)
    p.add_argument("--freeze", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("analyze")
    p.add_argument("--dataset", required=True)
    p.add_argument("--json", required=True)
    p.add_argument("--md")
    p.add_argument("--formal", action="store_true")

    p = sub.add_parser("compare")
    p.add_argument("--dataset", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("recompute")
    p.add_argument("--input", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--json", required=True)
    p.add_argument("--formal", action="store_true")

    p = sub.add_parser("validate-receipt")
    p.add_argument("--receipt", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("trajectory")
    p.add_argument("--observations", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("inspect-zip")
    p.add_argument("--zip", required=True)
    p.add_argument("--comment", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("extract")
    p.add_argument("--zip", required=True)
    p.add_argument("--comment", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("manifest")
    p.add_argument("--root", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("verify-tree")
    p.add_argument("--root", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("exclusions")
    p.add_argument("--root", required=True)
    p.add_argument("--allow", action="append", default=[])
    p.add_argument("--json", required=True)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "design":
            out = design.build_design(repetitions=args.repetitions)
        elif args.cmd == "affordability":
            out = design.affordability(_read_json(args.design), _read_json(args.pilot))
        elif args.cmd == "run-request":
            out = design.build_run_request(_read_json(args.design), args.stage,
                                           _read_json(args.bounds), _read_json(args.freeze),
                                           args.dataset_id)
        elif args.cmd == "analyze":
            out = analysis.aggregate(_read_json(args.dataset), formal=args.formal)
            if args.md:
                Path(args.md).write_text(analysis.render_markdown(out), encoding="utf-8")
        elif args.cmd == "compare":
            out = analysis.compare_arms(analysis.aggregate(_read_json(args.dataset)))
        elif args.cmd == "recompute":
            out = recompute.compare(_read_json(args.input), _read_json(args.report),
                                    formal=args.formal)
        elif args.cmd == "validate-receipt":
            out = receipts.validate_receipt(_read_json(args.receipt))
        elif args.cmd == "trajectory":
            payload = _read_json(args.observations)
            observations = payload.get("observations") if isinstance(payload, dict) else payload
            out = trajectory.build(observations)
        elif args.cmd == "inspect-zip":
            out = reproduce.inspect_zip(args.zip, args.comment)
        elif args.cmd == "extract":
            out = reproduce.safe_extract(args.zip, args.dest, args.comment)
        elif args.cmd == "manifest":
            out = {"root": args.root, "entries": reproduce.manifest_tree(args.root)}
        elif args.cmd == "verify-tree":
            out = reproduce.verify_tree(args.root, _read_json(args.manifest)["entries"])
        elif args.cmd == "exclusions":
            entries = reproduce.manifest_tree(args.root)
            out = reproduce.exclusion_scan([e["path"] for e in entries], args.allow)
        else:
            raise SystemExit(2)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _write_json(args.json, out)
    print(json.dumps({"status": "OK", "out": args.json}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
