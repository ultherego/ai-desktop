"""Suite edits overlay selected changes on a full policy-bound local copy."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_desktop import permissions, project_edit_test, project_scope, task_cli, task_sandbox


class Fixture(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory(dir=pwd.getpwuid(os.getuid()).pw_dir)))
        self.enterContext(patch.object(permissions, "_identity", return_value=(os.getuid(), str(self.home))))
        self.config = self.home / ".config/ai-desktop/permissions.toml"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('version = 2\n[files]\nread = ["~/project/**"]\n'
                               'write = ["~/project/**"]\ndeny = ["~/project/private/**"]\n')
        self.config.chmod(0o600)
        self.root = self.home / "project"
        self.root.mkdir()
        self.target = self.root / "calc.py"
        self.original = "from dependency import VALUE\ndef answer(): return VALUE + 1\n"
        self.target.write_text(self.original)
        (self.root / "dependency.py").write_text("VALUE = 40\n")
        (self.root / "test_calc.py").write_text("from calc import answer\ndef test_answer(): assert answer() == 42\n")
        (self.root / "private").mkdir()
        (self.root / "private/secret").write_text("NEVER-COPY-OR-SEND\n")
        self.source = {"instruction": "Fix the answer", "files": [
            {"path": "calc.py", "content": self.original, "writable": True}]}
        self.proposed = [{"path": "calc.py", "content": self.original.replace("+ 1", "+ 2")}]
        self.before = {"workspace": str(self.root), "files": {"calc.py": {"file": {"content": self.original}}}}
        self.output = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.consent = self.enterContext(patch.object(project_edit_test, "confirm", return_value=True))
        self.runner = self.enterContext(patch.object(task_sandbox, "run_copy", return_value={
            "passed": True, "exit_code": 0, "output": "LOCAL-TEST-OUTPUT", "check": "pytest"}))

    def prepared(self):
        return project_edit_test.PreparedProjectTest(permissions.load_policy(), str(self.root), str(self.root), self.before)

    def run_test(self, prepared=None, source=None, proposed=None):
        return (prepared or self.prepared()).run(source or self.source, proposed or self.proposed,
                                                "pytest", lambda _: "yes", Mock())


class ProjectEditTestTests(Fixture):
    def test_unselected_dependencies_and_tests_are_copied_but_model_source_stays_selected(self):
        source, proposed = copy.deepcopy(self.source), copy.deepcopy(self.proposed)
        prepared = self.prepared()
        result = self.run_test(prepared)
        self.assertTrue(result["passed"])
        files, check = self.runner.call_args.args
        self.assertEqual(check, "pytest")
        self.assertEqual(set(files), {"calc.py", "dependency.py", "test_calc.py"})
        self.assertEqual(files["calc.py"], self.proposed[0]["content"])
        self.assertEqual(files["dependency.py"], "VALUE = 40\n")
        self.assertNotIn("NEVER-COPY-OR-SEND", repr(files))
        self.assertEqual((self.source, self.proposed), (source, proposed))
        self.assertEqual(self.target.read_text(), self.original)
        self.assertNotIn("LOCAL-TEST-OUTPUT", self.output.getvalue())
        self.consent.assert_called_once_with("project-test", reader=unittest.mock.ANY)
        self.assertIsNotNone(prepared._proof)

    def test_parent_workspace_maps_its_selected_target_to_project_relative_path(self):
        self.before["workspace"] = str(self.home)
        self.before["files"] = {"project/calc.py": self.before["files"]["calc.py"]}
        self.source["files"][0]["path"] = "project/calc.py"
        self.proposed[0]["path"] = "project/calc.py"
        prepared = project_edit_test.PreparedProjectTest(permissions.load_policy(), str(self.root), str(self.home), self.before)
        self.assertTrue(self.run_test(prepared)["passed"])
        self.assertIn("calc.py", self.runner.call_args.args[0])
        self.assertNotIn("project/calc.py", self.runner.call_args.args[0])

    def test_decline_and_failed_suite_never_produce_proof(self):
        prepared = self.prepared()
        self.consent.return_value = False
        self.assertFalse(self.run_test(prepared)["passed"])
        self.runner.assert_not_called()
        self.assertIsNone(prepared._proof)
        self.consent.return_value = True
        self.runner.return_value = {"passed": False, "exit_code": 1, "output": "failure", "check": "pytest"}
        self.assertFalse(self.run_test(prepared)["passed"])
        self.assertIsNone(prepared._proof)

    def test_dependency_changes_during_consent_or_tests_or_after_success_refuse(self):
        dependency = self.root / "dependency.py"
        prepared = self.prepared()
        self.consent.side_effect = lambda *args, **kwargs: dependency.write_text("VALUE = 1\n") or True
        with self.assertRaises(project_scope.ProjectError):
            self.run_test(prepared)
        self.runner.assert_not_called()
        self.consent.side_effect = None
        prepared = self.prepared()
        def suite(*args, **kwargs):
            dependency.write_text("VALUE = 2\n")
            return {"passed": True, "exit_code": 0, "output": "", "check": "pytest"}
        self.runner.side_effect = suite
        with self.assertRaises(project_scope.ProjectError):
            self.run_test(prepared)
        self.assertIsNone(prepared._proof)
        self.runner.side_effect = None
        prepared = self.prepared()
        self.assertTrue(self.run_test(prepared)["passed"])
        dependency.write_text("VALUE = 3\n")
        with self.assertRaises(project_scope.ProjectError):
            prepared.check()

    def test_source_mismatch_readonly_target_and_outside_project_refuse(self):
        for mutate in (lambda: self.source["files"][0].update(content="unapproved"),
                       lambda: self.source["files"][0].update(writable=False)):
            saved = copy.deepcopy(self.source)
            mutate()
            with self.assertRaises(ValueError):
                self.run_test()
            self.source = saved
        self.before["workspace"] = str(self.home)
        prepared = project_edit_test.PreparedProjectTest(permissions.load_policy(), str(self.root), str(self.home), self.before)
        with self.assertRaises(ValueError):
            self.run_test(prepared)
        self.runner.assert_not_called()

    def test_new_file_enters_local_copy_and_appeared_target_refuses(self):
        self.before["files"]["new.py"] = {"file": None}
        self.source["files"].append({"path": "new.py", "content": None, "writable": True})
        self.proposed.append({"path": "new.py", "content": "NEW = True\n"})
        self.assertTrue(self.run_test()["passed"])
        self.assertEqual(self.runner.call_args.args[0]["new.py"], "NEW = True\n")
        self.assertFalse((self.root / "new.py").exists())
        prepared = self.prepared()
        (self.root / "new.py").write_text("concurrent\n")
        self.runner.reset_mock()
        with self.assertRaises(project_scope.ProjectError):
            self.run_test(prepared)
        self.runner.assert_not_called()

    def test_overlay_enforces_copy_budget_and_original_windows_bytes(self):
        self.config.write_text(self.config.read_text() + "test_copy_file_size = 1024\n")
        self.proposed[0]["content"] = "#" * 1025
        with self.assertRaises(project_scope.TestScopeRefused):
            self.run_test()
        self.runner.assert_not_called()
        self.original = "\ufeffVALUE = 1\r\n"
        self.target.write_bytes(self.original.encode())
        self.before["files"]["calc.py"]["file"]["content"] = self.original
        self.source["files"][0]["content"] = "VALUE = 1\n"
        self.proposed[0]["content"] = "VALUE = 2\n"
        self.assertTrue(self.run_test()["passed"])
        self.assertEqual(self.runner.call_args.args[0]["calc.py"], "\ufeffVALUE = 2\r\n")

    def test_policy_revocation_binary_file_and_exclusion_changes_refuse(self):
        prepared = self.prepared()
        self.config.write_text(self.config.read_text() + "# changed\n")
        with self.assertRaises(ValueError):
            self.run_test(prepared)
        (self.root / "binary.dat").write_bytes(b"\xff")
        with self.assertRaises(project_scope.ProjectError):
            self.run_test()
        self.runner.assert_not_called()
        (self.root / "binary.dat").unlink()
        prepared = self.prepared()
        (self.root / "new-link").symlink_to(self.target)
        with self.assertRaises(project_scope.ProjectError):
            prepared.check()

    def test_finish_blocks_failed_suite_and_rechecks_dependencies_after_write_consent(self):
        args = SimpleNamespace(test="pytest", details=False, format="plain", workspace=str(self.root))
        response = json.dumps({"summary": "Synthetic change", "files": self.proposed})
        with patch.object(task_cli.workspace, "unchanged"), patch.object(task_cli.workspace, "apply") as apply, \
                patch.object(task_cli, "confirm", return_value=True) as consent:
            self.runner.return_value = {"passed": False, "exit_code": 1, "output": "failed", "check": "pytest"}
            self.assertEqual(task_cli.finish_task(self.before, self.source, response, args, lambda _: "yes",
                                                 project_test=self.prepared()), 2)
            apply.assert_not_called()
            consent.assert_not_called()
            self.runner.return_value = {"passed": True, "exit_code": 0, "output": "passed", "check": "pytest"}
            self.assertEqual(task_cli.finish_task(self.before, self.source, response, args, lambda _: "yes",
                                                 project_test=self.prepared()), 0)
            apply.assert_called_once()
            self.assertTrue(callable(apply.call_args.kwargs["write_guard"]))
            apply.reset_mock()
            def concurrent_change(kind, **kwargs):
                self.assertEqual(kind, "write")
                (self.root / "dependency.py").write_text("VALUE = 0\n")
                return True
            consent.side_effect = concurrent_change
            with self.assertRaises(project_scope.ProjectError):
                task_cli.finish_task(self.before, self.source, response, args, lambda _: "yes",
                                     project_test=self.prepared())
            apply.assert_not_called()


class ApprovedProjectTestTests(Fixture):
    """S9: the owner's earlier yes for exactly this declared test replaces the second question, once."""

    def run_approved(self, approved, prepared=None):
        return (prepared or self.prepared()).run(self.source, self.proposed, "pytest", lambda _: "yes", Mock(),
                                                 approved=approved)

    def test_a_valid_earlier_approval_runs_the_test_without_asking_again(self):
        spent = []
        result = self.run_approved(lambda: spent.append(True))
        self.assertTrue(result["passed"])
        self.assertEqual(spent, [True])
        self.consent.assert_not_called()
        self.assertIn("Running the test you approved with the change.", self.output.getvalue())

    def test_an_expired_or_changed_approval_asks_here_and_a_no_runs_nothing(self):
        from ai_desktop.analysis_contract import AnalysisDenied

        def expired():
            raise AnalysisDenied("consent")
        self.consent.return_value = False
        result = self.run_approved(expired)
        self.assertFalse(result["passed"])
        self.consent.assert_called_once_with("project-test", reader=unittest.mock.ANY)
        self.runner.assert_not_called()
        self.assertIn("no longer valid", self.output.getvalue())

    def test_a_changed_dependency_still_refuses_after_an_earlier_approval(self):
        prepared = self.prepared()
        (self.root / "dependency.py").write_text("VALUE = 41\n")
        with self.assertRaises(project_scope.ProjectError):
            self.run_approved(lambda: None, prepared)
        self.runner.assert_not_called()

    def test_finish_refuses_an_approval_without_a_project_copy_or_beside_a_task_consent(self):
        from ai_desktop.analysis_contract import AnalysisDenied
        args = SimpleNamespace(test="pytest", details=False, format="plain", workspace=str(self.root))
        response = json.dumps({"summary": "Synthetic change", "files": self.proposed})
        with patch.object(task_cli.workspace, "unchanged"), patch.object(task_cli.workspace, "apply") as apply, \
                patch.object(task_cli, "confirm", return_value=True):
            with self.assertRaises(AnalysisDenied):
                task_cli.finish_task(self.before, self.source, response, args, lambda _: "yes",
                                     approved_test=lambda: None)
            with self.assertRaises(AnalysisDenied):
                task_cli.finish_task(self.before, self.source, response, args, lambda _: "yes",
                                     project_test=self.prepared(), approved_test=lambda: None, test_consent=object())
            apply.assert_not_called()
            self.assertEqual(task_cli.finish_task(self.before, self.source, response, args, lambda _: "yes",
                                                 project_test=self.prepared(), approved_test=lambda: None), 0)
            apply.assert_called_once()
        self.consent.assert_not_called()


class OutcomeReasonTests(Fixture):
    """S9: the controller's own reason for a result, recorded with the next step (never the model's words)."""

    def finish(self, outcome, **patches):
        args = SimpleNamespace(test="pytest", details=False, format="plain", workspace=str(self.root))
        response = json.dumps({"summary": "Synthetic change", "files": self.proposed})
        with patch.object(task_cli.workspace, "unchanged"), patch.object(task_cli.workspace, "apply"), \
                patch.object(task_cli, "confirm", return_value=patches.get("write", True)):
            return task_cli.finish_task(self.before, self.source, response, args, lambda _: "yes",
                                        project_test=self.prepared(), outcome=outcome)

    def test_each_result_names_its_reason(self):
        outcome = {}
        self.assertEqual(self.finish(outcome), 0)
        self.assertEqual(outcome["reason"], "saved")
        # The exact saved task, so a wide write scope's long history needs no list to pick from.
        self.assertRegex(self.output.getvalue(), r"Undo: ai-desktop task rollback --workspace \S+ --plan [0-9a-f]{12}\n")
        outcome = {}
        self.assertEqual(self.finish(outcome, write=False), 2)
        self.assertEqual(outcome["reason"], "write-declined")
        self.runner.return_value = {"passed": False, "exit_code": 1, "output": "failed", "check": "pytest"}
        outcome = {}
        self.assertEqual(self.finish(outcome), 2)
        self.assertEqual(outcome["reason"], "test-failed")
        self.consent.return_value = False
        outcome = {}
        self.assertEqual(self.finish(outcome), 2)
        self.assertEqual(outcome["reason"], "test-cancelled")

    def test_the_recorded_fact_has_the_next_step(self):
        from ai_desktop import ask_edit
        response = json.dumps({"summary": "Fix", "files": self.proposed})
        self.assertIn("Next: fix the change or the test", ask_edit._change_fact(response, 2, "pytest", "test-failed"))
        self.assertIn("test pytest failed", ask_edit._change_fact(response, 2, "pytest", "test-failed"))
        self.assertIn("declined the save", ask_edit._change_fact(response, 2, "pytest", "write-declined"))
        self.assertIn("undo: ai-desktop task rollback", ask_edit._change_fact(response, 0, "pytest", "saved"))
        self.assertIn("code 2", ask_edit._change_fact(response, 2, "pytest", None))
        self.assertNotIn(str(self.root), ask_edit._change_fact(response, 0, "pytest", "saved"))


if __name__ == "__main__":
    unittest.main()
