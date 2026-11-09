"""The Claude model list comes from the client itself (its `initialize` answer), like the /model menu of Claude Code,
read offline with no account; the maintainer asked for it instead of a list written into this program."""
import json
import unittest
from unittest.mock import patch

from ai_desktop import analysis_sandbox as sandbox, management_cli, model_catalog as catalog
from system_client_fixtures import FakeBinding

MODELS = [
    {"value": "default", "displayName": "Default (recommended)",
     "description": "Use the default model (currently Opus 5.5) · $4/$20 per Mtok"},
    {"value": "opus", "displayName": "Opus", "description": "Opus 5.5 · Best for everyday, complex tasks · $4/$20 per Mtok"},
    {"value": "fable", "displayName": "Fable",
     "description": "Fable 5.2 · Most capable for your hardest and longest-running tasks · $8/$40 per Mtok"},
    {"value": "haiku", "displayName": "Haiku",
     "description": "Haiku 5.5 · Fastest for quick answers · $0.10/$0.50 per Mtok ($0.50/$2.50 for prompts over 100k)"},
]


def stream(models=MODELS, request_id="ai-desktop-models", subtype="success"):
    lines = [{"type": "system", "subtype": "ui_invalidate"},
             {"type": "control_response", "response": {"subtype": subtype, "request_id": request_id,
                                                        "response": {"models": models, "account": {}}}}]
    return b"".join(json.dumps(line).encode() + b"\n" for line in lines)


class ParseTests(unittest.TestCase):
    def test_labels_show_the_model_each_alias_is_now_without_prices(self):
        self.assertEqual(catalog.parse_claude(stream()), (
            ("default", "Default (recommended)", "currently Opus 5.5"),
            ("opus", "Opus 5.5", "Best for everyday, complex tasks"),
            ("fable", "Fable 5.2", "Most capable for your hardest and longest-running tasks"),
            ("haiku", "Haiku 5.5", "Fastest for quick answers")))

    def test_only_this_programs_aliases_and_only_our_response(self):
        foreign = MODELS + [{"value": "claude-opus-9", "displayName": "X", "description": "X"},
                            {"value": "opus", "displayName": "Again", "description": "Again"}]
        self.assertEqual([alias for alias, _, _ in catalog.parse_claude(stream(foreign))],
                         ["default", "opus", "fable", "haiku"])
        for raw in (stream(request_id="other"), stream(subtype="error"), stream([]), b"not json\n", b"",
                    stream([{"value": 5}])):
            with self.subTest(raw=raw[:40]), self.assertRaises(catalog.CatalogUnavailable):
                catalog.parse_claude(raw)

    def test_a_control_character_never_reaches_a_label(self):
        bad = [{"value": "opus", "displayName": "Opus\x1b[31m", "description": "Opus 5.5\x1b[2J · x"}]
        ((alias, label, note),) = catalog.parse_claude(stream(bad))
        self.assertNotIn("\x1b", label + note)


class FetchTests(unittest.TestCase):
    def test_the_client_runs_offline_with_no_account_and_only_the_request(self):
        seen = {}

        def run(command, **kwargs):
            seen.update(command=command, **kwargs)
            return 0, stream(), b""
        with patch.object(sandbox, "runtime_ready"), patch.object(sandbox, "_check_client") as compatible, \
                patch.object(catalog.client_binding, "bind", return_value=FakeBinding("claude")):
            entries = catalog.fetch_claude(run=run)
        compatible.assert_called_once()                       # the same compatibility as every request, first
        self.assertEqual(entries[1][1], "Opus 5.5")
        command = seen["command"]
        self.assertEqual(command[-2], "catalog")
        self.assertNotIn("/run/egress.sock", command)
        mounts = [command[index + 2] for index, part in enumerate(command[:-2]) if part in ("--bind", "--ro-bind")]
        self.assertNotIn(sandbox.INNER_PROFILE, mounts)          # no account: the profile is never mounted
        self.assertEqual(seen["input_bytes"], catalog.CLAUDE_REQUEST)

    def test_any_failure_is_unavailable(self):
        for result in ((1, stream(), b""), (0, b"", b"")):
            with self.subTest(result=result), patch.object(sandbox, "runtime_ready"), \
                    patch.object(sandbox, "_check_client"), \
                    patch.object(catalog.client_binding, "bind", return_value=FakeBinding("claude")), \
                    self.assertRaises(catalog.CatalogUnavailable):
                catalog.fetch_claude(run=lambda command, **kwargs: result)

    def test_tests_never_start_the_real_client(self):
        with self.assertRaises(catalog.CatalogUnavailable):
            catalog.fetch_claude()

    def test_the_catalog_command_has_no_tools_and_reads_its_request_from_stdin(self):
        command = sandbox.client_command("catalog")
        for flag in ("--input-format", "stream-json", "--no-session-persistence"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertNotIn("--system-prompt", command)


class MenuTests(unittest.TestCase):
    def test_the_menu_lists_the_clients_models_then_the_other_aliases(self):
        with patch.object(catalog, "fetch_claude", return_value=catalog.parse_claude(stream())):
            values, labels, note = management_cli.model_choices("claude")
        self.assertEqual(values[:4], ("default", "opus", "fable", "haiku"))
        self.assertIn("Opus 5.5 — Best for everyday, complex tasks", labels)
        self.assertIn("sonnet", values)                       # an alias the client did not list stays available
        self.assertIsNone(note)

    def test_without_the_client_list_the_aliases_remain(self):
        with patch.object(catalog, "fetch_claude", side_effect=catalog.CatalogUnavailable()):
            values, _, note = management_cli.model_choices("claude")
        self.assertIn("opus", values)
        self.assertIn("unavailable", note)


if __name__ == "__main__":
    unittest.main()
