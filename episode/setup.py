"""Explicit clean-source draft preparation; never executes Docker or a provider."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from adapters.budget import BudgetError, CampaignLedger
from episode import live
from episode.driver import _path_value, _same_or_inside, DriverError
from episode.workspace import assert_no_reparse_chain, WorkspaceError


class SetupError(ValueError):
    pass


def _source_identity(source):
    if source != Path(__file__).resolve().parents[1]:
        raise SetupError("source_module_mismatch")
    def git(*args):
        try:
            result = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(source), *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SetupError("source_unverified") from None
        if result.returncode:
            raise SetupError("source_unverified")
        return result.stdout.decode("ascii", "replace").strip()
    if git("status", "--porcelain", "--untracked-files=all"):
        raise SetupError("source_not_clean")
    return {"commit": live._git_head(source), "tree": live._git_tree(source)}


def prepare(args):
    if not args.prepare:
        raise SetupError("prepare_required")
    roots = {}
    for name in ("project_root", "source_root", "campaign_root", "run_root"):
        path = _path_value(getattr(args, name), "invalid_path")
        assert_no_reparse_chain(path)
        roots[name] = path.resolve()
    project, source, campaign, run = (roots[name] for name in (
        "project_root", "source_root", "campaign_root", "run_root"))
    if not project.is_dir() or not source.is_dir() or project == source:
        raise SetupError("distinct_existing_roots_required")
    if (source / ".env.local").exists() or live.check_ledger_path(source).exists():
        raise SetupError("source_contains_private_storage")
    if campaign.exists() or run.exists():
        raise SetupError("fresh_campaign_and_run_required")
    from scenarios.prepare import _protected_output_roots
    protected = [source, *_protected_output_roots(source), *_protected_output_roots(project)]
    for output in (campaign, run):
        if any(_same_or_inside(output, tree) or _same_or_inside(tree, output)
               for tree in protected):
            raise SetupError("protected_output_overlap")
    if _same_or_inside(run, campaign) or _same_or_inside(campaign, run):
        raise SetupError("campaign_run_overlap")
    live._valid_episode_id(args.episode_id)
    live._valid_episode_id(args.campaign_id)
    operation = live._valid_operation({
        "evidence_kind": "real_provider", "campaign_id": args.campaign_id,
        "operation_id": args.operation_id, "run_id": args.run_id,
        "stage": args.stage, "scenario_id": args.scenario_id, "arm": args.arm,
        "trial": args.trial, "dataset_id": args.dataset_id,
    }, args.episode_id)
    source_identity = _source_identity(source)
    canonical = live.check_ledger_path(project)
    assert_no_reparse_chain(canonical)
    if args.initialize_ledger and canonical.exists():
        raise SetupError("canonical_ledger_exists_refused")
    if not args.initialize_ledger and not canonical.is_file():
        raise SetupError("canonical_ledger_missing_refused")
    # Existing CampaignLedger construction validates the existing DB read-only;
    # explicit initialization alone may create the accepted canonical DB.
    if not args.initialize_ledger:
        CampaignLedger(str(project), campaign_id=args.campaign_id, cap_usd=live.CAP_USD).snapshot()
    gate = {
        "gate_id": "G4-R1-READY", "status": "DRAFT_REQUIRES_REVIEW",
        "source": source_identity,
        "operation": {key: operation[key] for key in live.G4_OPERATION_KEYS},
        "limits": {
            "max_calls": live.MAX_CALLS,
            "max_output_tokens_per_call": live.MAX_OUTPUT_TOKENS,
            "max_call_seconds": live.MAX_CALL_SECONDS,
            "max_call_reservation_micro_usd": live._ceil_micro(live.MAX_CALL_COST_USD),
            "max_operation_reservation_micro_usd": live._ceil_micro(live.MAX_OPERATION_COST_USD),
            "max_wall_seconds": int(live.MAX_WALL_SECONDS),
        },
        "budget": {"campaign_id": args.campaign_id,
                   "cap_microdollars": int(live.CAP_USD) * 1_000_000},
        "preparation": "empty_registry_no_runtime_qualification",
    }
    if args.initialize_ledger:
        live.initialize_ledger(project, args.campaign_id)
    campaign.mkdir(parents=True, exist_ok=False)
    run.parent.mkdir(parents=True, exist_ok=True)
    def write(relative, value):
        path = campaign / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
    released = {"status": "RELEASED_AFTER_SETTLEMENT", "unknown_settlement": False,
                "active_docker_operations": 0,
                "preparation": "empty_registry_no_operation_started"}
    write("lease/P2-AUTHORED-001/owner.json", {"owner": "P2", **released})
    write("leases/docker.json", released)
    write("gate-draft.json", gate)
    return {"status": "PREPARED_DRAFT_REQUIRES_REVIEW", "provider_calls": 0,
            "docker_operations": 0, "runtime_qualified": False,
            "source": source_identity, "ledger": str(canonical),
            "gate": str(campaign / "gate-draft.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project-root", "source-root", "campaign-root", "run-root",
                 "campaign-id", "episode-id", "operation-id", "run-id",
                 "stage", "scenario-id", "arm", "dataset-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--trial", type=int, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--initialize-ledger", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare(args)
    except (SetupError, live.LiveGateError, BudgetError, DriverError, WorkspaceError) as error:
        print("setup refused: " + str(error), file=sys.stderr)
        return 2
    except OSError:
        print("setup refused: preparation_io_failed", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
