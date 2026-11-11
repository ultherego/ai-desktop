"""S4: installed manual pages as task evidence, read by the program, never through `man` or the network."""
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_desktop import assistant_contract, discovery_contract, manuals

PAGE = b""".TH "TOOL" "8"
.SH "NAME"
tool \\- a synthetic tool
.SH "OPTIONS"
.PP
\\fB\\-\\-overwrite\\fR <glob>
.RS 4
Bypass file conflict checks\\&.
.RE
"""


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "man8").mkdir()
        (self.root / "man8/tool.8.gz").write_bytes(gzip.compress(PAGE))
        self.enterContext(patch.object(manuals, "ROOT", str(self.root)))
        real = os.fstat
        # The synthetic tree belongs to the test user; production requires root-owned pages.
        self.enterContext(patch.object(manuals.os, "fstat",
                                       side_effect=lambda fd: type("S", (), {"st_mode": real(fd).st_mode, "st_uid": 0,
                                                                             "st_size": real(fd).st_size})()))

    def test_a_phrase_returns_its_excerpt_as_plain_text(self):
        found = manuals.excerpt("tool", "8", "--overwrite")
        self.assertEqual(len(found["fragments"]), 1)
        self.assertIn("--overwrite <glob>", found["fragments"][0]["text"])
        self.assertIn("Bypass file conflict checks.", found["fragments"][0]["text"])
        self.assertNotIn("\\f", found["fragments"][0]["text"])

    def test_missing_names_links_and_bad_names_are_refused(self):
        with self.assertRaises(ValueError):
            manuals.excerpt("absent", "8")
        for page, section in (("../etc/shadow", "8"), ("tool", "../8"), ("Tool", "8")):
            with self.subTest(page=page), self.assertRaises(ValueError):
                manuals.excerpt(page, section)
        (self.root / "man8/link.8.gz").symlink_to(self.root / "man8/tool.8.gz")
        with self.assertRaises(ValueError):
            manuals.excerpt("link", "8")

    def test_the_real_pacman_page_when_installed(self):
        if not os.path.exists("/usr/share/man/man8/pacman.8.gz"):
            self.skipTest("no pacman manual on this system")
        with patch.object(manuals, "ROOT", "/usr/share/man"), patch.object(manuals.os, "fstat", os.fstat):
            found = manuals.excerpt("pacman", "8", "--overwrite")
        self.assertTrue(any("--overwrite" in item["text"] for item in found["fragments"]))


class ManualContractTests(unittest.TestCase):
    ACCESS = {"files": [], "systemd": [], "weather": False}

    def test_the_plan_and_the_worker_step_are_bounded(self):
        plan = assistant_contract.parse_plan(json.dumps({"action": "manual", "page": "pacman", "section": "8",
                                                         "needle": "--overwrite"}), self.ACCESS)
        self.assertEqual(plan["page"], "pacman")
        for bad in ({"action": "manual", "page": "../x", "section": "8"},
                    {"action": "manual", "page": "pacman", "section": "8", "path": "/etc/shadow"},
                    {"action": "manual", "page": "pacman", "section": "8", "needle": "x"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                assistant_contract.parse_plan(json.dumps(bad), self.ACCESS)
        step = {"op": "manual", "page": "pacman", "section": "8", "status": "ok", "lines": 10, "complete": True,
                "fragments": [{"first": 1, "last": 2, "text": "--overwrite <glob>"}]}
        document = {"steps": [step], "remaining": {"rounds": 1, "corrections": 1, "commands": 0}}
        self.assertEqual(discovery_contract.validate_discovery(document)["steps"][0]["page"], "pacman")
        for broken in (dict(step, path="/etc"), dict(step, status="missing"),
                       dict(step, fragments=[{"first": 2, "last": 1, "text": "x"}])):
            with self.subTest(broken=sorted(broken)), self.assertRaises(ValueError):
                discovery_contract.validate_discovery(dict(document, steps=[broken]))


if __name__ == "__main__":
    unittest.main()
