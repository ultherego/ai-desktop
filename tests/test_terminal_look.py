"""The conversation head: a frame in a colour terminal, the plain one-line label elsewhere."""
import io
import os
import unittest
from unittest.mock import patch

from ai_desktop import run_controls

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


if __name__ == "__main__":
    unittest.main()
