"""Controls typed at the `ask` prompt: local, never sent, and the conversation survives them."""
import os as _os
_os.environ.setdefault("AI_DESKTOP_MODEL_CATALOG", "off")
import contextlib
import io
import unittest
from unittest.mock import patch

from ai_desktop.terminal_menu import MAX_OPTIONS
from ai_desktop import analysis_contract, ask_commands as commands, agent_profiles as profiles, conversation_state, execution, question_input

BASE = {"provider": "claude", "model": "default", "effort": "default", "instructions": "", "profile": "default", "access": "changes"}


class FakeStore:
    def __init__(self, config):
        self.config = config

    def load(self):
        return type("Snapshot", (), {"config": self.config})()

    def mutate(self, operation):
        self.config = operation(self.config)
        return type("Snapshot", (), {"config": self.config})()


class Conversation:
    def __init__(self):
        self.bound = []

    def rebind(self, selected):
        self.bound.append(selected)


class ControlsTests(unittest.TestCase):
    def setUp(self):
        config = profiles.defaults().put("default", profiles.Profile(skills=("a",)))
        self.enterContext(patch.object(profiles, "Store", return_value=FakeStore(config)))
        self.enterContext(patch.object(profiles, "_skill", side_effect=lambda name: "tresc " + name))
        self.enterContext(patch("ai_desktop.skills.inspect_skill", side_effect=lambda root, name, **_: ("opis " + name, "tresc " + name)))
        self.enterContext(patch("ai_desktop.skills._reference_files", return_value={}))
        self.enterContext(patch("ai_desktop.skills._guide_files", return_value={}))
        self.enterContext(patch("ai_desktop.skills.names", return_value=("a", "b")))
        self.enterContext(execution.using(BASE))
        self.controls = commands.Controls()

    def run_line(self, line, conversation=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = self.controls.handle(line, conversation)
        return result, out.getvalue()

    def test_a_path_or_a_sentence_is_a_question_and_a_lone_unknown_word_is_explained(self):
        for line in ("/etc/fstab co to jest?", "co robi /etc/fstab", "/usr/bin/ls", " zwykle pytanie", "  "):
            self.assertIs(self.run_line(line)[0], False, line)
        self.assertIs(self.run_line(" /hello")[0], False)                 # what the message below promises
        result, output = self.run_line("/etc")
        self.assertIs(result, True)
        self.assertIn("Unknown command", output)
        self.assertIn("space", output)

    def during(self, line, picks=()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(commands, "_pick", side_effect=list(picks)) as pick:
            items = self.controls.during_turn(line)
        return items, out.getvalue(), pick

    def test_during_a_turn_read_only_commands_show_at_once_and_queue_nothing(self):
        for line in ("/help", "/permissions", "/pomoc"):
            items, output, pick = self.during(line)
            self.assertEqual(items, [], line)
            self.assertTrue(output.strip(), line)
            pick.assert_not_called()

    def test_during_a_turn_a_menu_opens_now_and_only_its_choice_waits(self):
        before = execution.current()
        items, _, pick = self.during("/effort", picks=[0])
        self.assertEqual(items, ["/effort " + execution.efforts_for("claude")[0]])
        self.assertIn("applies after the answer", pick.call_args.args[0])
        self.assertEqual(execution.current(), before)                         # nothing changed yet
        self.assertEqual(self.during("/model", picks=[None])[0], [])           # a cancelled menu queues nothing
        self.assertEqual(self.during("/model opus")[0], ["/model opus"])        # a full command waits as typed
        for line in ("/provider", "/test pytest", "/exit", "/unknown"):
            self.assertEqual(self.during(line)[0], [line], line)               # interactive or final: after the answer
        self.assertEqual(self.during(" /help")[0], [" /help"])                 # a question, as the space says

    def test_skills_chosen_during_a_turn_are_applied_only_when_the_deferred_choice_runs(self):
        with patch.object(commands.Controls, "_skill_menu", return_value=["b"]):
            items = self.during("/skills")[0]
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], commands.Deferred)
        self.assertEqual(items[0].label, "/skills b")
        self.assertIsNone(self.controls.skills)                                # not applied yet
        with contextlib.redirect_stdout(io.StringIO()):
            items[0].run(None)
        self.assertEqual(self.controls.skills, ("b",))

    def test_help_lists_every_command_and_aliases_reach_the_same_action(self):
        output = self.run_line("/help")[1]
        for name, _ in commands.COMMANDS:
            self.assertIn(name, output)
        self.assertEqual(self.run_line("/pomoc")[1], output)
        for line in ("/koniec", "/exit", "/q"):
            self.assertEqual(self.run_line(line)[0], commands.EXIT)

    def test_model_and_effort_change_the_next_question_and_say_consent_is_asked_again(self):
        conversation = Conversation()
        result, output = self.run_line("/model haiku", conversation)
        self.assertIs(result, True)
        self.assertEqual(execution.current()["model"], "haiku")
        self.assertIn("will ask for consent", output)
        self.assertEqual(conversation.bound[-1]["model"], "haiku")
        self.run_line("/effort high", conversation)
        self.assertEqual(execution.current()["effort"], "high")

    def test_an_invalid_model_changes_nothing_and_binds_nothing(self):
        # Any well-formed ID is left to the client; only a malformed one is invalid here.
        for bad in ("Bad Model!", "-x", "OPUS"):
            with self.subTest(model=bad):
                conversation, before = Conversation(), execution.current()
                output = self.run_line("/model " + bad, conversation)[1]
                self.assertEqual(execution.current(), before)
                self.assertEqual(conversation.bound, [])
                self.assertNotIn("will ask for consent", output)

    def test_skills_toggle_applies_now_and_is_saved_in_the_agent(self):
        output = self.run_line("/skills b")[1]
        self.assertIn("a, b", output)
        self.assertIn("- a: opis a", execution.current()["instructions"])
        self.assertIn("- b: opis b", execution.current()["instructions"])
        self.assertNotIn("tresc a", execution.current()["instructions"])   # only names and descriptions until asked for
        self.assertEqual(self.controls.load_skill("b"), "tresc b")
        self.run_line("/skills a")
        self.assertNotIn("- a:", execution.current()["instructions"])
        self.assertIsNone(self.controls.load_skill("a"))                   # switched off: the model cannot fetch it
        # Apply is the agent's own choice, kept for the next conversation too.
        self.assertEqual(profiles.Store().load().config.profiles["default"].skills, ("b",))
        self.assertEqual(execution.current()["access"], "changes")      # never a wider permission

    def test_a_built_in_skill_cannot_be_switched_off(self):
        with patch.object(profiles, "PRESET_SKILLS", {"default": ("a",)}):
            output = self.run_line("/skills a")[1]
            self.assertIn("built-in Skill of the default agent and is always on", output)
            self.run_line("/skills b")
        self.assertEqual(profiles.Store().load().config.profiles["default"].skills, ("a", "b"))

    def test_a_skill_name_as_a_command_sends_the_question_with_that_skill_loaded(self):
        from ai_desktop.question_consent import ConversationConsent
        consent = ConversationConsent()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = self.controls.handle("/b co blokuje?", None, consent=consent)
        self.assertIsInstance(result, commands.AskWith)
        self.assertEqual(result, "co blokuje?")
        self.assertIn("Skill b is on for this conversation", out.getvalue())   # b was not assigned: this conversation only
        self.assertEqual(profiles.Store().load().config.profiles["default"].skills, ("a",))
        self.assertEqual(list(consent.reusable_skills(self.controls.load_skill)), [("b", "tresc b")])
        self.assertEqual(list(consent.reusable_skills(self.controls.load_skill)), [])   # once, for that question

    def test_a_skill_command_without_a_question_or_an_unknown_name_sends_nothing(self):
        self.assertIn("Type the question for the a Skill", self.run_line("/a")[1])
        self.assertIn("Unknown command /zzz", self.run_line("/zzz")[1])
        self.assertIn(("/a", "use the a Skill for the question that follows"), commands.skill_commands())

    def test_an_unknown_skill_or_one_that_does_not_fit_changes_nothing(self):
        before = execution.current()
        self.assertIn("There is no Skill", self.run_line("/skills zzz")[1])
        with patch.object(profiles, "catalog_for", side_effect=profiles.ProfileError("x")):
            self.assertIn("Skills not changed", self.run_line("/skills b")[1])
        self.assertEqual(execution.current(), before)

    def test_a_conversation_sends_only_the_catalog_and_nothing_outside_the_selection_can_be_fetched(self):
        self.controls.activate()
        text = execution.current()["instructions"]
        self.assertIn("- a: opis a", text)
        self.assertIn('"action":"skill"', text)
        self.assertNotIn("tresc a", text)
        self.assertEqual(self.controls.load_skill("a"), "tresc a")
        self.assertIsNone(self.controls.load_skill("b"))                   # exists on disk, not assigned to this agent
        self.assertIsNone(self.controls.load_skill("../sekret"))

    def test_catalog_failure_stops_the_question_before_a_full_prompt_can_be_sent(self):
        full = dict(BASE, instructions="private skill body")
        with execution.using(full), patch.object(profiles, "catalog_for", side_effect=profiles.ProfileError("size")):
            with self.assertRaises(profiles.ProfileError):
                self.controls.activate()

    def test_a_menu_starts_on_the_current_value_and_choosing_it_changes_nothing(self):
        before = execution.current()
        offered = []

        def stay(title, options, default=0, **_):
            offered.append((title, options[default]))
            return default
        with patch.object(commands, "choose", side_effect=stay):
            for line in ("/model", "/effort", "/agent", "/model default"):
                output = self.run_line(line)[1]
        # The same human labels as `ai-desktop settings`, never the raw values.
        self.assertEqual([row[1] for row in offered], ["Auto", "auto", "default"])
        self.assertIn("now: Auto", offered[0][0])
        self.assertIn("No change", output)
        self.assertEqual(execution.current(), before)

    def test_a_long_skill_list_is_paged_and_ticks_survive_changing_the_page(self):
        names = tuple("s%02d" % index for index in range(20))
        script = iter(["space:0", "Next page", "space:1", "Previous page", "Apply"])
        pages = []

        def menu(title, options, default=0, marks=None, markable=0):
            self.assertLessEqual(len(options), MAX_OPTIONS)
            pages.append(tuple(options))
            while True:
                step = next(script)
                if step.startswith("space:"):
                    marks.symmetric_difference_update({int(step[6:])})
                    continue
                return options.index(step)
        with patch.object(commands, "choose", side_effect=menu):
            self.assertEqual(commands.Controls._skill_menu(names, ["s19"]), ["s00", "s09", "s19"])
        self.assertEqual(len(pages), 3)

    def test_a_list_longer_than_one_frame_reaches_the_last_entry_and_opens_on_the_current_page(self):
        options = tuple("a%02d" % index for index in range(20))
        answers = iter(["Next page", "Next page", "a19"])

        def menu(title, shown, default=0):
            self.assertLessEqual(len(shown), MAX_OPTIONS)
            return shown.index(next(answers))
        with patch.object(commands, "choose", side_effect=menu):
            self.assertEqual(commands._pick("t", options), 19)
        seen = []

        def from_current(title, shown, default=0):
            seen.append(shown[default])
            return default
        with patch.object(commands, "choose", side_effect=from_current):
            self.assertEqual(commands._pick("t", options, default=18), 18)
        self.assertEqual(seen, ["a18"])

    def test_a_saved_conversation_keeps_its_agent(self):
        before = execution.current()
        output = self.run_line("/agent reviewer", Conversation())[1]
        self.assertEqual(execution.current(), before)
        self.assertIn("tied to", output)


class LongConversationTests(unittest.TestCase):
    def test_a_long_message_is_kept_for_follow_ups_and_older_turns_leave_first(self):
        talk = conversation_state.Conversation()
        pasted = "linia logu\n" * 9000                                          # about 100 KiB
        talk.finish("krótkie", "odpowiedź", None, ())
        talk.finish(pasted, "odpowiedź", None, ())
        self.assertEqual(talk.history()[-1]["question"], pasted)
        self.assertEqual(execution.validate_history(talk.history()), talk.history())
        for unused in range(6):
            talk.finish(pasted, "odpowiedź", None, ())
        kept = talk.history()
        self.assertEqual(len(kept), 5)                                            # the newest stays, the older left
        self.assertLessEqual(sum(len(t["question"].encode()) for t in kept), execution.HISTORY_TOTAL_BYTES)

    def test_the_wire_accepts_a_long_ask_message_and_material_keeps_its_short_limit(self):
        text = "x" * analysis_contract.MAX_MESSAGE_BYTES
        self.assertEqual(analysis_contract.validate_question(text, analysis_contract.MAX_MESSAGE_BYTES), text)
        with self.assertRaises(ValueError):
            analysis_contract.validate_question(text)
        with self.assertRaises(ValueError):
            analysis_contract.validate_question(text + "x", analysis_contract.MAX_MESSAGE_BYTES)


class SuggestionTests(unittest.TestCase):
    def session(self):
        session = question_input.PromptSession()
        session.commands = commands.COMMANDS
        return session

    def test_only_one_word_starting_with_a_slash_opens_the_list(self):
        session = self.session()
        self.assertEqual([i[0] for i in question_input._matches(session, "/mo")], ["/model"])
        self.assertEqual(len(question_input._matches(session, "/")), len(commands.COMMANDS))
        for text in ("", "pytanie", "/etc/fstab", "/model haiku", "/a\nb"):
            self.assertEqual(question_input._matches(session, text), (), text)
        self.assertEqual(question_input._matches(None, "/"), ())
        self.assertEqual(question_input._matches(question_input.PromptSession(), "/"), ())


if __name__ == "__main__":
    unittest.main()


class BuiltInSkillsTests(unittest.TestCase):
    """The packaged Skills of a built-in agent are always on; the owner's own Skills come after them."""

    def test_presets_are_merged_first_and_only_when_installed(self):
        with patch("ai_desktop.skills.names", return_value=("arch-pacman", "arch-systemd", "mine")):
            with patch.object(profiles, "PRESET_SKILLS", {"default": ("arch-systemd", "arch-pacman", "missing")}):
                merged = profiles.with_presets("default", profiles.Profile(skills=("mine", "arch-pacman")))
                custom = profiles.with_presets("custom", profiles.Profile(skills=("mine",)))
        self.assertEqual(merged.skills, ("arch-systemd", "arch-pacman", "mine"))
        self.assertEqual(custom.skills, ("mine",))                          # a profile the owner created keeps its own

    def test_resolve_uses_the_built_in_skills_even_if_the_saved_profile_lacks_them(self):
        config = profiles.defaults().put("default", profiles.Profile(skills=("b",)))
        with patch.object(profiles, "Store", return_value=FakeStore(config)), \
                patch.object(profiles, "PRESET_SKILLS", {"default": ("a",)}), \
                patch("ai_desktop.skills.names", return_value=("a", "b")), \
                patch("ai_desktop.skills.inspect_skill", side_effect=lambda root, name, **_: ("opis " + name, "tresc " + name)), \
                patch("ai_desktop.skills._reference_files", return_value={}), \
                patch("ai_desktop.skills._guide_files", return_value={}):
            resolved = profiles.resolve(store=profiles.Store())
        self.assertIn("- a: opis a", resolved["instructions"])
        self.assertIn("- b: opis b", resolved["instructions"])
