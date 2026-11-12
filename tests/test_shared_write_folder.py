"""A write scope on a shared disk (folders of yours with mode 0777) works like any other; a folder that belongs to
another account is refused and named. Real folders, no mocks of the file operations."""
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import patch

from ai_desktop import ask_edit, permissions, task_workspace as tw, workspace_transfer as transfer
from ai_desktop.task_plan import TaskPlan


class SharedFolderTests(unittest.TestCase):
    def setUp(self):
        home = pwd.getpwuid(os.getuid()).pw_dir
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="ai-shared-", dir=home)))
        self.shared = self.base / "shared"                       # like a shared disk: everyone may write
        self.project = self.shared / "project"
        (self.project / "source/package").mkdir(parents=True)
        (self.project / "source/package/notes.txt").write_text("synthetic\n")
        (self.project / "target").mkdir()
        for folder in (self.shared, self.project, self.project / "target"):
            folder.chmod(0o777)
        self.addCleanup(lambda: [f.chmod(0o755) for f in (self.shared, self.project, self.project / "target")])
        self.enterContext(patch.object(permissions.Policy, "recheck"))      # no policy file here; the folders are real

    def policy(self, root):
        home = pwd.getpwuid(os.getuid()).pw_dir
        rule = permissions._rule(str(root) + "/**", home, version=2)
        return permissions.Policy(uid=os.getuid(), home=home, _raw=b"", _identity=(), _read=(rule,), _deny=(),
                                  _write=(rule,), _version=2)

    def test_a_scope_in_a_shared_folder_of_yours_is_accepted(self):
        self.assertEqual(ask_edit.write_root(self.policy(self.project), 0), str(self.project))
        self.assertEqual(permissions.owned_by_others(str(self.project), os.getuid()), [])

    def test_move_copy_and_save_work_there_and_can_be_undone(self):
        workspace = str(self.project)
        prepared = transfer.prepare(workspace, "move", "source/package", "target/package")
        plan = transfer.execute(prepared, prepared.authorize())
        self.assertTrue((self.project / "target/package/notes.txt").is_file())
        self.assertTrue(transfer.undo(workspace, plan))
        self.assertTrue((self.project / "source/package/notes.txt").is_file())
        prepared = transfer.prepare(workspace, "copy", "source/package", "target/copy")
        transfer.execute(prepared, prepared.authorize())
        self.assertEqual((self.project / "target/copy/notes.txt").read_text(), "synthetic\n")
        (self.project / "target/example.txt").write_text("original\n")
        before = tw.snapshot(workspace, ["target/example.txt"])
        source = {"instruction": "synthetic", "files": [{"path": "target/example.txt", "writable": True,
                  "content": before["files"]["target/example.txt"]["file"]["content"]}]}
        task = TaskPlan(before, source, {"summary": "synthetic", "files": [
            {"path": "target/example.txt", "content": "written\n"}]}, "text")
        tw.apply(task, task.authorize("SAVE " + task.sha256))
        self.assertEqual((self.project / "target/example.txt").read_text(), "written\n")

    def test_a_folder_of_another_account_is_named(self):
        real = os.lstat

        def lstat(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            if path == str(self.shared):
                values = list(info)
                values[4] = 4242                                      # st_uid of another account
                return os.stat_result(values)
            return info
        with patch.object(permissions.os, "lstat", side_effect=lstat):
            self.assertEqual([(f, o) for f, o, _ in permissions.owned_by_others(str(self.project), os.getuid())],
                             [(str(self.shared), 4242)])
        hint = ask_edit.foreign_folders_hint([("/srv/shared data", 4242, 0o777)], os.getuid())
        self.assertIn("/srv/shared data (owner uid 4242) belongs to another account", hint)
        self.assertIn("sudo chown " + pwd.getpwuid(os.getuid()).pw_name + " '/srv/shared data'", hint)
        self.assertIn("belong to you", ask_edit.foreign_folders_hint([], os.getuid()))


if __name__ == "__main__":
    unittest.main()
