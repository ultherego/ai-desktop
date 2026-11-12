"""Accounts and homes are discovered on the user's system, never assumed to be /home and UID 1000."""
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

from ai_desktop import command_ops, host_accounts as h, permissions


def account(name, uid, home):
    return pwd.struct_passwd((name, "x", uid, uid, "", home, "/bin/sh"))


class UidRangeTests(unittest.TestCase):
    def defs(self, text, *, mode=0o644):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = directory / "login.defs"
        path.write_text(text)
        path.chmod(mode)
        return str(path)

    def test_the_range_comes_from_login_defs_only_when_it_is_root_owned(self):
        path = self.defs("# comment\nUID_MIN   2000\nUID_MAX 70000 # end\n")
        # A file owned by the test user is not trusted: the defaults stay.
        self.assertEqual(h.uid_range(path), (h.DEFAULT_UID_MIN, h.DEFAULT_UID_MAX))
        real = os.fstat
        with patch.object(h.os, "fstat", side_effect=lambda fd: os.stat_result(
                (*real(fd)[:4], 0, *real(fd)[5:]))):
            self.assertEqual(h.uid_range(path), (2000, 70000))
        self.assertEqual(h.uid_range("/nonexistent/login.defs"), (1000, 60000))

    def test_a_broken_range_falls_back(self):
        path = self.defs("UID_MIN 9000\nUID_MAX 10\n")
        real = os.fstat
        with patch.object(h.os, "fstat", side_effect=lambda fd: os.stat_result((*real(fd)[:4], 0, *real(fd)[5:]))):
            self.assertEqual(h.uid_range(path), (1000, 60000))


class RegularUidTests(unittest.TestCase):
    def test_the_lower_bound_is_the_systems_and_homed_accounts_above_uid_max_stay_regular(self):
        with patch.object(h, "uid_range", return_value=(500, 60000)):
            self.assertTrue(h.is_regular_uid(500))
            self.assertTrue(h.is_regular_uid(60100))               # systemd-homed range lies above UID_MAX
            self.assertFalse(h.is_regular_uid(499))
            self.assertFalse(h.is_regular_uid(2**32 - 1))
            self.assertFalse(h.is_regular_uid(True))
            self.assertFalse(h.is_regular_uid("1000"))


class HomeRootTests(unittest.TestCase):
    def test_homes_outside_home_are_found_and_nested_ones_collapse(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (base / "var-home/alice").mkdir(parents=True)
        (base / "srv/bob").mkdir(parents=True)
        entries = [account("alice", 1001, str(base / "var-home/alice")), account("bob", 1002, str(base / "srv/bob")),
                   account("daemon", 2, "/"), account("svc", 980, str(base / "srv")),          # system accounts: no
                   account("ghost", 1003, str(base / "missing"))]                               # missing: skipped
        with patch.object(h, "uid_range", return_value=(1000, 60000)):
            roots = h.home_roots(entries)
        self.assertIn(str(base / "var-home/alice"), roots)
        self.assertIn(str(base / "srv/bob"), roots)
        self.assertNotIn("/", roots)
        self.assertNotIn(str(base / "srv"), roots)
        self.assertNotIn(str(base / "missing"), roots)
        with patch.object(h, "uid_range", return_value=(1000, 60000)):
            nested = h.home_roots([account("a", 1001, str(base / "srv")), account("b", 1002, str(base / "srv/bob"))])
        self.assertIn(str(base / "srv"), nested)
        self.assertNotIn(str(base / "srv/bob"), nested)                  # covered by its parent

    def test_the_read_sandbox_hides_every_discovered_home(self):
        base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        elsewhere = str(base / "var-home")
        os.mkdir(elsewhere)
        with patch("ai_desktop.host_accounts.home_roots", return_value=("/home", elsewhere)):
            command = command_ops.sandbox_command("/usr/bin/uname", ["uname"])
            if command is None:
                self.skipTest("bubblewrap is not available")
            hidden = [command[index + 1] for index, item in enumerate(command) if item == "--tmpfs"]
            self.assertIn(elsewhere, hidden)
            self.assertIn("/home", hidden)
            self.assertTrue(permissions._foreign_home(elsewhere + "/other/.ssh/id_ed25519", "/home/me"))
            self.assertFalse(permissions._foreign_home("/home/me/notes.txt", "/home/me"))


if __name__ == "__main__":
    unittest.main()
