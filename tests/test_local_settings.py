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


class WeatherCitySettingTests(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(tempfile.TemporaryDirectory(dir=pwd.getpwuid(os.geteuid()).pw_dir))
        self.path = Path(self.home) / ".config/ai-desktop/weather-city"

    def test_a_city_in_any_language_is_remembered_privately_and_forgotten(self):
        self.assertIsNone(local_settings.weather_city(self.home))
        local_settings.set_weather_city("  Example Città  ", self.home)
        self.assertEqual(local_settings.weather_city(self.home), "Example Città")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "Example Città\n")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        local_settings.set_weather_city(None, self.home)
        self.assertFalse(self.path.exists())
        local_settings.set_weather_city(None, self.home)                          # forgetting twice is fine

    def test_an_invalid_name_is_refused_and_a_bad_file_reads_as_none(self):
        from ai_desktop import weather_context
        for bad in ("", "x", "a\x1b[2Jb"):
            with self.subTest(bad=bad), self.assertRaises(weather_context.WeatherError):
                local_settings.set_weather_city(bad, self.home)
        self.path.parent.mkdir(parents=True)
        for content in (b"\xff\xfe", b"x" * 200, b"\n"):
            self.path.write_bytes(content)
            self.assertIsNone(local_settings.weather_city(self.home))
        self.path.unlink()
        target = Path(self.home) / "elsewhere"
        target.write_text("Example City\n")
        self.path.symlink_to(target)
        self.assertIsNone(local_settings.weather_city(self.home))               # a link is never followed

    def test_settings_weather_city_shows_sets_and_forgets(self):
        calls = []
        with patch.object(local_settings, "set_weather_city", side_effect=calls.append), \
                patch.object(local_settings, "weather_city", return_value="Example City"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(management_cli.main(["weather-city", "Example", "City"], prog="ai-desktop settings",
                                                 provider_neutral=True), 0)
            self.assertEqual(management_cli.main(["weather-city", "off"], prog="ai-desktop settings",
                                                 provider_neutral=True), 0)
        self.assertEqual(calls, ["Example City", None])
        self.assertIn("Weather city: Example City", out.getvalue())
        with patch.object(local_settings, "weather_city", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(management_cli.main(["weather-city"], prog="ai-desktop settings", provider_neutral=True), 0)
        self.assertIn("Weather city: none", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(management_cli.main(["weather-city", "x"], prog="ai-desktop settings", provider_neutral=True), 2)
        self.assertIn("Invalid city name.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
