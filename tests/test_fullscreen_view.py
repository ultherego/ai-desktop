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


class ResizeTests(unittest.TestCase):
    """CachyOS check of candidate 146: a resize left blank rows between the question and the next text."""

    def test_growing_brings_rows_back_and_the_end_stays_right_above_the_input(self):
        model = ScreenModel(10, 20)
        model.feed("\x1b[1;6r\x1b[1;1H" + "\n".join("row %d" % n for n in range(12)))
        model.feed("\x1b7")                                       # the end of the conversation
        model.feed("\x1b[9;1Hband")
        model.refit(14, 20, old_top=6, new_top=10)
        self.assertEqual([text(model, row) for row in range(10)], ["row %d" % n for n in range(2, 12)])
        self.assertEqual(model.saved[0], 9)                       # the next text continues on the last area row
        self.assertNotIn("band", [text(model, row) for row in range(14)])

    def test_shrinking_sends_the_top_rows_to_the_history(self):
        model = ScreenModel(10, 20)
        model.feed("\x1b[1;6r\x1b[1;1H" + "\n".join("row %d" % n for n in range(4)) + "\x1b7")
        model.refit(7, 20, old_top=6, new_top=3)
        self.assertEqual([text(model, row) for row in range(3)], ["row 1", "row 2", "row 3"])
        self.assertEqual([ScreenModel.render(row) for row in model.history], ["row 0"])
        self.assertEqual(model.saved[0], 2)

    def test_a_short_conversation_stays_at_the_top(self):
        model = ScreenModel(10, 20)
        model.feed("\x1b[1;6r\x1b[1;1Hone\ntwo\x1b7")
        model.refit(14, 20, old_top=6, new_top=10)
        self.assertEqual([text(model, 0), text(model, 1), text(model, 2)], ["one", "two", ""])
        self.assertEqual(model.saved[0], 1)

    def test_the_frame_redraws_without_a_gap_after_a_resize(self):
        frame, terminal = FrameTests.frame(self)
        for number in range(30):
            print("row %d" % number)
        frame.enter()
        sizes = iter([(30, 60)] * 50)
        with patch.object(terminal_frame, "_size", side_effect=lambda stream: next(sizes)):
            frame.draw("", 0, typing=False)
        model = frame.screen.model
        self.assertEqual(text(model, model.saved[0] - 1), "row 29")   # the next text goes on the row below it
        self.assertEqual(model.saved[0], frame._top() - 1)             # right above the input: no gap


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.frame, self.terminal = FrameTests.frame(self)
        self.copied = []
        self.enterContext(patch.object(terminal_frame, "copy_to_clipboard",
                                       side_effect=lambda text, stream, **_: self.copied.append(text) or True))

    def click(self, kind, button, column, row):
        self.frame.mouse_event(b"[<%d;%d;%d%s" % (button, column, row, kind.encode()))

    def test_dragging_selects_and_releasing_copies_the_text(self):
        print("first line of the answer")
        print("second line")
        top = self.frame._top()
        start, rows = self.frame.screen.model.window(top, 0)
        first = next(index for index, row in enumerate(rows) if "first" in ScreenModel.render(row)) + 1
        self.click("M", 0, 7, first)
        self.assertTrue(self.frame.screen.selecting)
        print("LIVE DURING SELECTION")                          # held back until the selection ends
        self.click("M", 32, 6, first + 1)
        self.click("m", 0, 6, first + 1)
        self.assertEqual(self.copied, ["line of the answer\nsecond"])
        self.assertFalse(self.frame.screen.selecting)
        self.assertIn("copied", self.terminal.getvalue())
        self.assertIn("LIVE DURING SELECTION", self.terminal.getvalue())

    def test_a_single_click_copies_nothing_and_a_double_click_takes_a_word(self):
        print("path ~/projects/a.py here")
        _, rows = self.frame.screen.model.window(self.frame._top(), 0)
        row = next(index for index, cells in enumerate(rows) if "path" in ScreenModel.render(cells)) + 1
        self.click("M", 0, 10, row)
        self.click("m", 0, 10, row)
        self.assertEqual(self.copied, [])
        self.click("M", 0, 10, row)
        self.click("m", 0, 10, row)
        self.assertEqual(self.copied, ["~/projects/a.py"])

    def test_a_key_ends_an_unfinished_selection(self):
        print("text")
        self.click("M", 0, 1, 1)
        self.frame.scroll(b"scroll-end")
        self.assertFalse(self.frame.screen.selecting)

    def test_wrapped_rows_are_copied_as_one_line(self):
        model = ScreenModel(5, 10)
        model.feed("0123456789abcdef\nnext")
        self.assertTrue(model.grid[1].wrapped)
        self.assertFalse(model.grid[2].wrapped)
        self.assertEqual(model.text_between(5, (0, 0), (2, 3)), "0123456789abcdef\nnext")


class ClipboardTests(unittest.TestCase):
    def test_osc_52_carries_the_text_and_wl_copy_runs_only_on_wayland(self):
        import base64
        launched = []

        class Process:
            def __init__(self, argv, **kwargs):
                launched.append(argv)
                self.stdin = io.BytesIO()
        stream = io.StringIO()
        with patch.object(terminal_frame.os.path, "isfile", return_value=True), \
                patch.object(terminal_frame.os, "access", return_value=True), \
                patch("subprocess.Popen", Process):
            self.assertTrue(terminal_frame.copy_to_clipboard("zażółć", stream, environ={"WAYLAND_DISPLAY": "wayland-1"}))
            terminal_frame.copy_to_clipboard("x", io.StringIO(), environ={})
        self.assertIn("\x1b]52;c;" + base64.b64encode("zażółć".encode()).decode() + "\x07", stream.getvalue())
        self.assertEqual(launched, [[terminal_frame.WL_COPY]])
        self.assertFalse(terminal_frame.copy_to_clipboard("", io.StringIO(), environ={}))


class EditingTests(unittest.TestCase):
    def line(self, text, cursor=None):
        line = question_input._Line(10000)
        line.insert(text)
        if cursor is not None:
            line.cursor = cursor
        return line

    def test_words_lines_and_undo(self):
        line = self.line("ala ma kota")
        line.edit("word-left")
        self.assertEqual(line.cursor, 7)
        line.edit("word-left")
        self.assertEqual(line.cursor, 4)
        line.edit("word-right")
        self.assertEqual(line.cursor, 6)
        line.edit("kill-word-back")
        self.assertEqual((line.text, line.cursor), ("ala  kota", 4))
        line.edit("undo")
        self.assertEqual(line.text, "ala ma kota")
        line.cursor = 4
        line.edit("kill-word-forward")
        self.assertEqual(line.text, "ala  kota")
        line = self.line("one\ntwo three", cursor=8)
        line.edit("home")
        self.assertEqual(line.cursor, 4)
        line.edit("end")
        self.assertEqual(line.cursor, 13)
        line.edit("text-start")
        self.assertEqual(line.cursor, 0)
        line = self.line("one\ntwo three", cursor=8)
        line.edit("kill-start")
        self.assertEqual(line.text, "one\nthree")
        line.edit("kill-end")
        self.assertEqual(line.text, "one\n")

    def test_a_paste_marker_is_cut_as_one_unit(self):
        marker = "[paste #1: 3 lines, 10 B]"
        line = self.line("see " + marker)
        line.edit("kill-word-back")
        self.assertEqual(line.text, "see ")

    def test_terminal_key_forms_decode_to_the_same_edits(self):
        cases = {b"[1;5D": "word-left", b"[1;3C": "word-right", b"[1;5H": "text-start", b"[1;5F": "text-end",
                 b"[3;5~": "kill-word-forward", b"[127;5u": "kill-word-back", b"[119;5u": "kill-word-back",
                 b"[117;5u": "kill-start", b"[107;5u": "kill-end", b"[122;5u": "undo", b"b": "word-left",
                 b"f": "word-right", b"d": "kill-word-forward", b"\x7f": "kill-word-back", b"O5D": "word-left",
                 b"[1;133D": "word-left"}
        for key, expected in cases.items():
            with self.subTest(key=key):
                self.assertEqual(question_input.edit_key(key), expected)
        for plain in (b"[D", b"[1;1D", b"[1;2D", b"[3~", b"[99;3u"):
            self.assertIsNone(question_input.edit_key(plain))
        self.assertEqual(question_input.CONTROL_EDITS[b"\x17"], "kill-word-back")

    def test_the_turn_reader_edits_with_the_same_keys(self):
        owner = terminal_input.TypeAhead.__new__(terminal_input.TypeAhead)
        owner.frame = object()
        owner.line = question_input._Line(1000)
        owner._question = owner._answer = None
        owner._sequence = None
        owner._epoch = owner._sequence_epoch = 0
        owner._paste_open = False
        owner.commands, owner.pick, owner.queue = (), 0, []
        for byte in b"ala ma kota\x1b[1;5D\x1b[1;5D\x17X\x1bfY":
            owner._feed(bytes([byte]))
        self.assertEqual(owner.line.text, "XmaY kota")                # Alt+F: to the end of the next word


class WebLimitTests(unittest.TestCase):
    def test_a_long_max_reasoning_transcript_fits(self):
        from ai_desktop import web_search_result
        self.assertGreaterEqual(web_search_result.MAX_EVENTS, 512)
        self.assertGreaterEqual(web_search_result.MAX_STREAM_BYTES, 512 * 1024)


class EdgeScrollTests(unittest.TestCase):
    """CachyOS check of candidate 147: dragging a selection past the edge must scroll, as in any terminal."""

    def setUp(self):
        self.frame, self.terminal = FrameTests.frame(self)
        self.copied = []
        self.enterContext(patch.object(terminal_frame, "copy_to_clipboard",
                                       side_effect=lambda text, stream, **_: self.copied.append(text) or True))
        self.frame.EDGE_STEP = 0.001
        for number in range(80):
            print("row %02d" % number)

    def event(self, kind, button, column, row):
        self.frame.mouse_event(b"[<%d;%d;%d%s" % (button, column, row, kind.encode()))

    def wait(self, condition):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not condition():
            time.sleep(0.005)
        return condition()

    def test_holding_at_the_top_scrolls_back_and_the_selection_grows(self):
        top = self.frame._top()
        self.event("M", 0, 1, top - 1)
        self.event("M", 32, 3, 1)                                    # dragged to the top row and held there
        self.assertTrue(self.wait(lambda: self.frame.screen.offset >= 10))
        self.event("m", 0, 3, 1)
        self.assertTrue(self.copied)
        self.assertGreater(self.copied[0].count("\n"), top)          # more rows than one screen

    def test_dragging_below_over_the_input_scrolls_forward(self):
        self.frame.scroll(b"page-up")
        self.frame.scroll(b"page-up")
        before = self.frame.screen.offset
        self.event("M", 0, 1, 2)
        self.event("M", 32, 1, self.frame._top() + 2)                # over the input rows
        self.assertTrue(self.wait(lambda: self.frame.screen.offset < before))
        self.event("m", 0, 1, self.frame._top() + 2)
        self.assertTrue(self.copied)

    def test_the_wheel_during_a_drag_keeps_the_selection(self):
        self.event("M", 0, 1, 5)
        self.event("M", 32, 4, 6)
        self.frame.scroll(b"wheel-up")
        self.assertTrue(self.frame.screen.selecting)
        self.assertEqual(self.frame.screen.offset, 3)
        self.event("m", 0, 4, 6)
        self.assertTrue(self.copied)


class TurnOutputTests(unittest.TestCase):
    """CachyOS check of candidate 153: a message printed during an answer was glued to the waiting indicator's line."""

    def test_a_message_after_the_indicator_line_starts_on_a_clean_line(self):
        stream = io.StringIO()

        class Owner:
            lock = __import__("threading").RLock()

            def band_cursor(self):
                return ""
        out = terminal_input._Output(stream, Owner())
        out.write("\r\x1b[2K⠦ Searching (2)")
        out.write("Claude · profile\n")
        out.write("next line\n")
        text = stream.getvalue()
        self.assertIn("\r\x1b[2KClaude · profile\n", text)
        self.assertNotIn("\r\x1b[2Knext line", text)               # an ordinary line after it is left alone


class MenuSettleTests(unittest.TestCase):
    def test_a_key_still_arriving_from_the_command_that_opened_the_menu_is_dropped(self):
        import pty
        import threading
        from types import SimpleNamespace
        from ai_desktop import overlay_menu
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        frame = SimpleNamespace(stream=io.StringIO(), note="", show_commands=lambda rows, pick: None,
                                draw=lambda *a, **k: None, leave=lambda: None)
        # The Enter that submitted "/model", reported again a moment later in the kitty keyboard protocol form,
        # must not choose; the user's own Down + Enter afterwards does.
        threading.Timer(0.03, os.write, (master, b"\x1b[13u")).start()
        threading.Timer(0.4, os.write, (master, b"\x1b[B\r")).start()
        with patch.object(sys, "stdin", SimpleNamespace(fileno=lambda: slave)):
            answer = overlay_menu._idle(frame, overlay_menu.Picker("/model", ["Auto", "Opus 5.5"]))
        self.assertEqual(answer, 1)
