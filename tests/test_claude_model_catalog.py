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

    def test_any_well_formed_model_and_only_our_response(self):
        # A model the provider adds later is kept without an update of this program; a malformed or repeated one is not.
        foreign = MODELS + [{"value": "claude-opus-9", "displayName": "Opus 9", "description": "Opus 9 · New"},
                            {"value": "opus", "displayName": "Again", "description": "Again"},
                            {"value": "Bad Model!", "displayName": "X", "description": "X"},
                            {"value": "-x", "displayName": "X", "description": "X"},
                            {"value": "", "displayName": "X", "description": "X"}]
        self.assertEqual([alias for alias, _, _ in catalog.parse_claude(stream(foreign))],
                         ["default", "opus", "fable", "haiku", "claude-opus-9"])
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
        for result in ((1, b"", b""), (0, b"", b""), (1, stream(subtype="error"), b"")):
            with self.subTest(result=result), patch.object(sandbox, "runtime_ready"), \
                    patch.object(sandbox, "_check_client"), \
                    patch.object(catalog.client_binding, "bind", return_value=FakeBinding("claude")), \
                    self.assertRaises(catalog.CatalogUnavailable):
                catalog.fetch_claude(run=lambda command, **kwargs: result)

    def fetch(self, run):
        with patch.object(sandbox, "runtime_ready"), patch.object(sandbox, "_check_client"), \
                patch.object(catalog.client_binding, "bind", return_value=FakeBinding("claude")):
            return catalog.fetch_claude(run=run)

    def test_an_answer_counts_even_when_the_client_then_exits_with_an_error(self):
        # The client ends at the end of its input; its status after answering does not decide.
        self.assertEqual(self.fetch(lambda command, **kwargs: (1, stream(), b"error"))[1][1], "Opus 5.5")

    def test_reading_stops_as_soon_as_the_answer_has_arrived(self):
        later = []

        def run(command, *, stdout_chunk, **kwargs):
            raw = stream()
            stdout_chunk(raw[:30])                                # an incomplete line: keep reading
            stdout_chunk(raw[30:])
            later.append("read past the answer")
            raise AssertionError("not stopped")
        self.assertEqual(self.fetch(run)[0][0], "default")
        self.assertEqual(later, [])

    def test_a_real_process_that_keeps_running_after_answering_is_stopped_early(self):
        import time
        from ai_desktop.process_io import bounded_process
        reply = stream().decode().replace("'", "")
        command = ["/bin/sh", "-c", "cat >/dev/null; printf '%s' '" + reply + "'; exec sleep 30"]
        began = time.monotonic()
        with patch.object(sandbox, "namespace_command", return_value=command):
            entries = self.fetch(lambda argv, **kwargs: bounded_process(argv, **kwargs))
        self.assertEqual(entries[1][1], "Opus 5.5")
        self.assertLess(time.monotonic() - began, 10)

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
    def test_the_menu_lists_only_the_clients_models_then_other_id(self):
        new = MODELS + [{"value": "claude-opus-9", "displayName": "Opus 9", "description": "Opus 9 · New"}]
        with patch.object(catalog, "fetch_claude", return_value=catalog.parse_claude(stream(new))):
            values, labels, note = management_cli.model_choices("claude")
        self.assertEqual(values, ("default", "opus", "fable", "haiku", "claude-opus-9", "custom"))
        self.assertIn("Opus 5.5 — Best for everyday, complex tasks", labels)
        self.assertNotIn("sonnet", values)                    # nothing baked in: only what the client lists
        self.assertIsNone(note)

    def test_auto_is_added_when_the_client_does_not_list_it(self):
        with patch.object(catalog, "fetch_claude", return_value=catalog.parse_claude(stream(MODELS[1:]))):
            values, labels, _ = management_cli.model_choices("claude")
        self.assertEqual(values, ("opus", "fable", "haiku", "default", "custom"))
        self.assertEqual(labels[-2:], ("Auto", "Other ID (type it)"))

    def test_without_the_client_list_only_auto_and_other_id_remain(self):
        with patch.object(catalog, "fetch_claude", side_effect=catalog.CatalogUnavailable()):
            values, _, note = management_cli.model_choices("claude")
        self.assertEqual(values, ("default", "custom"))
        self.assertIn("unavailable", note)


if __name__ == "__main__":
    unittest.main()
