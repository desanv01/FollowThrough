"""Read-only portable HTML report viewer.

Consumes an accepted ``report-output-0.1`` object plus optional trajectory and
sanitized event/binding receipts.  Produces a single self-contained HTML file
with no scripts, external assets, network calls or execution controls.  All
supplied strings are HTML-escaped and control characters removed; values remain
labelled synthetic/provider and development/pilot/formal.

Proposed P4.6.1-4; pending integration/review.  Importing performs no I/O.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import tempfile

SCHEMA = "report-viewer-data-0.1"
MAX_INPUT_BYTES = 16 * 1024 * 1024
ELIGIBLE = ("completed", "partial", "limit_reached")


class ViewerError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _text(value, limit=4096):
    text = "" if value is None else str(value)
    cleaned = []
    for character in text:
        code = ord(character)
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            cleaned.append(" ")
        else:
            cleaned.append(character)
    return "".join(cleaned)[:limit]


def _esc(value, limit=4096):
    return html.escape(_text(value, limit), quote=True)


def _cost(value):
    if value is None:
        return "unknown"
    if type(value) is not int or value < 0:
        return "unknown"
    dollars, micros = divmod(value, 1_000_000)
    return "$%d.%06d" % (dollars, micros)


def _value(value):
    return "unknown" if value is None else _text(value, 256)


def _strict_label(outcome):
    return {
        "pass": "PASS",
        "fail": "FAIL",
        "unknown": "UNKNOWN",
        "not_scored": "EXCLUDED",
    }.get(outcome, "UNKNOWN")


def _load_json(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_INPUT_BYTES + 1)
    except OSError:
        raise ViewerError("input_unreadable") from None
    if len(data) > MAX_INPUT_BYTES:
        raise ViewerError("input_too_large")
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ViewerError("invalid_json") from None


def _validate_report(report):
    if not isinstance(report, dict):
        raise ViewerError("invalid_report")
    if report.get("schema_version") != "report-output-0.1":
        raise ViewerError("unsupported_report")
    if not isinstance(report.get("episodes"), list) or not isinstance(
        report.get("groups"), list
    ):
        raise ViewerError("invalid_report")
    return report


def build_viewer_data(report, trajectory=None, events=None, binding=None):
    """Build a sanitized, display-only projection; no I/O."""
    report = _validate_report(report)
    inventory = report.get("inventory", {})
    synthetic = inventory.get("evidence_counts", {}).get(
        "synthetic_development", 0
    )
    provider = inventory.get("evidence_counts", {}).get("real_provider", 0)
    episodes = []
    for episode in report["episodes"]:
        resources = episode.get("resources", {})
        label = (
            "SYNTHETIC"
            if episode.get("evidence_kind") == "synthetic_development"
            else "REAL_PROVIDER (supplied provenance, not verified)"
        )
        episodes.append(
            {
                "episode_id": episode.get("episode_id"),
                "scenario_id": episode.get("scenario_id"),
                "family": episode.get("family"),
                "arm": episode.get("arm"),
                "trial": episode.get("trial"),
                "stage": episode.get("stage"),
                "evidence_kind": episode.get("evidence_kind"),
                "label": label,
                "status": episode.get("status"),
                "strict_outcome": episode.get("strict_outcome"),
                "strict_label": _strict_label(episode.get("strict_outcome")),
                "eligible": episode.get("status") in ELIGIBLE,
                "outcomes": dict(episode.get("outcomes", {})),
                "resources": [
                    ("Model responses", _value(resources.get("model_responses"))),
                    ("Tool actions", _value(resources.get("tool_actions"))),
                    ("Input tokens", _value(resources.get("input_tokens"))),
                    ("Output tokens", _value(resources.get("output_tokens"))),
                    ("Elapsed", _value(resources.get("elapsed_ms")) + " ms"
                     if resources.get("elapsed_ms") is not None else "unknown"),
                    ("Estimated cost (estimate)",
                     _cost(resources.get("estimated_cost_micro_usd"))),
                    ("Reserved (authorisation)",
                     _cost(resources.get("reserved_micro_usd"))),
                ],
                "trace": list(episode.get("trace", [])),
            }
        )
    comparisons = {}
    for group in report["groups"]:
        scenario = group.get("scenario_id")
        comparisons.setdefault(scenario, []).append(
            {
                "arm": group.get("arm"),
                "evidence_kind": group.get("evidence_kind"),
                "stage": group.get("stage"),
                "attempted": group.get("attempted"),
                "eligible": group.get("eligible"),
                "excluded": group.get("excluded"),
                "strict_counts": dict(group.get("strict_counts", {})),
                "success_fraction": dict(group.get("success_fraction", {})),
                "component_counts": group.get("component_counts", {}),
            }
        )
    timeline = []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            timeline.append(
                {
                    "type": event.get("type"),
                    "role": event.get("role"),
                    "status": event.get("status"),
                    "action": event.get("action"),
                    "elapsed_ms": event.get("elapsed_ms"),
                }
            )
    return {
        "schema_version": SCHEMA,
        "dataset_id": report.get("dataset_id"),
        "evidence_notice": report.get("evidence_notice"),
        "inventory": {
            "attempted": inventory.get("attempted"),
            "evidence_counts": dict(inventory.get("evidence_counts", {})),
            "stage_counts": dict(inventory.get("stage_counts", {})),
        },
        "has_synthetic": bool(synthetic),
        "has_provider": bool(provider),
        "episodes": episodes,
        "comparisons": comparisons,
        "timeline": timeline,
        "trajectory": trajectory if isinstance(trajectory, dict) else None,
        "binding": binding if isinstance(binding, dict) else None,
    }


_CSS = """
:root { color-scheme: dark; }
body { margin:0; background:#0b1114; color:#e8f4f6;
  font:15px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; }
main { max-width:1100px; margin:0 auto; padding:1.5rem; }
h1,h2,h3 { color:#4ff2ff; font-weight:600; letter-spacing:.02em; }
h1 { font-size:1.5rem; border-bottom:2px solid #1de0ef; padding-bottom:.4rem; }
h2 { margin-top:2rem; font-size:1.2rem; }
.warning { border:1px solid #ffd34d; background:#2b2410; color:#ffe9a3;
  padding:.6rem .8rem; margin:1rem 0; font-weight:700; }
.prov { color:#ffd34d; }
nav, .note { color:#9fb3b8; }
table { width:100%; border-collapse:collapse; margin:.6rem 0 1.2rem; }
caption { text-align:left; color:#9fb3b8; padding:.2rem 0; }
th,td { border:1px solid #24666c; padding:.35rem .5rem; text-align:left;
  vertical-align:top; }
th { background:#063a40; color:#b8fbff; }
tr:focus-within { outline:2px solid #4ff2ff; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:.7rem; margin:.7rem 0; }
.card { border:1px solid #24666c; background:#0f1c20; padding:.7rem; }
.card h3 { margin:.1rem 0 .4rem; font-size:1rem; }
.badge { display:inline-block; border:1px solid currentColor; border-radius:3px;
  padding:0 .35rem; margin-right:.3rem; font-size:.85rem; }
.pass { color:#7dffa8; } .fail { color:#ff8b8b; }
.unknown { color:#ffe28a; } .excluded { color:#9fb3b8; }
details { border:1px solid #24666c; background:#0f1c20; margin:.5rem 0; padding:.5rem; }
summary { cursor:pointer; color:#b8fbff; }
dt { color:#9fb3b8; } dd { margin:0 0 .35rem 0; }
.trace { max-height:16rem; overflow:auto; border:1px solid #1b4b50;
  padding:.5rem; background:#081114; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:0; }
footer { margin:2rem 0 1rem; border-top:1px solid #24666c; padding-top:.8rem;
  color:#9fb3b8; }
"""


def _table(headers, rows, caption=None):
    parts = ["<table>"]
    if caption:
        parts.append("<caption>%s</caption>" % _esc(caption))
    parts.append("<thead><tr>" + "".join(
        "<th scope=col>%s</th>" % _esc(header) for header in headers
    ) + "</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>" + "".join(
            "<td>%s</td>" % value for value in row
        ) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def render_html(view):
    """Render deterministic self-contained HTML; no scripts or external URLs."""
    evidence = view["inventory"]["evidence_counts"]
    stages = view["inventory"]["stage_counts"]
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>FollowThrough read-only report</title><style>%s</style></head>"
        % _CSS,
        "<body><main>",
        "<h1>FollowThrough — read-only report</h1>",
    ]
    if view["has_synthetic"]:
        parts.append(
            "<div class=warning role=alert>SYNTHETIC DEVELOPMENT DATA — "
            "NOT MODEL RESULTS. Authored synthetic previews only; supplied "
            "judgments are not benchmark performance.</div>"
        )
    if view["has_provider"]:
        parts.append(
            "<p class=prov><strong>REAL_PROVIDER-labeled records</strong>: "
            "provenance is supplied by the producer and not verified by this "
            "viewer. No model ranking or pooled headline is derived.</p>"
        )
    parts.append("<p><strong>Dataset:</strong> %s</p>" % _esc(view["dataset_id"]))
    parts.append(
        "<p><strong>Evidence notice:</strong> %s</p>"
        % _esc(view["evidence_notice"])
    )
    parts.append(
        "<p><strong>Inventory:</strong> attempted=%s; "
        "synthetic_development=%s; real_provider=%s; development=%s; "
        "pilot=%s; formal=%s. Unknown is not zero; no pooled score.</p>"
        % (
            _value(view["inventory"].get("attempted")),
            _value(evidence.get("synthetic_development")),
            _value(evidence.get("real_provider")),
            _value(stages.get("development")),
            _value(stages.get("pilot")),
            _value(stages.get("formal")),
        )
    )

    if view.get("binding"):
        binding = view["binding"]
        parts.append("<h2>Source / run binding</h2><dl>")
        for key in ("episode_id", "run_id", "candidate_sha256",
                    "requirements_version", "binding_match"):
            if key in binding:
                parts.append("<dt>%s</dt><dd>%s</dd>" % (_esc(key), _esc(binding[key])))
        parts.append("</dl>")

    parts.append("<h2>Within-scenario arm comparison</h2>")
    parts.append(
        "<p class=note>Counts are per scenario/arm; denominators and unknown "
        "counts are shown. No cross-scenario pooled headline.</p>"
    )
    for scenario in sorted(view["comparisons"]):
        rows = []
        for group in view["comparisons"][scenario]:
            strict = group["strict_counts"]
            frac = group["success_fraction"]
            numerator = frac.get("numerator") or 0
            denominator = frac.get("denominator") or 0
            percentage = (
                "N/A" if denominator == 0 else
                "%.1f%%" % (100.0 * numerator / denominator)
            )
            rows.append(
                [
                    _esc(group.get("arm")),
                    _esc(group.get("evidence_kind")),
                    _esc(group.get("stage")),
                    _value(group.get("attempted")),
                    _value(group.get("eligible")),
                    _value(group.get("excluded")),
                    "%s/%s/%s" % (_value(strict.get("pass")),
                                  _value(strict.get("fail")),
                                  _value(strict.get("unknown"))),
                    "%s/%s (%s)" % (_value(numerator), _value(denominator),
                                    percentage),
                ]
            )
        parts.append("<h3>%s</h3>" % _esc(scenario))
        parts.append(_table(
            ["Arm", "Evidence", "Stage", "Attempted", "Eligible", "Excluded",
             "Strict P/F/U", "Confirmed successes / eligible"],
            rows,
            caption="Scenario %s — arm comparison" % scenario,
        ))

    parts.append("<h2>Episodes</h2>")
    for episode in view["episodes"]:
        parts.append("<section id='ep-%s' class=card>" % _esc(episode["episode_id"], 128))
        parts.append(
            "<h3>%s</h3>" % _esc(episode["episode_id"])
        )
        parts.append(
            "<p><span class='badge %s'>%s</span> %s · %s · %s · trial %s · "
            "%s / %s</p>"
            % (
                "pass" if episode["strict_outcome"] == "pass" else
                "fail" if episode["strict_outcome"] == "fail" else
                "unknown" if episode["strict_outcome"] == "unknown" else
                "excluded",
                _esc(episode["strict_label"]),
                _esc(episode["scenario_id"]),
                _esc(episode["arm"]),
                _esc(episode["status"]),
                _esc(episode["trial"]),
                _esc(episode["evidence_kind"]),
                _esc(episode["stage"]),
            )
        )
        parts.append("<p><strong>%s</strong></p>" % _esc(episode["label"]))
        outcomes = episode["outcomes"]
        parts.append(
            "<p>Functional <strong>%s</strong> · Workflow <strong>%s</strong> "
            "· Evidence <strong>%s</strong> · Completion <strong>%s</strong>"
            % tuple(_esc(_strict_label(outcomes.get(key))) for key in
                    ("functional", "workflow", "evidence", "completion"))
        )
        parts.append("<dl>")
        for label, value in episode["resources"]:
            parts.append("<dt>%s</dt><dd>%s</dd>" % (_esc(label), _esc(value)))
        parts.append("</dl>")
        if episode["trace"]:
            parts.append(
                "<details><summary>Recorded display trace (data only; not "
                "execution evidence)</summary><div class=trace><pre>"
            )
            for event in episode["trace"]:
                parts.append(
                    "%s\n" % _esc(
                        "#%s %s/%s: %s"
                        % (
                            _value(event.get("seq")),
                            event.get("role"),
                            event.get("kind"),
                            event.get("summary"),
                        )
                    )
                )
            parts.append("</pre></div></details>")
        parts.append("</section>")

    timeline = view.get("timeline") or []
    if timeline:
        parts.append("<h2>Event timeline</h2>")
        rows = []
        for event in timeline:
            rows.append(
                [
                    _value(event.get("elapsed_ms")),
                    _esc(event.get("role")),
                    _esc(event.get("type")),
                    _esc(event.get("status") or ""),
                    _esc(event.get("action") or ""),
                ]
            )
        parts.append(_table(
            ["Elapsed ms", "Role", "Event type", "Status", "Action"],
            rows,
            caption="Supplied safe event fields; no raw content",
        ))

    trajectory = view.get("trajectory")
    if trajectory:
        parts.append("<h2>Checkpoint trajectory</h2>")
        rows = []
        for segment in trajectory.get("segments", []):
            first = segment.get("first_observed_correct")
            first_text = "none" if not first else "%s (seq %s)" % (
                _esc(first.get("checkpoint_id")), _esc(first.get("seq")))
            rows.append(
                [
                    _esc(segment.get("requirements_version")),
                    _value(segment.get("start_seq")),
                    _value(segment.get("end_seq")),
                    _value(segment.get("snapshot_count")),
                    _value(segment.get("known_pass")),
                    _value(segment.get("known_fail")),
                    _value(segment.get("unknown")),
                    first_text,
                    _value(len(segment.get("observed_regressions", []))),
                ]
            )
        parts.append(_table(
            ["Version", "Start seq", "End seq", "Snapshots", "Pass", "Fail",
             "Unknown", "First observed pass", "Regressions"],
            rows,
            caption="Observed supplied checkpoint snapshots; not latent truth",
        ))

    parts.append(
        "<footer>Read-only viewer. No execution controls, credentials, raw "
        "prompts, network dependencies or pooled benchmark headline. Unknown "
        "values remain unknown; estimates and reservations are not billed "
        "cost. Design/synthetic previews must stay labelled.</footer>"
    )
    parts.append("</main></body></html>")
    return "".join(parts)


def write_html(output_path, html_text):
    directory = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(directory):
        raise ViewerError("output_parent_missing")
    fd, temporary = tempfile.mkstemp(prefix=".viewer-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(html_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise ViewerError("output_write_failed") from None


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -B -m reporting.viewer",
        description="Generate a self-contained read-only report viewer.",
    )
    parser.add_argument("--report", required=True)
    parser.add_argument("--trajectory")
    parser.add_argument("--events")
    parser.add_argument("--binding")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        report = _validate_report(_load_json(args.report))
        trajectory = _load_json(args.trajectory) if args.trajectory else None
        events = _load_json(args.events) if args.events else None
        binding = _load_json(args.binding) if args.binding else None
        view = build_viewer_data(report, trajectory, events, binding)
        write_html(args.output, render_html(view))
        print("wrote " + args.output)
        return 0
    except ViewerError as error:
        print("error: viewer: " + error.code, file=sys.stderr)
        return 1


__all__ = [
    "ViewerError", "build_viewer_data", "main", "render_html", "write_html",
]


if __name__ == "__main__":
    raise SystemExit(main())
