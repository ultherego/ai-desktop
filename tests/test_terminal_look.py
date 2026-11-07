"""The conversation head: a frame in a colour terminal, the plain one-line label elsewhere."""
import io
import os
import unittest
from unittest.mock import patch

from ai_desktop import run_controls, terminal_frame

CONFIG = {"provider": "claude", "profile": "diagnostic", "model": "default", "effort": "high",
          "access": "read-only", "instructions": ""}


class Terminal(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True

    def fileno(self):
        raise OSError("no real terminal")


class BannerTests(unittest.TestCase):
    def test_a_colour_terminal_gets_a_frame_with_service_profile_model_and_hints(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            lines = run_controls.banner(config=CONFIG, stream=Terminal())
        text = "\n".join(lines)
        self.assertIn(">_ AI Desktop", lines[0])
        for part in ("service:  Claude", "profile:  diagnostic (read-only)", "model:    default", "reasoning: high",
                     "/settings to change"):
            self.assertIn(part, text)
        self.assertTrue(lines[-1].startswith("\x1b["))

    def test_without_colour_it_is_the_one_line_label(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color", "NO_COLOR": "1"}, clear=True):
            self.assertEqual(run_controls.banner(config=CONFIG, stream=Terminal()), [run_controls.label(config=CONFIG)])

    def test_the_default_configuration_also_gets_a_frame(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            text = "\n".join(run_controls.banner(config=None, stream=Terminal()))
        self.assertIn("service:  Claude", text)
        self.assertIn("profile:  default", text)

    def test_only_an_interactive_ask_opens_with_the_frame(self):
        self.assertTrue(run_controls._conversation([]))
        self.assertTrue(run_controls._conversation(["--session", "new"]))
        for arguments in (["--question", "x"], ["--question=x"], ["--preview"]):
            self.assertFalse(run_controls._conversation(arguments))


class FrameTests(unittest.TestCase):
    def frame(self, rows=24, columns=80):
        with patch.object(terminal_frame, "_size", return_value=(rows, columns)):
            return terminal_frame.Frame(Terminal(), "SYNTHETIC status")

    def test_no_frame_without_colour_a_terminal_or_room(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color", "NO_COLOR": "1"}, clear=True):
            self.assertIsNone(terminal_frame.start(Terminal(), "x"))
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            self.assertIsNone(terminal_frame.start(io.StringIO(), "x"))            # not a terminal
            with patch.object(terminal_frame.os, "isatty", return_value=True), \
                    patch.object(terminal_frame, "_size", return_value=(8, 80)):
                self.assertIsNone(terminal_frame.start(Terminal(), "x"))          # too few rows to pin anything
        self.assertIsNone(terminal_frame.current())

    def test_the_reserved_rows_are_a_three_row_band_a_status_and_a_hint_at_the_bottom(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            frame = self.frame()
            rows, cursor = frame.layout("", 0)
            self.assertEqual([number for number, _ in rows], [20, 21, 22, 23, 24])
            self.assertIn(terminal_frame.PLACEHOLDER, rows[1][1])
            self.assertIn("SYNTHETIC status", rows[3][1])
            self.assertIn(terminal_frame.HINT, rows[4][1])
            self.assertEqual(cursor, (21, 3))
            rows, cursor = frame.layout("x" * 200, 200)                     # a long line scrolls with the cursor
            self.assertEqual(cursor[0], 21)
            self.assertLessEqual(cursor[1], 80)
            frame.note = "(Press Ctrl+C again to exit)"
            self.assertIn("(Press Ctrl+C again to exit)", frame.layout("", 0)[0][4][1])

    def test_a_resize_sets_the_region_again_and_returns_to_its_last_row(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            frame = self.frame()
            with patch.object(terminal_frame, "_size", return_value=(30, 80)):
                frame.draw("", 0)
            written = frame.stream.getvalue()
            # Taller: the old band (from row 20) is cleared, the region now ends at row 25 and the band follows.
            self.assertIn("\x1b[r\x1b[20;1H\x1b[J\x1b[1;25r", written)
            self.assertIn("\x1b[27;1H", written)
            self.assertEqual(frame._back(), "\x1b[25;1H")
            self.assertEqual(frame._back(), "\x1b8")

    def test_a_resize_while_typing_redraws_the_band_at_once_with_the_typed_text(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            frame = self.frame(columns=40)
            frame.draw("SYNTHETIC draft", 15)
            frame.stream.seek(0)
            frame.stream.truncate()
            with patch.object(terminal_frame, "_size", return_value=(24, 120)):
                frame._on_resize()
            written = frame.stream.getvalue()
            self.assertIn("SYNTHETIC draft", written)
            self.assertIn("\x1b[1;19r", written)
            frame.leave()
            frame.stream.seek(0)
            frame.stream.truncate()
            frame._on_resize()                                   # not typing: only noted, drawn on the next draw
            self.assertEqual(frame.stream.getvalue(), "")

    def test_the_conversation_starts_at_the_top_and_old_screen_text_goes_to_the_history(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True), \
                patch.object(terminal_frame.os, "isatty", return_value=True), \
                patch.object(terminal_frame, "_size", return_value=(24, 80)):
            stream = Terminal()
            stream.fileno = lambda: 1
            terminal_frame.clear_view(stream)
        self.assertEqual(stream.getvalue(), "\x1b[24;1H" + "\n" * 24 + "\x1b[H")
        plain = io.StringIO()
        terminal_frame.clear_view(plain)
        self.assertEqual(plain.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
