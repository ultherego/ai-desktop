"""Grouped command lists, templates that leave their arguments open with "...", and commands.deny."""
import json
import unittest

from ai_desktop import command_ops, permissions
from tests.test_change_plan import ChangeBase


class TemplateTests(unittest.TestCase):
    def test_an_open_template_ends_with_three_dots_after_a_literal_verb(self):
        self.assertEqual(command_ops.parse_template("docker ps ..."), ("docker", "ps", "..."))
        self.assertEqual(command_ops.parse_template("journalctl ..."), ("journalctl", "..."))
        for bad in ("docker ...", "...", "ps ... aux", "systemctl ... status", "docker <word> ..."):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                command_ops.parse_template(bad)

    def test_open_words_are_plain_words_and_an_exact_template_leaves_none(self):
        templates = command_ops.parse_templates(["docker ps", "docker ps ...", "uname -r"])
        self.assertEqual(command_ops.extra_words(templates, ["docker", "ps"]), ())
        self.assertEqual(command_ops.extra_words(templates, ["docker", "ps", "-a", "--format=json"]), ("-a", "--format=json"))
        self.assertIsNone(command_ops.extra_words(templates, ["docker", "images"]))
        self.assertIsNone(command_ops.extra_words(templates, ["docker", "ps", "a b"]))
        self.assertIsNone(command_ops.extra_words(templates, ["docker", "ps", "$(id)"]))
        self.assertFalse(command_ops.exact_match(templates[1:2], ["docker", "ps", "-a"]))
        self.assertTrue(command_ops.exact_match(templates, ["uname", "-r"]))

    def test_deny_entries_are_literal_word_prefixes(self):
        denies = command_ops.parse_deny(["docker rm", "docker ps -a"])
        self.assertTrue(command_ops.denied(denies, ["docker", "rm", "-f", "x"]))
        self.assertTrue(command_ops.denied(denies, ["docker", "ps", "-a"]))
        self.assertFalse(command_ops.denied(denies, ["docker", "ps"]))
        for bad in (["docker <word>"], ["docker ..."], ["docker rm", "docker rm"], [""]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                command_ops.parse_deny(bad)

    def test_a_state_changing_verb_cannot_be_left_open(self):
        self.assertTrue(command_ops.opens_a_change(command_ops.parse_template("docker run --rm ...")))
        self.assertFalse(command_ops.opens_a_change(command_ops.parse_template("docker run --rm")))
        self.assertFalse(command_ops.opens_a_change(command_ops.parse_template("docker ps ...")))


class GroupedPolicyTests(ChangeBase):
    change = "[]"
    share_line = ""

    def load(self, commands, change="[]", extra_line=""):
        self.commands, self.change, self.share_line = commands, change, extra_line
        self.write_policy()
        return permissions.load_policy()

    def test_groups_are_one_list_to_the_program(self):
        policy = self.load('{general = ["uname -r", "uptime"], docker = ["docker ps"]}')
        self.assertEqual([" ".join(t) for t in policy.commands()], ["uname -r", "uptime", "docker ps"])
        self.assertEqual(policy.access()["commands"], ["uname -r", "uptime", "docker ps"])

    def test_bad_groups_are_refused_with_a_reason(self):
        for value in ('{General = ["uptime"]}', '{general = "uptime"}', '{a = ["uptime"], b = ["uptime"]}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)

    def test_an_open_change_verb_in_allow_is_refused_but_accepted_in_change(self):
        with self.assertRaises(ValueError) as raised:
            self.load('["docker run --rm ..."]')
        self.assertIn("commands.change", " ".join(raised.exception.problem))
        policy = self.load('["docker ps"]', change='["docker run --rm ..."]')
        self.assertTrue(policy.allows_change(["docker", "run", "--rm", "hello-world"]))

    def test_deny_wins_over_an_open_template(self):
        policy = self.load('["docker ps ..."]', extra_line='deny = ["docker ps -a"]\n')
        self.assertTrue(policy.allows_command(["docker", "ps"]))
        self.assertTrue(policy.allows_command(["docker", "ps", "--format=json"]))
        self.assertFalse(policy.allows_command(["docker", "ps", "-a"]))
        self.assertTrue(policy.allows_command(["docker", "ps", "--all"]))   # a flag deny is not exhaustive (documented)

    def test_paths_left_open_follow_files_read_and_deny(self):
        policy = self.load('["journalctl ..."]')
        home = self.home
        self.assertTrue(policy.allows_command(["journalctl", "--file=%s/.config/app/x.journal" % home]))
        self.assertTrue(policy.allows_command(["journalctl", "%s/.config/app/x.journal" % home]))
        for word in ("--file=%s/.config/vault/x" % home, "/etc/shadow", "relative/path", "~/x", "--file=relative/x"):
            with self.subTest(word=word):
                self.assertFalse(policy.allows_command(["journalctl", word]))

    def test_an_open_change_path_must_also_be_writable_and_test_or_undo_never_use_open_templates(self):
        policy = self.load('["docker ps ..."]', change='["docker run --rm ..."]')
        self.assertFalse(policy.allows_change(["docker", "run", "--rm", "-v", "%s/.config/app:/data" % self.home, "x"]))
        self.assertFalse(policy.allows_exact_command(["docker", "ps", "-a"]))


if __name__ == "__main__":
    unittest.main()
