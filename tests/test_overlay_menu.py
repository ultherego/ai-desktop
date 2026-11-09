"""`/` menus of a conversation in the list above the input band: the owner's choice of where they belong."""
import io
import os
import pty
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_desktop import overlay_menu, terminal_input
from ai_desktop.terminal_menu import MenuInputError


class PickerTests(unittest.TestCase):
    def test_keys_move_tick_select_and_cancel(self):
        picker = overlay_menu.Picker("/model", ["default", "opus", "sonnet"], default=1)
        self.assertEqual(picker.rows(), (("default", ""), ("opus", ""), ("sonnet", "")))
        self.assertEqual(picker.key("down"), (False, None))
        self.assertEqual(picker.key("down"), (False, None))
        self.assertEqual(picker.pick, 0)                                   # wraps around
        self.assertEqual(picker.key("up"), (False, None))
        self.assertEqual(picker.key("enter"), (True, 2))
        self.assertEqual(picker.key("cancel"), (True, None))
        self.assertIn("/model", picker.note())
        marks = {0}
        ticks = overlay_menu.Picker("Skills", ["a", "b", "Apply"], marks=marks, markable=2)
        ticks.key("down")
        ticks.key("space")
        self.assertEqual(marks, {0, 1})
        self.assertEqual(ticks.rows()[:2], (("[x] a", ""), ("[x] b", "")))
        ticks.key("down")
        ticks.key("space")                                                 # not markable: nothing happens
        self.assertEqual(marks, {0, 1})
        self.assertIn("space ticks", ticks.note())

    def test_untrusted_or_malformed_menus_are_refused(self):
        for title, options, kwargs in (("t", [], {}), ("t", ["x"] * 13, {}), ("\x1b[2J", ["x"], {}),
                                       ("t", ["a\nb"], {}), ("t", ["a"], {"default": 1}),
                                       ("t", ["a"], {"marks": [0], "markable": 1})):
            with self.subTest(title=title, options=options), self.assertRaises(MenuInputError):
                overlay_menu.Picker(title, options, **kwargs)

    def test_key_names(self):
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        for byte, key in ((b"\r", "enter"), (b"\n", "enter"), (b" ", "space"), (b"\x03", "cancel"),
                          (b"\x04", "cancel"), (b"x", "ignore")):
            self.assertEqual(overlay_menu.key_of(read, byte), key)
        os.write(write, b"[B")
        self.assertEqual(overlay_menu.key_of(read, b"\x1b"), "down")
        self.assertEqual(overlay_menu.key_of(read, b"\x1b"), "cancel")     # a lone Esc
        os.write(write, b"\x01")
        self.assertEqual(overlay_menu.key_of(read, b"\x1b"), "ignore")     # malformed: ignored, never an answer


class ChooseRoutingTests(unittest.TestCase):
    def test_without_a_conversation_frame_the_inline_menu_is_used(self):
        with patch.object(overlay_menu.terminal_frame, "current", return_value=None), \
                patch.object(overlay_menu.terminal_menu, "choose", return_value=1) as inline:
            self.assertEqual(overlay_menu.choose("t", ["a", "b"]), 1)
        inline.assert_called_once()

    def test_during_a_turn_only_the_live_reader_may_open_a_menu(self):
        owner = SimpleNamespace(_thread=object())
        with patch.object(overlay_menu.terminal_frame, "current", return_value=object()), \
                patch.object(terminal_input, "current", return_value=owner), self.assertRaises(MenuInputError):
            overlay_menu.choose("t", ["a"])


def live_owner(fd):
    with patch.object(terminal_input.termios, "tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0] * 32]):
        owner = terminal_input.TypeAhead(SimpleNamespace(busy=None, note="", show_commands=lambda *a: None,
                                                         draw=lambda *a, **k: None, cursor_at=(1, 1)),
                                         io.StringIO())
    owner.fd = fd
    owner._sequence = None
    return owner


class LiveMenuTests(unittest.TestCase):
    def setUp(self):
        self.read, self.write = os.pipe()
        self.addCleanup(os.close, self.read)
        self.addCleanup(os.close, self.write)

    def test_the_reader_drives_the_menu_and_draws_it_above_the_band(self):
        owner = live_owner(self.read)
        shown = []
        owner.frame.show_commands = lambda rows, pick: shown.append((rows, pick))
        os.write(self.write, b"\x1b[B\r")
        self.assertEqual(owner.run_picker(overlay_menu.Picker("/model", ["a", "b"])), 1)
        self.assertIn(((("a", ""), ("b", "")), 1), shown)               # in the list above the band
        self.assertIsNone(owner._picker)

    def test_sudo_takes_the_keyboard_from_an_open_menu_and_gives_it_back(self):
        owner = live_owner(self.read)
        result = []
        menu = threading.Thread(target=lambda: result.append(owner.run_picker(overlay_menu.Picker("t", ["a", "b"]))),
                                daemon=True)
        menu.start()
        time.sleep(0.1)
        owner._pause.set()
        self.assertTrue(owner._paused.wait(2), "the menu did not yield to the lease")
        os.write(self.write, b"\x1b[B")                                  # typed for sudo: the menu must not read it
        time.sleep(0.2)
        os.read(self.read, 3)                                             # sudo consumed it
        owner._pause.clear()
        os.write(self.write, b"\r")
        menu.join(2)
        self.assertEqual(result, [0])                                     # the earlier keys never moved the menu

    def test_a_consent_question_gets_the_keys_while_the_menu_waits(self):
        owner = live_owner(self.read)
        result = []
        menu = threading.Thread(target=lambda: result.append(owner.run_picker(overlay_menu.Picker("t", ["a", "b"]))),
                                daemon=True)
        menu.start()
        time.sleep(0.1)
        with patch.object(terminal_input.termios, "tcflush"):
            owner._open_question("Run it? [y/n]:", 64)
        os.write(self.write, b"y\r")
        self.assertTrue(owner._answered.wait(2))
        self.assertEqual(owner._answer.text, "y")
        owner._question = None
        os.write(self.write, b"\x1b[B\r")
        menu.join(2)
        self.assertEqual(result, [1])


class ReaderContextTests(unittest.TestCase):
    def test_the_reader_sees_the_turns_selected_service(self):
        """Review of 132: a menu opened during a Codex answer offered Claude's choices (empty context in the reader)."""
        import contextvars
        selected = contextvars.ContextVar("selected_for_test", default="claude")
        selected.set("codex")
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        owner = live_owner(read)
        seen = []
        owner._run = lambda: seen.append(selected.get())
        saved = terminal_input.sys.stdout
        self.addCleanup(setattr, terminal_input.sys, "stdout", saved)  # start() routes stdout through the band
        with patch.object(owner, "_raw"), patch.object(owner, "draw"):
            owner.start()
            owner._thread.join(2)
        self.assertEqual(seen, ["codex"])


class IdleMenuTests(unittest.TestCase):
    def test_between_turns_the_menu_is_drawn_above_the_band_and_the_terminal_mode_is_restored(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        before = overlay_menu.termios.tcgetattr(slave)
        calls = []
        frame = SimpleNamespace(stream=io.StringIO(), note="",
                                show_commands=lambda rows, pick: calls.append(("list", rows, pick)),
                                draw=lambda *a, **k: calls.append(("draw",)), leave=lambda: calls.append(("leave",)))
        os.write(master, b"\x1b[B\r")
        time.sleep(0.05)
        with patch.object(overlay_menu.sys if hasattr(overlay_menu, "sys") else __import__("sys"), "stdin",
                          SimpleNamespace(fileno=lambda: slave)), \
                patch.object(overlay_menu.termios, "tcflush"):
            answer = overlay_menu._idle(frame, overlay_menu.Picker("/effort", ["low", "high"]))
        self.assertEqual(answer, 1)
        self.assertIn(("list", (("low", ""), ("high", "")), 0), calls)
        self.assertEqual(calls[-2:], [("list", (), 0), ("leave",)])
        self.assertEqual(overlay_menu.termios.tcgetattr(slave), before)
        self.assertTrue(overlay_menu.termios.tcgetattr(slave)[1] & overlay_menu.termios.OPOST)

    def test_ctrl_c_or_ctrl_d_closes_the_menu_and_never_the_conversation(self):
        # Review of 2026-11-09: question_input._byte raised KeyboardInterrupt on Ctrl+C, which ended the conversation.
        for key in (b"\x03", b"\x04"):
            master, slave = pty.openpty()
            self.addCleanup(os.close, master)
            self.addCleanup(os.close, slave)
            frame = SimpleNamespace(stream=io.StringIO(), note="", show_commands=lambda rows, pick: None,
                                    draw=lambda *a, **k: None, leave=lambda: None)
            # Sent once the menu reads in raw mode: before that the terminal driver would turn Ctrl+C into a signal.
            import threading
            threading.Timer(0.3, os.write, (master, key)).start()
            with self.subTest(key=key), patch.object(__import__("sys"), "stdin", SimpleNamespace(fileno=lambda: slave)), \
                    patch.object(overlay_menu.termios, "tcflush"):
                self.assertIsNone(overlay_menu._idle(frame, overlay_menu.Picker("/effort", ["low", "high"])))


if __name__ == "__main__":
    unittest.main()
