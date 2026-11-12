"""Pure controller integration; filesystem executor and model are never run."""
from contextlib import nullcontext, redirect_stdout
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_desktop import ask_cli, ask_edit, assistant_flow, task_cli
from ai_desktop.analysis_contract import AnalysisConsent, AnalysisDenied
from ai_desktop.permissions import Scope
from ai_desktop.question_consent import QuestionConsent
from ai_desktop.terminal_consent import PROMPTS
from tests.profile_fixture import DEFAULT_PROFILE


WORKSPACE = "/home/synthetic/project"
QUESTION = "Napraw dodawanie i utwórz wynik."
ACCESS = {"files": [{"id": "f0", "pattern": WORKSPACE + "/**"}],
          "systemd": [], "weather": False,
          "write": [{"id": "w0", "pattern": WORKSPACE + "/*"}]}
PLAN = {"action": "edit", "write": "w0", "files": ["f0"], "create": ["result.txt"], "check": "text"}
ORIGINAL = "def add(a, b):\n    return a - b\n" + "# synthetic padding\n" * 460
PROPOSAL = {"summary": "Poprawiono dodawanie i dodano wynik.", "files": [
    {"path": "main.py", "content": ORIGINAL.replace("a - b", "a + b")},
    {"path": "result.txt", "content": "SYNTHETIC RESULT\n"}]}


class AskEditTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("ai_desktop.agent_profiles.resolve", return_value=DEFAULT_PROFILE))
        self.output = io.StringIO()
        self.output.isatty = lambda: True
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("unexpected process")))
        self.policy = Mock()
        self.snapshot = assistant_flow.AccessSnapshot(self.policy,
            {"claude": True, "systemd": [], "weather": False}, copy.deepcopy(ACCESS))
        self.policy.access.return_value = copy.deepcopy(ACCESS)
        self.policy.scopes.return_value = (Scope(0, WORKSPACE + "/**", WORKSPACE, "tree"),)
        self.policy.write_scopes.return_value = (Scope(0, WORKSPACE + "/*", WORKSPACE, "children"),)
        self.policy.write_workspace.return_value = WORKSPACE
        self.paths = (WORKSPACE + "/main.py", WORKSPACE + "/tests/info.txt")
        self.policy.expand.return_value = self.paths
        self.policy.select.side_effect = lambda paths, limit=None: tuple(paths)
        self.policy.can_write.side_effect = lambda path, index=None: index == 0 and path.rsplit("/", 1)[0] == WORKSPACE
        def require_write(paths, *, index=None):
            if not paths or any(not self.policy.can_write(path, index=index) for path in paths):
                raise ValueError("files-write-denied")
            return tuple(paths)
        self.policy.require_write.side_effect = require_write
        contents = (ORIGINAL, "SYNTHETIC READONLY\n")
        self.bundle = {"kind": "files", "files": [{"path": path, "content": content}
                                                     for path, content in zip(self.paths, contents)]}
        def record(content, inode):
            return {"inode": [1, inode], "mode": 0o600, "sha256": "a" * 64, "content": content}
        self.before = {"workspace": WORKSPACE, "root": [1, 2], "files": {
            "main.py": {"parent": [1, 2], "file": record(ORIGINAL, 3)},
            "tests/info.txt": {"parent": [1, 4], "file": record(contents[1], 5)},
            "result.txt": {"parent": [1, 2], "file": None}}}
        def snapshot(workspace, names, *, must_be_absent=()):
            self.assertEqual(workspace, WORKSPACE)
            if any(self.before["files"][name]["file"] is not None for name in must_be_absent):
                raise ValueError("creation target exists")
            return dict(self.before, files={name: copy.deepcopy(self.before["files"][name]) for name in names})
        self.capture = self.enterContext(patch.object(ask_edit.task_workspace, "snapshot", side_effect=snapshot))
        self.unchanged = self.enterContext(patch.object(ask_edit.task_workspace, "unchanged"))
        self.capacity = self.enterContext(patch.object(ask_edit.task_workspace, "_can_apply"))
        self.read = self.enterContext(patch.object(ask_edit.permissions, "read_selected", return_value=self.bundle))
        self.now = [0]
        self.permit = QuestionConsent(QUESTION, self.snapshot, clock=lambda: self.now[0])
        self.permit.approve()
        self.args = SimpleNamespace(details=False, format="plain")

    def prepare(self, plan=PLAN):
        return ask_edit.prepare(copy.deepcopy(plan), self.snapshot, self.args, Mock(), question_consent=self.permit)

    def planner(self):
        gate = AnalysisConsent(None, question=QUESTION, access=self.snapshot.access)
        self.permit.claim(gate, QUESTION, access=self.snapshot.access)
        self.permit.authorize(gate, QUESTION, access=self.snapshot.access)

    def test_suite_prepares_independent_local_copy_without_adding_it_to_model_source(self):
        from ai_desktop import project_edit_test
        helper = Mock()
        with patch("ai_desktop.project_checks.current_directory", return_value=WORKSPACE), \
                patch.object(project_edit_test, "PreparedProjectTest", return_value=helper) as prepare:
            prepared = self.prepare(dict(PLAN, check="pytest"))
        self.assertIs(prepared.project_test, helper)
        prepare.assert_called_once_with(self.policy, WORKSPACE, WORKSPACE, prepared.before)
        self.assertEqual(set(prepared.source), {"instruction", "files"})
        self.assertEqual({item["path"] for item in prepared.source["files"]},
                         {"main.py", "tests/info.txt", "result.txt"})
        with patch.object(task_cli, "finish_task", return_value=0) as finish:
            ask_edit.finish(prepared, json.dumps(PROPOSAL), self.args, Mock())
        self.assertIs(finish.call_args.kwargs["project_test"], helper)

    def large(self, version="a" * 64):
        """main.py is above read_file_size and Claude already read its second line in this question."""
        self.policy.limits.return_value = SimpleNamespace(read_file_size=1024, read_total_size=4096, read_files=64)
        self.enterContext(patch.object(ask_edit.permissions, "recheck_selection", side_effect=lambda policy, paths: tuple(paths)))
        self.read.return_value = {"kind": "files", "files": [self.bundle["files"][1]]}
        line = "    return a - b\n"
        start = ORIGINAL.index(line)
        return {WORKSPACE + "/main.py": {"version": version, "size": len(ORIGINAL.encode()), "fragments": [
            {"first": 2, "last": 2, "start": start, "end": start + len(line), "text": line}]}}

    def test_a_file_above_read_file_size_goes_out_only_as_the_excerpts_already_read(self):
        prepared = ask_edit.prepare(copy.deepcopy(PLAN), self.snapshot, self.args, Mock(), question_consent=self.permit,
                                    excerpts=self.large())
        wire = {item["path"]: item for item in prepared.source["files"]}
        self.assertEqual(set(wire["main.py"]), {"path", "excerpts", "size", "writable"})
        self.assertEqual(wire["main.py"]["excerpts"], [{"first": 2, "last": 2, "text": "    return a - b\n"}])
        self.assertNotIn("synthetic padding", json.dumps(prepared.source))         # the whole file never leaves
        self.read.assert_called_once_with(self.policy, (WORKSPACE + "/tests/info.txt",))
        local = {item["path"]: item for item in prepared.local["files"]}
        self.assertEqual(local["main.py"]["content"], ORIGINAL)
        reply = json.dumps({"summary": "Poprawiono znak.", "files": [
            {"path": "main.py", "replacements": [{"old": "return a - b", "new": "return a + b"}]}]})
        with patch.object(task_cli, "finish_task", return_value=0) as finish:
            ask_edit.finish(prepared, reply, self.args, Mock())
        self.assertIs(finish.call_args.args[1], prepared.local)
        self.assertEqual(finish.call_args.kwargs["excerpts"], {"main.py": ["    return a - b\n"]})

    def test_a_large_file_changed_since_its_excerpts_were_read_is_a_conflict(self):
        with self.assertRaises(ask_edit.EditConflict):
            ask_edit.prepare(copy.deepcopy(PLAN), self.snapshot, self.args, Mock(), question_consent=self.permit,
                             excerpts=self.large(version="b" * 64))

    def test_a_file_too_large_without_excerpts_is_refused_with_the_setting_named(self):
        self.policy.limits.return_value = SimpleNamespace(read_file_size=1024, read_total_size=4096, read_files=64)
        self.read.side_effect = ValueError("files-size")
        with self.assertRaises(ask_edit.EditDenied) as caught:
            self.prepare()
        self.assertIn("files.read_file_size (1.0 KiB)", caught.exception.hint)
        self.assertIn("find the place to change first", caught.exception.hint)

    def test_read_and_create_are_bound_to_one_workspace_with_readonly_context(self):
        prepared = self.prepare()
        self.assertEqual(prepared.workspace, WORKSPACE)
        self.assertEqual(prepared.source["instruction"], QUESTION)
        self.assertEqual([(item["path"], item["writable"]) for item in prepared.source["files"]],
                         [("main.py", True), ("tests/info.txt", False), ("result.txt", True)])
        self.assertGreater(len(prepared.source["files"][0]["content"].encode()), 4096)
        self.assertEqual(prepared.source["files"][0]["content"], ORIGINAL)
        self.assertIsNone(prepared.source["files"][2]["content"])
        self.capture.assert_called_once_with(WORKSPACE, ["main.py", "tests/info.txt", "result.txt"],
                                             must_be_absent=["result.txt"])
        self.capacity.assert_called_once_with(WORKSPACE)

    def test_a_file_with_a_bom_and_windows_line_ends_goes_to_the_model_as_plain_text(self):
        record = self.before["files"]["main.py"]["file"]
        record["content"] = "\ufeff" + ORIGINAL.replace("\n", "\r\n")
        prepared = self.prepare()
        self.assertEqual(prepared.source["files"][0]["content"], ORIGINAL)
        self.assertEqual(prepared.before["files"]["main.py"]["file"]["content"], record["content"])

    def test_a_file_whose_bytes_cannot_be_kept_is_explained_and_refused(self):
        self.capture.side_effect = ValueError("text")
        with self.assertRaises(ask_edit.EditDenied):
            self.prepare()
        self.assertIn("mixed line endings", self.output.getvalue())

    def test_outside_workspace_read_scope_is_refused_before_content_read(self):
        self.policy.expand.return_value = ("/home/synthetic/other/info.txt",)
        with self.assertRaises(ask_edit.EditDenied):
            self.prepare()
        self.read.assert_not_called()
        self.capture.assert_not_called()

    def test_narrow_selection_uses_only_bound_subpath_not_entire_scope(self):
        self.policy.expand_selected.return_value = self.paths
        plan = dict(PLAN, files=[{"id": "f0", "path": "main.py"},
                                 {"id": "f0", "path": "tests/info.txt"}])
        prepared = self.prepare(plan)
        self.policy.expand.assert_not_called()
        self.policy.expand_selected.assert_called_once_with(((0, "main.py"), (0, "tests/info.txt")))
        self.read.assert_called_once_with(self.policy, self.paths)
        self.assertEqual(prepared.write_grant._read_paths, self.paths)
        prepared.write_grant.check()

    def test_narrow_selection_denial_prevents_read_snapshot_and_transfer(self):
        self.policy.expand_selected.side_effect = ValueError("files-denied")
        with self.assertRaises(ask_edit.EditDenied):
            self.prepare(dict(PLAN, files=[{"id": "f0", "path": "private/**"}]))
        self.policy.expand.assert_not_called()
        self.read.assert_not_called()
        self.capture.assert_not_called()

    def test_narrow_selected_path_outside_write_workspace_is_not_read(self):
        self.policy.expand_selected.return_value = ("/home/synthetic/other/secret.txt",)
        with self.assertRaises(ask_edit.EditDenied):
            self.prepare(dict(PLAN, files=[{"id": "f0", "path": "main.py"}]))
        self.read.assert_not_called()
        self.capture.assert_not_called()

    def test_create_only_does_not_open_read_scopes_and_requires_absence(self):
        prepared = self.prepare(dict(PLAN, files=[]))
        self.policy.expand.assert_not_called()
        self.read.assert_not_called()
        self.assertEqual(prepared.source["files"], [{"path": "result.txt", "content": None, "writable": True}])
        self.capture.assert_called_once_with(WORKSPACE, ["result.txt"], must_be_absent=["result.txt"])

    def test_existing_creation_target_is_refused_without_reading_its_content(self):
        self.before["files"]["result.txt"]["file"] = {"content": "PRIVATE PREEXISTING"}
        with self.assertRaises(ask_edit.EditDenied):
            self.prepare(dict(PLAN, files=[]))
        self.read.assert_not_called()
        self.assertNotIn("PRIVATE PREEXISTING", self.output.getvalue())

    def test_unadvertised_scope_and_new_path_outside_selected_scope_are_refused(self):
        with self.assertRaises(ask_edit.EditDenied):
            self.prepare(dict(PLAN, write="w1"))
        self.policy.write_workspace.assert_not_called()
        self.read.assert_not_called()
        with self.assertRaises(ask_edit.EditDenied):
            self.prepare(dict(PLAN, files=[], create=["tests/new.txt"]))
        self.capture.assert_not_called()

    def test_source_snapshot_and_captured_policy_mutations_revoke_write_grant(self):
        prepared = self.prepare()
        mutations = (
            lambda: prepared.source["files"][0].update(content="MUTATED"),
            lambda: prepared.before.update(root=[9, 9]),
            lambda: self.snapshot.access["write"][0].update(pattern="/other/*"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                original_source = copy.deepcopy(prepared.source)
                original_before = copy.deepcopy(prepared.before)
                original_access = copy.deepcopy(self.snapshot.access)
                mutate()
                with self.assertRaises(ask_edit.EditDenied):
                    prepared.write_grant.check()
                prepared.source.clear(); prepared.source.update(original_source)
                prepared.before.clear(); prepared.before.update(original_before)
                self.snapshot.access.clear(); self.snapshot.access.update(original_access)
        self.unchanged.side_effect = ValueError("PRIVATE CHANGED FILE")
        with self.assertRaises(ask_edit.EditDenied):
            prepared.write_grant.check()

    def test_completed_question_cannot_transfer_again_but_write_review_has_separate_ttl(self):
        prepared = self.prepare()
        self.now[0] = 181
        self.permit.close()
        prepared.write_grant.check()
        with self.assertRaises(AnalysisDenied):
            prepared.write_grant.require_transfer(self.permit, prepared.source)
        self.policy.recheck.side_effect = ValueError("PRIVATE REVOKED POLICY")
        with self.assertRaises(ask_edit.EditDenied):
            prepared.write_grant.check()

    def test_grant_freezes_scope_index_read_paths_and_write_targets(self):
        prepared = self.prepare()
        grant = prepared.write_grant
        for field, value in (("_index", 1), ("_read_paths", (WORKSPACE + "/other.txt",)),
                             ("_targets", (WORKSPACE + "/other.txt",))):
            with self.subTest(field=field):
                original = getattr(grant, field)
                setattr(grant, field, value)
                with self.assertRaises(ask_edit.EditDenied):
                    grant.check()
                setattr(grant, field, original)

    def test_finish_maps_executor_and_recursive_json_failures_without_raw_details(self):
        prepared = self.prepare()
        for error in (ask_edit.ExecutionDenied("PRIVATE EXECUTOR"), RecursionError("PRIVATE JSON")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(task_cli, "finish_task", side_effect=error), self.assertRaises(ask_edit.EditDenied) as refused:
                ask_edit.finish(prepared, "untrusted", self.args, Mock())
            self.assertNotIn("PRIVATE", str(refused.exception))

    def test_a_version_conflict_or_a_busy_directory_ends_only_this_proposal(self):
        """Review of 132: another window or an editor changing the file ended the whole conversation."""
        from ai_desktop import conversation_state
        prepared = self.prepare()
        for error, kind in ((ValueError("workspace changed"), ask_edit.EditConflict),
                            (ValueError("changed target"), ask_edit.EditConflict),
                            (BlockingIOError(), ask_edit.EditBusy)):
            with self.subTest(error=error), conversation_state.recording() as recorder, \
                    patch.object(task_cli, "finish_task", side_effect=error), self.assertRaises(kind):
                ask_edit.finish(prepared, "untrusted", self.args, Mock())
            self.assertTrue(any("NOT saved" in fact for fact in recorder.actions))     # remembered as not applied
            self.assertTrue(issubclass(kind, ask_cli_refusals()))
        with patch.object(task_cli, "finish_task", side_effect=ValueError("other")), self.assertRaises(ValueError):
            ask_edit.finish(prepared, "untrusted", self.args, Mock())                   # unexpected stays unexpected
        from ai_desktop import ask_cli
        import contextlib, io
        for refusal, words in ((ask_edit.EditConflict("input"), "changed after it was read"),
                               (ask_edit.EditBusy("input"), "another window is saving")):
            with contextlib.redirect_stdout(io.StringIO()) as said:
                ask_cli._explain_refusal(refusal)
            self.assertIn(words, said.getvalue())

    def test_a_save_stopped_part_way_reports_what_was_saved_and_the_recovery(self):
        from ai_desktop import ask_cli, conversation_state, task_workspace
        import contextlib, io
        prepared = self.prepare()
        with conversation_state.recording() as recorder, \
                patch.object(task_cli, "finish_task", side_effect=task_workspace.SaveInterrupted(1, 2)), \
                self.assertRaises(ask_edit.EditInterrupted) as stopped:
            ask_edit.finish(prepared, "untrusted", self.args, Mock())
        fact = " ".join(recorder.actions)
        self.assertIn("INTERRUPTED", fact)
        self.assertIn("1 of 2", fact)
        self.assertNotIn("NOT saved", fact)                         # never "nothing was saved"
        with contextlib.redirect_stdout(io.StringIO()) as said:
            ask_cli._explain_refusal(stopped.exception)
        self.assertIn("1 of 2 file(s) were confirmed saved", said.getvalue())
        self.assertIn("task rollback", said.getvalue())
        self.assertIsInstance(stopped.exception, ask_cli.LOCAL_REFUSALS)   # the conversation goes on

    def test_a_file_saved_elsewhere_before_the_yes_is_a_conflict_not_a_policy_problem(self):
        """Regression: "aaaaa" written to the file while its diff was shown gave the generic policy hint,
        because the write approval's own check turned the version conflict into a plain refusal."""
        from ai_desktop import conversation_state
        prepared = self.prepare()
        with patch.object(ask_edit.task_workspace, "unchanged", side_effect=ValueError("workspace changed")):
            with self.assertRaises(ask_edit.EditConflict):
                prepared.write_grant.check()

            def finish_task(*args, write_guard=None, **kwargs):
                write_guard()                                    # the check right before the write
            with conversation_state.recording() as recorder, \
                    patch.object(task_cli, "finish_task", side_effect=finish_task), \
                    self.assertRaises(ask_edit.EditConflict):
                ask_edit.finish(prepared, "untrusted", self.args, Mock())
        self.assertTrue(any("NOT saved" in fact for fact in recorder.actions))
        with patch.object(ask_edit.task_workspace, "unchanged", side_effect=ValueError("other")), \
                self.assertRaises(ask_edit.EditDenied) as refused:
            prepared.write_grant.check()
        self.assertNotIsInstance(refused.exception, ask_edit.EditConflict)

    def test_workspace_is_second_exact_payload_and_cannot_replay_or_change_instruction(self):
        self.planner()
        prepared = self.prepare()
        gate = AnalysisConsent(None, task=prepared.source)
        kwargs = {"task": prepared.source, "write_grant": prepared.write_grant}
        self.permit.claim(gate, QUESTION, **kwargs)
        approval = self.permit.authorize(gate, QUESTION, **kwargs)
        self.assertEqual(json.loads(approval.payload)["task"], prepared.source)
        with self.assertRaises(AnalysisDenied):
            self.permit.claim(AnalysisConsent(None, task=prepared.source), QUESTION, **kwargs)

    def run_ask(self, answer="yes", *, revoke_write=False, proposal=None):
        reader = Mock()
        values = iter((QUESTION, "yes", answer))
        asked = []
        def respond(prompt):
            if prompt == ask_cli.QUESTION_PROMPT and asked:
                return ""  # an empty line ends the interactive conversation
            asked.append(prompt) if prompt == ask_cli.QUESTION_PROMPT else None
            if prompt == PROMPTS["write"] and revoke_write:
                self.policy.recheck.side_effect = ValueError("PRIVATE REVOKED POLICY")
            return next(values)
        reader.side_effect = respond
        modes = []
        def send(approval):
            payload = json.loads(approval.payload)
            if payload["mode"] != "assistant":
                modes.append(payload["mode"])
                return json.dumps(proposal or PROPOSAL)
            # After an applied change the model is asked for the next step; here the task is done.
            return json.dumps({"action": "answer", "text": "Gotowe."} if modes else PLAN)
        with patch.object(ask_cli.os, "getuid", return_value=1000), \
                patch.object(ask_cli.os, "geteuid", return_value=1000), \
                patch.object(ask_cli.sys.stdin, "isatty", return_value=True), \
                patch.object(assistant_flow, "capture_access", return_value=self.snapshot), \
                patch.object(ask_cli, "installation_ready"), \
                patch.object(ask_cli, "managed_session", side_effect=lambda **_: nullcontext()), \
                patch.object(ask_cli, "send_once", side_effect=send) as sent, \
                patch.object(task_cli.task_sandbox, "run", return_value={"passed": True, "output": "SYNTHETIC", "check": "text"}) as test, \
                patch.object(task_cli.workspace, "apply", side_effect=lambda plan, auth, **kwargs: plan.consume(auth)) as apply:
            code = ask_cli.main([], reader=reader)
        return SimpleNamespace(code=code, reader=reader, sent=sent, test=test, apply=apply)

    def test_ask_edit_has_one_question_approval_two_requests_and_separate_write_approval(self):
        result = self.run_ask()
        self.assertEqual(result.code, 0, self.output.getvalue())
        self.assertEqual([call.args[0] for call in result.reader.call_args_list],
                         [ask_cli.QUESTION_PROMPT, PROMPTS["ask-question"], PROMPTS["write"], ask_cli.QUESTION_PROMPT])
        self.assertEqual(result.sent.call_count, 3)
        self.assertEqual([json.loads(call.args[0].payload)["mode"] for call in result.sent.call_args_list],
                         ["assistant", "workspace", "assistant"])
        result.test.assert_called_once()
        result.apply.assert_called_once()
        self.assertTrue(callable(result.apply.call_args.kwargs["write_guard"]))
        self.assertIn("Changes saved.", self.output.getvalue())
        self.assertIn("+    return a + b", self.output.getvalue())
        self.assertIn("+SYNTHETIC RESULT", self.output.getvalue())
        self.assertIn("Gotowe.", self.output.getvalue())                     # the model closes the task after the write

    def test_ask_proposal_identical_to_the_file_has_its_own_message_and_no_write(self):
        same = {"summary": "bez zmian", "files": [{"path": "main.py", "content": ORIGINAL}]}
        result = self.run_ask(proposal=same)
        self.assertEqual(result.code, 2)
        self.assertIn("No change was proposed: the file already has this content. Nothing was saved.", self.output.getvalue())
        self.assertNotIn(PROMPTS["write"], [call.args[0] for call in result.reader.call_args_list])
        result.test.assert_not_called()
        result.apply.assert_not_called()

    def test_write_decline_or_revocation_never_reaches_apply(self):
        for answer, revoked in (("no", False), ("yes", True)):
            with self.subTest(answer=answer, revoked=revoked):
                result = self.run_ask(answer, revoke_write=revoked)
                self.assertEqual(result.code, 2)
                self.assertEqual(result.sent.call_count, 2)
                result.test.assert_called_once()
                result.apply.assert_not_called()
                self.assertNotIn("PRIVATE REVOKED POLICY", self.output.getvalue())
                self.policy.recheck.side_effect = None


if __name__ == "__main__":
    unittest.main()


def ask_cli_refusals():
    """The refusals after which `ask` keeps the conversation going."""
    from ai_desktop import ask_cli
    return ask_cli.LOCAL_REFUSALS
