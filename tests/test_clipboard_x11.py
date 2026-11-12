"""Copying reaches the desktop clipboard on X11 as well as on Wayland, with whichever tool is installed."""
import io
import unittest
from unittest.mock import patch

from ai_desktop import terminal_frame


class ClipboardTests(unittest.TestCase):
    def copy(self, environ, present):
        launched = []

        class Process:
            def __init__(self, argv, **kwargs):
                launched.append(argv)
                self.stdin = io.BytesIO()

        with patch.object(terminal_frame.os.path, "isfile", side_effect=lambda path: path in present), \
                patch.object(terminal_frame.os, "access", side_effect=lambda path, mode: path in present), \
                patch("subprocess.Popen", Process):
            self.assertTrue(terminal_frame.copy_to_clipboard("text", io.StringIO(), environ=environ))
        return launched

    def test_x11_uses_xclip_or_xsel_and_wayland_keeps_wl_copy(self):
        self.assertEqual(self.copy({"DISPLAY": ":0"}, {"/usr/bin/xclip"}), [["/usr/bin/xclip", "-selection", "clipboard"]])
        self.assertEqual(self.copy({"DISPLAY": ":0"}, {"/usr/bin/xsel"}), [["/usr/bin/xsel", "--clipboard", "--input"]])
        self.assertEqual(self.copy({"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}, {terminal_frame.WL_COPY}),
                         [[terminal_frame.WL_COPY]])
        self.assertEqual(self.copy({"DISPLAY": ":0"}, set()), [])                 # nothing installed: the terminal only
        self.assertEqual(self.copy({}, {"/usr/bin/xclip"}), [])                   # no display: nothing launched


if __name__ == "__main__":
    unittest.main()
