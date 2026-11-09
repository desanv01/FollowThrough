"""Focused tests for the read-only T3 run-ledger saved-report viewer.

All reports are built from the pinned synthetic reporting fixture or derived
synthetic test-only data; no live/provider/execution evidence is involved.
"""

import io
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reporting.report import build_report, _load_input  # noqa: E402
from reporting.run_ledger import (  # noqa: E402
    PALETTES,
    LedgerError,
    _TerminalSession,
    _read_posix_key,
    main,
    render_json,
    render_screen,
    render_text,
    run_tui,
)

FIXTURE = ROOT / "tests" / "fixtures" / "reporting" / "v0.1" / "input.json"
REPORT = build_report(_load_input(str(FIXTURE)))


class FakeTerminal:
    def __init__(self):
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited += 1
        return False


def empty_report():
    return build_report(
        {
            "schema_version": "report-input-0.1",
            "dataset_id": "empty-ledger",
            "episodes": [],
        }
    )


def escaped_report():
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = json.loads(json.dumps(source["episodes"][0]))
    row["episode_id"] = "escape-attempt"
    row["trace"] = [
        {
            "seq": 1,
            "role": "O1",
            "kind": "message",
            "summary": "\x1b[31mred\x1b[0m\nnext\tline",
        }
    ]
    return build_report(
        {
            "schema_version": "report-input-0.1",
            "dataset_id": "escape-ledger",
            "episodes": [row],
        }
    )


def mixed_report():
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    synthetic = json.loads(json.dumps(source["episodes"][0]))
    provider = json.loads(json.dumps(source["episodes"][0]))
    provider.update(
        episode_id="mixed-provider",
        scenario_id="R3",
        arm="ordinary",
        trial=1,
        stage="formal",
        evidence_kind="real_provider",
        status="completed",
        outcomes={
            "functional": "pass",
            "workflow": "pass",
            "evidence": "pass",
            "completion": "pass",
        },
    )
    return build_report(
        {
            "schema_version": "report-input-0.1",
            "dataset_id": "mixed-provenance",
            "episodes": [synthetic, provider],
        }
    )


def all_unknown_cost_report():
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    episodes = json.loads(json.dumps(source["episodes"]))
    for episode in episodes:
        episode["resources"]["estimated_cost_micro_usd"] = None
        episode["resources"]["reserved_micro_usd"] = None
    return build_report(
        {
            "schema_version": "report-input-0.1",
            "dataset_id": "all-unknown-cost",
            "episodes": episodes,
        }
    )


class TtyInput(io.StringIO):
    def isatty(self):
        return True


class TtyOutput(io.StringIO):
    def isatty(self):
        return True


class FailFirstWrite(TtyOutput):
    def __init__(self, error):
        super().__init__()
        self.error = error
        self.failed = False

    def write(self, text):
        if not self.failed:
            self.failed = True
            raise self.error
        return super().write(text)


class LedgerTests(unittest.TestCase):
    def test_wide_t3_rgb_framed_surface(self):
        state = {"selected_id": REPORT["episodes"][0]["episode_id"],
                 "filter": "", "filter_input": None, "evidence": False}
        frame = render_screen(REPORT, state, width=140, height=30)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", frame)
        self.assertIn("\x1b[48;2;0;0;0m", frame)
        self.assertIn("\x1b[38;2;0;231;235m", frame)
        self.assertIn("\x1b[48;2;0;101;111m", frame)
        self.assertIn("┌" + "─" * 138 + "┐", plain)
        self.assertIn("PROV/STAGE", plain)
        self.assertIn("GRADE=strict", plain)
        self.assertIn("SELECTED ATTEMPT / " + state["selected_id"], plain)
        self.assertIn("j/k move", plain)
        self.assertEqual(len(plain.splitlines()), 30)
        self.assertTrue(all(len(line) == 140 for line in plain.splitlines()))

    def test_compact_t3_frame_full_selected_identity_and_outcomes(self):
        state = {"selected_id": REPORT["episodes"][0]["episode_id"],
                 "filter": "", "filter_input": None, "evidence": False}
        plain = render_screen(REPORT, state, width=80, height=24, color=False)
        self.assertIn("┌" + "─" * 78 + "┐", plain)
        self.assertIn("SELECTED ATTEMPT / " + state["selected_id"], plain)
        for label in ("Functional=", "Workflow=", "Evidence=", "Completion=",
                      "Trial:", "Elapsed:", "Estimated cost", "Reserved"):
            self.assertIn(label, plain)
        self.assertEqual(len(plain.splitlines()), 24)
        self.assertTrue(all(len(line) == 80 for line in plain.splitlines()))

    def test_text_mode_uses_fixture_and_distinct_labels(self):
        text = render_text(REPORT, width=120)
        self.assertIn("synthetic-report-contract-v0.1", text)
        self.assertIn("SYNTHETIC DEVELOPMENT DATA", text)
        self.assertIn("development", text)
        self.assertIn("synthetic_development", text)
        self.assertIn("EXCLUDED", text)
        self.assertIn("UNKNOWN", text)
        self.assertIn("PASS", text)
        self.assertIn("FAIL", text)
        self.assertNotIn("\x1b", text)
        for episode in REPORT["episodes"]:
            self.assertIn(episode["episode_id"], text)
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 120)

    def test_json_mode_emits_existing_report_object(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--input", str(FIXTURE), "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), REPORT)

    def test_text_cli_exit_zero_and_no_escape(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--input", str(FIXTURE), "--format", "text"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("RUN LEDGER", stdout.getvalue())
        self.assertNotIn("\x1b", stdout.getvalue())

    def test_tui_selection_evidence_and_terminal_restore(self):
        terminal = FakeTerminal()
        out = io.StringIO()
        run_tui(
            REPORT,
            out_stream=out,
            key_source=["j", "j", "enter", "q"],
            terminal=terminal,
            width=120,
            height=24,
            color=False,
        )
        rendered = out.getvalue()
        self.assertIn("SELECTED ATTEMPT / synthetic-c3-success", rendered)
        self.assertIn("EVIDENCE VIEW", rendered)
        self.assertIn("Functional=PASS", rendered)
        self.assertEqual(terminal.entered, 1)
        self.assertEqual(terminal.exited, 1)

    def test_tui_filter_case_insensitive_and_clear(self):
        terminal = FakeTerminal()
        out = io.StringIO()
        run_tui(
            REPORT,
            out_stream=out,
            key_source=["/", "R", "3", "enter", "esc", "q"],
            terminal=terminal,
            width=120,
            color=False,
        )
        rendered = out.getvalue()
        self.assertIn("Filter: R3 (1 matches)", rendered)
        self.assertIn("SELECTED ATTEMPT / synthetic-r3-provider", rendered)
        self.assertIn("input_tokens=unknown", rendered)
        self.assertIn("output_tokens=unknown", rendered)
        self.assertIn("Estimated cost (estimate, not billed): unknown", rendered)
        self.assertEqual(terminal.entered, 1)
        self.assertEqual(terminal.exited, 1)

    def test_tui_no_matches_and_empty_dataset(self):
        out = io.StringIO()
        run_tui(
            REPORT,
            out_stream=out,
            key_source=["/", "z", "z", "enter", "q"],
            terminal=FakeTerminal(),
            width=100,
            color=False,
        )
        self.assertIn("No attempts match filter.", out.getvalue())
        self.assertIn("No attempt selected.", out.getvalue())

        empty_out = io.StringIO()
        run_tui(
            empty_report(),
            out_stream=empty_out,
            key_source=["q"],
            terminal=FakeTerminal(),
            width=100,
            color=False,
        )
        self.assertIn("No attempts in this report.", empty_out.getvalue())

    def test_terminal_escape_and_embedded_newline_are_literal(self):
        report = escaped_report()
        text = render_text(report, width=120)
        self.assertNotIn("\x1b", text)
        screen = render_screen(
            report,
            {
                "selected_id": "escape-attempt",
                "filter": "",
                "filter_input": None,
                "evidence": True,
            },
            width=120,
            color=False,
        )
        self.assertNotIn("\x1b", screen)
        self.assertIn("[31mred", screen)
        self.assertIn("next line", screen)

    def test_narrow_width_and_palette_are_readable(self):
        text = render_text(REPORT, width=50)
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 50)
        screen = render_screen(
            REPORT,
            {
                "selected_id": REPORT["episodes"][0]["episode_id"],
                "filter": "",
                "filter_input": None,
                "evidence": False,
            },
            width=50,
            color=False,
            palette="muted",
        )
        self.assertLessEqual(max(len(line) for line in screen.splitlines()), 50)
        self.assertIn("muted", PALETTES)

    def test_frame_fits_80x24_before_and_after_actions(self):
        terminal = FakeTerminal()
        out = io.StringIO()
        run_tui(
            REPORT,
            out_stream=out,
            key_source=["j", "/", "C", "3", "enter", "j", "enter", "q"],
            terminal=terminal,
            width=80,
            height=24,
            color=False,
        )
        frames = out.getvalue().split("\x1b[2J\x1b[H")[1:]
        self.assertGreaterEqual(len(frames), 4)
        for frame in frames:
            lines = frame.splitlines()
            self.assertLessEqual(len(lines), 24)
            self.assertLessEqual(max(len(line) for line in lines), 80)
        initial = frames[0]
        final = frames[-1]
        self.assertIn("> synthetic-c1-infra", initial)
        self.assertIn("Trial: 1", initial)
        self.assertIn("Functional=UNKNOWN", initial)
        self.assertIn("Workflow=UNKNOWN", initial)
        self.assertIn("Evidence=UNKNOWN", initial)
        self.assertIn("Completion=UNKNOWN", initial)
        self.assertIn("Elapsed: 00:01", initial)
        self.assertIn("Estimated cost (estimate, not billed):", initial)
        self.assertIn("Reserved (authorisation):", initial)
        self.assertNotEqual(initial, final)
        self.assertIn("EVIDENCE VIEW", final)
        self.assertIn("#1 controller/supplied_fixture", final)
        self.assertIn("#3 O1/completion", final)
        self.assertIn("SELECTED ATTEMPT / synthetic-c3-unknown", final)
        self.assertIn("j/k move", final)
        self.assertEqual(terminal.entered, 1)
        self.assertEqual(terminal.exited, 1)

    def test_plain_text_shows_all_ids_at_80_and_100(self):
        for width in (80, 100):
            text = render_text(REPORT, width=width)
            for episode in REPORT["episodes"]:
                self.assertIn(episode["episode_id"], text)
            self.assertLessEqual(
                max(len(line) for line in text.splitlines()), width
            )

    def test_mixed_provenance_and_stage_are_not_pooled(self):
        report = mixed_report()
        text = render_text(report, width=160)
        self.assertIn("synthetic_development", text)
        self.assertIn("real_provider", text)
        self.assertIn("development", text)
        self.assertIn("formal", text)
        self.assertIn("provider/formal", text)
        self.assertNotIn("protected", text.lower())
        self.assertNotIn("strict PASS", text)
        screen = render_screen(
            report,
            {
                "selected_id": "mixed-provider",
                "filter": "",
                "filter_input": None,
                "evidence": False,
            },
            width=120,
            height=24,
            color=False,
        )
        self.assertIn("Provenance: real_provider", screen)
        self.assertIn("Stage: formal", screen)
        self.assertNotIn("protected", screen.lower())

    def test_all_missing_costs_remain_unknown(self):
        report = all_unknown_cost_report()
        screen = render_screen(
            report,
            {
                "selected_id": report["episodes"][0]["episode_id"],
                "filter": "",
                "filter_input": None,
                "evidence": False,
            },
            width=160,
            height=24,
            color=False,
        )
        self.assertIn(
            "Estimated cost (estimate, not billed): unknown", screen
        )
        self.assertIn("Reserved (authorisation): unknown", screen)
        self.assertNotIn("$0.000000", screen)
        self.assertNotIn("known=0", screen)

    def test_posix_down_and_standalone_escape_use_one_layer(self):
        down = iter([b"\x1b", b"[", b"B"])
        ready = iter([True, True, False])
        self.assertEqual(
            _read_posix_key(
                0,
                read_byte=lambda: next(down),
                ready=lambda: next(ready, False),
            ),
            "down",
        )
        self.assertEqual(
            _read_posix_key(
                0, read_byte=lambda: b"\x1b", ready=lambda: False
            ),
            "esc",
        )

    def test_terminal_setup_failure_does_not_enter(self):
        terminal = _TerminalSession(
            TtyInput(),
            TtyOutput(),
            enable_mode=lambda stream: (_ for _ in ()).throw(
                LedgerError(
                    "tui_terminal_setup_failed",
                    "interactive TUI terminal setup is unsupported; use --format text",
                )
            ),
            restore_mode=lambda stream, token: None,
        )
        with self.assertRaises(LedgerError) as caught:
            terminal.__enter__()
        self.assertEqual(caught.exception.code, "tui_terminal_setup_failed")
        self.assertNotIn("\x1b[?25l", terminal.out_stream.getvalue())

    def test_terminal_startup_write_failure_restores_mode_and_cursor(self):
        restored = []
        out = FailFirstWrite(OSError("write failed"))
        terminal = _TerminalSession(
            TtyInput(),
            out,
            enable_mode=lambda stream: "MODE",
            restore_mode=lambda stream, token: restored.append(token),
        )
        with self.assertRaises(LedgerError) as caught:
            terminal.__enter__()
        self.assertEqual(caught.exception.code, "tui_terminal_setup_failed")
        self.assertEqual(restored, ["MODE"])
        self.assertIn("\x1b[?25h", out.getvalue())

    def test_terminal_startup_interrupt_restores_mode_and_propagates(self):
        restored = []
        out = FailFirstWrite(KeyboardInterrupt())
        terminal = _TerminalSession(
            TtyInput(),
            out,
            enable_mode=lambda stream: "MODE",
            restore_mode=lambda stream, token: restored.append(token),
        )
        with self.assertRaises(KeyboardInterrupt):
            terminal.__enter__()
        self.assertEqual(restored, ["MODE"])
        self.assertIn("\x1b[?25h", out.getvalue())

    def test_terminal_normal_exit_restores_mode_and_cursor(self):
        restored = []
        out = TtyOutput()
        terminal = _TerminalSession(
            TtyInput(),
            out,
            enable_mode=lambda stream: "MODE",
            restore_mode=lambda stream, token: restored.append(token),
        )
        with terminal:
            pass
        self.assertEqual(restored, ["MODE"])
        self.assertIn("\x1b[?25h", out.getvalue())

    def test_interactive_tui_non_tty_refusal(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "reporting.run_ledger",
                "--input",
                str(FIXTURE),
                "--format",
                "tui",
            ],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("use --format text", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
