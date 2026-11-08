"""`ai-desktop widget`: the DankBar widget is linked into DMS only where DMS is installed, never over another plugin."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ai_desktop import desktop_widget


class WidgetLinkTests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.packaged = folder / "share/AiDesktop"
        self.packaged.mkdir(parents=True)
        self.config = folder / "config"
        self.enterContext(patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config)}))
        self.enterContext(patch.object(desktop_widget, "PACKAGED", str(self.packaged)))
        self.target = self.config / "dms/plugins/AiDesktop"

    def test_without_dms_nothing_is_created(self):
        with patch.object(desktop_widget, "dms_present", return_value=False):
            self.assertEqual(desktop_widget.install(), "no-dms")
        self.assertFalse(self.config.exists())

    def test_with_dms_the_widget_is_linked_once(self):
        with patch.object(desktop_widget, "dms_present", return_value=True):
            self.assertEqual(desktop_widget.status(), "absent")
            self.assertEqual(desktop_widget.install(), "installed")
            self.assertEqual(os.readlink(self.target), str(self.packaged))
            self.assertEqual(desktop_widget.install(), "linked")
            self.assertEqual(desktop_widget.remove(), "removed")
            self.assertFalse(os.path.lexists(self.target))

    def test_another_plugin_of_the_same_name_is_never_replaced_or_removed(self):
        self.target.mkdir(parents=True)
        (self.target / "Widget.qml").write_text("SYNTHETIC user plugin")
        with patch.object(desktop_widget, "dms_present", return_value=True):
            self.assertEqual(desktop_widget.install(), "occupied")
            self.assertEqual(desktop_widget.remove(), "not-linked")
        self.assertEqual((self.target / "Widget.qml").read_text(), "SYNTHETIC user plugin")

    def test_dms_is_detected_by_its_command_in_usr_bin_only(self):
        with patch.object(desktop_widget.shutil, "which", return_value=None) as which:
            self.assertFalse(desktop_widget.dms_present())
        which.assert_called_once_with("dms", path="/usr/bin")

    def test_the_command_reports_without_dms_and_never_runs_as_root(self):
        output = io.StringIO()
        with patch.object(desktop_widget, "dms_present", return_value=False), redirect_stdout(output):
            self.assertEqual(desktop_widget.main(["install"]), 0)
        self.assertIn("DMS is not installed", output.getvalue())
        with patch.object(desktop_widget.os, "geteuid", return_value=0), redirect_stdout(io.StringIO()):
            self.assertEqual(desktop_widget.main(["install"]), 2)


if __name__ == "__main__":
    unittest.main()
