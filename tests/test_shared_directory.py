"""The directory the user started the program in reaches the model only inside a readable, unprotected scope."""
import os
import unittest
from unittest.mock import patch

from ai_desktop import assistant_contract as contract, assistant_flow as flow, permissions

HOME = "/home/synthetic"


def policy(read=("~/**",), deny=(), version=2):
    return permissions.Policy(uid=os.getuid(), home=HOME, _raw=b"", _identity=(),
                              _read=tuple(permissions._rule(rule, HOME, version=version) for rule in read),
                              _deny=tuple(permissions._rule(rule, HOME, version=version) for rule in deny),
                              _version=version, _protect=True)


class SharedDirectoryTests(unittest.TestCase):
    def shared(self, cwd, selected=None, *, real=None):
        with patch.object(flow.os, "getcwd", return_value=cwd), \
                patch.object(flow.os.path, "realpath", side_effect=lambda path, **_: real or path):
            return flow.shared_directory(policy() if selected is None else selected)

    def test_a_directory_inside_a_read_scope_is_written_like_the_scope_patterns(self):
        self.assertEqual(self.shared(HOME + "/projects/example"), "~/projects/example")
        self.assertEqual(self.shared(HOME), "~")
        self.assertEqual(self.shared("/srv/data/example", policy(read=("~/**", "/srv/data/**"))), "/srv/data/example")

    def test_it_stays_local_outside_a_scope_under_a_deny_rule_or_default_protection(self):
        self.assertIsNone(self.shared("/srv/data/example"))
        self.assertIsNone(self.shared(HOME + "/private/notes", policy(deny=("~/private/",))))
        self.assertIsNone(self.shared(HOME + "/.ssh"))
        self.assertIsNone(self.shared(HOME + "/.config/gh/hosts"))
        self.assertIsNone(self.shared(HOME + "/projects/example", policy(read=("~/projects/example/*",))))   # not recursive
        self.assertIsNone(self.shared(HOME + "/projects/example", real="/elsewhere"))     # reached through a link
        self.assertIsNone(flow.shared_directory(None))
        self.assertIsNone(self.shared(HOME + "/projects/a\u200db"))                         # the contract refuses it
        self.assertIsNone(self.shared(HOME + "/" + "/".join(["x" * 60] * 10)))                # over 512 bytes
        with patch.object(flow.os, "getcwd", side_effect=FileNotFoundError()):
            self.assertIsNone(flow.shared_directory(policy()))

    def test_the_contract_accepts_only_a_plain_directory_with_a_file_scope(self):
        base = {"files": [{"id": "f0", "pattern": "~/"}], "systemd": [], "weather": False}
        for cwd in ("~", "~/projects/example", "/srv/data/example", "~/Folder z spacją"):
            with self.subTest(cwd=cwd):
                self.assertEqual(contract.validate_access(dict(base, cwd=cwd))["cwd"], cwd)
        for cwd in ("", "projects", "~user/x", "~/a/../b", "~/./a", "/a//b", "~/a\nb", 1, None):
            with self.subTest(cwd=cwd), self.assertRaises(ValueError):
                contract.validate_access(dict(base, cwd=cwd))
        with self.assertRaises(ValueError):
            contract.validate_access({"files": [], "systemd": [], "weather": False, "cwd": "~"})

    def test_capture_access_adds_it_only_when_shared(self):
        selected = policy()
        with patch.object(flow.permissions, "load_policy", return_value=selected), \
                patch.object(flow, "_usable_writes", side_effect=lambda p, access: access), \
                patch.object(flow.os, "getcwd", return_value=HOME + "/projects/example"), \
                patch.object(flow.os.path, "realpath", side_effect=lambda path, **_: path), \
                patch("ai_desktop.execution.current", return_value=None):
            self.assertEqual(flow.capture_access().access["cwd"], "~/projects/example")
        with patch.object(flow.permissions, "load_policy", return_value=selected), \
                patch.object(flow, "_usable_writes", side_effect=lambda p, access: access), \
                patch.object(flow.os, "getcwd", return_value="/srv/data"), \
                patch("ai_desktop.execution.current", return_value=None):
            self.assertNotIn("cwd", flow.capture_access().access)


if __name__ == "__main__":
    unittest.main()
