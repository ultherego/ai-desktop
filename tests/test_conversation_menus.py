"""Conversation menus and the live input: the same choices, labels and keys as the settings and the idle prompt."""
import os as _os
_os.environ.setdefault("AI_DESKTOP_MODEL_CATALOG", "off")
import contextlib
import inspect
import io
import unittest
from unittest.mock import patch

from ai_desktop import (agent_profiles as profiles, ask_commands as commands, cli, execution, management_cli,
                        material_cli, model_catalog, project_cli, question_input, run_controls, terminal_input)

BASE = {"provider": "claude", "model": "default", "effort": "default", "instructions": "", "profile": "default",
        "access": "changes"}
CODEX = dict(BASE, provider="codex")
CATALOG = (("gpt-synthetic-a", "Synthetic A"), ("gpt-synthetic-b", "Synthetic B"))


class FakeStore:
    def __init__(self, config):
        self.config = config

    def load(self):
        return type("Snapshot", (), {"config": self.config})()


class Conversation:
    def __init__(self):
        self.bound = []

    def rebind(self, selected):
        self.bound.append(selected)


def quiet(function, *args, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = function(*args, **kwargs)
    return result, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        config = profiles.defaults()
        self.enterContext(patch.object(profiles, "Store", return_value=FakeStore(config)))
        self.enterContext(patch.object(profiles, "_skill", side_effect=lambda name: "body " + name))
        self.enterContext(patch("ai_desktop.skills.names", return_value=()))
        management_cli._choices.clear()
        self.addCleanup(management_cli._choices.clear)


class ModelChoiceTests(Base):
    """Item 1: one source of the model / effort choices, the clients' own lists."""

    def offered(self, line, picks, *, during=False, selected=CODEX, controls=None):
        controls = controls or commands.Controls()
        menus = []

        def pick(title, options, default=0):
            menus.append((title, tuple(options)))
            return picks.pop(0)
        with execution.using(selected), patch.object(commands, "_pick", side_effect=pick), \
                patch.object(model_catalog, "fetch", return_value=CATALOG) as fetch:
            if during:
                result, output = quiet(controls.during_turn, line)
            else:
                result, output = quiet(controls.handle, line)
            current = execution.current()
        return result, output, menus, fetch, current

    def test_idle_and_during_a_turn_offer_the_settings_menu_with_the_codex_catalog(self):
        values, labels, _ = (None, None, None)
        with execution.using(CODEX), patch.object(model_catalog, "fetch", return_value=CATALOG):
            values, labels, _ = quiet(management_cli.model_choices, "codex")[0]
        management_cli._choices.clear()
        _, _, idle, _, current = self.offered("/model", [1])
        self.assertEqual(idle[0][1], labels)                                   # exactly the settings menu
        self.assertIn("Synthetic A (gpt-synthetic-a)", labels)
        self.assertEqual(labels[-1], management_cli.MODEL_LABELS["custom"])    # Other ID, as in the settings
        self.assertEqual(current["model"], "gpt-synthetic-a")
        management_cli._choices.clear()
        items, _, during, _, current = self.offered("/model", [2], during=True)
        self.assertEqual(during[0][1], labels)
        self.assertEqual(items, ["/model gpt-synthetic-b"])                   # applied after the answer
        self.assertEqual(current["model"], "default")

    def test_other_id_puts_the_command_back_in_the_prompt_for_the_id(self):
        session = question_input.PromptSession()
        controls = commands.Controls(session=session)
        _, output, menus, _, current = self.offered("/model", [3], controls=controls)
        self.assertEqual(session.prefill, "/model ")
        self.assertIn("Type the Codex model ID", output)
        self.assertEqual(current["model"], "default")                          # nothing changed yet
        items = self.offered("/model", [3], during=True)[0]
        self.assertEqual(items, [terminal_input.Prefill("/model ")])
        self.assertIsInstance(items[0], terminal_input.Prefill)
        # The typed ID is checked by the existing validators: a Claude alias is not a Codex ID.
        with execution.using(CODEX):
            output = quiet(controls.handle, "/model opus")[1]
            self.assertEqual(execution.current()["model"], "default")
        self.assertIn("not changed", output)

    def test_the_client_list_is_read_once_for_menus_opened_in_a_short_time(self):
        controls = commands.Controls()
        with execution.using(CODEX), patch.object(commands, "_pick", return_value=None), \
                patch.object(model_catalog, "fetch", return_value=CATALOG) as fetch:
            quiet(controls.during_turn, "/model")
            quiet(controls.handle, "/model")
        self.assertEqual(fetch.call_count, 1)
        clock = iter((0.0, management_cli.CHOICES_SECONDS + 1))
        management_cli._choices.clear()
        with patch.object(model_catalog, "fetch", return_value=CATALOG) as fetch:
            quiet(management_cli.model_choices, "codex", clock=lambda: next(clock))
            quiet(management_cli.model_choices, "codex", clock=lambda: next(clock))
        self.assertEqual(fetch.call_count, 2)                                 # an older list is asked again
        with patch.object(model_catalog, "fetch", side_effect=model_catalog.CatalogUnavailable) as fetch:
            management_cli._choices.clear()
            quiet(management_cli.model_choices, "codex")
            quiet(management_cli.model_choices, "codex")
        self.assertEqual(fetch.call_count, 2)                                 # a failure is never kept

    def test_local_and_effort_menus_use_the_settings_labels(self):
        _, _, menus, _, _ = self.offered("/model", [None], selected=dict(BASE, provider="local"))
        self.assertEqual(menus[0][1], ("Current server model", management_cli.MODEL_LABELS["custom"]))
        _, _, menus, _, current = self.offered("/effort", [4], selected=BASE)
        self.assertIn("very high", menus[0][1])
        self.assertNotIn("xhigh", menus[0][1])
        self.assertEqual(current["effort"], "xhigh")


class LabelTests(Base):
    """Item 2: one set of human labels."""

    def test_status_line_and_banner_show_labels_not_raw_values(self):
        selected = dict(BASE, model="opus", effort="xhigh")
        text = run_controls.label(config=selected)
        self.assertIn("model: Opus", text)
        self.assertIn("reasoning: very high", text)
        self.assertNotIn("xhigh", text)

    def test_one_provider_name_for_every_screen(self):
        self.assertEqual([execution.provider_name(name) for name in execution.PROVIDERS],
                         ["Claude", "Codex", "Local server"])
        self.assertEqual(management_cli.SERVICE_LABELS, {name: execution.provider_name(name) for name in execution.PROVIDERS})
        self.assertIs(management_cli.MODEL_LABELS, execution.MODEL_LABELS)
        self.assertIs(management_cli.EFFORT_LABELS, execution.EFFORT_LABELS)
        shown = []
        with patch("ai_desktop.terminal_menu.choose", side_effect=lambda title, options: shown.extend(options)):
            quiet(cli.login_menu)
        self.assertEqual(shown, ["Claude — subscription", "Codex — subscription", "Cancel"])


class AgentTests(Base):
    """Item 3: a saved conversation refuses another agent at once, with a way forward that exists."""

    def test_during_a_turn_the_agent_menu_never_opens_for_a_saved_conversation(self):
        with execution.using(BASE), patch.object(commands, "_pick") as pick:
            items, output = quiet(commands.Controls().during_turn, "/agent", Conversation())
            pick.assert_not_called()
            self.assertEqual(items, [])
            self.assertEqual(quiet(commands.Controls().during_turn, "/agent reviewer", Conversation())[0], [])
            idle = quiet(commands.Controls().handle, "/agent reviewer", Conversation())[1]
        self.assertEqual(output.strip(), idle.strip())                         # the same message as idle
        self.assertIn("ai-desktop ask --agent NAME", output)
        self.assertNotIn("--session new", output)

    def test_without_a_saved_conversation_the_menu_still_opens(self):
        with execution.using(BASE), patch.object(commands, "_pick", return_value=None) as pick:
            quiet(commands.Controls().during_turn, "/agent")
        pick.assert_called_once()


class SettingsMenuTests(Base):
    """Item 4: /settings opens a menu of the existing pickers."""

    def test_settings_opens_a_menu_and_its_entry_opens_that_picker(self):
        controls = commands.Controls()
        titles = []

        def pick(title, options, default=0):
            titles.append((title, tuple(options)))
            return 1 if len(titles) == 1 else 3                                # Reasoning, then "high"
        with execution.using(BASE), patch.object(commands, "_pick", side_effect=pick):
            result, output = quiet(controls.handle, "/settings")
            self.assertIs(result, True)
            self.assertEqual(execution.current()["effort"], "high")
        self.assertEqual(titles[0][1][:2], ("Model: Auto", "Reasoning: auto"))
        self.assertTrue(any(row.startswith("Service: Claude") for row in titles[0][1]))
        self.assertIn("profile: default", output)                              # the settings are still shown

    def test_settings_during_a_turn_queues_what_the_chosen_picker_chose(self):
        with execution.using(BASE), patch.object(commands, "_pick", side_effect=[1, 3]):
            items = quiet(commands.Controls().during_turn, "/settings")[0]
        self.assertEqual(items, ["/effort high"])

    def test_without_a_terminal_settings_only_shows(self):
        with execution.using(BASE), patch.object(commands, "_pick", return_value=None):
            result, output = quiet(commands.Controls().handle, "/settings")
        self.assertIs(result, True)
        self.assertIn("reasoning", output)


class MaterialProjectTests(Base):
    """Items 6 and 8: material and project accept the settings commands and the same exit words."""

    def session(self, conversation=None):
        session = material_cli.Session.__new__(material_cli.Session)
        session.prompt_session, session.reader, session.conversation = question_input.PromptSession(), input, conversation
        session.question = lambda line: self.fail("sent as a question: " + line)
        return session

    def test_help_lists_the_commands_that_work_there(self):
        for help_text in (material_cli.HELP, project_cli.HELP):
            for command in ("/model", "/effort", "/agent", "/settings", "/exit", "/quit"):
                self.assertIn(command, help_text)

    def test_model_and_effort_change_in_a_saved_conversation_and_the_agent_does_not(self):
        conversation = Conversation()
        session = self.session(conversation)
        with execution.using(BASE):
            self.assertTrue(quiet(session._dispatch, "/model haiku")[0])
            self.assertTrue(quiet(session._dispatch, "/effort high")[0])
            self.assertEqual((execution.current()["model"], execution.current()["effort"]), ("haiku", "high"))
            output = quiet(session._dispatch, "/agent reviewer")[1]
            self.assertEqual(execution.current()["profile"], "default")
        self.assertEqual(conversation.bound[-1]["model"], "haiku")              # the saved conversation follows
        self.assertIn(run_controls.AGENT_TIED, output)

    def test_settings_menu_there_offers_only_what_works_there(self):
        session = self.session()
        with execution.using(BASE), patch.object(commands, "_pick", return_value=None) as pick:
            quiet(session._dispatch, "/settings")
        self.assertEqual([row.split(":")[0] for row in pick.call_args.args[1]], ["Model", "Reasoning", "Agent"])

    def test_the_exit_words_match_ask(self):
        session = self.session()
        for line in ("/exit", "/quit", "/q"):
            self.assertIs(session._dispatch(line), False, line)
        self.assertEqual(material_cli.EXIT_COMMANDS, project_cli.EXIT_COMMANDS)

    def test_the_footer_names_only_what_works(self):
        hint = question_input.footer_text(question_input.PromptSession(), False)
        self.assertNotIn("new line", hint)
        self.assertNotIn("/ commands", hint)
        self.assertIn("/help", hint)
        ask = question_input.PromptSession()
        ask.commands = commands.COMMANDS
        self.assertEqual(question_input.footer_text(ask, True), question_input.FOOTER)


class AgentsCommandTests(unittest.TestCase):
    """Item 7: `ai-desktop agents` is provider-neutral and says so in its usage."""

    def test_agents_dispatches_provider_neutral(self):
        with patch.object(management_cli, "main", return_value=0) as main:
            cli.main(["agents", "list"])
        self.assertEqual(main.call_args.args[0], ["agents", "list"])
        self.assertEqual(main.call_args.kwargs, {"prog": "ai-desktop", "provider_neutral": True})
        usage = management_cli.build_parser(prog="ai-desktop", provider_neutral=True)
        self.assertIn("ai-desktop agents", usage._subparsers._group_actions[0].choices["agents"].format_usage())


class LiveInputParityTests(unittest.TestCase):
    """Items 5 and 9: the input during a turn edits like the idle prompt."""

    def owner(self, resolver=None, session=None, commands_list=()):
        with patch.object(terminal_input.termios, "tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0] * 32]):
            owner = terminal_input.TypeAhead(object(), io.StringIO(), resolver=resolver, commands=commands_list,
                                             session=session)
        owner._sequence = None
        return owner

    def feed(self, owner, data):
        out = io.StringIO()
        with patch("sys.stdout", out):
            for byte in data:
                owner._feed(bytes([byte]))
        return out.getvalue()

    def test_shift_alt_and_backslash_enter_break_the_line(self):
        owner = self.owner()
        self.feed(owner, b"a\x1b\rb\x1b[13;2uc\x1b[27;2;13~d\\\re")
        self.assertEqual(owner.line.text, "a\nb\nc\nd\ne")
        self.assertEqual(owner.queue, [])
        self.feed(owner, b"\r")
        self.assertEqual(owner.queue, ["a\nb\nc\nd\ne"])

    def test_up_and_down_walk_the_shared_history_on_a_single_line_draft(self):
        session = question_input.PromptSession()
        session.remember("first")
        owner = self.owner(session=session)
        self.feed(owner, b"second\r")
        self.assertEqual(session.history, ["first", "second"])                  # queued messages join the history
        self.feed(owner, b"draft\x1b[A")
        self.assertEqual(owner.line.text, "second")
        self.feed(owner, b"\x1b[A")
        self.assertEqual(owner.line.text, "first")
        self.feed(owner, b"\x1b[B\x1b[B")
        self.assertEqual(owner.line.text, "draft")                              # back to what was being typed
        owner.line.text, owner.line.cursor = "x\ny", 3
        self.feed(owner, b"\x1b[A")
        self.assertEqual((owner.line.text, owner.line.cursor), ("x\ny", 1))     # a multi-line draft moves lines

    def test_a_multi_line_paste_becomes_the_same_marker_and_is_expanded_when_queued(self):
        session = question_input.PromptSession()
        owner = self.owner(session=session)
        block = "line one\nline two\n"
        self.feed(owner, b"see ")
        self.feed(owner, b"\x1b[200~" + block.encode() + b"\x1b[201~")
        self.assertEqual(owner.line.text, "see " + question_input.paste_token(1, block))
        self.feed(owner, b"\r")
        self.assertEqual(owner.queue, ["see " + block.strip("\n")])
        self.assertEqual(session.take_pastes("anything"), [])
        self.feed(owner, b"\x1b[200~short\x1b[201~")
        self.assertEqual(owner.line.text, "short")                              # a short one-line paste is text

    def test_a_paste_in_the_unsent_draft_survives_into_the_idle_prompt(self):
        session = question_input.PromptSession()
        owner = self.owner(session=session)
        self.feed(owner, b"\x1b[200~a\nb\x1b[201~")
        draft = owner.line.text
        session.reset_pastes(keep=draft)
        self.assertEqual(session.expand(draft), "a\nb")

    def test_tab_adds_a_space_and_ctrl_d_on_an_empty_draft_is_ignored(self):
        owner = self.owner(commands_list=commands.COMMANDS)
        self.feed(owner, b"/he\t")
        self.assertEqual(owner.line.text, "/help ")
        owner.line.text, owner.line.cursor = "", 0
        with patch.object(terminal_input.os, "kill") as kill:
            self.feed(owner, b"\x04")
        kill.assert_not_called()
        self.assertEqual(owner.line.text, "")
        self.feed(owner, b"ab\x01\x04")
        self.assertEqual(owner.line.text, "b")

    def test_ctrl_c_reported_by_the_keyboard_protocol_still_stops_the_turn_and_ends_a_stale_paste(self):
        for report in (b"\x1b[99;5u", b"\x1b[27;5;99~"):
            with self.subTest(report=report):
                owner = self.owner()
                self.feed(owner, b"\x1b[200~ye")
                with patch.object(terminal_input.termios, "tcflush"):
                    owner._open_question("Run it? [y/n]:", 64)
                with patch.object(terminal_input.os, "kill") as kill:
                    self.feed(owner, report)
                kill.assert_called_once()
                self.assertEqual((owner._sequence, owner._paste_open, owner._answer.text), (None, False, ""))

    def test_the_keyboard_protocol_is_given_back_before_others_read_keys(self):
        stream = io.StringIO()
        with patch.object(terminal_input.termios, "tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0] * 32]):
            owner = terminal_input.TypeAhead(object(), stream)
        with patch.object(question_input, "_KITTY", True):
            owner._push_keys()
            owner._push_keys()                                                 # pushed once
            owner._pop_keys()
            owner._pop_keys()
        self.assertEqual(stream.getvalue(), "\x1b[>1u\x1b[<u")

    def test_one_key_table_and_one_current(self):
        self.assertFalse(hasattr(terminal_input, "_EDITS"))
        self.assertIs(terminal_input._KEYS, question_input._KEYS)
        self.assertEqual(inspect.getsource(terminal_input).count("def current("), 1)

    def test_a_bare_slash_and_enter_opens_the_commands_menu(self):
        seen = []
        owner = self.owner(resolver=lambda line: seen.append(line) or [], commands_list=commands.COMMANDS)
        self.feed(owner, b"/\r")
        self.assertEqual(seen, ["/"])
        self.feed(owner, b"/\x1b[B\r")                                          # a moved highlight still runs it
        self.assertEqual(seen, ["/", commands.COMMANDS[1][0]])
        with execution.using(BASE), patch.object(commands, "_pick", return_value=None) as pick:
            quiet(commands.Controls().during_turn, "/")
        self.assertEqual(pick.call_args.args[0], "Commands")

    def test_other_id_from_a_menu_comes_back_into_the_draft(self):
        owner = self.owner(resolver=lambda line: [terminal_input.Prefill("/model ")] if line == "/model" else [line],
                           commands_list=commands.COMMANDS)
        self.feed(owner, b"/model\r")
        self.assertEqual((owner.line.text, owner.queue), ("/model ", []))
        self.feed(owner, b"gpt-x\r")
        self.assertEqual(owner.queue, ["/model gpt-x"])


if __name__ == "__main__":
    unittest.main()


class StaleCopyHintTests(unittest.TestCase):
    """/model says when the user's own client changed after AI Desktop registered its copy (the list comes from the copy)."""

    def hint(self, registered, source):
        with patch.dict(_os.environ, {"AI_DESKTOP_MODEL_CATALOG": "on"}), \
                patch("ai_desktop.client_registration.registration_status", return_value=registered), \
                patch("ai_desktop.client_sources.discover", return_value=source) as discover:
            return management_cli.stale_copy_hint("claude"), discover

    def test_a_changed_source_names_the_refresh_command(self):
        from types import SimpleNamespace
        text, discover = self.hint({"status": "registered", "digest": "a" * 64, "path": "/home/user/.local/bin/claude"},
                                   SimpleNamespace(status="present", digest="b" * 64))
        self.assertIn("ai-desktop clients refresh claude", text)
        discover.assert_called_once_with("claude", "/home/user/.local/bin/claude")

    def test_an_unchanged_missing_or_foreign_copy_gives_no_hint(self):
        from types import SimpleNamespace
        same = SimpleNamespace(status="present", digest="a" * 64)
        self.assertIsNone(self.hint({"status": "registered", "digest": "a" * 64, "path": "/x/claude"}, same)[0])
        self.assertIsNone(self.hint({"status": "missing", "digest": None, "path": None}, same)[0])
        self.assertIsNone(self.hint({"status": "registered", "digest": "a" * 64, "path": None}, same)[0])
        self.assertIsNone(self.hint({"status": "registered", "digest": "a" * 64, "path": "/x/claude"},
                                    SimpleNamespace(status="missing", digest=None))[0])
        self.assertIsNone(management_cli.stale_copy_hint("local"))

    def test_tests_and_a_switched_off_catalog_never_look_at_this_computer(self):
        with patch("ai_desktop.client_registration.registration_status") as status:
            self.assertIsNone(management_cli.stale_copy_hint("claude"))
        status.assert_not_called()
