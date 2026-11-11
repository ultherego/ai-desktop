"""`ai-desktop commands`: the allowed operations with class, installation and owning package (S5 catalog)."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_desktop import commands_cli


class CommandsCatalogTests(unittest.TestCase):
    def policy(self):
        return SimpleNamespace(commands=lambda: (("uname", "..."), ("missing-tool", "--x")),
                               changes=lambda: (("docker", "stop", "<word>"),))

    def run_cli(self, argv):
        out = io.StringIO()
        with patch.object(commands_cli.permissions, "load_policy", return_value=self.policy()), \
                patch.object(commands_cli.command_ops, "resolve",
                             side_effect=lambda program: None if program == "missing-tool" else "/usr/bin/" + program), \
                patch.object(commands_cli.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout="/usr/bin/x is owned by synthetic-pkg 1.2-3\n")) as run, \
                contextlib.redirect_stdout(out):
            self.assertEqual(commands_cli.main(argv), 0)
        return out.getvalue(), run

    def test_json_lists_class_installation_package_and_preset_membership(self):
        text, run = self.run_cli(["--json"])
        entries = json.loads(text)["commands"]
        self.assertEqual([(e["template"], e["class"], e["installed"]) for e in entries],
                         [("uname ...", "read", True), ("missing-tool --x", "read", False),
                          ("docker stop <word>", "change", True)])
        self.assertEqual((entries[0]["package"], entries[0]["version"]), ("synthetic-pkg", "1.2-3"))
        self.assertTrue(entries[0]["in_preset"])
        self.assertFalse(entries[2]["in_preset"])
        for call in run.call_args_list:                                   # only a local package query, never the tool
            self.assertEqual(call.args[0][:2], ["/usr/bin/pacman", "-Qo"])

    def test_text_output_separates_read_and_change(self):
        text, _ = self.run_cli([])
        self.assertIn("Run without asking (read): 2", text)
        self.assertIn("Ask every time (change): 1", text)
        self.assertIn("not installed", text)


if __name__ == "__main__":
    unittest.main()
