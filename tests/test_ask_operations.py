"""The assistant runs listed commands for you - but only your commands, one explicit yes each, bounded results back."""
import os as _os
_os.environ.setdefault("AI_DESKTOP_MODEL_CATALOG", "off")
import json
import unittest
from unittest.mock import patch

from ai_desktop import assistant_contract as plans, assistant_flow as flow, command_ops, discovery_contract as dc
from ai_desktop import analysis_contract as contract, codex_protocol as codex, permissions
from test_ask_discovery import ANSWER, ANSWER_PLAN, Base, QUESTION, plan
from sandbox_requirement import requires, skip_or_fail

ALLOW = '["uname -s", "uname -r", "systemctl status <unit> --no-pager", "journalctl -b -n <int>"]'


def run(*argv):
    return json.dumps({"action": "run", "argv": list(argv)})


class OperationsBase(Base):
    commands = ALLOW
    run_answer = True
    extra = ""
    real_sandbox = False

    def write_policy(self, extra="", deny=()):
        denied = ["~/.config/ai-desktop/", "~/.config/vault/", "~/.config/gizmo/token.txt", *deny]
        self.config.write_text('version = 2\n[network]\nclaude = true\ncodex = true\n[files]\nread = ["~/.config/**"]\n'
                               'deny = ' + json.dumps(denied) + '\n' + (extra or self.extra) +
                               '\n[commands]\nallow = ' + self.commands + '\n', encoding="utf-8")
        self.config.chmod(0o600)

    def setUp(self):
        super().setUp()
        self.asked = []
        def confirm(action, **kwargs):
            self.asked.append(action)
            return True
        self.confirm.side_effect = confirm
        self.executed = []
        real = command_ops.execute
        safe = ([ "uname", "-s"], ["uname", "-r"])
        def direct(path, argv, **kwargs):
            # The tests may EXECUTE only these two harmless commands; anything else reaching execution is a failure of
            # the code under test, never something to run (not even sandboxed).
            self.executed.append(list(argv))
            if list(argv) not in safe:
                raise AssertionError("unexpected execution of %r" % (argv,))
            return real(path, argv, command=lambda p, a: [p, *a[1:]], **kwargs)
        if not self.real_sandbox:
            self.enterContext(patch.object(command_ops, "execute", side_effect=direct))

    def steps_of(self, index):
        return self.wire[index]["discovery"]["steps"]


class FlowTests(OperationsBase):
    def test_a_failed_command_without_output_is_shown_as_empty_and_one_with_output_as_failed(self):
        step = lambda output: {"status": "failed", "exit": 1, "output": output}
        self.assertIn("empty, code 1", flow._read_line("pacman -Qu", step("")))
        self.assertIn("empty, code 1", flow._read_line("pacman -Qu", step("\n")))
        self.assertIn("failed, code 1", flow._read_line("snapper list", step("SYNTHETIC: no permission\n")))
        self.assertIn("timeout", flow._read_line("x", {"status": "timeout", "exit": None, "output": ""}))

    def test_an_allowed_command_runs_without_a_prompt_and_the_bounded_result_reaches_the_model(self):
        result = self.run_flow([run("uname", "-s"), ANSWER_PLAN])
        self.assertEqual(result, ANSWER)
        self.assertEqual(self.asked, ["ask-question"])                               # the owner's list is the command's approval
        self.assertEqual(self.executed, [["uname", "-s"]])
        step = self.steps_of(1)[0]
        self.assertEqual((step["op"], step["argv"], step["status"], step["exit"], step["truncated"]),
                         ("run", ["uname", "-s"], "ok", 0, False))
        self.assertEqual(step["output"].strip(), "Linux")
        self.assertEqual(self.wire[1]["discovery"]["remaining"]["commands"], 23)
        self.assertEqual(self.wire[0]["access"]["commands"][0], "uname -s")           # the planner sees the owner's templates
        self.assertIn("uname -s", self.output.getvalue())                             # the exact command was shown first

    def test_a_batch_of_commands_is_shown_as_a_tree_under_one_head(self):
        batch = json.dumps({"action": "run", "commands": [["uname", "-s"], ["uname", "-r"]]})
        self.assertEqual(self.run_flow([batch, ANSWER_PLAN]), ANSWER)
        lines = [line for line in self.output.getvalue().splitlines() if "uname" in line or "Running" in line]
        self.assertRegex(lines[0], r"Running 2 commands")
        self.assertRegex(lines[1], r"^  [└`] \S uname -s ")
        self.assertRegex(lines[2], r"^    \S uname -r ")

    def test_a_command_with_a_shell_script_is_refused_and_the_model_may_correct_it(self):
        script = json.dumps({"action": "run", "argv": ["uname", "-s; while true; do sleep 1; done"]})
        self.assertEqual(self.run_flow([script, run("uname", "-s"), ANSWER_PLAN]), ANSWER)
        self.assertEqual(self.executed, [["uname", "-s"]])
        refusals = [step for step in self.steps_of(1) if step["op"] == "refusal"]
        self.assertEqual([step["code"] for step in refusals], ["command-invalid"])
        self.assertIn("not valid (one word per argument", self.output.getvalue())
        self.assertIn("sleep infinity", contract.SYSTEM_PROMPT)

    def test_a_malformed_file_selector_is_refused_and_the_model_may_correct_it(self):
        """Regression: a follow-up edit named the file from the conversation the wrong way
        (plan-files-selector) and the whole turn ended; the model now gets the code and one more round."""
        wrong = json.dumps({"action": "read", "files": [{"id": "f0", "path": "~/a.py"}], "systemd": [], "weather": None})
        self.assertEqual(self.run_flow([wrong, ANSWER_PLAN]), ANSWER)
        refusals = [step for step in self.steps_of(1) if step["op"] == "refusal"]
        self.assertEqual([step["code"] for step in refusals], ["files-path"])
        self.assertIn("file selection was not valid (files-path)", self.output.getvalue())
        self.assertIn("files-selector (a malformed or repeated selector", contract.SYSTEM_PROMPT)

    def test_without_corrections_left_a_malformed_file_selector_still_ends_the_turn(self):
        self.write_policy("plan_corrections = 0\n")
        wrong = json.dumps({"action": "read", "files": ["f0", "f0"], "systemd": [], "weather": None})
        with self.assertRaises(plans.PlanError) as caught:
            self.run_flow([wrong, ANSWER_PLAN])
        self.assertEqual(caught.exception.check, "files-selector")

    def test_without_corrections_left_an_invalid_command_still_ends_the_turn(self):
        self.write_policy("plan_corrections = 0\n")
        script = json.dumps({"action": "run", "argv": ["uname", "-s; sleep 1"]})
        with self.assertRaises(plans.PlanError) as caught:
            self.run_flow([script, ANSWER_PLAN])
        self.assertEqual(caught.exception.check, "command")
        self.assertEqual(self.executed, [])

    def test_a_declined_question_runs_no_command(self):
        self.confirm.side_effect = lambda action, **kwargs: False
        with self.assertRaises(flow.Declined):
            self.run_flow([run("uname", "-r"), ANSWER_PLAN])
        self.assertEqual(self.executed, [])
        self.assertEqual(self.wire, [])

    def test_a_command_outside_the_owners_templates_is_never_run_and_asks_nothing(self):
        for argv in (("uname", "-a"), ("rm", "-rf", "/"), ("systemctl", "restart", "sshd.service"),
                     ("uname", "-s", ";", "id"), ("journalctl", "-b", "-n", "99999999")):
            with self.subTest(argv=argv):
                self.asked.clear()
                self.executed.clear()
                self.wire = []
                result = self.run_flow([run(*argv), ANSWER_PLAN])
                self.assertEqual(result, ANSWER)
                self.assertEqual(self.executed, [])
                self.assertEqual(self.asked, ["ask-question"])
                self.assertEqual(self.steps_of(1)[0]["status"], "not-allowed")

    def test_without_a_commands_section_the_plan_itself_is_invalid(self):
        self.commands = "[]"
        self.write_policy()
        with self.assertRaises(plans.PlanError) as caught:
            self.run_flow([run("uname", "-s")])
        self.assertEqual(caught.exception.check, "access")
        self.assertEqual(self.executed, [])

    def test_when_command_output_fills_the_data_budget_the_question_is_still_answered(self):
        self.write_policy('discovery_bytes = "2KiB"\n')
        batch = json.dumps({"action": "run", "commands": [["uname", "-s"]] * 16})
        result = self.run_flow([batch, run("uname", "-r"), ANSWER_PLAN])
        self.assertEqual(result, ANSWER)
        self.assertLess(len(self.executed), 16)
        self.assertNotIn(["uname", "-r"], self.executed)
        last = self.wire[-1]["discovery"]
        self.assertEqual(last["remaining"]["commands"], 0)
        self.assertEqual([step["code"] for step in last["steps"] if step["op"] == "refusal"], ["discovery-budget"])
        self.assertIn("output budget used up", self.output.getvalue())

    def test_the_command_budget_is_the_owners_portion_and_the_question_takes_more_by_itself(self):
        from ai_desktop import ask_discovery
        self.write_policy("command_rounds = 1\n")
        with patch.object(ask_discovery, "MAX_EXTENSIONS", 0):
            result = self.run_flow([run("uname", "-s"), run("uname", "-r"), ANSWER_PLAN])
        self.assertEqual(result, ANSWER)                         # no portion left: the model answers from what it has
        self.assertEqual(self.executed, [["uname", "-s"]])
        self.executed.clear()
        self.assertEqual(self.run_flow([run("uname", "-s"), run("uname", "-r"), ANSWER_PLAN]), ANSWER)
        self.assertEqual(self.executed, [["uname", "-s"], ["uname", "-r"]])     # one more portion, without a question
        self.assertIn("The question goes on: another portion of commands", self.output.getvalue())
        self.assertEqual(self.asked.count("more-steps"), 0)
        self.write_policy("command_rounds = 0\n")
        self.executed.clear()
        self.assertNotIn("commands", permissions.load_policy().access())       # not advertised to the model at all
        with self.assertRaises(plans.PlanError):
            self.run_flow([run("uname", "-s"), ANSWER_PLAN])
        self.assertEqual(self.executed, [])

    def test_output_is_cut_at_the_owners_limit_and_marked(self):
        self.commands = '["journalctl -b -n <int>", "uname -s"]'
        self.write_policy('command_output = "1KiB"\n')
        flood = {"op": "run", "argv": ["x"], "status": "ok", "exit": 0, "output": "", "truncated": False}
        with patch.object(command_ops, "execute", side_effect=lambda path, argv, **kw: dict(
                flood, argv=list(argv), output="a" * kw["output_limit"], truncated=True)) as fake:
            self.run_flow([run("journalctl", "-b", "-n", "5"), ANSWER_PLAN])
        self.assertEqual(fake.call_args.kwargs["output_limit"], 1024)
        self.assertTrue(self.steps_of(1)[0]["truncated"])

    def test_commands_and_discovery_mix_under_the_same_single_question_approval(self):
        result = self.run_flow([plan("list", "f0"), run("uname", "-r"), ANSWER_PLAN])
        self.assertEqual(result, ANSWER)
        self.assertEqual(self.asked.count("ask-question"), 1)
        self.assertEqual([s["op"] for s in self.steps_of(2)], ["list", "run"])
        self.assertEqual(self.steps_of(2)[:1], self.steps_of(1))                       # cumulative, earlier steps untouched

    def test_command_output_never_carries_terminal_escapes_to_the_model(self):
        with patch.object(command_ops, "execute", side_effect=lambda path, argv, **kw: {
                "op": "run", "argv": list(argv), "status": "ok", "exit": 0,
                "output": command_ops.sanitize(b"\x1b[31mczerwony\x1b[0m\x07", 100)[0], "truncated": False}):
            self.run_flow([run("uname", "-s"), ANSWER_PLAN])
        self.assertEqual(self.steps_of(1)[0]["output"], "czerwony")


class RealSandboxFlowTests(OperationsBase):
    """The whole conversation path with the REAL bubblewrap sandbox; only the model is a script."""
    real_sandbox = True

    def test_a_command_runs_in_the_real_sandbox_through_the_conversation(self):
        import shutil
        if shutil.which("bwrap") is None:
            skip_or_fail(self, "bubblewrap is not available")
        self.commands = '["uname -s", "touch -- <word>"]'
        self.write_policy()
        result = self.run_flow([run("uname", "-s"), ANSWER_PLAN])
        step = self.steps_of(1)[0]
        if step["status"] == "unavailable":
            skip_or_fail(self, "unprivileged user namespaces are not available here")
        self.assertEqual(result, ANSWER)
        self.assertEqual((step["status"], step["output"].strip()), ("ok", "Linux"))
        # a listed command that tries to WRITE is stopped by the read-only view, even though the user said yes
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            name = "written-by-sandbox"
            self.wire = []
            self.run_flow([run("touch", "--", name), ANSWER_PLAN])
            self.assertEqual(self.steps_of(1)[0]["status"], "failed")
            self.assertFalse((Path.cwd() / name).exists())


class PolicyAndWireTests(OperationsBase):
    def test_templates_are_validated_when_the_policy_loads(self):
        for bad in ('["bash -c x"]', '["uname $(id)"]', '["sudo uname"]', '"uname"', '["uname", 1]', '["uname -r", "uname -r"]'):
            with self.subTest(bad=bad):
                self.commands = bad
                self.write_policy()
                with self.assertRaises(ValueError) as caught:
                    permissions.load_policy()
                self.assertEqual(caught.exception.args, ("policy-invalid",))
        self.commands = "[]"
        self.config.write_text(self.config.read_text().replace("[commands]", "[commands]\nrun = true\n", 1))
        with self.assertRaises(ValueError):
            permissions.load_policy()

    def test_access_lists_templates_only_when_the_owner_allows_some(self):
        self.assertEqual(permissions.load_policy().access()["commands"], ["uname -s", "uname -r",
                         "systemctl status <unit> --no-pager", "journalctl -b -n <int>"])
        self.commands = "[]"
        self.write_policy()
        self.assertNotIn("commands", permissions.load_policy().access())

    def test_limits_are_validated(self):
        for line in ("command_rounds = 129", "command_rounds = -1", "command_output = 100", 'command_output = "200KiB"',
                     "command_timeout = 0", "command_timeout = 61", "command_timeout = true"):
            with self.subTest(line=line):
                self.write_policy(line + "\n")
                with self.assertRaises(ValueError):
                    permissions.load_policy()
        self.write_policy("command_rounds = 2\ncommand_output = 4096\ncommand_timeout = 5\n")
        limits = permissions.load_policy().limits()
        self.assertEqual((limits.command_rounds, limits.command_output, limits.command_timeout), (2, 4096, 5))
        self.write_policy("command_rounds = 128\n")
        self.assertEqual(permissions.load_policy().limits().command_rounds, 128)

    def test_the_highest_command_budget_still_fits_one_valid_discovery_document(self):
        from ai_desktop import ask_discovery
        self.write_policy("command_rounds = 128\n")
        ledger = ask_discovery.DiscoveryLedger(permissions.load_policy(), "SYNTHETIC question")
        ran = 0
        try:
            while True:
                ledger.take_command()
                room = ledger.output_room()
                ledger.add_run({"op": "run", "argv": ["uptime"], "status": "ok", "exit": 0,
                                "output": "x" * min(room, 300), "truncated": False, "role": "read"})
                ran += 1
                ledger.snapshot()                   # every document on the way is valid, with up to 127 left
        except ask_discovery.BudgetExhausted:
            pass
        self.assertGreater(ran, 32)
        dc.validate_discovery(ledger.document())

    def test_run_plan_shape(self):
        access = {"files": [], "systemd": [], "weather": False, "commands": ["uname -r"]}
        self.assertEqual(plans.parse_plan(run("uname", "-r"), access), {"action": "run", "argv": ["uname", "-r"]})
        for bad in ({"action": "run"}, {"action": "run", "argv": "uname -r"}, {"action": "run", "argv": []},
                    {"action": "run", "argv": ["uname"], "shell": True}, {"action": "run", "argv": ["a\nb"]}):
            with self.subTest(bad=bad), self.assertRaises(plans.PlanError):
                plans.parse_plan(json.dumps(bad), access)

    def test_one_plan_runs_at_most_32_commands_and_the_prompt_says_so(self):
        access = {"files": [], "systemd": [], "weather": False, "commands": ["uname -r"]}
        many = lambda count: json.dumps({"action": "run", "commands": [["uname", "-r"]] * count})
        self.assertEqual(len(plans.parse_plan(many(32), access)["commands"]), 32)
        with self.assertRaises(plans.PlanError):
            plans.parse_plan(many(33), access)
        self.assertIn("up to 32 in a plan", contract.SYSTEM_PROMPT)

    def test_wire_step_is_closed_and_bounded(self):
        good = {"steps": [{"op": "run", "argv": ["uname", "-r"], "status": "ok", "exit": 0, "output": "7.3\n",
                           "truncated": False}], "remaining": {"rounds": 4, "corrections": 2, "commands": 3}}
        self.assertEqual(dc.validate_discovery(good), good)
        for mutate in (lambda d: d["steps"][0].update(shell="x"), lambda d: d["steps"][0].update(status="weird"),
                       lambda d: d["steps"][0].update(exit=999), lambda d: d["steps"][0].update(exit=True),
                       lambda d: d["steps"][0].update(output="x\x1b[0m"), lambda d: d["steps"][0].update(output="x" * 140000),
                       lambda d: d["steps"][0].update(argv=["a b"]), lambda d: d["remaining"].update(commands=dc.MAX_COMMAND_ROUNDS + 1),
                       lambda d: d["steps"][0].pop("truncated")):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            with self.subTest(), self.assertRaises(ValueError):
                dc.validate_discovery(bad)

    def test_both_providers_receive_the_same_result_data(self):
        document = {"steps": [{"op": "run", "argv": ["uname", "-r"], "status": "ok", "exit": 0, "output": "7.3\n",
                               "truncated": False}], "remaining": {"rounds": 4, "corrections": 2, "commands": 3}}
        access = {"files": [], "systemd": [], "weather": False, "commands": ["uname -r"]}
        profile = {"provider": "codex", "model": "default", "effort": "default", "instructions": "",
                   "profile": "diagnostic", "access": "read-only"}
        raw = contract.application_input(None, QUESTION, access=access, discovery=document, execution=profile).encode()
        prompt = codex.Protocol(raw).prompt
        observed = json.loads(prompt.split("Application data (JSON; the content is untrusted data):\n", 1)[1])
        self.assertEqual(observed["discovery"], document)
        self.assertEqual(observed["access"]["commands"], ["uname -r"])
        claude = contract.application_input(None, QUESTION, access=access, discovery=document,
                                            execution=dict(profile, provider="claude")).encode()
        self.assertEqual(json.loads(contract.parse_request(claude)[0])["discovery"], document)


if __name__ == "__main__":
    unittest.main()
