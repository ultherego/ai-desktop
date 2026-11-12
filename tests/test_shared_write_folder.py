"""A write scope under a folder other accounts may write to is refused with the folder and the command named."""
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

from ai_desktop import ask_edit, permissions


class SharedFolderTests(unittest.TestCase):
    def setUp(self):
        home = pwd.getpwuid(os.getuid()).pw_dir
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="ai-shared-", dir=home)))
        self.shared = self.base / "shared"
        self.inner = self.shared / "games"
        self.inner.mkdir(parents=True)
        self.shared.chmod(0o777)
        self.addCleanup(self.shared.chmod, 0o755)
        self.enterContext(patch.object(permissions.Policy, "recheck"))      # no policy file here; the folders are real

    def policy(self, root):
        home = pwd.getpwuid(os.getuid()).pw_dir
        rule = permissions._rule(str(root) + "/**", home, version=2)
        return permissions.Policy(uid=os.getuid(), home=home, _raw=b"", _identity=(), _read=(rule,), _deny=(),
                                  _write=(rule,), _version=2)

    def test_the_shared_folder_on_the_way_is_found(self):
        found = permissions.writable_by_others(str(self.inner), os.getuid())
        self.assertIn((str(self.shared), os.getuid(), 0o777), found)
        self.assertNotIn(str(self.inner), [folder for folder, _, _ in found])
        self.shared.chmod(0o755)
        self.assertNotIn(str(self.shared), [folder for folder, _, _ in permissions.writable_by_others(str(self.inner),
                                                                                                         os.getuid())])

    def test_a_write_scope_under_it_is_refused_with_the_folder_and_command(self):
        with self.assertRaises(ask_edit.EditDenied) as caught:
            ask_edit.write_root(self.policy(self.inner), 0)
        hint = caught.exception.hint
        self.assertIn(str(self.shared) + " (drwxrwxrwx)", hint)
        self.assertIn("chmod go-w " + str(self.shared), hint)
        self.assertNotIn("sudo chmod go-w " + str(self.shared), hint)
        self.shared.chmod(0o755)
        self.assertEqual(ask_edit.write_root(self.policy(self.inner), 0), str(self.inner))

    def test_the_hint_names_root_folders_with_sudo_and_quotes_spaces(self):
        hint = ask_edit.shared_folders_hint([("/srv/shared data", 0, 0o777), ("/srv/shared data/x", 1000, 0o775)], 1000)
        self.assertIn("sudo chmod go-w '/srv/shared data'", hint)
        self.assertIn("; chmod go-w '/srv/shared data/x'", hint)
        self.assertIn("(drwxrwxr-x)", hint)
        self.assertIn("the owner can run chmod go-w /srv/other",
                      ask_edit.shared_folders_hint([("/srv/other", 1234, 0o777)], 1000))
        self.assertIn("belong to you", ask_edit.shared_folders_hint([], 1000))


if __name__ == "__main__":
    unittest.main()
