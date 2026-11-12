"""`task prune`: only finished history is removed, re-checked under the lock, after a list and a separate consent."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_desktop import task_cli as cli, task_workspace as tw, workspace_trash as trash, workspace_transfer as transfer
from ai_desktop.analysis_contract import canonical
from ai_desktop.task_plan import TaskPlan


class PruneEngineTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="ai-task-prune-")))
        self.root.chmod(0o700)
        self.ws = str(self.root)

    def apply(self, name="one.txt", content="CHANGED\n"):
        path = self.root / name
        if not path.exists():
            path.write_text("ORIGINAL\n")
        before = tw.snapshot(self.ws, [name])
        source = {"instruction": "synthetic", "files": [
            {"path": name, "content": before["files"][name]["file"]["content"], "writable": True}]}
        plan = TaskPlan(before, source, {"summary": "synthetic", "files": [{"path": name, "content": content}]}, "text")
        tw.apply(plan, plan.authorize("SAVE " + plan.sha256))
        return plan.sha256

    def record(self, plan):
        return next(item for item in tw.recovery_records(self.ws) if item["plan"] == plan)

    def journal(self, plan):
        return self.root / tw.STATE / (plan + ".json")

    def test_saved_and_undone_tasks_are_finished_and_removed_without_touching_files(self):
        saved = self.apply("one.txt")
        undone = self.apply("two.txt")
        self.assertTrue(tw.rollback(self.ws, undone))
        self.assertTrue(self.record(saved)["prunable"])
        self.assertTrue(self.record(undone)["prunable"])
        approved = {plan: self.record(plan)["journal_sha256"] for plan in (saved, undone)}
        self.assertEqual(tw.prune(self.ws, approved), 2)
        self.assertEqual(tw.recovery_records(self.ws), [])
        self.assertEqual((self.root / "one.txt").read_text(), "CHANGED\n")
        self.assertEqual((self.root / "two.txt").read_text(), "ORIGINAL\n")
        self.assertEqual([n for n in os.listdir(self.root / tw.STATE) if n.startswith(".pending-")], [])

    def test_unfinished_save_is_never_prunable_and_refuses_the_whole_request(self):
        saved = self.apply("one.txt")
        other = self.apply("two.txt")
        value = json.loads(self.journal(other).read_text())
        value["status"] = "prepared"
        self.journal(other).write_text(canonical(value))
        self.assertFalse(tw._finished(None, value, "same"))
        approved = {saved: self.record(saved)["journal_sha256"],
                    other: tw.sha(canonical(value).encode("utf-8"))}
        with self.assertRaisesRegex(ValueError, "journal unfinished"):
            tw.prune(self.ws, approved)
        self.assertTrue(self.journal(saved).exists())
        self.assertTrue(self.journal(other).exists())

    def test_conflict_with_a_stage_left_stays(self):
        plan = self.apply()
        value = json.loads(self.journal(plan).read_text())
        value["status"] = "conflict"
        self.journal(plan).write_text(canonical(value))
        stage = self.root / value["entries"][0]["temp"]
        stage.write_text("left behind\n")
        self.assertFalse(self.record(plan)["prunable"])
        with self.assertRaisesRegex(ValueError, "journal unfinished"):
            tw.prune(self.ws, {plan: self.record(plan)["journal_sha256"]})
        stage.unlink()
        self.assertTrue(self.record(plan)["prunable"])
        self.assertEqual(tw.prune(self.ws, {plan: self.record(plan)["journal_sha256"]}), 1)

    def test_a_journal_changed_after_the_listing_is_kept(self):
        plan = self.apply()
        seen = self.record(plan)["journal_sha256"]
        self.assertTrue(tw.rollback(self.ws, plan))
        with self.assertRaisesRegex(ValueError, "journal changed"):
            tw.prune(self.ws, {plan: seen})
        self.assertTrue(self.journal(plan).exists())

    def test_a_record_replaced_between_the_check_and_the_unlink_goes_back(self):
        plan = self.apply()
        held = os.stat(self.journal(plan))
        self.journal(plan).unlink()
        self.journal(plan).write_text("replaced\n")
        fd = os.open(self.root / tw.STATE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(ValueError, "journal changed"):
                tw.retire(fd, plan + ".json", held)
        finally:
            os.close(fd)
        self.assertEqual(self.journal(plan).read_text(), "replaced\n")

    def test_approval_shape_is_refused_before_any_lookup(self):
        for approved in ({}, [], {"x": "y"}, {"a" * 64: "b" * 63}, {"a" * 32: "b" * 64}, {"a" * 64: 1}):
            with self.subTest(approved=approved), self.assertRaisesRegex(ValueError, "prune approval"):
                tw.prune(self.ws, approved)

    def test_a_full_history_accepts_a_new_task_after_pruning(self):
        plan = self.apply()
        value = json.loads(self.journal(plan).read_text())
        for index in range(tw.MAX_JOURNALS - 1):
            copy = dict(value, plan=f"{index:064x}")
            target = self.journal(copy["plan"])
            target.write_text(canonical(copy))
            target.chmod(0o600)
        with self.assertRaises(tw.JournalLimit):
            tw._can_apply(self.ws)
        tw.prune(self.ws, {plan: self.record(plan)["journal_sha256"]})
        tw._can_apply(self.ws)

    def test_only_a_fully_restored_deletion_is_prunable(self):
        (self.root / "gone.txt").write_text("keep me\n")
        prepared = trash.prepare(self.ws, ["gone.txt"])
        deletion = trash.execute(prepared, prepared.authorize())
        held = trash.recovery_records(self.ws)[0]
        self.assertFalse(held["prunable"])
        with self.assertRaisesRegex(ValueError, "journal unfinished"):
            trash.prune(self.ws, {deletion: held["journal_sha256"]})
        self.assertTrue(trash.restore(self.ws, deletion))
        restored = trash.recovery_records(self.ws)[0]
        self.assertTrue(restored["prunable"])
        self.assertEqual(trash.prune(self.ws, {deletion: restored["journal_sha256"]}), 1)
        self.assertEqual(trash.recovery_records(self.ws), [])
        self.assertEqual((self.root / "gone.txt").read_text(), "keep me\n")

    def test_restore_by_id_does_not_read_the_whole_history_and_room_is_rechecked_under_the_lock(self):
        (self.root / "one.txt").write_text("one\n")
        prepared = trash.prepare(self.ws, ["one.txt"])
        deletion = trash.execute(prepared, prepared.authorize())
        with patch.object(trash, "recovery_records", side_effect=ValueError("trash journal count")):
            self.assertTrue(trash.known(self.ws, deletion))       # one name only, not the list
        self.assertFalse(trash.known(self.ws, "f" * 32))
        self.assertFalse(trash.known(self.ws, "not an id"))
        (self.root / "two.txt").write_text("two\n")
        prepared = trash.prepare(self.ws, ["two.txt"])            # room at the first check
        full = [{}] * tw.MAX_JOURNALS
        with patch.object(trash, "recovery_records", side_effect=[[], full]) as listed, \
                self.assertRaises(trash.TrashFull):
            trash.execute(prepared, prepared.authorize())          # another deletion took the last entry meanwhile
        self.assertEqual(listed.call_count, 2)                     # the second look is the one under the lock
        self.assertEqual((self.root / "two.txt").read_text(), "two\n")
        self.assertEqual(len(trash.recovery_records(self.ws)), 1)

    def test_trash_prune_refuses_a_task_plan_id(self):
        with self.assertRaisesRegex(ValueError, "prune approval"):
            trash.prune(self.ws, {"a" * 64: "b" * 64})


class PruneCliTests(unittest.TestCase):
    def args(self, details=False):
        return SimpleNamespace(workspace="/tmp/project", details=details)

    def record(self, plan, status="rolled-back", prunable=True, kind=None, modified=1):
        value = {"plan": plan, "status": status, "files": [{"path": "one.txt", "action": "restore"}],
                 "journal_sha256": "f" * 64, "modified": modified, "prunable": prunable}
        if kind:
            value["kind"] = kind
        return value

    def run_prune(self, records, answers, moved=None):
        replies = iter(answers)
        output = io.StringIO()
        with patch.object(cli, "confirm", side_effect=lambda action, reader: next(replies) == "y") as confirm, \
                patch.object(cli.workspace, "prune", return_value=1) as tasks, \
                patch.object(transfer, "prune", return_value=1) as transfers, \
                patch.object(trash, "prune", return_value=1) as deleted, contextlib.redirect_stdout(output):
            code = cli.prune_history(self.args(), records, lambda prompt: next(replies))
        if moved is not None:
            moved.append(transfers)
        return code, output.getvalue(), confirm, tasks, deleted

    def test_a_finished_move_reaches_the_transfer_engine_and_is_shown_as_a_move(self):
        record = dict(self.record("d" * 32, "undone", kind="transfer"), operation="move", files=[],
                      **{"from": "source/package", "to": "target/package"})
        moved = []
        code, text, _, tasks, deleted = self.run_prune([record], ["1", "y"], moved)
        self.assertEqual(code, 0)
        self.assertIn("Move: source/package  ->  target/package", text)
        moved[0].assert_called_once_with("/tmp/project", {"d" * 32: "f" * 64})
        tasks.assert_not_called()
        deleted.assert_not_called()

    def test_nothing_finished_asks_nothing(self):
        code, text, confirm, tasks, _ = self.run_prune([self.record("a" * 64, "prepared", False)], [])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to remove", text)
        self.assertIn("Files still in the trash, moves and copies that can still be undone, and unfinished tasks stay", text)
        confirm.assert_not_called()
        tasks.assert_not_called()

    def test_chosen_numbers_reach_the_matching_engine_with_their_hashes(self):
        records = [self.record("a" * 64, "applied"), self.record("b" * 32, "restored", kind="trash"),
                   self.record("c" * 64, "prepared", False)]
        code, text, confirm, tasks, deleted = self.run_prune(records, ["x", "1 2", "y"])
        self.assertEqual(code, 0)
        self.assertIn("Enter numbers from 1 to 2", text)
        self.assertIn("1 other entry stays: files still in the trash", text)
        self.assertIn("can no longer be undone", text)
        self.assertIn("files in the project are not changed", text)
        tasks.assert_called_once_with("/tmp/project", {"a" * 64: "f" * 64})
        deleted.assert_called_once_with("/tmp/project", {"b" * 32: "f" * 64})
        self.assertIn("Removed 2 entries.", text)

    def test_all_and_cancel(self):
        records = [self.record("a" * 64), self.record("b" * 64)]
        code, text, _, tasks, deleted = self.run_prune(records, ["all", "y"])
        self.assertEqual(code, 0)
        tasks.assert_called_once_with("/tmp/project", {"a" * 64: "f" * 64, "b" * 64: "f" * 64})
        deleted.assert_not_called()
        self.assertNotIn("can no longer be undone", text)
        for answers in (["n"], ["1", "n"]):
            with self.subTest(answers=answers):
                code, text, _, tasks, _ = self.run_prune(records, answers)
                self.assertEqual(code, 2)
                self.assertIn("Nothing was removed", text)
                tasks.assert_not_called()

    def test_restore_of_an_unknown_id_is_refused_before_the_consent(self):
        args = SimpleNamespace(workspace="/tmp/project", plan="a" * 32, details=False)
        with patch.object(trash, "known", return_value=False), \
                patch.object(cli, "confirm") as confirm, patch.object(trash, "restore") as restore, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "unknown plan"):
            cli.restore_deleted(args, lambda prompt: "y")
        confirm.assert_not_called()
        restore.assert_not_called()

    def test_chosen_answer_parsing(self):
        self.assertEqual(cli._chosen("ALL", 3), [0, 1, 2])
        self.assertEqual(cli._chosen("3,1 1", 3), [0, 2])
        for answer in ("", "0", "4", "1-2", "1 x"):
            with self.subTest(answer=answer):
                self.assertIsNone(cli._chosen(answer, 3))

    def test_expired_choice_is_refused(self):
        clock = iter([0, 181])
        with patch.object(cli.time, "monotonic", side_effect=lambda: next(clock)), \
                patch.object(cli.workspace, "prune") as tasks, contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(ValueError, "expired consent"):
            cli.prune_history(self.args(), [self.record("a" * 64)], lambda prompt: "1")
        tasks.assert_not_called()

    def test_prune_needs_a_terminal_and_the_limit_message_names_it(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=False), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["prune", "--workspace", "/tmp/project"]), 2)
        self.assertIn("interactive terminal", out.getvalue())
        with patch.object(cli.workspace, "recovery_records", side_effect=tw.JournalLimit("journal limit")), \
                patch.object(cli.sys.stdin, "isatty", return_value=True), \
                patch.object(cli.sys.stdout, "isatty", return_value=True), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["status", "--workspace", "/tmp/project"]), 2)
        self.assertIn("ai-desktop task prune --workspace /tmp/project", out.getvalue())
        self.assertIn("task prune", trash.FULL_MESSAGE)


if __name__ == "__main__":
    unittest.main()
