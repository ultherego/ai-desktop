"""The conversation head: a frame in a colour terminal, the plain one-line label elsewhere."""
import io
import os
import unittest
from unittest.mock import patch

from ai_desktop import ask_commands, run_controls, terminal_frame, terminal_input

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
            self.assertEqual([number for number, _ in rows], [19, 20, 21, 22, 23, 24])
            self.assertEqual(rows[0][1], "\x1b[2K")                     # the spacer row
            self.assertIn(terminal_frame.PLACEHOLDER, rows[2][1])
            self.assertIn("SYNTHETIC status", rows[4][1])
            self.assertIn(terminal_frame.HINT, rows[5][1])
            self.assertEqual(cursor, (21, 3))
            rows, cursor = frame.layout("x" * 200, 200)                     # a long line scrolls with the cursor
            self.assertEqual(cursor[0], 21)
            self.assertLessEqual(cursor[1], 80)
            frame.note = "(Press Ctrl+C again to exit)"
            self.assertIn("(Press Ctrl+C again to exit)", frame.layout("", 0)[0][5][1])

    def test_a_resize_sets_the_region_again_and_returns_to_its_last_row(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            frame = self.frame()
            with patch.object(terminal_frame, "_size", return_value=(30, 80)):
                frame.draw("", 0)
            written = frame.stream.getvalue()
            # Taller: the old band (from row 20) is cleared, the region now ends at row 25 and the band follows.
            self.assertIn("\x1b[r\x1b[19;1H\x1b[J\x1b[1;24r", written)
            self.assertIn("\x1b[27;1H", written)
            self.assertEqual(frame._back(), "\x1b[24;1H")
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
            self.assertIn("\x1b[1;18r", written)
            frame.leave()
            frame.stream.seek(0)
            frame.stream.truncate()
            frame._on_resize()                                   # not typing: only noted, drawn on the next draw
            self.assertEqual(frame.stream.getvalue(), "")

    def test_after_a_resize_the_conversation_is_drawn_again_from_the_top_and_the_input_stays_at_the_bottom(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            frame = self.frame()
            frame.repaint = lambda: frame.stream.write("SYNTHETIC HEAD AND THREAD\n")
            frame.draw("SYNTHETIC draft", 15)
            frame.stream.seek(0)
            frame.stream.truncate()
            with patch.object(terminal_frame, "_size", return_value=(40, 120)):
                frame._on_resize()
            written = frame.stream.getvalue()
        cleared = written.index("\x1b[H\x1b[2J")
        self.assertLess(cleared, written.index("SYNTHETIC HEAD AND THREAD"))     # cleared, then drawn from the top
        self.assertIn("\x1b[1;34r\x1b[H", written)                                # region 1-35, starting at row 1
        self.assertIn("\x1b[37;1H", written)                                      # the input row at the new bottom
        self.assertLess(written.index("SYNTHETIC HEAD AND THREAD"), written.index("SYNTHETIC draft"))
        self.assertEqual(frame._back(), "\x1b8")                                  # the thread's new end was saved

    def test_slash_commands_open_as_a_list_above_the_band_and_the_conversation_makes_room(self):
        commands = [("/help", "list commands"), ("/model", "change the model"), ("/exit", "end the conversation")]
        with patch.dict(os.environ, {"TERM": "xterm-256color", "COLORTERM": "truecolor"}, clear=True):
            frame = self.frame()
            frame.show_commands(commands, 1)
            frame.draw("/", 1)
            written = frame.stream.getvalue()
            self.assertIn("\x1b[18;1H\n\n\n", written)                  # the conversation scrolls up three rows
            self.assertIn("\x1b[1;15r", written)                          # and its region ends above the list
            rows = dict(frame.layout("/", 1)[0])
            self.assertIn("/help", rows[17])
            self.assertIn("\x1b[48;2;88;140;230m", rows[18])               # the highlighted row
            self.assertIn("/model", rows[18])
            self.assertIn("/exit", rows[19])
            self.assertNotIn("/model", rows[21])                          # the band itself keeps only the typed text
            frame.stream.seek(0)
            frame.stream.truncate()
            frame.leave()                                                 # closed: the region grows back, rows cleared
            written = frame.stream.getvalue()
            self.assertIn("\x1b[1;18r", written)
            for row in (16, 17, 18):
                self.assertIn("\x1b[%d;1H\x1b[2K" % row, written)
            self.assertEqual(frame.extra, 0)

    def test_the_band_grows_with_longer_text_and_the_conversation_makes_room(self):
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True), \
                patch.object(terminal_frame, "_size", return_value=(24, 40)):
            frame = terminal_frame.Frame(Terminal(), "SYNTHETIC status")
            text = "first line\nsecond line\n" + "x" * 50            # the last line wraps at 36 columns
            frame.draw(text, len(text))
            self.assertEqual(frame.lines, 4)
            self.assertIn("\x1b[18;1H\n\n\n", frame.stream.getvalue())  # the conversation scrolled up three rows
            self.assertIn("\x1b[1;15r", frame.stream.getvalue())
            rows = dict(frame.layout(text, len(text))[0])
            self.assertIn("first line", rows[18])
            self.assertIn("second line", rows[19])
            self.assertIn("x" * 36, rows[20])
            self.assertIn("x" * 14, rows[21])
            self.assertEqual(frame.cursor_at, (21, 3 + 14))
            frame.draw("y" * 2000, 2000)                                  # never more than about half the window
            self.assertEqual(frame.lines, 8)
            self.assertEqual(frame.cursor_at[0], 21)                      # the cursor's row stays visible
            frame.leave()
            self.assertEqual((frame.lines, frame.extra), (1, 0))

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


class LiveInputTests(unittest.TestCase):
    def owner(self, resolver, commands=()):
        with patch.object(terminal_input.termios, "tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0] * 32]):
            owner = terminal_input.TypeAhead(object(), io.StringIO(), resolver=resolver, commands=commands)
        owner._sequence = None
        return owner

    def type(self, owner, text):
        output = io.StringIO()
        with patch("sys.stdout", output):
            for char in text:
                owner._key(char.encode())
        return output.getvalue()

    def test_enter_on_a_slash_command_during_a_turn_queues_what_its_menu_chose(self):
        seen = []
        choice = ask_commands.Deferred("/skills b", lambda conversation: None)

        def resolver(line):
            seen.append(line)
            return {"/model": ["/model opus"], "/help": [], "/skills": [choice]}[line]

        owner = self.owner(resolver)
        printed = self.type(owner, "/model\r/help\r/skills\rnext question\r")
        self.assertEqual(seen, ["/model", "/help", "/skills"])            # an ordinary message never reaches it
        self.assertEqual(owner.queue, ["/model opus", choice, "next question"])
        self.assertIn("/model opus \u00b7 after the answer", printed)
        self.assertIn("/skills b \u00b7 after the answer", printed)

    def test_a_failing_menu_keeps_the_typed_command_for_after_the_answer(self):
        def resolver(line):
            raise RuntimeError("SYNTHETIC menu failure")

        owner = self.owner(resolver)
        self.type(owner, "/model\r")
        self.assertEqual(owner.queue, ["/model"])

    def test_a_lease_is_refused_when_the_live_input_does_not_confirm_its_pause(self):
        owner = self.owner(None)
        owner._thread = type("Busy", (), {"is_alive": lambda self: True})()
        owner._paused.wait = lambda timeout=None: False                        # the reader never confirms
        with patch.object(terminal_input.termios, "tcsetattr") as mode, self.assertRaises(OSError):
            with owner.lease():
                self.fail("the keyboard was handed over")
        mode.assert_not_called()
        self.assertFalse(owner._pause.is_set())

    def test_sequences_are_collected_byte_by_byte_and_an_unfinished_one_is_dropped(self):
        owner = self.owner(None)
        owner._sequence = None
        for byte in "\x1b[200~ab\x1b[201~".encode() + "ż".encode() + b"c":
            owner._feed(bytes([byte]))
        self.assertEqual(owner.line.text, "abżc")
        owner._feed(b"\x1b")
        owner._feed(b"[")
        owner._feed(b"2")                                                      # an unfinished escape sequence ...
        owner._sequence = None                                                 # ... dropped when a lease begins
        owner._feed(b"x")
        self.assertEqual(owner.line.text, "abżcx")

    def test_during_a_turn_slash_offers_the_command_list_and_enter_runs_the_highlighted_one(self):
        seen = []
        owner = self.owner(lambda line: seen.append(line) or [], commands=ask_commands.COMMANDS)
        self.type(owner, "/e")
        self.assertEqual([name for name, _ in owner._matches()], ["/effort", "/exit"])
        owner._feed(b"\x1b"), owner._feed(b"["), owner._feed(b"B")             # Down: the second match
        self.assertEqual(owner.pick, 1)
        self.type(owner, "\r")
        self.assertEqual(seen, ["/exit"])                                     # Enter takes the highlighted command
        self.type(owner, "/he\t")
        self.assertEqual(owner.line.text, "/help")                            # Tab completes it
        self.type(owner, " x")
        self.assertEqual(owner._matches(), ())                                 # a space ends the list, as when idle


class StatusLineTests(unittest.TestCase):
    def test_the_line_under_the_input_follows_a_changed_setting(self):
        from ai_desktop import ask_cli, execution
        frame = type("Frame", (), {"status": "Claude · profile: diagnostic · model: default · reasoning: high"})()
        selected = {"provider": "claude", "model": "default", "effort": "max", "instructions": "",
                    "profile": "diagnostic", "access": "changes"}
        with patch.object(terminal_frame, "_current", frame), execution.using(selected):
            ask_cli._refresh_status()
        self.assertIn("reasoning: max", frame.status)
        with patch.object(terminal_frame, "_current", None):
            ask_cli._refresh_status()                                           # no pinned input: nothing to update


class FailureMessageTests(unittest.TestCase):
    def test_an_unusual_failure_names_its_fixed_reason_code(self):
        from types import SimpleNamespace
        from ai_desktop import ask_cli
        from ai_desktop.analysis_contract import AnalysisDenied
        output = io.StringIO()
        with patch("sys.stdout", output):
            ask_cli._explain_denied(AnalysisDenied("result"), SimpleNamespace(details=False), {"execution": None})
        self.assertIn("Could not get an answer (result).", output.getvalue())
