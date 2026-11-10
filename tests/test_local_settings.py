"""S1: the persistent choice whether conversations save themselves (~/.config/ai-desktop/save)."""
import contextlib
import io
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

from ai_desktop import local_settings, management_cli


class SavingSettingTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory(dir=pwd.getpwuid(os.geteuid()).pw_dir))

    def test_saving_is_on_by_default_and_the_choice_persists_privately(self):
        self.assertTrue(local_settings.saving(self.home))
        local_settings.set_saving(False, self.home)
        self.assertFalse(local_settings.saving(self.home))
        path = Path(self.home) / ".config/ai-desktop/save"
        self.assertEqual(path.read_text(), "off\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        local_settings.set_saving(True, self.home)
        self.assertTrue(local_settings.saving(self.home))
        self.assertEqual([p.name for p in path.parent.iterdir()], ["save"])      # no temporary file left behind

    def test_an_unknown_value_or_a_link_falls_back_to_the_default(self):
        directory = Path(self.home) / ".config/ai-desktop"
        directory.mkdir(parents=True)
        (directory / "save").write_text("maybe\n")
        self.assertTrue(local_settings.saving(self.home))
        (directory / "save").unlink()
        target = Path(self.home) / "elsewhere"
        target.write_text("off\n")
        (directory / "save").symlink_to(target)
        self.assertTrue(local_settings.saving(self.home))                       # a link is never followed

    def test_settings_save_shows_and_changes_the_choice(self):
        calls = []
        out = io.StringIO()
        with patch.object(local_settings, "set_saving", side_effect=calls.append), \
                patch.object(local_settings, "saving", return_value=False), contextlib.redirect_stdout(out):
            self.assertEqual(management_cli.main(["save", "off"], prog="ai-desktop settings", provider_neutral=True), 0)
        self.assertEqual(calls, [False])
        self.assertIn("Saving conversations: off", out.getvalue())
        out = io.StringIO()
        with patch.object(local_settings, "saving", return_value=True), contextlib.redirect_stdout(out):
            self.assertEqual(management_cli.main(["save"], prog="ai-desktop settings", provider_neutral=True), 0)
        self.assertIn("Saving conversations: on", out.getvalue())


if __name__ == "__main__":
    unittest.main()
