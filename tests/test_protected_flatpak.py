"""Browser and mail profiles of Flatpak apps are protected by default like their native counterparts."""
import unittest
from unittest.mock import patch

from ai_desktop import permissions as p


def load(raw):
    real = p._read
    with patch.object(p, "_read", side_effect=lambda path, uid, limit, **kw:
                      (raw.encode(), ("synthetic",)) if kw.get("policy") else real(path, uid, limit, **kw)):
        return p.load_policy()


class FlatpakProtectionTests(unittest.TestCase):
    def test_flatpak_browser_profiles_stay_out_under_a_broad_rule(self):
        policy = load('version = 2\n[files]\nread = ["~/**"]\ndeny = []\n')
        home = policy.home
        for path in (".var/app/org.mozilla.firefox/.mozilla/firefox/x.default/logins.json",
                     ".var/app/com.google.Chrome/config/google-chrome/Default/Cookies",
                     ".var/app/org.mozilla.Thunderbird/.thunderbird/x/key4.db"):
            with self.subTest(path=path):
                self.assertFalse(policy.allows(home + "/" + path))
        self.assertTrue(policy.allows(home + "/.var/app/org.example.Notes/config/notes.txt"))   # other apps: readable


if __name__ == "__main__":
    unittest.main()
