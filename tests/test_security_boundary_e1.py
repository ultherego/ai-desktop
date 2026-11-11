"""E1 regression: existing trust boundaries and fail-closed gates.

These tests do NOT prove isolation of the host-launched Claude process.
Full OAuth/network/host-process proofs belong to E2 on CachyOS.
"""
import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from ai_desktop import diagnose, executor
from ai_desktop.report_policy import serialize_report
from test_report_policy import report_fixture


class E1SecurityBoundaryTests(unittest.TestCase):
    def test_worker_arguments_are_fixed_and_reject_host_commands(self):
        for action in ("--shell", "bash", "--exec", "--write", "/bin/sh", "", "inspect"):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    executor.build_command(executor.PACKAGE_ROOT, action, "/home/example", "net:[1]")

    def test_offline_worker_mounts_only_readonly_sources_and_no_host_network(self):
        command = executor.build_command(executor.PACKAGE_ROOT, "--check", "/home/example", "net:[1]")
        self.assertIn("--unshare-net", command)
        self.assertIn("--unshare-pid", command)
        self.assertIn("--unshare-user", command)
        self.assertIn("--clearenv", command)
        self.assertIn("--new-session", command)
        self.assertNotIn("--bind", command)
        self.assertNotIn("--dev-bind", command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("--cap-add", command)
        self.assertNotIn("sudo", command)
        self.assertEqual(command[-1], "--check")
        sources = [command[i + 1] for i, a in enumerate(command[:-2]) if a == "--ro-bind"]
        self.assertEqual(len(sources), len(executor.WORKER_FILES) + 1)
        self.assertEqual(sources[0], "/usr")
        self.assertTrue(all(Path(f).parent == executor.PACKAGE_ROOT
                            for f in sources[1:]))
        self.assertNotIn("/home/example", sources)
        self.assertTrue(all("claude" not in s.lower() for s in sources))

    def test_diagnostic_preview_never_starts_a_model_or_process(self):
        report = report_fixture()
        with (
            patch.object(diagnose.os, "geteuid", return_value=1000),
            patch.object(diagnose, "collect", return_value=report) as collect,
            patch.object(diagnose.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(diagnose.main(["preview"]), 0)
        collect.assert_called_once_with()
        run.assert_not_called()
        self.assertIn("PREVIEW: PASS (no data sent to AI)", output.getvalue())
        self.assertIn(serialize_report(report), output.getvalue())

    def test_invalid_actions_and_root_refused_before_collect(self):
        for args, is_root in ((["shell"], False), (["ask"], False), (["preview", "anything"], False),
                              (["preview"], True), (["snapshot"], True)):
            with self.subTest(args=args, root=is_root):
                with (
                    patch.object(diagnose.os, "geteuid", return_value=0 if is_root else 1000),
                    patch.object(diagnose, "collect") as collect,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(diagnose.main(args), 2)
                collect.assert_not_called()

    def test_threat_matrix_and_fallback_are_explicit(self):
        text = (Path(__file__).resolve().parent.parent /
                "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")
        for threat in (f"T{i:02d}" for i in range(1, 15)):
            with self.subTest(threat=threat):
                self.assertIn(threat, text)
        for critical in ("OAuth", "TOCTOU", "Fail-closed", "SHA-256",
                         "localhost", "T03", "T06", "does not start the model",
                         "PREFERRED, TO BE VERIFIED IN E2"):
            with self.subTest(critical=critical):
                self.assertIn(critical, text)


if __name__ == "__main__":
    unittest.main()
