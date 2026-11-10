"""Codex model ids are not baked in: any well-formed id reaches the pinned client, which owns the verdict."""
import os as _os
_os.environ.setdefault("AI_DESKTOP_MODEL_CATALOG", "off")
import argparse
import json
import unittest

from ai_desktop import agent_profiles as profiles, analysis_contract as contract, execution
from ai_desktop import codex_protocol as codex

CODEX = {"provider": "codex", "model": "gpt-example-1.2", "effort": "default", "instructions": "",
         "profile": "diagnostic", "access": "read-only"}


class ModelShapeTests(unittest.TestCase):
    def test_codex_accepts_well_formed_ids_and_default(self):
        for model in ("default", "gpt-example-1.2", "o9", "gpt-5.5-codex", "a" * 96):
            with self.subTest(model=model):
                self.assertTrue(execution.model_allowed("codex", model))
                self.assertEqual(profiles.Profile(provider="codex", model=model).model, model)

    def test_malformed_ids_option_lookalikes_and_claude_aliases_are_refused_for_codex(self):
        for model in ("", "--flag", "-x", "GPT-5", "gpt 5", "gpt/5", "gpt\n5", "x" * 97, "opus", "sonnet", "haiku",
                      "gpt[1m]", None, 5):
            with self.subTest(model=model):
                self.assertFalse(execution.model_allowed("codex", model))
                with self.assertRaises(profiles.ProfileError):
                    profiles.Profile(provider="codex", model=model)

    def test_claude_accepts_any_well_formed_id_the_client_owns_the_verdict(self):
        # Nothing baked in: a model Anthropic adds later is accepted without an update of this program.
        # A bracketed variant such as "opus[1m]" (1M context) is a documented --model value (code.claude.com model-config).
        for model in ("default", "sonnet", "opus", "haiku", "claude-opus-4", "claude-opus-9", "a" * 96, "opus[1m]",
                      "claude-opus-4-6[1m]"):
            with self.subTest(model=model):
                self.assertTrue(execution.model_allowed("claude", model))
        for model in ("", "--flag", "-x", "Bad Model!", "OPUS", "opus 5", "opus/5", "opus\n", "x" * 97,
                      "opus[]", "opus[1m", "opus[1m]x", "[1m]", "opus[TOOLONGXX]", None, 5):
            with self.subTest(model=model):
                self.assertFalse(execution.model_allowed("claude", model))

    def test_execution_snapshot_validates_the_provider_specific_id(self):
        self.assertEqual(execution.validate(CODEX), CODEX)
        claude = dict(CODEX, provider="claude", model="claude-opus-9")
        self.assertEqual(execution.validate(claude), claude)
        for bad in (dict(CODEX, model="opus"), dict(CODEX, model="--x"), dict(claude, model="--x"),
                    dict(claude, model="Bad Model!")):
            with self.assertRaises(ValueError):
                execution.validate(bad)

    def test_cli_argument_type(self):
        for ok in ("default", "opus", "gpt-example-1.2"):
            self.assertEqual(execution.model_argument(ok), ok)
        for bad in ("GPT", "a b", "--x", ""):
            with self.assertRaises(argparse.ArgumentTypeError):
                execution.model_argument(bad)


class ProtocolTests(unittest.TestCase):
    def test_selected_id_is_sent_only_as_thread_and_turn_parameter(self):
        with execution.using(CODEX):
            raw = contract.application_input(None, "Pytanie", access={"files": [], "systemd": [], "weather": False}).encode()
        protocol = codex.Protocol(raw, model=CODEX["model"], effort="default")
        protocol.start()
        out = protocol.feed(json.dumps({"id": 0, "result": {}}).encode())
        thread = json.loads(out[1])
        self.assertEqual(thread["method"], "thread/start")
        self.assertEqual(thread["params"]["model"], "gpt-example-1.2")
        self.assertNotIn("gpt-example-1.2", json.dumps(protocol.prompt))
        with self.assertRaises(contract.AnalysisDenied):
            codex.Protocol(raw, model="gpt-other", effort="default")     # must equal the consented execution snapshot


if __name__ == "__main__":
    unittest.main()


class MenuTextInputTests(unittest.TestCase):
    def test_terminal_text_prompt_is_one_the_terminal_reader_accepts(self):
        # Regression: a custom prompt made read_question refuse ("question limit") on a real terminal.
        import io
        from unittest.mock import patch
        from ai_desktop import management_cli, question_input
        menu = management_cli._SettingsMenu.__new__(management_cli._SettingsMenu)
        menu.reader = input
        with patch("sys.stdin", io.StringIO()), patch("sys.stdout", io.StringIO()):
            with self.assertRaises(question_input.QuestionInputError) as caught:
                menu.text("Codex model ID: ", 96)
        self.assertEqual(str(caught.exception), "question terminal")
