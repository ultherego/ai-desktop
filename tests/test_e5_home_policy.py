"""Shared HOME refusal for the legacy workspace and tasks.

Only synthetic identity and descriptor metadata are used; no real HOME is
opened. Separate integration tests cover the ordinary project filesystem path.
"""
import os
import pwd
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_desktop import workspace_executor as workspace
from ai_desktop.typed_plan import PlanSession


class WorkspaceHomePolicyTests(unittest.TestCase):
    def setUp(self):
        self.home = "/home/synthetic"
        self.enterContext(patch.object(workspace.os, "geteuid", return_value=1000))
        self.passwd = self.enterContext(patch.object(pwd, "getpwuid",
            return_value=SimpleNamespace(pw_dir=self.home)))

    def test_passwd_home_refuses_before_any_filesystem_access(self):
        with patch.object(workspace.os, "open", side_effect=AssertionError("HOME opened")) as opened:
            with self.assertRaises(workspace.ExecutionDenied):
                workspace.inspect_state(self.home, (("new.txt", "SYNTHETIC"),))
            session = PlanSession()
            preview = session.preview(self.home,
                [{"action": "create_text", "path": "new.txt", "content": "SYNTHETIC"}],
                state_sha256="0" * 64)
            approval = session.authorize(confirmed_sha256=preview.sha256,
                nonce=preview.nonce, state_sha256=preview.state_sha256)
            with self.assertRaises(workspace.ExecutionDenied):
                workspace.WorkspaceExecutor(self.home).apply(session, approval)
        opened.assert_not_called()
        self.passwd.assert_called_with(1000)

    def test_environment_cannot_redirect_the_home_refusal(self):
        with patch.dict(os.environ, {"HOME": "/tmp/pretend-home", "XDG_DATA_HOME": "/tmp/data"}), \
                patch.object(workspace.os, "open", side_effect=AssertionError("HOME opened")):
            with self.assertRaises(workspace.ExecutionDenied):
                workspace._root(self.home)

    def test_passwd_trailing_separator_is_still_the_same_home(self):
        self.passwd.return_value = SimpleNamespace(pw_dir=self.home + "/")
        with patch.object(workspace.os, "open", side_effect=AssertionError("HOME opened")):
            with self.assertRaises(workspace.ExecutionDenied):
                workspace._root(self.home)

    def test_unknown_passwd_identity_refuses_before_open(self):
        self.passwd.side_effect = KeyError("synthetic missing UID")
        with patch.object(workspace.os, "open", side_effect=AssertionError("unknown identity opened")):
            with self.assertRaises(workspace.ExecutionDenied):
                workspace._root(self.home + "/project")


if __name__ == "__main__":
    unittest.main()
