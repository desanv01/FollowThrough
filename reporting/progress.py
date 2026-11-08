"""Deterministic live-progress projection for saved episode event receipts.

The renderer consumes an explicit JSONL event log (normally a live episode's
sanitized events) and produces a scriptable status line/JSON.  This packet does
not observe a live process or poll a file; P3/Main supply actual host receipts.
Values are counts/elapsed/estimates from supplied events and remain unknown when
absent.  No raw event text, prompts or credentials are displayed.

Proposed P4.5.4 offline presentation; import performs no I/O.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

SCHEMA = "live-progress-0.1"
MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 100000
MAX_TEXT = 160

_COUNT_EVENTS = {
    "model_response": "model_responses",
    "action_attempt": "tool_actions",
    "write_result": "writes",
    "check_result": "checks",
    "reviewer_review": "reviews",
    "requirements_update": "requirements_updates",
}
_ROLE_LABEL = {"O1": "O1", "W1": "W1", "R1": "R1", "host": "controller",
               "controller": "controller"}
_SAFE_EVENT = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")


class ProgressError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _sanitize(value):
    text = "" if value is None else str(value)
    out = []
    for character in text:
        code = ord(character)
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            out.append(" ")
        else:
            out.append(character)
    return "".join(out)[:MAX_TEXT]


def _optional_int(value):
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ProgressError("invalid_value")
    return value


def _read_jsonl(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_EVENT_BYTES + 1)
    except OSError:
        raise ProgressError("events_unreadable") from None
    if len(data) > MAX_EVENT_BYTES:
        raise ProgressError("events_too_large")
    events = []
    for line in data.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            raise ProgressError("invalid_event_json") from None
        if not isinstance(event, dict):
            raise ProgressError("invalid_event")
        events.append(event)
        if len(events) > MAX_EVENTS:
            raise ProgressError("too_many_events")
    return events


def summarize_progress(meta, events, result=None):
    """Summarize supplied events/result without inventing live observations."""
    if not isinstance(meta, dict):
        raise ProgressError("invalid_meta")
    if not isinstance(events, list):
        raise ProgressError("invalid_events")
    counts = {
        "model_responses": 0,
        "tool_actions": 0,
        "writes": 0,
        "checks": 0,
        "reviews": 0,
        "requirements_updates": 0,
    }
    latest_role = meta.get("role")
    last_event_type = None
    elapsed = None
    evidence_ids = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if not isinstance(event_type, str) or not _SAFE_EVENT.fullmatch(event_type):
            continue
        last_event_type = event_type
        if event.get("role") in _ROLE_LABEL:
            latest_role = _ROLE_LABEL[event["role"]]
        counter = _COUNT_EVENTS.get(event_type)
        if counter is not None:
            counts[counter] += 1
        if type(event.get("elapsed_ms")) is int and event["elapsed_ms"] >= 0:
            elapsed = event["elapsed_ms"]
        evidence = event.get("evidence_id")
        if isinstance(evidence, str):
            evidence_ids.append(_sanitize(evidence))
    result = result if isinstance(result, dict) else {}
    result_status = result.get("status") or meta.get("status")
    if isinstance(result_status, str) and _SAFE_EVENT.fullmatch(result_status):
        state = result_status
    elif last_event_type is None:
        state = "no_events"
    else:
        state = "running_or_partial"
    cleanup = result.get("cleanup") or meta.get("cleanup")
    if cleanup not in ("settled", "unknown", "not_created"):
        cleanup = "unknown"
    estimate = _optional_int(meta.get("estimated_cost_micro_usd"))
    reserved = _optional_int(meta.get("reserved_micro_usd"))
    return {
        "schema_version": SCHEMA,
        "episode_id": _sanitize(meta.get("episode_id")),
        "run_id": _sanitize(meta.get("run_id") or meta.get("episode_id")),
        "scenario_id": _sanitize(meta.get("scenario_id")),
        "arm": _sanitize(meta.get("arm")),
        "stage": _sanitize(meta.get("stage")),
        "evidence_kind": _sanitize(meta.get("evidence_kind")),
        "source_sha256": _sanitize(meta.get("source_sha256")),
        "requirements_version": _sanitize(meta.get("requirements_version")),
        "role": _sanitize(latest_role),
        "state": state,
        "cleanup": cleanup,
        "event_count": len(events),
        "last_event_type": last_event_type,
        "counts": counts,
        "elapsed_ms": elapsed,
        "estimated_cost_micro_usd": estimate,
        "reserved_micro_usd": reserved,
        "cost_note": "estimate and authorisation; never billed cost",
        "evidence_ids": evidence_ids[-5:],
        "saved_paths": {
            "events": _sanitize(meta.get("events_path")),
            "result": _sanitize(meta.get("result_path")),
            "report": _sanitize(meta.get("report_path")),
        },
        "limitations": [
            "supplied receipts only; no live polling or success inference",
            "unknown values remain unknown",
            "cleanup/status labels are supplied, not verified by this renderer",
        ],
    }


def render_progress(progress):
    """Stable noninteractive one-line view; no ANSI/control text."""
    counts = progress["counts"]
    elapsed = progress["elapsed_ms"]
    elapsed_text = "unknown" if elapsed is None else "%.1fs" % (elapsed / 1000.0)
    estimate = (
        "unknown" if progress["estimated_cost_micro_usd"] is None
        else "$%d.%06d" % divmod(progress["estimated_cost_micro_usd"], 1_000_000)
    )
    reserved = (
        "unknown" if progress["reserved_micro_usd"] is None
        else "$%d.%06d" % divmod(progress["reserved_micro_usd"], 1_000_000)
    )
    parts = [
        "[%s]" % progress["state"],
        "%s/%s/%s" % (progress["scenario_id"], progress["arm"], progress["stage"]),
        "role=%s" % (progress["role"] or "unknown"),
        "responses=%d" % counts["model_responses"],
        "actions=%d" % counts["tool_actions"],
        "checks=%d" % counts["checks"],
        "elapsed=%s" % elapsed_text,
        "estimate=%s" % estimate,
        "reserved=%s" % reserved,
        "cleanup=%s" % progress["cleanup"],
        "events=%d" % progress["event_count"],
    ]
    if progress["saved_paths"]["report"]:
        parts.append("report=%s" % progress["saved_paths"]["report"])
    return " | ".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -B -m reporting.progress",
        description="Render saved sanitized episode progress receipts.",
    )
    parser.add_argument("--events", required=True)
    parser.add_argument("--result")
    parser.add_argument("--meta")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        events = _read_jsonl(args.events)
        meta = {}
        if args.meta:
            try:
                with open(args.meta, encoding="utf-8") as handle:
                    meta = json.loads(handle.read())
            except (OSError, ValueError):
                raise ProgressError("meta_unreadable")
        result = None
        if args.result:
            try:
                with open(args.result, encoding="utf-8") as handle:
                    result = json.loads(handle.read())
            except (OSError, ValueError):
                raise ProgressError("result_unreadable")
        state = summarize_progress(meta, events, result)
        if args.json:
            sys.stdout.write(json.dumps(state, ensure_ascii=False,
                                        indent=2) + "\n")
        else:
            sys.stdout.write(render_progress(state) + "\n")
        return 0
    except ProgressError as error:
        print("error: progress: " + error.code, file=sys.stderr)
        return 1


__all__ = ["ProgressError", "main", "render_progress", "summarize_progress"]


if __name__ == "__main__":
    raise SystemExit(main())
