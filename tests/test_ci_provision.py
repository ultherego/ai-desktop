"""The privileged CI provisioning driver must be unreachable in ordinary host tests."""
import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

import ci_provision as ci


class ProvisionCIDriverTests(unittest.TestCase):
    def test_no_flag_never_inspects_or_changes_host(self):
        with (patch.object(ci, "_require_ci") as preflight,
              patch.object(ci, "_run") as command,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(ci.main([]), 2)
            preflight.assert_not_called()
            command.assert_not_called()

    def test_non_ci_and_root_refuse_before_commands(self):
        for env, uid in (({}, 1000), ({"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux"}, 0)):
            with (self.subTest(uid=uid), patch.dict(ci.os.environ, env, clear=True),
                  patch.object(ci.os, "geteuid", return_value=uid),
                  patch.object(ci, "_run") as command,
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(ci.main(["--allow-disposable-systemd-test"]), 2)
                command.assert_not_called()

    def test_install_or_verification_failure_never_reports_success(self):
        for failing in ("install", "verify"):
            with (self.subTest(failing=failing),
                  patch.object(ci, "_require_ci", return_value=1000),
                  patch.object(ci, "_run", side_effect=subprocess.CalledProcessError(1, "x")
                               if failing == "install" else None),
                  patch.object(ci.installed_system, "_installed_matches",
                               side_effect=ValueError("PRIVATE") if failing == "verify" else None),
                  contextlib.redirect_stdout(io.StringIO()) as output):
                self.assertEqual(ci.main(["--allow-disposable-systemd-test"]), 2)
            self.assertEqual(output.getvalue(), "CI PROVISION: FAIL stage=" + failing + "\n")

    def test_installs_no_removed_handoff_units_or_worker(self):
        calls = []
        with patch.object(ci, "_run", side_effect=lambda *args: calls.append(args)):
            ci._install()
        text = repr(calls)
        self.assertNotIn("handoff", text)
        self.assertNotIn("activated-worker", text)
        self.assertIn("systemd-sysusers", text)


if __name__ == "__main__":
    unittest.main()
