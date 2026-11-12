"""S10 stage C: archives are inspected without running anything and installed with a plan, a journal and undo.
Real archives built in the test (zip, tar.gz, tar.xz); synthetic names only."""
import contextlib
import io
import json
import os
from pathlib import Path
import pwd
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from ai_desktop import (archive_install as a, ask_edit, assistant_contract, discovery_contract, terminal_consent,
                        workspace_transfer as transfer)

FOLDER = "~/.local/state/ai-desktop/downloads/20260101-120000-0a1b2c3d"


class Base(unittest.TestCase):
    def setUp(self):
        home = pwd.getpwuid(os.getuid()).pw_dir
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="ai-archive-", dir=home)))
        self.quarantine = self.home / FOLDER[2:]
        self.quarantine.mkdir(parents=True, mode=0o700)
        self.workspace = self.home / "project"
        (self.workspace / "target").mkdir(parents=True)

    def zip(self, name, entries, *, link=None, encrypted=False):
        path = self.quarantine / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry, data in entries.items():
                info = zipfile.ZipInfo(entry)
                info.external_attr = ((0o40755 if entry.endswith("/") else 0o100644) << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
            if link:
                info = zipfile.ZipInfo(link)
                info.external_attr = (0o120777 << 16)
                archive.writestr(info, "target")
        if encrypted:                     # zipfile clears the flag when writing: set it in the central directory
            raw = bytearray(path.read_bytes())
            index = raw.index(b"PK\x01\x02")
            raw[index + 8] |= 0x1
            path.write_bytes(bytes(raw))
        path.chmod(0o600)
        return path

    def tar(self, name, entries, mode="w:gz", *, extra=()):
        path = self.quarantine / name
        with tarfile.open(path, mode) as archive:
            for entry, data in entries.items():
                info = tarfile.TarInfo(entry)
                if data is None:
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    archive.addfile(info)
                else:
                    info.size, info.mode = len(data), 0o755 if entry.endswith(".sh") else 0o644
                    archive.addfile(info, io.BytesIO(data))
            for info in extra:
                archive.addfile(info)
        path.chmod(0o600)
        return path

    def label(self, path):
        return FOLDER + "/" + path.name


class InspectTests(Base):
    def test_a_zip_with_one_top_folder_is_described_with_its_executable_content(self):
        path = self.zip("package-1.0.zip", {"package/": b"", "package/notes.txt": b"notes",
                                           "package/lib/run.sh": b"#!/bin/sh\n"})
        facts = a.inspect(str(path))
        self.assertEqual((facts["format"], facts["files"], facts["single"], facts["top"]), ("zip", 2, True, ["package"]))
        self.assertEqual((facts["executables"], facts["examples"]), (1, ["package/lib/run.sh"]))

    def test_tar_formats_are_recognised_from_the_content_not_the_name(self):
        for mode, form in (("w:gz", "tar.gz"), ("w:xz", "tar.xz"), ("w:bz2", "tar.bz2"), ("w", "tar")):
            with self.subTest(form=form):
                path = self.tar("misleading-name.zip", {"package/notes.txt": b"x"}, mode)
                self.assertEqual(a.inspect(str(path))["format"], form)

    def test_unsafe_or_unsupported_archives_are_refused_before_anything_is_written(self):
        link = tarfile.TarInfo("package/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        device = tarfile.TarInfo("package/device")
        device.type = tarfile.CHRTYPE
        cases = {
            "path": lambda: self.tar("a.tar.gz", {"../outside.txt": b"x"}),
            "link": lambda: self.tar("b.tar.gz", {"package/notes.txt": b"x"}, extra=(link,)),
            "type": lambda: self.tar("c.tar.gz", {"package/notes.txt": b"x"}, extra=(device,)),
            "duplicate": lambda: self.zip("d.zip", {"package/notes.txt": b"x", "package/notes.txt/": b""}),
            "encrypted": lambda: self.zip("e.zip", {"package/notes.txt": b"x"}, encrypted=True),
            "format": lambda: (self.quarantine / "f.txt").write_bytes(b"plain text") and self.quarantine / "f.txt",
        }
        for reason, build in cases.items():
            with self.subTest(reason=reason), self.assertRaises(a.ArchiveDenied) as caught:
                a.inspect(str(build()))
            self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(a.ArchiveDenied) as caught:
            a.inspect(str(self.zip("g.zip", {"package/notes.txt": b"x"}, link="package/link")))
        self.assertEqual(caught.exception.reason, "link")
        for name in ("/etc/passwd", "package/../../x", "a\\b", "package/‮x", ".ai-desktop-tasks/x"):
            with self.subTest(name=name), self.assertRaises(a.ArchiveDenied):
                a.clean_name(name)
        self.assertEqual(a.clean_name("./package/notes.txt"), "package/notes.txt")

    def test_bombs_and_oversized_or_crowded_archives_are_refused(self):
        path = self.zip("bomb.zip", {"package/zero.bin": b"\0" * 300000})
        with patch.object(a, "BOMB_FLOOR", 1000), self.assertRaises(a.ArchiveDenied) as caught:
            a.inspect(str(path))
        self.assertEqual(caught.exception.reason, "bomb")
        with patch.object(a, "MAX_BYTES", 100), self.assertRaises(a.ArchiveDenied) as caught:
            a.inspect(str(path))
        self.assertEqual(caught.exception.reason, "size")
        crowded = self.zip("crowded.zip", {"package/%d.txt" % i: b"x" for i in range(5)})
        with patch.object(a, "MAX_ENTRIES", 3), self.assertRaises(a.ArchiveDenied) as caught:
            a.inspect(str(crowded))
        self.assertEqual(caught.exception.reason, "count")
        deep = self.zip("deep.zip", {"/".join(["d"] * 40) + "/x.txt": b"x"})
        with self.assertRaises(a.ArchiveDenied) as caught:
            a.inspect(str(deep))
        self.assertEqual(caught.exception.reason, "depth")

    def test_only_quarantine_labels_resolve(self):
        self.assertEqual(a.resolve(FOLDER + "/x.zip", "/home/synthetic"),
                         "/home/synthetic/.local/state/ai-desktop/downloads/20260101-120000-0a1b2c3d/x.zip")
        for label in ("~/x.zip", FOLDER + "/../x.zip", FOLDER + "/a/b.zip", FOLDER + "/x.part", "/etc/passwd"):
            with self.subTest(label=label), self.assertRaises(a.ArchiveDenied):
                a.resolve(label, "/home/synthetic")


class InstallTests(Base):
    def install(self, path, to):
        prepared = a.prepare(str(self.workspace), self.label(path), str(path), to)
        return prepared, a.execute(prepared, prepared.authorize())

    def test_one_top_folder_lands_inside_the_destination_and_undo_keeps_it(self):
        path = self.zip("package-1.0.zip", {"package/notes.txt": b"notes", "package/lib/run.sh": b"#!/bin/sh\n"})
        prepared, plan = self.install(path, "target")
        self.assertEqual(prepared.data()["to"], "target/package")
        installed = self.workspace / "target/package"
        self.assertEqual((installed / "notes.txt").read_bytes(), b"notes")
        self.assertEqual(stat.S_IMODE((installed / "lib/run.sh").stat().st_mode), 0o644)    # never executable
        self.assertEqual(stat.S_IMODE((installed / "lib").stat().st_mode), 0o755)
        self.assertFalse([n for n in os.listdir(self.workspace / "target") if n.startswith(".ai-desktop-copy-")])
        record = next(r for r in transfer.recovery_records(str(self.workspace)) if r["plan"] == plan)
        self.assertEqual((record["operation"], record["status"], record["to"]), ("install", "done", "target/package"))
        self.assertTrue(transfer.undo(str(self.workspace), plan))
        self.assertFalse(installed.exists())
        self.assertTrue((self.workspace / ".ai-desktop-tasks" / ("transfer-" + plan) / "notes.txt").is_file())

    def test_several_top_entries_create_the_new_folder_and_the_scope_itself_works(self):
        path = self.tar("bundle.tar.xz", {"a.txt": b"a", "docs/b.txt": b"b"}, "w:xz")
        prepared, _ = self.install(path, "target/bundle")
        self.assertEqual((self.workspace / "target/bundle/docs/b.txt").read_bytes(), b"b")
        single = self.zip("other.zip", {"other/notes.txt": b"x"})
        prepared, _ = self.install(single, ".")
        self.assertTrue((self.workspace / "other/notes.txt").is_file())

    def test_nothing_is_overwritten_and_a_changed_archive_is_refused(self):
        path = self.zip("package-1.0.zip", {"package/notes.txt": b"notes"})
        (self.workspace / "target/package").mkdir()
        with self.assertRaises(a.ArchiveDenied) as caught:
            a.prepare(str(self.workspace), self.label(path), str(path), "target")
        self.assertEqual(caught.exception.reason, "exists")
        (self.workspace / "target/package").rmdir()
        prepared = a.prepare(str(self.workspace), self.label(path), str(path), "target")
        self.zip("package-1.0.zip", {"package/notes.txt": b"other"})                # replaced after the plan
        with self.assertRaises(a.ArchiveDenied) as caught:
            a.execute(prepared, prepared.authorize())
        self.assertEqual(caught.exception.reason, "changed")
        self.assertFalse((self.workspace / "target/package").exists())
        with self.assertRaises(a.ArchiveDenied) as caught:
            a.prepare(str(self.workspace), self.label(path), str(path), "missing/folder")
        self.assertEqual(caught.exception.reason, "missing")

    def test_the_conversation_step_shows_the_plan_and_asks(self):
        path = self.zip("package-1.0.zip", {"package/notes.txt": b"notes", "package/run.sh": b"#!/bin/sh\n"})
        policy = Mock(home=str(self.home))
        policy.write_scopes.return_value = ()
        snapshot = Mock(policy=policy, access={"write": [{"id": "w0", "pattern": "~/project/**"}]})
        plan = {"action": "install", "archive": self.label(path), "write": "w0", "to": "target"}
        for answer, code in ((False, 2), (True, 0)):
            with self.subTest(answer=answer), contextlib.redirect_stdout(io.StringIO()) as out, \
                    patch.object(ask_edit, "write_root", return_value=str(self.workspace)), \
                    patch.object(terminal_consent, "confirm", return_value=answer), \
                    patch("ai_desktop.run_controls.require_changes"):
                result = ask_edit.install_archive(plan, snapshot, reader=None)
            self.assertEqual(result.code, code)
            self.assertIn("package-1.0.zip  ->  target/package", out.getvalue())
            self.assertIn("Contains programs or scripts (1)", out.getvalue())
            self.assertEqual((self.workspace / "target/package/notes.txt").exists(), answer)
            if answer:
                self.assertIn("ai-desktop task restore --workspace", out.getvalue())


class ContractTests(unittest.TestCase):
    ACCESS = {"files": [], "systemd": [], "weather": False, "downloads": True,
              "write": [{"id": "w0", "pattern": "~/project/**"}]}

    def test_plans_need_downloads_a_quarantine_label_and_a_write_scope(self):
        inspect = {"action": "inspect", "archive": FOLDER + "/package.zip"}
        install = {"action": "install", "archive": FOLDER + "/package.zip", "write": "w0", "to": "target"}
        self.assertEqual(assistant_contract.parse_plan(json.dumps(inspect), self.ACCESS), inspect)
        self.assertEqual(assistant_contract.parse_plan(json.dumps(install), self.ACCESS), install)
        self.assertEqual(assistant_contract.parse_plan(json.dumps(dict(install, to=".")), self.ACCESS)["to"], ".")
        written = dict(install, archive="/home/synthetic" + FOLDER[1:] + "/package.zip")     # the path the user saw
        self.assertEqual(assistant_contract.parse_plan(json.dumps(written), self.ACCESS), install)
        with self.assertRaises(assistant_contract.PlanError) as caught:
            assistant_contract.parse_plan(json.dumps(dict(install, archive="~/Downloads/package.zip")), self.ACCESS)
        self.assertEqual(caught.exception.check, "archive")                  # correctable, not the end of the question
        self.assertIsNone(a.label("/etc/passwd"))
        self.assertIsNone(a.label(FOLDER + "/sub/package.zip"))
        without = dict(self.ACCESS)
        del without["downloads"]
        for access, value in ((without, inspect), (self.ACCESS, dict(inspect, archive="~/package.zip")),
                              (self.ACCESS, dict(install, write="w1")), (self.ACCESS, dict(install, to="../x")),
                              (self.ACCESS, dict(install, extra=1))):
            with self.subTest(value=value), self.assertRaises(Exception):
                assistant_contract.parse_plan(json.dumps(value), access)

    def test_the_archive_step_the_model_receives(self):
        ok = {"op": "archive", "archive": FOLDER + "/package.zip", "status": "ok", "reason": "", "format": "zip",
              "files": 2, "folders": 1, "bytes": 15, "top": ["package"], "top_count": 1, "executables": 1,
              "examples": ["package/run.sh"]}
        document = {"steps": [ok], "remaining": {"rounds": 1, "corrections": 1, "commands": 1}}
        self.assertEqual(discovery_contract.validate_discovery(document)["steps"][0], ok)
        refused = dict(ok, status="refused", reason="link", format="", files=0, folders=0, bytes=0, top=[],
                       top_count=0, executables=0, examples=[])
        discovery_contract.validate_discovery(dict(document, steps=[refused]))
        for bad in (dict(ok, reason="x"), dict(refused, reason=""), dict(ok, format="rar"), dict(ok, top=["a\x1b"]),
                    dict(ok, files=-1), dict(ok, extra=1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                discovery_contract.validate_discovery(dict(document, steps=[bad]))


if __name__ == "__main__":
    unittest.main()
