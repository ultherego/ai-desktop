"""Built-in command preset and default-protected locations (policy version 2)."""
import os
import unittest
from unittest.mock import patch

from ai_desktop import command_ops, permissions as p
from ai_desktop.command_presets import DIAGNOSTICS, PRESETS


def load(raw):
    real = p._read
    with patch.object(p, "_read", side_effect=lambda path, uid, limit, **kw:
                      (raw.encode(), ("synthetic",)) if kw.get("policy") else real(path, uid, limit, **kw)):
        return p.load_policy()


BASE = 'version = 2\n[files]\nread = ["~/**", "/etc/**"%s]\ndeny = []\n%s\n%s'


class PresetTests(unittest.TestCase):
    def test_every_preset_template_is_a_read_template_the_parser_accepts(self):
        for name, templates in PRESETS.items():
            parsed = command_ops.parse_templates(list(templates))           # also refuses repeats
            for tokens in parsed:
                with self.subTest(preset=name, template=" ".join(tokens)):
                    self.assertFalse(command_ops.opens_a_change(tokens))
                    self.assertNotIn(tokens[0].casefold(), command_ops.FORBIDDEN_PROGRAMS)

    def test_open_arguments_never_follow_a_tool_whose_free_words_could_change_something(self):
        risky = {"nft", "ufw", "smartctl", "flatpak", "sensors", "ss", "powerprofilesctl", "scx_loader"}
        for template in DIAGNOSTICS:
            words = template.split()
            if words[0] in risky and words[-1] == "...":
                with self.subTest(template=template):
                    self.assertGreaterEqual(len(words), 3)              # a fixed read command before the open words
                    self.assertIn(words[1], {"list", "info", "remotes", "permission-show", "documents", "history"})

    def test_the_preset_is_added_to_the_owners_own_list_and_deny_still_wins(self):
        policy = load(BASE % ("", "", '[commands]\npreset = "diagnostics"\ndeny = ["journalctl"]\n'
                                      '[commands.allow]\nmine = ["exampletool status", "uptime"]\n'))
        self.assertTrue(policy.allows_command(["systemctl", "is-active", "ufw"]))
        self.assertTrue(policy.allows_command(["exampletool", "status"]))
        self.assertFalse(policy.allows_command(["ufw", "allow", "22"]))
        self.assertFalse(policy.allows_command(["systemctl", "restart", "ufw"]))
        self.assertEqual(len(policy.access()["commands"]), len(DIAGNOSTICS) + 1)   # uptime is in both: once
        self.assertFalse(policy.allows_command(["journalctl", "-b"]))           # commands.deny removes from the preset

    def test_root_and_boot_filesystem_reads_work_with_a_narrow_read_scope(self):
        # CachyOS check (candidate 164): "/" and "/boot" are rarely in files.read, so "findmnt ..." refused them.
        policy = load('version = 2\n[files]\nread = ["~/Projects/**"]\ndeny = []\n[commands]\npreset = "diagnostics"\n')
        for argv in (["findmnt", "/"], ["findmnt", "/boot"], ["findmnt", "/boot/efi"], ["btrfs", "filesystem", "usage", "/"],
                     ["btrfs", "device", "stats", "/"], ["btrfs", "scrub", "status", "/"]):
            with self.subTest(argv=argv):
                self.assertTrue(policy.allows_command(argv))
        self.assertFalse(policy.allows_command(["findmnt", "/home"]))         # the sandbox shows an empty /home
        self.assertFalse(policy.allows_command(["findmnt", "-no", "SOURCE", "/etc"]))  # other paths: files.read decides

    def test_a_compositor_check_sees_only_the_allowed_configuration_it_names(self):
        # CachyOS check (candidate 164): `niri validate` could not run, and in the sandbox /home is empty anyway.
        import tempfile
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            home = os.path.realpath(temp)
            config = os.path.join(home, ".config/niri")
            os.makedirs(os.path.join(config, "dms"))
            for name in ("config.kdl", "dms/binds.kdl"):
                open(os.path.join(config, name), "w").close()
            os.makedirs(os.path.join(home, ".ssh"))
            open(os.path.join(home, ".ssh/config"), "w").close()
            with patch.object(p, "_identity", return_value=(os.getuid(), home)):
                policy = load('version = 2\n[files]\nread = ["~/**"]\ndeny = []\n[commands]\npreset = "diagnostics"\n')
                argv = policy.expand_home(["niri", "validate", "-c", "~/.config/niri/config.kdl"])
                self.assertEqual(argv[3], config + "/config.kdl")
                self.assertTrue(policy.allows_command(argv))
                self.assertEqual(policy.sandbox_binds(argv), (config,))               # with the files it includes
                ssh = policy.expand_home(["niri", "validate", "-c", "~/.ssh/config"])
                self.assertFalse(policy.allows_command(ssh))                          # protected: neither run nor shown
                self.assertEqual(policy.sandbox_binds(ssh), ())
                self.assertEqual(policy.sandbox_binds(["uname", "-a"]), ())
                policy = load('version = 2\n[files]\nread = ["~/**"]\ndeny = ["~/.config/niri/dms/"]\n'
                              '[commands]\npreset = "diagnostics"\n')
                self.assertEqual(policy.sandbox_binds(argv), (config + "/config.kdl",))  # a denied file inside: file only
                os.symlink(config + "/config.kdl", home + "/link.kdl")
                self.assertEqual(policy.sandbox_binds(["niri", "validate", "-c", home + "/link.kdl"]), ())

    def test_an_unknown_preset_is_refused(self):
        with self.assertRaises(ValueError):
            load(BASE % ("", "", '[commands]\npreset = "everything"\n'))


class ProtectTests(unittest.TestCase):
    def test_secrets_are_protected_under_a_broad_read_rule(self):
        policy = load(BASE % ("", "", ""))
        home = policy.home
        for path in (home + "/.ssh/my_github_key", home + "/.config/borgmatic/config.yaml", home + "/.gnupg/x",
                     home + "/.config/vivaldi/Default/Cookies", home + "/.netrc",
                     "/etc/NetworkManager/system-connections/wifi.nmconnection", "/etc/wireguard/wg0.conf"):
            with self.subTest(path=path):
                self.assertTrue(policy._excluded(path))
        for path in (home + "/projects/a.py", "/etc/fstab", "/etc/NetworkManager/NetworkManager.conf"):
            with self.subTest(path=path):
                self.assertFalse(policy._excluded(path))

    def test_an_explicit_read_rule_lifts_only_what_it_names(self):
        policy = load(BASE % (', "~/.ssh/config"', "", ""))
        self.assertFalse(policy._excluded(policy.home + "/.ssh/config"))
        self.assertTrue(policy._excluded(policy.home + "/.ssh/id_work"))
        policy = load(BASE % (', "~/.ssh/**"', "", ""))
        self.assertFalse(policy._excluded(policy.home + "/.ssh/id_work"))
        self.assertNotIn(policy.home + "/.ssh", policy.protected_locations())

    def test_protect_false_switches_the_list_off_and_version_one_is_unchanged(self):
        policy = load(BASE % ("", "protect = false", ""))
        self.assertEqual(policy.protected_locations(), ())
        self.assertFalse(policy._excluded(policy.home + "/.ssh/id_work"))
        with self.assertRaises(ValueError):
            load(BASE % ("", 'protect = "yes"', ""))
        legacy = load('version = 1\n[files]\nread = ["~/**"]\ndeny = []\n')
        self.assertEqual(legacy.protected_locations(), ())


if __name__ == "__main__":
    unittest.main()
