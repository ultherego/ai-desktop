"""Service-account identity check used by the analysis sandbox and Codex administration.

Synthetic passwd entries and credentials only; no host account, OAuth or socket.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_desktop import e2_account, e2_socket_activation as activation


def _account(**changes):
    values = dict(pw_name=e2_account.ACCOUNT, pw_uid=970, pw_gid=970,
                  pw_dir=e2_account.HOME, pw_shell="/usr/bin/nologin")
    values.update(changes)
    return SimpleNamespace(**values)


class WorkerIdentityTests(unittest.TestCase):
    def identity(self, account=None, *, uid=970, gid=970, groups=(970,)):
        with (patch.object(activation.pwd, "getpwnam", return_value=account or _account()) as lookup,
              patch.object(activation.os, "getuid", return_value=uid),
              patch.object(activation.os, "geteuid", return_value=uid),
              patch.object(activation.os, "getgid", return_value=gid),
              patch.object(activation.os, "getegid", return_value=gid),
              patch.object(activation.os, "getgroups", return_value=list(groups)),
              patch.object(activation.os, "open", side_effect=AssertionError("HOME opened"))):
            result = activation._worker_identity()
        lookup.assert_called_once_with(e2_account.ACCOUNT)
        return result

    def test_exact_service_account_and_credentials_pass(self):
        self.assertTrue(self.identity())
        self.assertTrue(self.identity(groups=()))

    def test_another_identity_home_shell_or_group_is_refused(self):
        for account in (_account(pw_name="other"), _account(pw_uid=0), _account(pw_gid=0),
                        _account(pw_dir="/home/synthetic"), _account(pw_shell="/bin/sh")):
            with self.subTest(account=account):
                self.assertFalse(self.identity(account))
        self.assertFalse(self.identity(uid=1000))
        self.assertFalse(self.identity(gid=1000))
        self.assertFalse(self.identity(groups=(970, 10)))

    def test_missing_account_raises_for_the_caller_to_refuse(self):
        with patch.object(activation.pwd, "getpwnam", side_effect=KeyError("synthetic")):
            with self.assertRaises(KeyError):
                activation._worker_identity()

    def test_removed_one_shot_handoff_stays_gone(self):
        for name in ("send_once", "receive_once", "worker_main", "socket_path", "_confinement_ready"):
            self.assertFalse(hasattr(activation, name), name)


if __name__ == "__main__":
    unittest.main()
