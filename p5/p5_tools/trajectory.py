"""Checkpoint trajectory reconstruction from verified snapshot observations.

This accepts a future, explicitly versioned observation input. The current
report-input-0.1 projection has no checkpoint fields, so an empty input yields
available=false rather than invented values. Flags are contextual, not penalties.
"""
from __future__ import annotations

SCHEMA = "p5-checkpoint-observations-0.1"


class TrajectoryError(ValueError):
    """Invalid trajectory observation input."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def from_projection(documents, episodes):
    """Consume trajectory-output-0.1 documents and join them to report episodes.

    P4's trajectory projection is per-episode and labels grading provenance in
    ``evidence_kind``; report rows label provider provenance. The join is by
    ``episode_id`` only and the two evidence-kind axes are never pooled.
    """
    if not isinstance(documents, list):
        raise TrajectoryError("documents must be a list")
    if not documents:
        return {
            "schema_version": "p5-trajectory-consumer-0.1",
            "available": False,
            "reason": ("No trajectory-output-0.1 document supplied. P5.2.2 remains pending "
                       "until accepted checkpoint projections exist."),
            "episodes": [],
            "unmapped_episode_ids": [],
        }
    mapping = {}
    for ep in episodes:
        if not isinstance(ep, dict) or "episode_id" not in ep:
            raise TrajectoryError("episodes must carry episode_id")
        mapping[ep["episode_id"]] = ep
    rows = []
    unmapped = []
    seen = set()
    for doc in documents:
        if not isinstance(doc, dict) or doc.get("schema_version") != "trajectory-output-0.1":
            raise TrajectoryError("unsupported trajectory document")
        eid = doc.get("episode_id")
        if eid in seen:
            raise TrajectoryError("duplicate trajectory episode")
        seen.add(eid)
        ep = mapping.get(eid)
        if ep is None:
            unmapped.append(eid)
            continue
        snapshots = doc.get("snapshots")
        segments = doc.get("segments")
        if not isinstance(snapshots, list) or not isinstance(segments, list):
            raise TrajectoryError("snapshots and segments must be lists")
        known = {"pass": 0, "fail": 0, "unknown": 0}
        first_candidates = []
        regressions = 0
        versions = []
        for seg in segments:
            if not isinstance(seg, dict):
                raise TrajectoryError("invalid segment")
            versions.append(seg.get("requirements_version"))
            counts = (seg.get("known_pass", 0), seg.get("known_fail", 0), seg.get("unknown", 0))
            if any(not _is_int(v) or v < 0 for v in counts):
                raise TrajectoryError("invalid segment counts")
            if sum(counts) != seg.get("snapshot_count"):
                raise TrajectoryError("segment counts do not partition snapshot_count")
            known["pass"] += counts[0]
            known["fail"] += counts[1]
            known["unknown"] += counts[2]
            first = seg.get("first_observed_correct")
            if isinstance(first, dict) and _is_int(first.get("seq")):
                first_candidates.append(first["seq"])
            regressions += len(seg.get("observed_regressions") or [])
        if sum(known.values()) != len(snapshots):
            raise TrajectoryError("trajectory counts do not match snapshots")
        rows.append({
            "cell_id": f'{ep.get("scenario_id")}.{ep.get("arm")}.r{ep.get("trial")}',
            "episode_id": eid,
            "scenario_id": ep.get("scenario_id"),
            "arm": ep.get("arm"),
            "trial": ep.get("trial"),
            "trajectory_evidence_kind": doc.get("evidence_kind"),
            "snapshots": len(snapshots),
            "known_pass": known["pass"],
            "known_fail": known["fail"],
            "unknown": known["unknown"],
            "first_observed_correct_seq": min(first_candidates) if first_candidates else None,
            "observed_regressions": regressions,
            "requirements_versions": versions,
        })
    return {
        "schema_version": "p5-trajectory-consumer-0.1",
        "available": True,
        "episodes": rows,
        "unmapped_episode_ids": unmapped,
        "notes": [
            "Trajectory evidence_kind is grading provenance, not provider provenance.",
            "Join to report rows is by episode_id only; never pool the two schemas' evidence kinds.",
            "Regressions are observed adjacent pass->fail transitions only; flags need contextual review.",
            "Repeated unchanged-input checks are not in trajectory-output-0.1; that diagnostic remains pending.",
        ],
    }


def build(observations):
    if not isinstance(observations, list):
        raise TrajectoryError("observations must be a list")
    if not observations:
        return {
            "schema_version": "p5-trajectory-0.1",
            "available": False,
            "reason": ("No verified checkpoint observations supplied. The current "
                       "report-input-0.1 projection has no checkpoint trajectory; a future "
                       "P4 checkpoint projection is required. No values invented."),
            "checkpoints": [],
            "flags": [],
        }
    required = {"cell_id", "trial", "checkpoint_id", "sequence", "correct", "input_sha256"}
    seen = set()
    for o in observations:
        if not isinstance(o, dict) or not required.issubset(o):
            raise TrajectoryError("observation missing required fields")
        if not isinstance(o["cell_id"], str) or not o["cell_id"]:
            raise TrajectoryError("cell_id required")
        if not _is_int(o["trial"]) or o["trial"] < 1:
            raise TrajectoryError("trial must be a positive integer")
        if not isinstance(o["checkpoint_id"], str) or not o["checkpoint_id"]:
            raise TrajectoryError("checkpoint_id required")
        if not _is_int(o["sequence"]) or o["sequence"] < 1:
            raise TrajectoryError("sequence must be a positive integer")
        if not isinstance(o["correct"], bool):
            raise TrajectoryError("correct must be boolean")
        if not isinstance(o["input_sha256"], str) or len(o["input_sha256"]) != 64:
            raise TrajectoryError("input_sha256 must be a SHA-256 hex digest")
        key = (o["cell_id"], o["trial"], o["checkpoint_id"], o["sequence"])
        if key in seen:
            raise TrajectoryError("duplicate observation sequence")
        seen.add(key)

    groups = {}
    for o in observations:
        groups.setdefault((o["cell_id"], o["trial"], o["checkpoint_id"]), []).append(o)
    rows = []
    flags = []
    for (cell, trial, checkpoint), items in sorted(groups.items()):
        items = sorted(items, key=lambda x: x["sequence"])
        correct_seqs = [x["sequence"] for x in items if x["correct"]]
        first = min(correct_seqs) if correct_seqs else None
        regressions = [x["sequence"] for x in items
                       if first is not None and not x["correct"] and x["sequence"] > first]
        repeats = {}
        for x in items:
            repeats[x["input_sha256"]] = repeats.get(x["input_sha256"], 0) + 1
        repeated = sorted(sha for sha, count in repeats.items() if count > 1)
        rows.append({
            "cell_id": cell,
            "trial": trial,
            "checkpoint_id": checkpoint,
            "observations": len(items),
            "first_correct_sequence": first,
            "regression_after_first_correct": bool(regressions),
            "regression_sequences": regressions,
            "repeated_unchanged_check": bool(repeated),
            "unchanged_input_sha256": repeated,
        })
        if regressions:
            flags.append({"type": "regression", "cell_id": cell, "trial": trial,
                          "checkpoint_id": checkpoint, "sequences": regressions})
        if repeated:
            flags.append({"type": "unchanged_input_repeat", "cell_id": cell, "trial": trial,
                          "checkpoint_id": checkpoint, "input_sha256": repeated})
    return {
        "schema_version": "p5-trajectory-0.1",
        "available": True,
        "observations": len(observations),
        "checkpoints": rows,
        "flags": flags,
        "note": ("First-correct and repeated-check findings are diagnostic only; review them "
                 "in context. They are not automatic penalties. No hidden grading is inferred."),
    }
