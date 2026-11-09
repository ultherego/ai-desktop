"""The full-screen conversation view: the input always visible, earlier rows reachable with PgUp/PgDn and the wheel,
and the conversation left in the terminal's history when the view closes (the maintainer's choice, 2026-11-09)."""
import io
import json
import os
import pty
import re
import select
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_desktop import question_input, screen_model, terminal_frame, terminal_input
from ai_desktop.screen_model import ScreenModel

ROOT = Path(__file__).resolve().parents[1]


def text(model, row):
    return "".join(char for char, _ in model.grid[row]).rstrip()


class ScreenModelTests(unittest.TestCase):
    def test_text_lines_and_a_scroll_region_keep_scrolled_rows_in_the_history(self):
        model = ScreenModel(6, 20)
        model.feed("\x1b[1;4r\x1b[1;1H")                   # the conversation area is rows 1-4
        model.feed("\x1b[6;1Hband")                         # a row below the region stays put
        model.feed("\x1b[1;1H" + "\n".join("line %d" % n for n in range(1, 8)))
        self.assertEqual([text(model, row) for row in range(4)], ["line 4", "line 5", "line 6", "line 7"])
        self.assertEqual([ScreenModel.render(row) for row in model.history], ["line 1", "line 2", "line 3"])
        self.assertEqual(text(model, 5), "band")

    def test_carriage_return_erase_and_cursor_moves_rewrite_in_place(self):
        model = ScreenModel(5, 30)
        model.feed("Waiting 1s\r\x1b[KWaiting 2s")
        self.assertEqual(text(model, 0), "Waiting 2s")
        model.feed("\nrow two\nrow three\x1b[2A\r\x1b[2Kfirst again")
        self.assertEqual([text(model, row) for row in range(3)], ["first again", "row two", "row three"])
        model.feed("\x1b7\x1b[5;1Hbottom\x1b8!")
        self.assertEqual(text(model, 0), "first again!")
        self.assertEqual(text(model, 4), "bottom")

    def test_colours_are_kept_and_drawn_back(self):
        model = ScreenModel(3, 20)
        model.feed("a\x1b[1;31mred\x1b[0m b")
        self.assertEqual(ScreenModel.render(model.grid[0]), "a\x1b[0m\x1b[1;31mred\x1b[0m b")

    def test_a_sequence_split_between_writes_is_never_shown_as_text(self):
        model = ScreenModel(3, 20)
        model.feed("ab\x1b[")
        model.feed("31mc\x1b]0;title")
        model.feed("\x07d")
        self.assertEqual(text(model, 0), "abcd")

    def test_long_rows_wrap_like_the_terminal(self):
        model = ScreenModel(4, 5)
        model.feed("abcdefgh")
        self.assertEqual([text(model, 0), text(model, 1)], ["abcde", "fgh"])
        model.feed("\r\n12345")                             # exactly full: the next character would wrap
        self.assertEqual(text(model, 2), "12345")
        self.assertEqual(model.row, 2)

    def test_the_view_shows_earlier_rows_and_the_transcript_counts_from_a_mark(self):
        model = ScreenModel(4, 20)
        model.feed("\x1b[1;3r\x1b[1;1Hhead")
        mark = model.total(3)
        model.feed("\n" + "\n".join("row %d" % n for n in range(1, 7)))
        self.assertEqual([ScreenModel.render(row) for row in model.region_rows(3, 2)], ["row 2", "row 3", "row 4"])
        self.assertEqual(len(model.region_rows(3, 100)), 3)
        self.assertEqual(model.transcript(3, since=mark), ["row %d" % n for n in range(1, 7)])
        self.assertIn("\x1b[1;1H", model.view(3, 2))

    def test_the_history_is_bounded_and_the_mark_still_counts(self):
        with patch.object(screen_model, "MAX_HISTORY", 5):
            model = ScreenModel(2, 10)
            model.history = screen_model.deque(maxlen=5)
            model.feed("\n".join("r%d" % n for n in range(20)))
        self.assertEqual(len(model.history), 5)
        self.assertEqual(model.transcript(2, since=model.total(2) - 2), ["r18", "r19"])

    def test_resizing_keeps_the_rows(self):
        model = ScreenModel(4, 10)
        model.feed("one\ntwo\nthree\nfour")
        model.resize(2, 6)
        self.assertEqual([text(model, 0), text(model, 1)], ["three", "four"])
        self.assertEqual([ScreenModel.render(row) for row in model.history], ["one", "two"])


class FakeTerminal(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 1


class FrameTests(unittest.TestCase):
    def frame(self):
        self.enterContext(patch.object(terminal_frame, "_size", return_value=(20, 60)))
        self.enterContext(patch.object(terminal_frame, "usable", return_value=True))
        self.enterContext(patch.object(terminal_frame.signal, "signal"))
        self.enterContext(patch.object(sys, "stdout", sys.stdout))
        terminal = FakeTerminal()
        frame = terminal_frame.start(terminal, "SYNTHETIC status", fullscreen=True)
        self.addCleanup(terminal_frame.stop)
        return frame, terminal

    def test_the_view_uses_the_alternate_screen_and_every_print_reaches_the_model(self):
        frame, terminal = self.frame()
        self.assertTrue(terminal.getvalue().startswith(terminal_frame._ENTER_FULL))
        self.assertIs(sys.stdout, frame.screen)
        frame.repaint = lambda: print("HEAD")
        frame.begin()
        for number in range(40):
            print("answer row %d" % number)
        self.assertIn("answer row 39", "\n".join(ScreenModel.render(row) for row in frame.screen.model.grid))
        self.assertTrue(frame.screen.model.history)

    def test_paging_shows_earlier_rows_holds_live_text_and_any_key_returns(self):
        frame, terminal = self.frame()
        for number in range(60):
            print("row %d" % number)
        before = terminal.getvalue()
        frame.scroll(b"page-up")
        self.assertTrue(frame.viewing)
        shown = terminal.getvalue()[len(before):]
        self.assertIn("history", shown)
        print("LIVE WHILE VIEWING")                          # kept in the model, not drawn over the history
        self.assertNotIn("LIVE WHILE VIEWING", terminal.getvalue()[len(before):])
        frame.scroll(b"wheel-down")
        frame.scroll(b"scroll-end")
        self.assertFalse(frame.viewing)
        self.assertIn("LIVE WHILE VIEWING", terminal.getvalue()[len(before):])   # drawn back from the model

    def test_the_wheel_is_on_only_while_this_program_reads_keys(self):
        frame, terminal = self.frame()
        frame.mouse(True)
        frame.mouse(True)
        frame.mouse(False)
        self.assertEqual(terminal.getvalue().count(terminal_frame._MOUSE_ON), 1)
        self.assertEqual(terminal.getvalue().count(terminal_frame._MOUSE_OFF), 1)

    def test_closing_leaves_the_conversation_in_the_terminal_history(self):
        frame, terminal = self.frame()
        frame.repaint = lambda: print("HEAD ALREADY ON THE SCREEN")
        frame.begin()
        print("the answer")
        terminal_frame.stop()
        output = terminal.getvalue()
        tail = output[output.rindex(terminal_frame._LEAVE_FULL):]
        self.assertIn("the answer", tail)
        self.assertNotIn("HEAD ALREADY ON THE SCREEN", tail)      # printed before the view on the ordinary screen
        self.assertIsNot(sys.stdout, frame.screen)

    def test_an_inline_frame_has_no_view_and_ignores_scrolling(self):
        self.enterContext(patch.object(terminal_frame, "_size", return_value=(20, 60)))
        self.enterContext(patch.object(terminal_frame, "usable", return_value=True))
        self.enterContext(patch.object(terminal_frame.signal, "signal"))
        terminal = FakeTerminal()
        frame = terminal_frame.start(terminal, "x", fullscreen=False)
        self.addCleanup(terminal_frame.stop)
        frame.scroll(b"page-up")
        frame.mouse(True)
        self.assertFalse(frame.viewing)
        self.assertNotIn("1049", terminal.getvalue())

    def test_the_setting_file_chooses_the_view(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(terminal_frame.view_setting(home), "fullscreen")
            path = Path(home, ".config/ai-desktop")
            path.mkdir(parents=True)
            (path / "view").write_text("inline\n")
            self.assertEqual(terminal_frame.view_setting(home), "inline")
            (path / "view").write_text("anything else")
            self.assertEqual(terminal_frame.view_setting(home), "fullscreen")


class KeyTests(unittest.TestCase):
    def test_page_keys_and_the_wheel_are_scroll_keys_and_other_mouse_reports_are_ignored(self):
        self.assertEqual(question_input.scroll_key(b"[5~"), b"page-up")
        self.assertEqual(question_input.scroll_key(b"[6~"), b"page-down")
        self.assertEqual(question_input.scroll_key(b"[<64;10;5M"), b"wheel-up")
        self.assertEqual(question_input.scroll_key(b"[<65;10;5M"), b"wheel-down")
        self.assertEqual(question_input.scroll_key(b"[<0;10;5M"), b"ignore")
        self.assertIsNone(question_input.scroll_key(b"[A"))

    def test_a_wheel_report_during_a_turn_never_reaches_the_draft(self):
        seen = []

        class Frame:
            def scroll(self, key):
                seen.append(key)

        owner = terminal_input.TypeAhead.__new__(terminal_input.TypeAhead)
        owner.frame = Frame()
        owner.line = question_input._Line(1000)
        owner._question = owner._answer = None
        owner._sequence = None
        owner._epoch = owner._sequence_epoch = 0
        owner._paste_open = False
        owner.commands, owner.pick, owner.queue = (), 0, []
        for byte in b"ab\x1b[<64;12;7M\x1b[6~c":
            owner._feed(bytes([byte]))
        self.assertEqual(owner.line.text, "abc")
        self.assertIn(b"wheel-up", seen)
        self.assertIn(b"page-down", seen)


@unittest.skipUnless(sys.stdin.isatty() or True, "uses its own pty")
class RealTerminalTests(unittest.TestCase):
    def test_a_real_terminal_enters_and_leaves_the_view_and_keeps_the_answer(self):
        script = ("import sys\nsys.path.insert(0, %r)\n"
                  "import ai_desktop.terminal_frame as frame\n"
                  "f = frame.start(sys.stdout, 'SYNTHETIC status', fullscreen=True)\n"
                  "f.repaint = lambda: print('HEAD')\n"
                  "f.begin()\n"
                  "for n in range(50): print('SYNTHETIC row %%d' %% n)\n"
                  "frame.stop()\n"
                  "print('DONE', flush=True)\n" % str(ROOT))
        master, slave = pty.openpty()
        import fcntl
        import struct
        import termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        process = subprocess.Popen([sys.executable, "-I", "-B", "-c", script], stdin=slave, stdout=slave, stderr=slave,
                                   close_fds=True, env={"PATH": "/usr/bin", "TERM": "xterm-256color", "LANG": "C.UTF-8"})
        os.close(slave)
        output = bytearray()
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                if b"DONE" in output and process.poll() is not None:
                    break
        finally:
            process.wait(timeout=5)
            os.close(master)
        text = output.decode("utf-8", "replace")
        self.assertIn("\x1b[?1049h", text)
        leave = text.index("\x1b[?1049l")
        after = text[leave:]
        for number in (0, 25, 49):
            self.assertIn("SYNTHETIC row %d" % number, after)          # the whole answer stays in the history
        self.assertNotIn("HEAD", after)
        self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
