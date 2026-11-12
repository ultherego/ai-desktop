"""The distribution comes from os-release; CachyOS procedures only where it says CachyOS."""
import unittest
from unittest.mock import patch

from ai_desktop import agent_profiles, distro


class DistroTests(unittest.TestCase):
    def test_os_release_values_and_family(self):
        values = distro._parse('NAME="Example Linux"\nID=examplelinux\nID_LIKE="arch"\nPRETTY_NAME=\'Example\'\n# x\n')
        self.assertEqual(values["PRETTY_NAME"], "Example")
        self.assertEqual(distro.ids(values), ("examplelinux", "arch"))
        self.assertFalse(distro.is_cachyos(values))
        self.assertTrue(distro.is_cachyos({"ID": "cachyos", "ID_LIKE": "arch"}))
        self.assertFalse(distro.is_cachyos({}))
        self.assertEqual(distro.read(("/nonexistent/os-release",)), {})

    def test_the_cachyos_skill_is_built_in_only_on_cachyos(self):
        profile = agent_profiles.Profile(skills=())
        for cachy, expected in ((True, True), (False, False)):
            with self.subTest(cachyos=cachy), patch.object(distro, "is_cachyos", return_value=cachy):
                skills = agent_profiles.with_presets("diagnostic", profile).skills
                self.assertEqual("cachyos" in skills, expected)
                self.assertIn("arch", skills)


if __name__ == "__main__":
    unittest.main()
