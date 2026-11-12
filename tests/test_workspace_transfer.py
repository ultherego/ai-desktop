"""Moving and copying files and whole folders inside one write workspace (S10): real files, journal, undo, refusals."""
import io
import contextlib
import json
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_desktop import assistant_contract, ask_edit, conversation_state as cs, terminal_consent
from ai_desktop import workspace_transfer as t


class Workspace(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(os.geteuid(), 0)
        home = pwd.getpwuid(os.getuid()).pw_dir
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="ai-transfer-", dir=home)))
        self.ws = str(self.root)
        package = self.root / "source/package"
        (package / "media").mkdir(parents=True)
        (package / "notes.txt").write_text("a package\n")
        (package / "media/image.bin").write_bytes(os.urandom(200_000))
        (self.root / "target").mkdir()

    def journal(self, plan):
        return json.loads((self.root / ".ai-desktop-tasks" / (plan + ".transfer.json")).read_text())

    def run_transfer(self, operation, source, target):
        prepared = t.prepare(self.ws, operation, source, target)
        return prepared, t.execute(prepared, prepared.authorize())


class TransferTests(Workspace):
    def test_a_folder_is_copied_whole_and_the_undo_keeps_the_copy_in_the_task_state(self):
        prepared, plan = self.run_transfer("copy", "source/package", "target/package")
        self.assertEqual((prepared.data()["files"], prepared.data()["folders"], prepared.data()["kind"]), (2, 2, "folder"))
        copy = self.root / "target/package"
        self.assertEqual((copy / "media/image.bin").read_bytes(), (self.root / "source/package/media/image.bin").read_bytes())
        self.assertTrue((self.root / "source/package").is_dir())                 # the source stays
        self.assertFalse(any(name.startswith(".ai-desktop-copy-") for name in os.listdir(self.root / "target")))
        self.assertEqual(self.journal(plan)["status"], "done")
        self.assertTrue(t.undo(self.ws, plan))
        self.assertFalse(copy.exists())
        kept = self.root / ".ai-desktop-tasks" / ("transfer-" + plan)
        self.assertTrue((kept / "notes.txt").is_file())                           # moved away, not deleted
        self.assertEqual(self.journal(plan)["status"], "undone")
        with self.assertRaises(t.TransferDenied):
            t.undo(self.ws, plan)                                                 # once only

    def test_a_move_is_one_rename_and_its_undo_puts_it_back(self):
        inode = (self.root / "source/package").stat().st_ino
        _, plan = self.run_transfer("move", "source/package", "target/package")
        self.assertFalse((self.root / "source/package").exists())
        self.assertEqual((self.root / "target/package").stat().st_ino, inode)
        self.assertTrue(t.undo(self.ws, plan))
        self.assertEqual((self.root / "source/package").stat().st_ino, inode)

    def test_nothing_is_ever_overwritten(self):
        (self.root / "target/package").mkdir()
        for operation in ("move", "copy"):
            with self.subTest(operation=operation), self.assertRaises(t.TransferDenied) as caught:
                t.prepare(self.ws, operation, "source/package", "target/package")
            self.assertEqual(caught.exception.reason, "exists")
        # The destination appears between the question and the yes: refused, the newer entry is untouched.
        (self.root / "target/package").rmdir()
        prepared = t.prepare(self.ws, "copy", "source/package", "target/package")
        (self.root / "target/package").write_text("someone else's file")
        with self.assertRaises(t.TransferDenied) as caught:
            t.execute(prepared, prepared.authorize())
        self.assertEqual(caught.exception.reason, "exists")
        self.assertEqual((self.root / "target/package").read_text(), "someone else's file")
        self.assertFalse(any(name.startswith(".ai-desktop-copy-") for name in os.listdir(self.root / "target")))

    def test_links_special_files_a_folder_into_itself_and_a_changed_source_are_refused(self):
        os.symlink("/etc/passwd", self.root / "source/package/leak")
        with self.assertRaises(t.TransferDenied) as caught:
            t.prepare(self.ws, "copy", "source/package", "target/package")
        self.assertEqual(caught.exception.reason, "type")
        os.unlink(self.root / "source/package/leak")
        with self.assertRaises(t.TransferDenied) as caught:
            t.prepare(self.ws, "move", "source", "source/inner")
        self.assertEqual(caught.exception.reason, "inside")
        with self.assertRaises(t.TransferDenied) as caught:
            t.prepare(self.ws, "copy", "source/missing", "target/missing")
        self.assertEqual(caught.exception.reason, "missing")
        prepared = t.prepare(self.ws, "move", "source/package", "target/package")
        os.rename(self.root / "source/package", self.root / "source/old")
        (self.root / "source/package").mkdir()                                  # another folder now under the name
        with self.assertRaises(t.TransferDenied) as caught:
            t.execute(prepared, prepared.authorize())
        self.assertEqual(caught.exception.reason, "changed")
        self.assertTrue((self.root / "source/old/notes.txt").exists())

    def test_one_approval_runs_once_and_an_undo_after_a_change_is_a_conflict(self):
        prepared = t.prepare(self.ws, "move", "source/package", "target/package")
        approval = prepared.authorize()
        plan = t.execute(prepared, approval)
        with self.assertRaises(t.TransferDenied):
            t.execute(prepared, approval)
        (self.root / "source/package").mkdir()                                  # the old name is taken now
        self.assertFalse(t.undo(self.ws, plan))
        self.assertTrue((self.root / "target/package/notes.txt").exists())
        self.assertEqual(self.journal(plan)["status"], "conflict")

    def test_real_folder_names_with_spaces_other_languages_and_depth_work(self):
        # Regression: a real folder path with spaces was refused by the narrow edit-path rules.
        deep = self.root / "projects/My Files/level 2/level 3/Target Folder"
        deep.mkdir(parents=True)
        (self.root / "projects/Ünïcode nàme").mkdir()
        _, plan = self.run_transfer("move", "source/package", "projects/My Files/level 2/level 3/Target Folder/package")
        self.assertTrue((deep / "package/notes.txt").exists())
        _, copied = self.run_transfer("copy", "projects/My Files/level 2/level 3/Target Folder/package",
                                      "projects/Ünïcode nàme/Package copy")
        self.assertTrue((self.root / "projects/Ünïcode nàme/Package copy/media/image.bin").exists())
        self.assertTrue(t.undo(self.ws, copied) and t.undo(self.ws, plan))
        for bad in ("a/../b", "/abs", "a//b", "a/b/", ".ai-desktop-tasks/x", "x/.ai-desktop-copy-1", "a\u202eb", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                t.path(bad)

    def test_a_copy_checks_free_space_first(self):
        class Full:
            f_bavail, f_frsize = 0, 4096
        with patch.object(t.os, "statvfs", return_value=Full()), self.assertRaises(t.TransferDenied) as caught:
            t.prepare(self.ws, "copy", "source/package", "target/package")
        self.assertEqual(caught.exception.reason, "space")


class HistoryTests(Workspace):
    """A move or copy has its own journal; it must not break saves, the trash, task status or task prune."""

    def record(self, plan):
        return next(item for item in t.recovery_records(self.ws) if item["plan"] == plan)

    def test_a_transfer_journal_does_not_block_saves_the_trash_or_task_status(self):
        from ai_desktop import task_workspace as tw, workspace_trash
        _, copied = self.run_transfer("copy", "source/package", "target/package")
        self.assertTrue(t.undo(self.ws, copied))                                 # leaves transfer-<plan> in the state
        self.run_transfer("move", "source/package/notes.txt", "target/notes.txt")
        self.assertEqual(tw.recovery_records(self.ws), [])
        self.assertEqual(tw.status(self.ws), [])
        self.assertEqual(tw._can_apply(self.ws), 0)
        workspace_trash._require_room(self.ws)                                   # no false "trash full"

    def test_only_a_transfer_that_holds_nothing_can_be_pruned(self):
        _, copied = self.run_transfer("copy", "source/package", "target/package")
        _, moved = self.run_transfer("move", "source/package", "target/moved")
        self.assertEqual({(r["plan"], r["kind"], r["operation"], r["status"], r["prunable"]) for r in t.recovery_records(self.ws)},
                         {(copied, "transfer", "copy", "done", False), (moved, "transfer", "move", "done", False)})
        self.assertTrue(t.undo(self.ws, copied))
        self.assertTrue(t.undo(self.ws, moved))
        self.assertFalse(self.record(copied)["prunable"])                       # the copy is kept in the task state
        self.assertTrue(self.record(moved)["prunable"])
        with self.assertRaises(ValueError):
            t.prune(self.ws, {copied: self.record(copied)["journal_sha256"]})
        with self.assertRaises(ValueError):
            t.prune(self.ws, {moved: "0" * 64})                                   # changed since the listing
        self.assertEqual(t.prune(self.ws, {moved: self.record(moved)["journal_sha256"]}), 1)
        self.assertEqual([r["plan"] for r in t.recovery_records(self.ws)], [copied])
        self.assertTrue((self.root / "source/package/notes.txt").is_file())     # pruning never touches files
        self.assertTrue((self.root / ".ai-desktop-tasks" / ("transfer-" + copied) / "notes.txt").is_file())

    def test_a_failed_transfer_is_finished_history(self):
        _, plan = self.run_transfer("move", "source/package", "target/package")
        path = self.root / ".ai-desktop-tasks" / (plan + ".transfer.json")
        value = self.journal(plan)
        value["status"] = "failed"
        path.write_text(json.dumps(value))
        self.assertTrue(self.record(plan)["prunable"])

    def test_task_status_lists_a_move_with_its_undo_command(self):
        from ai_desktop import task_cli
        _, plan = self.run_transfer("move", "source/package", "target/package")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(task_cli.main(["status", "--workspace", self.ws]), 0)
        self.assertIn("State: moved", out.getvalue())
        self.assertIn("Move: source/package  ->  target/package", out.getvalue())
        self.assertIn("Undo: ai-desktop task restore --workspace " + self.ws + " --plan " + plan, out.getvalue())


class ConversationTests(Workspace):
    def snapshot(self, readable=True, writable=True):
        policy = Mock()
        policy.write_workspace.return_value = self.ws
        policy.require_write.side_effect = None if writable else ValueError("files-write")
        policy.allows.return_value = readable
        return Mock(policy=policy, access={"write": [{"id": "w0"}]})

    def ask(self, plan, answer=True, **kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), cs.recording() as recorded, \
                patch.object(terminal_consent, "confirm", return_value=answer) as confirm, \
                patch("ai_desktop.run_controls.require_changes"):
            result = ask_edit.transfer_files(plan, self.snapshot(**kwargs), reader=None)
        return result, out.getvalue(), confirm, recorded.actions

    def test_the_plan_is_shown_then_one_yes_moves_and_the_undo_command_is_printed(self):
        result, out, confirm, facts = self.ask({"action": "move", "write": "w0", "from": "source/package",
                                                "to": "target/package"})
        self.assertEqual(result.code, 0)
        self.assertEqual(confirm.call_args.args[0], "move")
        self.assertIn("source/package  ->  target/package", out)
        self.assertIn("folder (2 files, 2 folders)", out)
        self.assertIn("ai-desktop task restore --workspace", out)
        self.assertTrue((self.root / "target/package/notes.txt").exists())
        self.assertTrue(any(fact.startswith("Moved source/package -> target/package") for fact in facts))

    def test_no_means_nothing_changes_and_refusals_say_why(self):
        result, out, _, facts = self.ask({"action": "copy", "write": "w0", "from": "source/package",
                                          "to": "target/package"}, answer=False)
        self.assertEqual(result.code, 2)
        self.assertFalse((self.root / "target/package").exists())
        self.assertIn("Nothing was changed", out)
        (self.root / "target/package").mkdir()
        result, out, confirm, _ = self.ask({"action": "copy", "write": "w0", "from": "source/package",
                                            "to": "target/package"})
        self.assertEqual(result.code, 2)
        self.assertIn("the destination already exists (nothing is overwritten)", out)
        confirm.assert_not_called()

    def test_a_source_the_policy_does_not_let_you_read_is_refused_before_any_question(self):
        with self.assertRaises(ask_edit.EditDenied):
            self.ask({"action": "copy", "write": "w0", "from": "source/package", "to": "target/package"}, readable=False)
        with self.assertRaises(ask_edit.EditDenied):
            self.ask({"action": "move", "write": "w0", "from": "source/package", "to": "target/package"}, writable=False)
        self.assertFalse((self.root / "target/package").exists())

    def test_task_restore_undoes_a_move_by_its_id(self):
        from types import SimpleNamespace
        from ai_desktop import task_cli
        _, plan = self.run_transfer("move", "source/package", "target/package")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(task_cli, "confirm", return_value=True) as confirm:
            code = task_cli.restore_deleted(SimpleNamespace(workspace=self.ws, plan=plan), reader=None)
        self.assertEqual((code, confirm.call_args.args[0]), (0, "transfer-undo"))
        self.assertTrue((self.root / "source/package/notes.txt").exists())
        self.assertIn("Undone.", out.getvalue())

    def test_a_shared_folder_of_yours_is_a_normal_destination(self):
        (self.root / "target").chmod(0o777)
        try:
            result, out, confirm, facts = self.ask({"action": "move", "write": "w0", "from": "source/package",
                                                    "to": "target/package"})
        finally:
            (self.root / "target").chmod(0o755)
        self.assertEqual(result.code, 0)
        confirm.assert_called_once()
        self.assertTrue((self.root / "target/package/notes.txt").exists())

    def test_the_plan_contract(self):
        access = {"files": [], "systemd": [], "weather": False, "write": [{"id": "w0", "pattern": "~/x/**"}]}
        good = {"action": "copy", "write": "w0", "from": "a/b", "to": "c/b"}
        self.assertEqual(assistant_contract.parse_plan(json.dumps(good), access), good)
        for broken in (dict(good, write="w1"), dict(good, to="a/b/c"), dict(good, to="a/b"), dict(good, to="../x"),
                       dict(good, extra=1), dict(good, action="rename")):
            with self.subTest(broken=broken), self.assertRaises(assistant_contract.PlanError):
                assistant_contract.parse_plan(json.dumps(broken), access)


if __name__ == "__main__":
    unittest.main()
