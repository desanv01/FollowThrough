"""Read-only T3 run-ledger viewer for saved report-output-0.1 objects.

``python -B -m reporting.run_ledger --input PATH --format text|json|tui``

Text and JSON modes are non-interactive.  TUI is opt-in, requires a terminal,
and presents the selected attempt evidence with navigation/filtering.  The
viewer reads one explicit report-input-0.1 file through the accepted reporting
loader/builder and performs no execution, discovery, provider call, grading,
retry or artifact write.  Importing this module performs no I/O.
"""

import argparse
import json
import os
import select
import shutil
import sys
import textwrap

from .report import ReportError, _load_input, build_report

FORMATS = ("text", "json", "tui")
ELIGIBLE_STATUSES = ("completed", "partial", "limit_reached")
GRADE_LABELS = {
    "pass": "PASS",
    "fail": "FAIL",
    "unknown": "UNKNOWN",
    "not_scored": "EXCLUDED",
}
# Kept for the optional post-phase muted palette.  TUI defaults to the selected
# dark/cyan T3 palette; text mode is uncoloured.
PALETTES = {
    "cyan": {
        "reset": "\x1b[0m",
        "header": "\x1b[48;2;0;222;225m\x1b[38;2;0;0;0m",
        "accent": "\x1b[38;2;0;231;235m",
        "selected": "\x1b[48;2;0;101;111m\x1b[38;2;245;255;255m",
        "footer": "\x1b[38;2;0;231;235m",
        "warn": "\x1b[93m",
        "muted": "\x1b[90m",
    },
    "muted": {
        "footer": "\x1b[36m",
        "reset": "\x1b[0m",
        "header": "\x1b[30;46m",
        "accent": "\x1b[36m",
        "selected": "\x1b[30;46m",
        "warn": "\x1b[33m",
        "muted": "\x1b[90m",
    },
}

FILTER_FIELDS = ("episode_id", "scenario_id", "arm", "status")
MAX_FILTER = 64


class LedgerError(Exception):
    """Bounded viewer error with a stable code; never echoes supplied content."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Terminal-safe text and formatting
# ---------------------------------------------------------------------------


def _sanitize(value):
    """Render supplied text as terminal data: no controls/escapes/newlines."""
    text = "unknown" if value is None else str(value)
    out = []
    for character in text:
        code = ord(character)
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            out.append(" ")
        else:
            out.append(character)
    return "".join(out)


def _fit(text, width):
    text = _sanitize(text)
    width = max(1, width)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _wrap(text, width):
    width = max(8, width)
    wrapped = textwrap.wrap(
        _sanitize(text),
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped or [""]


def _emit(lines, text, width):
    lines.extend(_wrap(text, width))


def _pad(text, width):
    return _fit(text, width).ljust(width)


def _style(text, style, color):
    return (style + text + PALETTES["cyan"]["reset"]) if color else text


def _fmt_value(value):
    return "unknown" if value is None else str(value)


def _fmt_elapsed(value):
    if value is None:
        return "unknown"
    seconds = int(value) // 1000
    return "%02d:%02d" % (seconds // 60, seconds % 60)


def _fmt_cost(value):
    if value is None:
        return "unknown"
    dollars, micros = divmod(int(value), 1_000_000)
    return "$%d.%06d" % (dollars, micros)


def _strict_counts(report):
    counts = {"pass": 0, "fail": 0, "unknown": 0, "not_scored": 0}
    for episode in report["episodes"]:
        counts[episode["strict_outcome"]] += 1
    return counts


def _resource_total(report, field):
    known_sum = 0
    known_count = 0
    unknown_count = 0
    for episode in report["episodes"]:
        value = episode["resources"][field]
        if value is None:
            unknown_count += 1
        else:
            known_sum += value
            known_count += 1
    return known_sum, known_count, unknown_count


# ---------------------------------------------------------------------------
# Rows and filtering
# ---------------------------------------------------------------------------


def _rows(report):
    rows = []
    for episode in report["episodes"]:
        resources = episode["resources"]
        rows.append(
            {
                "episode": episode,
                "episode_id": episode["episode_id"],
                "scenario_id": episode["scenario_id"],
                "arm": episode["arm"],
                "status": episode["status"],
                "evidence_kind": episode["evidence_kind"],
                "stage": episode["stage"],
                "arm_label": (
                    "structured"
                    if episode["arm"] == "structured_progress"
                    else "ordinary"
                ),
                "prov_stage": (
                    ("synthetic" if episode["evidence_kind"] == "synthetic_development"
                     else "provider")
                    + "/"
                    + episode["stage"]
                ),
                "grade": GRADE_LABELS[episode["strict_outcome"]],
                "calls": _fmt_value(resources["model_responses"]),
                "elapsed": _fmt_elapsed(resources["elapsed_ms"]),
            }
        )
    return rows


def _matches(row, pattern):
    if not pattern:
        return True
    needle = pattern.casefold()
    return any(
        needle in _sanitize(row[field]).casefold() for field in FILTER_FIELDS
    )


def _visible_rows(rows, pattern):
    return [row for row in rows if _matches(row, pattern)]


def _row_by_id(rows, episode_id):
    for row in rows:
        if row["episode_id"] == episode_id:
            return row
    return None


def _first_id(rows):
    return rows[0]["episode_id"] if rows else None


def _ensure_selected(state, rows):
    visible = _visible_rows(rows, state["filter"])
    if not visible:
        state["selected_id"] = None
        return
    if _row_by_id(visible, state["selected_id"]) is None:
        state["selected_id"] = visible[0]["episode_id"]


# ---------------------------------------------------------------------------
# Header, table, footer and details
# ---------------------------------------------------------------------------


def _header_lines(report, width, color, palette_name):
    palette = PALETTES[palette_name]
    lines = []
    title = "FOLLOWTHROUGH / RUN LEDGER / READ ONLY"
    lines.append(
        _style(_pad(title, width), palette["header"], color)
        if color
        else _fit(title, width)
    )
    inventory = report["inventory"]
    evidence = ", ".join(
        "%s=%d" % (key, count)
        for key, count in inventory["evidence_counts"].items()
        if count
    ) or "none"
    stages = ", ".join(
        "%s=%d" % (key, count)
        for key, count in inventory["stage_counts"].items()
        if count
    ) or "none"
    _emit(lines, "Dataset: %s    Evidence: %s" % (report["dataset_id"], evidence), width)
    _emit(
        lines,
        "Stage: %s    Attempts: %d"
        % (stages, inventory["attempted"]),
        width,
    )
    if inventory["evidence_counts"].get("synthetic_development", 0):
        warning = "SYNTHETIC DEVELOPMENT DATA - NOT MODEL RESULTS"
        lines.append(
            _style(_fit(warning, width), palette["warn"], color)
            if color
            else _fit(warning, width)
        )
    return lines


def _table_widths(rows, width):
    headers = (
        "ID", "SCENARIO", "ARM", "STATE", "GRADE", "PROV/STAGE", "CALLS", "ELAPSED",
    )
    keys = (
        "episode_id", "scenario_id", "arm_label", "status", "grade",
        "prov_stage", "calls", "elapsed",
    )
    max_widths = (30, 8, 12, 22, 9, 18, 6, 9)
    min_widths = (8, 6, 8, 8, 8, 8, 5, 8)
    widths = []
    for index, key in enumerate(keys):
        natural = len(headers[index])
        for row in rows:
            natural = max(natural, len(_sanitize(row[key])))
        widths.append(min(max(natural, min_widths[index]), max_widths[index]))
    separators = 2 * (len(headers) - 1)
    while sum(widths) + separators > width:
        candidates = [
            index for index in range(len(widths)) if widths[index] > min_widths[index]
        ]
        if not candidates:
            return None, headers, keys
        index = max(candidates, key=lambda item: widths[item])
        widths[index] -= 1
    return widths, headers, keys


def _card_lines(rows, selected_id, width, max_rows):
    lines = []
    for index, row in enumerate(rows[:max_rows]):
        marker = "> " if row["episode_id"] == selected_id else "  "
        lines.append(
            _fit(
                marker + row["episode_id"] + "  " + row["scenario_id"]
                + "  " + row["arm"],
                width,
            )
        )
        lines.append(
            _fit(
                "  " + row["status"] + " / " + row["grade"]
                + "  prov=" + row["evidence_kind"]
                + " stage=" + row["stage"],
                width,
            )
        )
        lines.append(
            _fit(
                "  calls=" + row["calls"] + " elapsed=" + row["elapsed"],
                width,
            )
        )
        if len(lines) >= max_rows * 3:
            break
    return lines


def _table_lines(rows, selected_id, width, color, palette_name, max_rows=None):
    if not rows:
        return ["No attempts."]
    palette = PALETTES[palette_name]
    widths, headers, keys = _table_widths(rows, width)
    if widths is None or width < 108:
        # Narrow/unsafe table: readable cards.  No budget means all rows.
        if max_rows is None:
            visible = rows
            max_cards = len(rows)
        else:
            max_cards = max(1, max_rows // 3)
            visible = _window(rows, selected_id, max_cards)
        return _card_lines(visible, selected_id, width, max_cards)
    max_table_rows = None if max_rows is None else max(1, max_rows - 1)
    visible = _window(rows, selected_id, max_table_rows or len(rows))
    header = "  ".join(_pad(label, widths[i]) for i, label in enumerate(headers))
    lines = [_style(_pad(header, width), palette["header"], color)]
    for row in visible:
        line = "  ".join(
            _pad(row[keys[i]], widths[i]) for i in range(len(keys))
        )
        line = _fit(line, width)
        if row["episode_id"] == selected_id:
            line = _style(line, palette["selected"], color) if color else line
        lines.append(line)
    return lines


def _window(rows, selected_id, max_rows):
    """Bounded window that always contains the selected row when possible."""
    if not rows:
        return []
    max_rows = max(1, min(max_rows, len(rows)))
    index = 0
    for position, row in enumerate(rows):
        if row["episode_id"] == selected_id:
            index = position
            break
    start = max(0, min(index - max_rows // 2, len(rows) - max_rows))
    return rows[start : start + max_rows]


def _trace_excerpt(episode, limit):
    parts = []
    for event in episode["trace"]:
        parts.append(
            "#%s %s/%s: %s"
            % (
                _fmt_value(event["seq"]),
                _sanitize(event["role"]),
                _sanitize(event["kind"]),
                _sanitize(event["summary"]),
            )
        )
    text = "; ".join(parts) if parts else "none"
    if len(text) > limit:
        text = text[: max(0, limit - 3)] + "..."
    return text


def _detail_lines(row, width, evidence_open, color, palette_name):
    palette = PALETTES[palette_name]
    episode = row["episode"]
    resources = episode["resources"]
    lines = []
    title = "SELECTED ATTEMPT / %s" % _sanitize(episode["episode_id"])
    lines.append(
        _style(_fit(title, width), palette["accent"], color)
        if color
        else _fit(title, width)
    )
    if evidence_open:
        lines.append(
            _fit("EVIDENCE VIEW / recorded display trace (not execution)", width)
        )
        events = episode["trace"][:8]
        if not events:
            lines.append(_fit("No recorded trace events.", width))
        for event in events:
            lines.append(
                _fit(
                    "#%s %s/%s: %s"
                    % (
                        _fmt_value(event["seq"]),
                        _sanitize(event["role"]),
                        _sanitize(event["kind"]),
                        _sanitize(event["summary"]),
                    ),
                    width,
                )
            )
        lines.append(
            _fit("enter: back to details   esc/q: close viewer", width)
        )
        return lines

    eligibility = (
        "ELIGIBLE" if episode["status"] in ELIGIBLE_STATUSES else "EXCLUDED"
    )
    lines.append(
        _fit(
            "State: %s  Grade: %s"
            % (_sanitize(episode["status"]), row["grade"]),
            width,
        )
    )
    lines.append(
        _fit(
            "Scenario: %s  Arm: %s  Trial: %s"
            % (
                _sanitize(episode["scenario_id"]),
                _sanitize(episode["arm"]),
                _fmt_value(episode["trial"]),
            ),
            width,
        )
    )
    lines.append(
        _fit(
            "Stage: %s  Provenance: %s  Eligibility: %s"
            % (
                _sanitize(episode["stage"]),
                _sanitize(episode["evidence_kind"]),
                eligibility,
            ),
            width,
        )
    )
    lines.append(
        _fit(
            "Functional=%s  Workflow=%s"
            % (
                _sanitize(episode["outcomes"]["functional"]).upper(),
                _sanitize(episode["outcomes"]["workflow"]).upper(),
            ),
            width,
        )
    )
    lines.append(
        _fit(
            "Evidence=%s  Completion=%s"
            % (
                _sanitize(episode["outcomes"]["evidence"]).upper(),
                _sanitize(episode["outcomes"]["completion"]).upper(),
            ),
            width,
        )
    )
    lines.append(
        _fit(
            "Resources: calls=%s tool_actions=%s input_tokens=%s output_tokens=%s"
            % (
                _fmt_value(resources["model_responses"]),
                _fmt_value(resources["tool_actions"]),
                _fmt_value(resources["input_tokens"]),
                _fmt_value(resources["output_tokens"]),
            ),
            width,
        )
    )
    lines.append(
        _fit("Elapsed: %s" % _fmt_elapsed(resources["elapsed_ms"]), width)
    )
    lines.append(
        _fit(
            "Estimated cost (estimate, not billed): %s"
            % _fmt_cost(resources["estimated_cost_micro_usd"]),
            width,
        )
    )
    lines.append(
        _fit(
            "Reserved (authorisation): %s"
            % _fmt_cost(resources["reserved_micro_usd"]),
            width,
        )
    )
    lines.append(
        _fit(
            "Recorded trace excerpt (display only): "
            + _trace_excerpt(episode, 400),
            width,
        )
    )
    return lines


def _footer_lines(report, width):
    """Inventory only; outcome/resource summaries stay in accepted groups/details."""
    inventory = report["inventory"]
    evidence = ", ".join(
        "%s=%d" % (key, count)
        for key, count in inventory["evidence_counts"].items()
        if count
    ) or "none"
    stages = ", ".join(
        "%s=%d" % (key, count)
        for key, count in inventory["stage_counts"].items()
        if count
    ) or "none"
    return [
        _fit(
            "Attempts %d | evidence: %s | stages: %s"
            % (inventory["attempted"], evidence, stages),
            width,
        )
    ]


HINTS = "j/k move   enter evidence   / filter   esc back/clear   q close viewer"


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _filtered_state_rows(report, state):
    rows = _rows(report)
    visible = _visible_rows(rows, state["filter"])
    return rows, visible


def _render_screen(report, state, width, height, color, palette_name):
    """Actual framed T3 terminal surface; report/text/JSON semantics unchanged."""
    palette = PALETTES[palette_name]
    inner = width - 4
    base = "\x1b[48;2;0;0;0m\x1b[38;2;235;241;242m" if color else ""
    accent = palette["accent"] if color else ""

    def paint(text, style=""):
        return base + style + text + ("\x1b[0m" if color else "")

    def edge(left, right):
        return paint(left + "─" * (width - 2) + right, accent)

    def cell(text, style=""):
        return (paint("│", accent) + paint(" " + _pad(text, inner) + " ", style)
                + paint("│", accent))

    rows, visible = _filtered_state_rows(report, state)
    selected = _row_by_id(visible, state["selected_id"])
    headers = _header_lines(report, width, False, palette_name)
    lines = [paint(_pad(headers[0] + "    GRADE=strict", width), accent)]
    lines.extend(paint(_pad(line, width)) for line in headers[1:])
    if selected is None:
        details = ["No attempt selected."]
    else:
        details = _wrap("SELECTED ATTEMPT / " + selected["episode_id"], inner)
        legacy = _detail_lines(selected, inner, state["evidence"], False, palette_name)
        if state["evidence"]:
            details.extend(legacy[1:])
        else:
            resources = selected["episode"]["resources"]
            details.extend(legacy[1:6])
            details.extend(_wrap(
                "Resources: calls=%s tool_actions=%s input_tokens=%s output_tokens=%s"
                % tuple(_fmt_value(resources[key]) for key in (
                    "model_responses", "tool_actions", "input_tokens", "output_tokens"
                )), inner))
            details.extend(legacy[7:10])
    filter_line = None
    if state["filter_input"] is not None:
        filter_line = "Filter (enter apply, esc cancel): %s_" % state["filter_input"]
    elif state["filter"]:
        filter_line = "Filter: %s (%d matches)" % (state["filter"], len(visible))
    if filter_line:
        lines.append(paint(_pad(filter_line, width), accent))
    # Preserve selected details, then spend remaining height on table rows.
    table_budget = max(2, height - len(lines) - len(details) - 6)
    lines.append(edge("┌", "┐"))
    if not rows:
        table = [("No attempts in this report.", "")]
    elif not visible:
        table = [("No attempts match filter.", "")]
    elif width >= 108:
        widths, labels, keys = _table_widths(visible, inner)
        table = [("  ".join(_pad(label, widths[i]) for i, label in enumerate(labels)),
                  palette["header"] if color else "")]
        for row in _window(visible, state["selected_id"], max(1, table_budget - 1)):
            text = "  ".join(_pad(row[key], widths[i]) for i, key in enumerate(keys))
            table.append((text, palette["selected"] if color and row is selected else ""))
    else:
        table = [("ID / SCENARIO                         STATE / GRADE (strict)",
                  palette["header"] if color else "")]
        for row in _window(visible, state["selected_id"], max(1, table_budget - 1)):
            text = ("> " if row is selected else "  ") + row["episode_id"]
            text += "  " + row["scenario_id"] + "  " + row["status"] + " / " + row["grade"]
            table.append((text, palette["selected"] if color and row is selected else ""))
    lines.extend(cell(text, style) for text, style in table[:table_budget])
    lines.append(edge("└", "┘"))
    lines.append(edge("┌", "┐"))
    room = max(1, height - len(lines) - 3)
    lines.extend(cell(detail, accent if index == 0 else "")
                 for index, detail in enumerate(details[:room]))
    while len(lines) < height - 3:
        lines.append(cell(""))
    lines.append(edge("├", "┤"))
    lines.append(cell(HINTS, palette["footer"] if color else ""))
    lines.append(edge("└", "┘"))
    return "\n".join(lines[:height])


def render_screen(
    report, state, width=100, height=24, color=True, palette="cyan"
):
    """Deterministic TUI frame fitted to width/height; tests use color=False."""
    return _render_screen(
        report,
        state,
        max(20, width),
        max(8, height),
        color,
        palette,
    )


def render_text(report, width=100):
    """Plain non-interactive saved-report table."""
    width = max(20, width)
    rows = _rows(report)
    lines = _header_lines(report, width, False, "cyan")
    lines.append("")
    lines.extend(_table_lines(rows, None, width, False, "cyan"))
    lines.append("")
    lines.extend(_footer_lines(report, width))
    _emit(
        lines,
        "Use --format tui for interactive selected-attempt details (read-only).",
        width,
    )
    return "\n".join(lines)


def render_json(report):
    """Existing report-output-0.1 object as deterministic JSON text."""
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# TUI interaction
# ---------------------------------------------------------------------------


def _new_state(report):
    rows = _rows(report)
    return {
        "selected_id": _first_id(rows),
        "filter": "",
        "filter_input": None,
        "evidence": False,
    }


def _move(state, rows, delta):
    visible = _visible_rows(rows, state["filter"])
    if not visible:
        state["selected_id"] = None
        return
    current = _row_by_id(visible, state["selected_id"])
    index = 0 if current is None else visible.index(current)
    index = (index + delta) % len(visible)
    state["selected_id"] = visible[index]["episode_id"]


def _handle_key(report, state, key):
    """Apply one normalized key; return 'quit' or None."""
    rows = _rows(report)
    if state["filter_input"] is not None:
        if key == "enter":
            state["filter"] = state["filter_input"]
            state["filter_input"] = None
            _ensure_selected(state, rows)
        elif key == "esc":
            state["filter_input"] = None
        elif key == "backspace":
            state["filter_input"] = state["filter_input"][:-1]
        elif len(key) == 1 and ord(key) >= 32:
            if len(state["filter_input"]) < MAX_FILTER:
                state["filter_input"] += key
        return None

    if key == "q":
        return "quit"
    if key == "esc":
        if state["evidence"]:
            state["evidence"] = False
        elif state["filter"]:
            state["filter"] = ""
            _ensure_selected(state, rows)
        else:
            return "quit"
        return None
    if key in ("up", "k"):
        _move(state, rows, -1)
    elif key in ("down", "j"):
        _move(state, rows, 1)
    elif key == "enter":
        selected = _row_by_id(rows, state["selected_id"])
        if selected is not None and selected in _visible_rows(rows, state["filter"]):
            state["evidence"] = not state["evidence"]
    elif key == "/":
        state["filter_input"] = ""
    return None


class _NullTerminal:
    """Injected-terminal test seam; no platform mode changes."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _terminal_setup_error():
    return LedgerError(
        "tui_terminal_setup_failed",
        "interactive TUI terminal setup is unsupported; use --format text",
    )


def _enable_terminal_mode(in_stream):
    """Apply raw/cbreak or VT mode; raise on unsupported setup, with cleanup."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                raise _terminal_setup_error()
            if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                raise _terminal_setup_error()
            return ("win", mode.value)
        except LedgerError:
            raise
        except Exception:
            raise _terminal_setup_error() from None
    try:
        import termios
        import tty

        fd = in_stream.fileno()
        attrs = termios.tcgetattr(fd)
    except Exception:
        raise _terminal_setup_error() from None
    try:
        tty.setcbreak(fd)
    except BaseException as exc:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
        except Exception:
            pass
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise _terminal_setup_error() from None
    return ("posix", attrs)


def _restore_terminal_mode(in_stream, token):
    """Restore a token returned by _enable_terminal_mode; no-op if absent."""
    if token is None:
        return
    kind, value = token
    if kind == "win":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        kernel32.SetConsoleMode(handle, value)
    else:
        import termios

        termios.tcsetattr(in_stream.fileno(), termios.TCSADRAIN, value)


class _TerminalSession:
    """Terminal mode/cursor save/restore, including startup-failure rollback."""

    def __init__(
        self,
        in_stream,
        out_stream,
        *,
        enable_mode=None,
        restore_mode=None,
    ):
        self.in_stream = in_stream
        self.out_stream = out_stream
        self._enable_mode = enable_mode or _enable_terminal_mode
        self._restore_mode = restore_mode or _restore_terminal_mode
        self._mode = None

    def _rollback(self):
        if self._mode is not None:
            try:
                self._restore_mode(self.in_stream, self._mode)
            except Exception:
                pass
            finally:
                self._mode = None
        try:
            self.out_stream.write("\x1b[0m\x1b[?25h")
            self.out_stream.flush()
        except Exception:
            pass

    def __enter__(self):
        if not (
            getattr(self.in_stream, "isatty", lambda: False)()
            and getattr(self.out_stream, "isatty", lambda: False)()
        ):
            raise LedgerError(
                "tui_requires_terminal",
                "interactive TUI requires a terminal; use --format text",
            )
        try:
            self._mode = self._enable_mode(self.in_stream)
        except LedgerError:
            raise
        except Exception:
            raise _terminal_setup_error() from None
        try:
            self.out_stream.write("\x1b[?25l")
            self.out_stream.flush()
        except BaseException as exc:
            self._rollback()
            if isinstance(exc, KeyboardInterrupt):
                raise
            raise _terminal_setup_error() from None
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._mode is not None:
                try:
                    self._restore_mode(self.in_stream, self._mode)
                except Exception:
                    pass
                finally:
                    self._mode = None
        finally:
            try:
                self.out_stream.write("\x1b[0m\x1b[?25h")
                self.out_stream.flush()
            except Exception:
                pass
        return False


def _normalize_key(raw):
    if raw in ("\r", "\n"):
        return "enter"
    if raw == "\x1b":
        return "esc"
    if raw in ("\b", "\x7f"):
        return "backspace"
    return raw


def _read_posix_key(fd, read_byte=None, ready=None):
    """Decode one key using one raw-buffer layer for reads and readiness.

    Defaults read/wait on the same descriptor (``os.read`` + ``select``), so a
    buffered text wrapper cannot prefetch an escape sequence while readiness is
    checked on the underlying descriptor.
    """
    if read_byte is None:
        def read_byte():
            return os.read(fd, 1)
    if ready is None:
        def ready():
            return bool(select.select([fd], [], [], 0.01)[0])

    raw = read_byte()
    if not raw:
        return ""
    if isinstance(raw, bytes):
        try:
            char = raw.decode("utf-8", "replace")
        except Exception:
            char = ""
    else:
        char = raw
    if char == "\x1b":
        sequence = char
        while len(sequence) < 3 and ready():
            nxt = read_byte()
            if not nxt:
                break
            sequence += nxt.decode("utf-8", "replace") if isinstance(nxt, bytes) else nxt
        if sequence in ("\x1b[A", "\x1bOA"):
            return "up"
        if sequence in ("\x1b[B", "\x1bOB"):
            return "down"
        return "esc"
    return _normalize_key(char)


def _read_key(stream):
    if os.name == "nt":
        import msvcrt

        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {"H": "up", "P": "down"}.get(code, "")
        return _normalize_key(char)
    return _read_posix_key(stream.fileno())


def run_tui(
    report,
    *,
    in_stream=None,
    out_stream=None,
    key_source=None,
    terminal=None,
    width=None,
    height=None,
    color=True,
    palette="cyan",
):
    """Run the opt-in interactive viewer; restores terminal modes on all exits."""
    in_stream = in_stream or sys.stdin
    out_stream = out_stream or sys.stdout
    if width is None or height is None:
        size = shutil.get_terminal_size((100, 30))
        width = size.columns if width is None else width
        height = size.lines if height is None else height
    width = max(20, width)
    height = max(8, height)
    state = _new_state(report)
    if key_source is not None:
        keys = iter(key_source)
        context = terminal or _NullTerminal()
    else:
        keys = None
        context = terminal or _TerminalSession(in_stream, out_stream)
    with context:
        out_stream.write("\x1b[2J\x1b[H")
        out_stream.write(
            render_screen(report, state, width, height, color, palette)
        )
        out_stream.flush()
        while True:
            if keys is not None:
                try:
                    key = next(keys)
                except StopIteration:
                    break
            else:
                key = _read_key(in_stream)
            action = _handle_key(report, state, key)
            if action == "quit":
                break
            out_stream.write("\x1b[2J\x1b[H")
            out_stream.write(
                render_screen(report, state, width, height, color, palette)
            )
            out_stream.flush()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _terminal_width():
    return max(20, shutil.get_terminal_size((100, 30)).columns)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -B -m reporting.run_ledger",
        description="Read-only T3 viewer for a saved report-input-0.1 report.",
    )
    parser.add_argument("--input", required=True, help="explicit report-input JSON")
    parser.add_argument(
        "--format", choices=FORMATS, default="text", help="output mode (default: text)"
    )
    args = parser.parse_args(argv)
    try:
        report = build_report(_load_input(args.input))
        if args.format == "json":
            sys.stdout.write(render_json(report))
        elif args.format == "text":
            sys.stdout.write(render_text(report, _terminal_width()) + "\n")
        else:
            run_tui(report, width=_terminal_width())
    except ReportError as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ")[:512]
        print("error: %s: %s" % (exc.code, message), file=sys.stderr)
        return 1
    except LedgerError as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ")[:512]
        print("error: %s: %s" % (exc.code, message), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 0


__all__ = [
    "FORMATS",
    "LedgerError",
    "PALETTES",
    "main",
    "render_json",
    "render_screen",
    "render_text",
    "run_tui",
]


if __name__ == "__main__":
    raise SystemExit(main())
