"""Deterministic offline reporting projection for FollowThrough (contract v0.1).

This module summarizes *supplied* judgments and supplied provenance.  It does
not grade episodes, re-execute traces or establish functional/workflow/
evidence/completion truth.  It uses only the Python standard library, and
importing it performs no I/O.

Contract: docs/project/REPORTING-CONTRACT.md (Main-owned, frozen for
P4-REPORT/r1) with docs/project/REPORTING-NUMERIC-CLARIFICATION.md applied for
the r2 correction (explicit trial/trace-seq upper bound).  The older runtime
event envelope is a separate interface and is intentionally not interpreted
here.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import stat
import sys
import time

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

SCHEMA_INPUT = "report-input-0.1"
SCHEMA_OUTPUT = "report-output-0.1"

MAX_INPUT_BYTES = 10 * 1024 * 1024  # 10 MiB, enforced before JSON parsing
MAX_EPISODES = 10_000
MAX_TRACE_EVENTS = 1_000
MAX_STRING = 4_096
MAX_IDENTIFIER = 128
MAX_SAFE_INT = 9_007_199_254_740_991  # documented projection integer bound

EVIDENCE_KINDS = ("synthetic_development", "real_provider")
STAGES = ("development", "pilot", "formal")
SCENARIOS = ("R1", "R2", "R3", "C1", "C2", "C3")
ARMS = ("ordinary", "structured_progress")
STATUSES = (
    "completed",
    "partial",
    "limit_reached",
    "provider_error",
    "infrastructure_error",
    "invalid",
)
OUTCOMES = ("functional", "workflow", "evidence", "completion")
OUTCOME_VALUES = ("pass", "fail", "unknown")
RESOURCES = (
    "model_responses",
    "tool_actions",
    "input_tokens",
    "output_tokens",
    "elapsed_ms",
    "estimated_cost_micro_usd",
    "reserved_micro_usd",
)
TRACE_ROLES = ("O1", "W1", "R1", "controller")
TRACE_KINDS = (
    "supplied_fixture",
    "message",
    "edit",
    "check",
    "review",
    "requirement_update",
    "completion",
)

ELIGIBLE_STATUSES = ("completed", "partial", "limit_reached")
EXCLUDED_STATUSES = ("provider_error", "infrastructure_error", "invalid")
FAMILIES = {
    "R1": "retry-processing",
    "R2": "retry-processing",
    "R3": "retry-processing",
    "C1": "preview-cache",
    "C2": "preview-cache",
    "C3": "preview-cache",
}

EPISODE_KEYS = (
    "episode_id",
    "scenario_id",
    "arm",
    "trial",
    "stage",
    "evidence_kind",
    "status",
    "outcomes",
    "resources",
    "trace",
)
TRACE_KEYS = ("seq", "role", "kind", "summary")
TOP_LEVEL_KEYS = ("schema_version", "dataset_id", "episodes")

EVIDENCE_NOTICE = (
    "This report summarizes supplied judgments and supplied provenance only; "
    "it does not independently establish functional, workflow, evidence or "
    "completion truth and does not grade episodes. Synthetic development "
    "records are authored fixtures, not model results, live runs or benchmark "
    "performance. Provider, infrastructure and invalid attempts remain visible "
    "but are excluded from primary eligible denominators. Estimated cost and "
    "reserved allowance are not billed cost."
)

# Formula-leading text cells are prefixed for spreadsheet-open safety.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")
_FORMULA_PREFIX = "'"

_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9._:-]{1,%d}\Z" % MAX_IDENTIFIER)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ReportError(Exception):
    """Stable-code reporting error.

    Codes used by this implementation (worker-owned, documented):
      unsupported_schema, invalid_input, duplicate_key, duplicate_episode,
      duplicate_cell, input_too_large, output_exists, unsafe_path, io_error.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _invalid(where: str, why: str) -> "ReportError":
    return ReportError("invalid_input", "%s: %s" % (where, why))


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _is_reparse_stat(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _assert_no_reparse(path: str, what: str) -> None:
    """Walk existing components top-down and refuse any symlink/reparse point.

    This is deliberately the first filesystem probe for a caller-supplied path.
    Each component is checked only after every ancestor has been cleared, so a
    reparse ancestor stops the walk before its descendants are probed.  The
    stable-host assumption remains: this is a guard order, not race-proof
    filesystem access.
    """
    norm = os.path.abspath(os.path.normpath(path))
    drive, tail = os.path.splitdrive(norm)
    if drive:
        current = drive + os.sep
        parts = [part for part in tail.split(os.sep) if part]
    else:
        current = os.sep if norm.startswith(os.sep) else ""
        parts = [part for part in norm.split(os.sep) if part]
    for part in parts:
        current = os.path.join(current, part) if current else part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ReportError(
                "io_error", "cannot inspect %s path component" % what
            ) from exc
        if _is_reparse_stat(st):
            raise ReportError(
                "unsafe_path",
                "%s path contains a symlink or reparse point" % what,
            )


def _check_identifier(value, where: str) -> str:
    if not isinstance(value, str):
        raise _invalid(where, "expected a string identifier")
    if len(value) > MAX_IDENTIFIER:
        raise _invalid(where, "identifier exceeds 128 characters")
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise _invalid(where, "identifier must be nonempty ASCII A-Za-z0-9._:-")
    return value


def _check_string(value, where: str, maximum: int = MAX_STRING) -> str:
    if not isinstance(value, str):
        raise _invalid(where, "expected a string")
    if len(value) > maximum:
        raise _invalid(where, "string exceeds %d characters" % maximum)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid(where, "string is not valid Unicode text") from None
    return value


def _check_enum(value, where: str, allowed) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _invalid(where, "unsupported value")
    return value


def _check_int(value, where: str, minimum: int, maximum=None) -> int:
    if type(value) is not int:  # bool is a subclass; reject it explicitly
        raise _invalid(where, "expected an integer (bool not accepted)")
    if value < minimum:
        raise _invalid(where, "integer below lower bound")
    if maximum is not None and value > maximum:
        raise _invalid(where, "integer above upper bound")
    return value


def _check_object(value, where: str, keys) -> dict:
    if not isinstance(value, dict):
        raise _invalid(where, "expected an object")
    if set(value.keys()) != set(keys):
        raise _invalid(where, "unexpected or missing keys")
    return value


def _check_resource(value, where: str):
    if value is None:
        return None
    return _check_int(value, where, 0, MAX_SAFE_INT)


def _check_trace(value, where: str):
    if not isinstance(value, list):
        raise _invalid(where, "expected an array")
    if len(value) > MAX_TRACE_EVENTS:
        raise ReportError(
            "input_too_large",
            "%s: more than %d trace events" % (where, MAX_TRACE_EVENTS),
        )
    previous = 0
    events = []
    for index, raw in enumerate(value):
        at = "%s[%d]" % (where, index)
        _check_object(raw, at, TRACE_KEYS)
        seq = _check_int(raw["seq"], at + ".seq", 1, MAX_SAFE_INT)
        if seq <= previous:
            raise _invalid(at + ".seq", "sequence numbers must strictly increase")
        previous = seq
        role = _check_enum(raw["role"], at + ".role", TRACE_ROLES)
        kind = _check_enum(raw["kind"], at + ".kind", TRACE_KINDS)
        summary = _check_string(raw["summary"], at + ".summary")
        events.append(
            {"seq": seq, "role": role, "kind": kind, "summary": summary}
        )
    return events


def _check_episode(raw, index: int, seen_ids, seen_cells) -> dict:
    where = "episodes[%d]" % index
    _check_object(raw, where, EPISODE_KEYS)

    episode_id = _check_identifier(raw["episode_id"], where + ".episode_id")
    scenario_id = _check_enum(raw["scenario_id"], where + ".scenario_id", SCENARIOS)
    arm = _check_enum(raw["arm"], where + ".arm", ARMS)
    trial = _check_int(raw["trial"], where + ".trial", 1, MAX_SAFE_INT)
    stage = _check_enum(raw["stage"], where + ".stage", STAGES)
    evidence_kind = _check_enum(
        raw["evidence_kind"], where + ".evidence_kind", EVIDENCE_KINDS
    )
    status = _check_enum(raw["status"], where + ".status", STATUSES)

    if evidence_kind == "synthetic_development" and stage != "development":
        raise _invalid(
            where,
            "synthetic_development evidence requires stage=development",
        )

    outcomes_raw = _check_object(raw["outcomes"], where + ".outcomes", OUTCOMES)
    outcomes = {}
    for dimension in OUTCOMES:
        outcomes[dimension] = _check_enum(
            outcomes_raw[dimension],
            "%s.outcomes.%s" % (where, dimension),
            OUTCOME_VALUES,
        )

    resources_raw = _check_object(
        raw["resources"], where + ".resources", RESOURCES
    )
    resources = {}
    for name in RESOURCES:
        resources[name] = _check_resource(
            resources_raw[name], "%s.resources.%s" % (where, name)
        )

    trace = _check_trace(raw["trace"], where + ".trace")

    if episode_id in seen_ids:
        raise ReportError("duplicate_episode", "duplicate episode_id")
    seen_ids.add(episode_id)

    cell = (evidence_kind, stage, scenario_id, arm, trial)
    if cell in seen_cells:
        raise ReportError(
            "duplicate_cell",
            "duplicate (evidence_kind, stage, scenario_id, arm, trial) cell",
        )
    seen_cells.add(cell)

    return {
        "episode_id": episode_id,
        "scenario_id": scenario_id,
        "family": FAMILIES[scenario_id],
        "arm": arm,
        "trial": trial,
        "stage": stage,
        "evidence_kind": evidence_kind,
        "status": status,
        "outcomes": outcomes,
        "resources": resources,
        "trace": trace,
    }


def _validate_input(data) -> list:
    if not isinstance(data, dict):
        raise ReportError("invalid_input", "top-level value must be an object")

    if "schema_version" in data:
        version = data["schema_version"]
        if not isinstance(version, str):
            raise _invalid("schema_version", "expected a string")
        if version != SCHEMA_INPUT:
            raise ReportError(
                "unsupported_schema",
                "schema_version is not %s" % SCHEMA_INPUT,
            )

    if set(data.keys()) != set(TOP_LEVEL_KEYS):
        raise ReportError(
            "invalid_input",
            "top-level object must have exactly schema_version, dataset_id, episodes",
        )
    if data.get("schema_version") != SCHEMA_INPUT:
        raise ReportError(
            "unsupported_schema", "schema_version is not %s" % SCHEMA_INPUT
        )

    _check_identifier(data["dataset_id"], "dataset_id")

    episodes_raw = data["episodes"]
    if not isinstance(episodes_raw, list):
        raise _invalid("episodes", "expected an array")
    if len(episodes_raw) > MAX_EPISODES:
        raise ReportError(
            "input_too_large",
            "more than %d episode rows" % MAX_EPISODES,
        )

    seen_ids = set()
    seen_cells = set()
    episodes = []
    for index, raw in enumerate(episodes_raw):
        episodes.append(_check_episode(raw, index, seen_ids, seen_cells))
    return episodes


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


def _strict_outcome(status: str, outcomes: dict) -> str:
    if status in EXCLUDED_STATUSES:
        return "not_scored"
    if status != "completed":
        # Incomplete execution is never a strict success, even if every
        # supplied component happens to read "pass".
        return "fail"
    values = [outcomes[name] for name in OUTCOMES]
    if "fail" in values:
        return "fail"
    if "unknown" in values:
        return "unknown"
    return "pass"


def _episode_sort_key(episode: dict):
    return (
        episode["evidence_kind"],
        episode["stage"],
        episode["scenario_id"],
        episode["arm"],
        episode["trial"],
        episode["episode_id"],
    )


def _group_sort_key(group: dict):
    return (
        group["evidence_kind"],
        group["stage"],
        group["scenario_id"],
        group["arm"],
    )


def _empty_counts(keys) -> dict:
    return {key: 0 for key in keys}


def _summarize_group(group_key, rows) -> dict:
    evidence_kind, stage, scenario_id, arm = group_key
    attempted = len(rows)
    eligible_rows = [row for row in rows if row["status"] in ELIGIBLE_STATUSES]
    eligible = len(eligible_rows)
    excluded = attempted - eligible

    status_counts = _empty_counts(STATUSES)
    for row in rows:
        status_counts[row["status"]] += 1

    component_counts = {
        dimension: _empty_counts(OUTCOME_VALUES) for dimension in OUTCOMES
    }
    strict_counts = _empty_counts(OUTCOME_VALUES)
    for row in eligible_rows:
        for dimension in OUTCOMES:
            component_counts[dimension][row["outcomes"][dimension]] += 1
        strict_counts[row["strict_outcome"]] += 1

    resource_summary = {}
    for name in RESOURCES:
        known_sum = 0
        known_count = 0
        unknown_count = 0
        for row in rows:  # all attempts, including excluded rows
            value = row["resources"][name]
            if value is None:
                unknown_count += 1
            else:
                known_sum += value
                known_count += 1
        resource_summary[name] = {
            "known_sum": known_sum,
            "known_count": known_count,
            "unknown_count": unknown_count,
            "complete_total": None if unknown_count else known_sum,
        }

    return {
        "evidence_kind": evidence_kind,
        "stage": stage,
        "scenario_id": scenario_id,
        "arm": arm,
        "attempted": attempted,
        "eligible": eligible,
        "excluded": excluded,
        "status_counts": status_counts,
        "component_counts": component_counts,
        "strict_counts": strict_counts,
        "success_fraction": {
            "numerator": strict_counts["pass"],
            "denominator": eligible,
        },
        "resources": resource_summary,
    }


def build_report(data) -> dict:
    """Validate a parsed projection object and return a deterministic report.

    No I/O is performed.  Raises ReportError with a stable ``code`` attribute
    for invalid input.
    """
    episodes = _validate_input(data)

    for episode in episodes:
        episode["strict_outcome"] = _strict_outcome(
            episode["status"], episode["outcomes"]
        )
    episodes.sort(key=_episode_sort_key)

    groups_by_key = {}
    for episode in episodes:
        key = (
            episode["evidence_kind"],
            episode["stage"],
            episode["scenario_id"],
            episode["arm"],
        )
        groups_by_key.setdefault(key, []).append(episode)
    groups = [
        _summarize_group(key, groups_by_key[key])
        for key in sorted(groups_by_key, key=lambda k: k)
    ]

    evidence_counts = _empty_counts(EVIDENCE_KINDS)
    stage_counts = _empty_counts(STAGES)
    for episode in episodes:
        evidence_counts[episode["evidence_kind"]] += 1
        stage_counts[episode["stage"]] += 1

    report_episodes = []
    for episode in episodes:
        report_episodes.append(
            {
                "episode_id": episode["episode_id"],
                "scenario_id": episode["scenario_id"],
                "family": episode["family"],
                "arm": episode["arm"],
                "trial": episode["trial"],
                "stage": episode["stage"],
                "evidence_kind": episode["evidence_kind"],
                "status": episode["status"],
                "outcomes": {
                    dimension: episode["outcomes"][dimension]
                    for dimension in OUTCOMES
                },
                "resources": {
                    name: episode["resources"][name] for name in RESOURCES
                },
                "trace": [
                    {
                        "seq": event["seq"],
                        "role": event["role"],
                        "kind": event["kind"],
                        "summary": event["summary"],
                    }
                    for event in episode["trace"]
                ],
                "strict_outcome": episode["strict_outcome"],
            }
        )

    return {
        "schema_version": SCHEMA_OUTPUT,
        "dataset_id": data["dataset_id"],
        "evidence_notice": EVIDENCE_NOTICE,
        "inventory": {
            "attempted": len(episodes),
            "evidence_counts": evidence_counts,
            "stage_counts": stage_counts,
        },
        "groups": groups,
        "episodes": report_episodes,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _md_cell(value) -> str:
    """Escape arbitrary supplied text for a Markdown/HTML table cell.

    Besides backslashes, HTML, table delimiters and newlines, Markdown inline
    punctuation is escaped so a supplied summary containing image, link or
    emphasis syntax is rendered as literal text data rather than active markup.
    URL-critical punctuation (dot, colon, at-sign and slash) is also
    backslash-escaped: linkifying renderers then see no matchable URL or email
    in the source, while ordinary Markdown rendering drops the escapes and
    displays the supplied characters literally.  This is display hardening for
    supplied table text, not a universal Markdown/HTML sanitizer, and no URL is
    opened by this renderer.
    """
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    for character in "`*_[]()!~":
        text = text.replace(character, "\\" + character)
    for character in ".:@/":
        text = text.replace(character, "\\" + character)
    text = text.replace("|", "\\|")
    text = text.replace("\r\n", "<br>")
    text = text.replace("\r", "<br>")
    text = text.replace("\n", "<br>")
    return text


def _md_code_span(value: str) -> str:
    """Return a safe code span; identifiers cannot contain backticks."""
    return "`" + value.replace("`", "") + "`"


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "N/A"
    return "%.1f%%" % (100.0 * numerator / denominator)


def _render_group(group: dict) -> list:
    lines = []
    lines.append(
        "### %s / %s — %s / %s"
        % (
            group["scenario_id"],
            group["arm"],
            group["evidence_kind"],
            group["stage"],
        )
    )
    lines.append("")
    lines.append(
        "| Attempted | Eligible | Excluded |"
    )
    lines.append("| --- | --- | --- |")
    lines.append(
        "| %d | %d | %d |"
        % (group["attempted"], group["eligible"], group["excluded"])
    )
    lines.append("")

    lines.append("#### All-attempt status dispositions")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("| --- | --- |")
    for status in STATUSES:
        lines.append("| %s | %d |" % (status, group["status_counts"][status]))
    lines.append("")

    lines.append("#### Eligible component judgments")
    lines.append("")
    lines.append("| Component | pass | fail | unknown |")
    lines.append("| --- | --- | --- | --- |")
    for dimension in OUTCOMES:
        counts = group["component_counts"][dimension]
        lines.append(
            "| %s | %d | %d | %d |"
            % (
                dimension,
                counts["pass"],
                counts["fail"],
                counts["unknown"],
            )
        )
    lines.append("")

    strict = group["strict_counts"]
    fraction = group["success_fraction"]
    lines.append("#### Strict outcome (eligible rows only)")
    lines.append("")
    lines.append("| pass | fail | unknown |")
    lines.append("| --- | --- | --- |")
    lines.append(
        "| %d | %d | %d |" % (strict["pass"], strict["fail"], strict["unknown"])
    )
    lines.append("")
    lines.append(
        "Confirmed strict successes: **%d / %d eligible (%s)**; unknown "
        "eligible rows: %d. Strict precedence: an eligible non-completed "
        "attempt is a strict failure; a completed attempt with any failing "
        "component is a strict failure; otherwise it is strict unknown only "
        "when at least one component is unknown; otherwise it is strict pass. "
        "This is a count of confirmed successes among eligible attempts, not a "
        "benchmark score or frontier-model performance."
        % (
            fraction["numerator"],
            fraction["denominator"],
            _pct(fraction["numerator"], fraction["denominator"]),
            strict["unknown"],
        )
    )
    lines.append("")

    lines.append("#### Resources (all attempts, including excluded rows)")
    lines.append("")
    lines.append(
        "| Resource | known_sum | known_count | unknown_count | complete_total |"
    )
    lines.append("| --- | --- | --- | --- | --- |")
    for name in RESOURCES:
        resource = group["resources"][name]
        complete_total = (
            "null (incomplete)"
            if resource["complete_total"] is None
            else str(resource["complete_total"])
        )
        lines.append(
            "| %s | %d | %d | %d | %s |"
            % (
                name,
                resource["known_sum"],
                resource["known_count"],
                resource["unknown_count"],
                complete_total,
            )
        )
    lines.append("")
    lines.append(
        "Unknown resource values stay unknown and are never imputed as zero. "
        "An incomplete total is shown as null. Estimated cost is an estimate "
        "and reserved allowance is an authorization; neither is billed cost. "
        "Per-success cost is not computed in v0.1 because totals may be "
        "incomplete or confirmed successes may be zero."
    )
    lines.append("")
    return lines


def _render_episode(episode: dict) -> list:
    lines = []
    lines.append("### %s" % _md_code_span(episode["episode_id"]))
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    fields = (
        ("episode_id", episode["episode_id"]),
        ("scenario_id", episode["scenario_id"]),
        ("family", episode["family"]),
        ("arm", episode["arm"]),
        ("trial", episode["trial"]),
        ("stage", episode["stage"]),
        ("evidence_kind", episode["evidence_kind"]),
        ("status", episode["status"]),
        ("strict_outcome", episode["strict_outcome"]),
    )
    for label, value in fields:
        lines.append("| %s | %s |" % (label, _md_cell(value)))
    lines.append("")

    lines.append("#### Supplied outcome judgments (not model self-reports)")
    lines.append("")
    lines.append("| Component | Judgment |")
    lines.append("| --- | --- |")
    for dimension in OUTCOMES:
        lines.append(
            "| %s | %s |"
            % (dimension, _md_cell(episode["outcomes"][dimension]))
        )
    lines.append("")

    lines.append("#### Resources")
    lines.append("")
    lines.append("| Resource | Value |")
    lines.append("| --- | --- |")
    for name in RESOURCES:
        value = episode["resources"][name]
        lines.append(
            "| %s | %s |"
            % (name, "null" if value is None else _md_cell(value))
        )
    lines.append("")

    lines.append(
        "#### Recorded trace (display data only — never executed)"
    )
    lines.append("")
    if episode["trace"]:
        lines.append("| seq | role | kind | summary |")
        lines.append("| --- | --- | --- | --- |")
        for event in episode["trace"]:
            lines.append(
                "| %d | %s | %s | %s |"
                % (
                    event["seq"],
                    _md_cell(event["role"]),
                    _md_cell(event["kind"]),
                    _md_cell(event["summary"]),
                )
            )
    else:
        lines.append("No recorded trace events were supplied.")
    lines.append("")
    lines.append(
        "Trace summaries are supplied sanitized text rendered as data. They "
        "are not executed, and their prose does not prove a tool operation, "
        "checkpoint outcome or grade."
    )
    lines.append("")
    return lines


def render_markdown(report: dict) -> str:
    """Render a deterministic Markdown view of a built report (no I/O)."""
    lines = []
    dataset_id = report["dataset_id"]
    inventory = report["inventory"]
    synthetic_count = inventory["evidence_counts"]["synthetic_development"]
    real_count = inventory["evidence_counts"]["real_provider"]

    lines.append("# FollowThrough offline report")
    lines.append("")
    lines.append("Schema: %s  " % _md_code_span(report["schema_version"]))
    lines.append("Dataset: %s" % _md_code_span(dataset_id))
    lines.append("")

    if synthetic_count:
        lines.append(
            "> **SYNTHETIC DEVELOPMENT DATA — NOT MODEL RESULTS.** This report "
            "contains %d `synthetic_development` row(s): authored fixtures, not "
            "live model behavior, provider results or frontier-model "
            "performance. Every percentage below is a supplied-judgment count, "
            "not an evaluation score."
            % synthetic_count
        )
        lines.append("")
    if real_count and not synthetic_count:
        lines.append(
            "> **Supplied provenance only.** This report contains %d "
            "`real_provider`-labeled row(s) whose provenance was supplied by "
            "the caller; the reporter did not verify it." % real_count
        )
        lines.append("")
    elif real_count:
        lines.append(
            "> **Supplied provenance only.** This report also contains %d "
            "`real_provider`-labeled row(s) whose provenance was supplied by "
            "the caller and was not verified by the reporter." % real_count
        )
        lines.append("")

    lines.append("## Supplied-judgment limitations")
    lines.append("")
    lines.append(
        "- Functional, workflow, evidence and completion values are supplied "
        "independent judgments. This reporter does not grade, re-run "
        "checkpoints or judge model output."
    )
    lines.append(
        "- Provider, infrastructure and invalid attempts remain visible in "
        "all-attempt counts and resources but are excluded from primary "
        "eligible denominators; their strict outcome is `not_scored`."
    )
    lines.append(
        "- Strict precedence: an eligible non-completed attempt is a strict "
        "failure; a completed attempt with any failing component is a strict "
        "failure; otherwise it is strict unknown only when at least one "
        "component is unknown; otherwise it is strict pass. Excluded attempts "
        "remain `not_scored`. A partial row can have completion accuracy "
        "`pass` and still be a strict failure."
    )
    lines.append(
        "- Synthetic and provider evidence, and pilot and formal stages, are "
        "never pooled. Groups are partitioned by evidence_kind, stage, "
        "scenario_id and arm; no cross-scenario or cross-arm headline is "
        "reported."
    )
    lines.append(
        "- Estimated cost is an estimate and reserved allowance is an "
        "authorization; neither is billed cost. Unknown resource values are "
        "preserved, not imputed."
    )
    lines.append(
        "- Recorded traces are supplied display data. They are not executed, "
        "and their prose does not establish truth. No checkpoint regrading, "
        "first-correct-candidate metric, regression inference or significance "
        "test is implemented."
    )
    lines.append("")

    lines.append("## Dataset inventory")
    lines.append("")
    lines.append("| Inventory measure | Count |")
    lines.append("| --- | --- |")
    lines.append("| attempted rows | %d |" % inventory["attempted"])
    for evidence_kind in EVIDENCE_KINDS:
        lines.append(
            "| evidence_kind=%s | %d |"
            % (evidence_kind, inventory["evidence_counts"][evidence_kind])
        )
    for stage in STAGES:
        lines.append(
            "| stage=%s | %d |"
            % (stage, inventory["stage_counts"][stage])
        )
    lines.append("")
    lines.append(
        "The inventory is a count of supplied rows, not a benchmark score. No "
        "pooled headline is derived from it."
    )
    lines.append("")

    lines.append("## Group summaries")
    lines.append("")
    lines.append(
        "Groups are sorted by (evidence_kind, stage, scenario_id, arm). "
        "Synthetic and provider evidence and pilot and formal stages are "
        "separate partitions."
    )
    lines.append("")
    if report["groups"]:
        for group in report["groups"]:
            lines.extend(_render_group(group))
    else:
        lines.append("No episode groups were supplied.")
        lines.append("")

    lines.append("## Episode detail")
    lines.append("")
    lines.append(
        "Episodes are sorted by (evidence_kind, stage, scenario_id, arm, "
        "trial, episode_id). Trace order is the supplied recorded order."
    )
    lines.append("")
    if report["episodes"]:
        for episode in report["episodes"]:
            lines.extend(_render_episode(episode))
    else:
        lines.append("No episodes were supplied.")
        lines.append("")

    lines.append("## Scope and future integration")
    lines.append("")
    lines.append(
        "- This is an offline report over a fixed input projection. It does not "
        "integrate with the runtime, call providers, replay execution or expose "
        "live results."
    )
    lines.append(
        "- A later Main-owned adapter must translate qualified runtime and "
        "grader receipts into this projection. The runtime event envelope is a "
        "separate interface and is not interpreted by this reporter."
    )
    lines.append(
        "- Checkpoint-level requirement outcomes, first-correct-candidate and "
        "regression metrics depend on a future checkpoint projection; this "
        "version does not invent them."
    )
    lines.append(
        "- Any semantic grading, experiment freeze or acceptance decision "
        "remains with Main and independent review, not this reporter."
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV rendering
# ---------------------------------------------------------------------------


def _csv_text(value) -> str:
    """Escape a text cell and neutralize spreadsheet formula leaders."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_LEADERS):
        return _FORMULA_PREFIX + text
    return text


def _csv_number(value) -> str:
    return "" if value is None else str(value)


def render_csv(report: dict) -> str:
    """Render the deterministic fixed-column episode CSV (no I/O)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        [
            "episode_id",
            "scenario_id",
            "family",
            "arm",
            "trial",
            "stage",
            "evidence_kind",
            "status",
            "functional",
            "workflow",
            "evidence",
            "completion",
            "strict_outcome",
            "model_responses",
            "tool_actions",
            "input_tokens",
            "output_tokens",
            "elapsed_ms",
            "estimated_cost_micro_usd",
            "reserved_micro_usd",
        ]
    )
    for episode in report["episodes"]:
        writer.writerow(
            [
                _csv_text(episode["episode_id"]),
                _csv_text(episode["scenario_id"]),
                _csv_text(episode["family"]),
                _csv_text(episode["arm"]),
                _csv_number(episode["trial"]),
                _csv_text(episode["stage"]),
                _csv_text(episode["evidence_kind"]),
                _csv_text(episode["status"]),
                _csv_text(episode["outcomes"]["functional"]),
                _csv_text(episode["outcomes"]["workflow"]),
                _csv_text(episode["outcomes"]["evidence"]),
                _csv_text(episode["outcomes"]["completion"]),
                _csv_text(episode["strict_outcome"]),
                _csv_number(episode["resources"]["model_responses"]),
                _csv_number(episode["resources"]["tool_actions"]),
                _csv_number(episode["resources"]["input_tokens"]),
                _csv_number(episode["resources"]["output_tokens"]),
                _csv_number(episode["resources"]["elapsed_ms"]),
                _csv_number(episode["resources"]["estimated_cost_micro_usd"]),
                _csv_number(episode["resources"]["reserved_micro_usd"]),
            ]
        )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Input loading and staged publication
# ---------------------------------------------------------------------------


class _DuplicateKey(Exception):
    pass


class _BadConstant(Exception):
    pass


def _object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey()
        result[key] = value
    return result


def _reject_constant(_token):
    raise _BadConstant()


def _load_input(path: str) -> dict:
    input_abs = os.path.abspath(path)
    # Guard every existing ancestor/leaf before lexists/isfile/open, which would
    # otherwise resolve a descendant of an unchecked reparse point.
    _assert_no_reparse(input_abs, "input")
    if not os.path.lexists(input_abs):
        raise ReportError("io_error", "input file not found")
    if not os.path.isfile(input_abs):
        raise ReportError("io_error", "input path is not a regular file")
    try:
        with open(input_abs, "rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise ReportError("io_error", "input file could not be read") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise ReportError(
            "input_too_large",
            "input exceeds %d bytes" % MAX_INPUT_BYTES,
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReportError("invalid_input", "input is not valid UTF-8") from exc
    try:
        data = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey:
        raise ReportError("duplicate_key", "duplicate JSON object key") from None
    except (_BadConstant, json.JSONDecodeError, RecursionError):
        raise ReportError("invalid_input", "input is not valid JSON") from None
    except (ValueError, OverflowError):
        # Python's JSON decoder raises a bare ValueError (not JSONDecodeError)
        # when an integer literal exceeds the interpreter digit limit.  Report a
        # fixed, bounded error that does not echo the offending literal.
        raise ReportError(
            "invalid_input",
            "input contains an unsupported numeric value",
        ) from None
    return data


def _prepare_output(path: str, input_abs: str) -> str:
    final = os.path.abspath(path)
    # Check existing components top-down before output_exists/isdir predicates
    # so a reparse ancestor is never resolved through.
    _assert_no_reparse(final, "output")
    if os.path.lexists(final) or final == input_abs:
        raise ReportError("output_exists", "output directory already exists")
    parent = os.path.dirname(final)
    if not os.path.isdir(parent):
        raise ReportError(
            "io_error", "output parent directory does not exist or is not a directory"
        )
    return final


def _stage_path(final: str) -> str:
    parent, base = os.path.split(final)
    return os.path.join(parent, "." + base + ".p4-stage")


def _write_text_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


_RENAME_ATTEMPTS = 5
_RENAME_RETRY_SECONDS = 0.1
_TRANSIENT_WINERRORS = (5, 32)  # access denied / sharing violation


def _transient_rename_error(exc: OSError) -> bool:
    if os.name != "nt":
        return False
    return getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS


def _publish(stage: str, final: str) -> None:
    """Rename the completed stage into place without overwriting.

    On Windows, an anti-virus/indexer can briefly hold a freshly written file,
    making the directory rename fail with a sharing/access denial.  A small
    bounded retry for those two transient Windows errors keeps publication
    usable; it is not a general atomicity or race-proof claim.
    """
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.rename(stage, final)
            return
        except OSError as exc:
            if (
                not _transient_rename_error(exc)
                or attempt == _RENAME_ATTEMPTS - 1
            ):
                raise
            time.sleep(_RENAME_RETRY_SECONDS)


def _write_stage(stage: str, report: dict) -> None:
    report_json = json.dumps(
        report, ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"
    _write_text_file(os.path.join(stage, "report.json"), report_json)
    _write_text_file(os.path.join(stage, "episodes.csv"), render_csv(report))
    _write_text_file(os.path.join(stage, "report.md"), render_markdown(report))


def generate(input_path: str, output_dir: str) -> dict:
    """Build the report, stage it, then publish the directory by rename."""
    data = _load_input(input_path)
    report = build_report(data)

    final = _prepare_output(output_dir, os.path.abspath(input_path))
    stage = _stage_path(final)
    if os.path.lexists(stage):
        raise ReportError(
            "io_error",
            "staging path already exists; refusing to reuse it",
        )
    try:
        os.mkdir(stage)
    except OSError as exc:
        raise ReportError(
            "io_error", "could not create staging directory"
        ) from exc

    try:
        _write_stage(stage, report)
        # Directory rename publishes the complete tree without copying partial
        # files.  Under the documented stable-host assumption no concurrent
        # writer races this name; this is not a universal atomicity claim.
        _publish(stage, final)
    except OSError as exc:
        raise ReportError(
            "io_error",
            "publication failed; staged files retained at %s" % stage,
        ) from exc
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -B -m reporting.report",
        description=(
            "Render deterministic report.json, episodes.csv and report.md from "
            "an explicit report-input-0.1 JSON file into a new directory."
        ),
    )
    parser.add_argument("--input", required=True, help="explicit input JSON file")
    parser.add_argument(
        "--output-dir", required=True, help="new output directory to create"
    )
    args = parser.parse_args(argv)

    try:
        generate(args.input, args.output_dir)
    except ReportError as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ")
        if len(message) > 512:
            message = message[:509] + "..."
        print("error: %s: %s" % (exc.code, message), file=sys.stderr)
        return 1
    print("wrote report.json, episodes.csv, report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
