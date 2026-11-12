"""Weather is shown in the user's own units and time zone, discovered from the locale, never fixed."""
import unittest

from ai_desktop import weather_context as w

CURRENT = {"temperature_c": 10.0, "apparent_temperature_c": 8.0, "precipitation_mm": 2.54, "wind_kmh": 16.09344}


class WeatherUnitTests(unittest.TestCase):
    def test_the_measurement_system_follows_the_locale_territory(self):
        for environ, expected in (({"LANG": "en_US.UTF-8"}, "imperial"), ({"LANG": "de_DE.UTF-8"}, "metric"),
                                  ({"LC_MEASUREMENT": "en_US.UTF-8", "LANG": "de_DE.UTF-8"}, "imperial"),
                                  ({"LC_ALL": "fr_FR.UTF-8", "LC_MEASUREMENT": "en_US.UTF-8"}, "metric"),
                                  ({"LANG": "C.UTF-8"}, "metric"), ({}, "metric")):
            with self.subTest(environ=environ):
                self.assertEqual(w.measurement_system(environ), expected)

    def test_the_language_list_of_gettext_comes_first(self):
        self.assertEqual(w.language({"LANGUAGE": "de:en", "LANG": "fr_FR.UTF-8"}), "de")
        self.assertEqual(w.language({"LANGUAGE": "de", "LC_ALL": "C.UTF-8"}), "en")
        self.assertEqual(w.language({"LANGUAGE": "", "LANG": "fr_FR.UTF-8"}), "fr")

    def test_values_are_converted_only_for_imperial_readers(self):
        self.assertEqual(w.shown(CURRENT, "metric"), ("10.0 °C", "8.0 °C", "2.54 mm", "16.09344 km/h"))
        self.assertEqual(w.shown(CURRENT, "imperial"), ("50.0 °F", "46.4 °F", "0.10 in", "10.0 mph"))

    def test_the_data_time_is_local_with_the_original_utc_kept(self):
        shown = w.local_time("2026-01-15T12:00:00Z")
        self.assertTrue(shown.endswith("(2026-01-15T12:00:00Z)"), shown)
        self.assertEqual(w.local_time("not a time"), "not a time")


if __name__ == "__main__":
    unittest.main()
