"""One-hop selected directory aliases, with independent lexical authority."""
import os as _os
_os.environ.setdefault("AI_DESKTOP_MODEL_CATALOG", "off")
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import pwd
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_desktop import permissions as p


class DirectoryAliasTests(unittest.TestCase):
    def setUp(self):
        home = pwd.getpwuid(os.getuid()).pw_dir
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="aid-alias-", dir=home)))
        self.uid = os.getuid()
        self.enterContext(patch.object(p, "_identity", return_value=(self.uid, str(self.root))))
        self.config = self.root / ".config/ai-desktop/permissions.toml"
        self.config.parent.mkdir(parents=True, mode=0o700)
        self.gadget = self.file(".config/gadget/config.conf", 'panel = "left"')
        self.gizmo = self.file(".config/gizmo/settings.conf", 'mode = "basic"')
        self.alias = self.root / ".config/GizmoSuite"
        self.alias.symlink_to("gizmo", target_is_directory=True)

    def file(self, path, content="synthetic"):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def policy(self, *, read=("~/.config/**",), deny=(), version=2, write=()):
        self.config.write_text("version = %d\n[files]\nread = %s\ndeny = %s\nwrite = %s\n" %
            (version, json.dumps(read), json.dumps(deny), json.dumps(write)), encoding="utf-8")
        self.config.chmod(0o600)
        return p.load_policy()

    def selection(self, policy):
        return policy.expand_selected(((0, "gadget/**"), (0, "GizmoSuite/**")))

    def assert_reason(self, reason, function):
        with self.assertRaises(ValueError) as caught:
            function()
        self.assertEqual(caught.exception.args, (reason,))

    def test_niri_and_aliased_dms_read_physical_paths_and_bound_immutable_guards(self):
        policy = self.policy()
        paths = self.selection(policy)
        self.assertIs(type(paths), p._ExpandedSelection)
        self.assertIs(paths.policy, policy)
        self.assertEqual(paths.paths, tuple(sorted((str(self.gadget), str(self.gizmo)))))
        self.assertEqual(len(paths.aliases), 1)
        with self.assertRaises(FrozenInstanceError):
            paths.paths = ()
        result = p.read_selected(policy, paths)
        self.assertEqual({item["path"] for item in result["files"]}, {str(self.gadget), str(self.gizmo)})
        self.assertNotIn("GizmoSuite", json.dumps(result))

    def test_unaliased_expansion_preserves_the_original_tuple_api(self):
        policy = self.policy()
        paths = policy.expand_selected(((0, "gadget/**"),))
        self.assertIs(type(paths), tuple)
        self.assertEqual(paths, (str(self.gadget),))

    def test_explicit_alias_and_target_tree_scopes_are_both_required(self):
        policy = self.policy(read=("~/.config/GizmoSuite/**", "~/.config/gizmo/**"))
        paths = policy.expand_selected(((0, None),))
        self.assertEqual(paths.paths, (str(self.gizmo),))
        self.assertEqual(p.read_selected(policy, paths)["files"][0]["path"], str(self.gizmo))
        policy = self.policy(read=("~/.config/GizmoSuite/**",))
        self.assert_reason("files-denied", lambda: policy.expand_selected(((0, None),)))

    def test_denied_alias_root_is_pruned_before_even_its_metadata(self):
        policy = self.policy(deny=("~/.config/GizmoSuite/",))
        real_stat = os.stat
        def guarded(name, *args, **kwargs):
            self.assertNotEqual(name, "GizmoSuite", "denied alias was inspected")
            return real_stat(name, *args, **kwargs)
        with patch.object(p.os, "stat", side_effect=guarded):
            self.assertEqual(self.selection(policy), (str(self.gadget),))

    def test_target_children_rule_does_not_allow_recursive_alias_enumeration(self):
        self.file(".config/gizmo/nested/private.txt", "PRIVATE")
        policy = self.policy(read=("~/.config/GizmoSuite/**", "~/.config/gizmo/*"))
        real_open = os.open
        def guarded(name, *args, **kwargs):
            self.assertNotEqual(name, "nested", "children-only target scope was recursively opened")
            return real_open(name, *args, **kwargs)
        with patch.object(p.os, "open", side_effect=guarded):
            paths = policy.expand_selected(((0, None),))
            result = p.read_selected(policy, paths)
        self.assertEqual(paths.paths, (str(self.gizmo),))
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_denied_target_root_is_refused_before_its_metadata(self):
        policy = self.policy(deny=("~/.config/gizmo/",))
        real_stat = os.stat
        def guarded(name, *args, **kwargs):
            self.assertNotEqual(name, "gizmo", "denied target was inspected")
            return real_stat(name, *args, **kwargs)
        with patch.object(p.os, "stat", side_effect=guarded):
            self.assert_reason("files-denied", lambda: self.selection(policy))

    def test_descendant_deny_is_pruned_on_either_spelling_before_stat(self):
        self.file(".config/gizmo/private/secret.txt", "PRIVATE")
        self.file(".config/gizmo/omit.txt", "PRIVATE")
        for prefix in ("GizmoSuite", "gizmo"):
            policy = self.policy(deny=("~/.config/%s/private/" % prefix,
                                       "~/.config/%s/omit.txt" % prefix))
            real_stat = os.stat
            def guarded(name, *args, **kwargs):
                self.assertNotIn(name, ("private", "omit.txt"), "denied alias descendant was inspected")
                return real_stat(name, *args, **kwargs)
            with self.subTest(prefix=prefix), patch.object(p.os, "stat", side_effect=guarded):
                result = p.read_selected(policy, self.selection(policy))
            self.assertEqual({f["path"] for f in result["files"]}, {str(self.gadget), str(self.gizmo)})
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_absolute_parent_traversing_and_nested_link_targets_are_not_supported(self):
        (self.root / ".config/another").symlink_to("gizmo", target_is_directory=True)
        for target in (str(self.gizmo.parent), "../.config/gizmo", "./gizmo", "gizmo/", "another", "GizmoSuite"):
            self.alias.unlink()
            self.alias.symlink_to(target, target_is_directory=True)
            policy = self.policy()
            with self.subTest(target=target):
                self.assert_reason("files-symlink", lambda: self.selection(policy))

    def test_alias_to_a_file_or_missing_sibling_does_not_gain_directory_authority(self):
        for target, reason in (("ordinary.txt", "files-symlink"), ("missing", "files-missing")):
            self.file(".config/ordinary.txt")
            self.alias.unlink()
            self.alias.symlink_to(target)
            policy = self.policy()
            with self.subTest(target=target):
                self.assert_reason(reason, lambda: self.selection(policy))

    def test_nested_directory_and_file_symlinks_remain_refused(self):
        for directory in (False, True):
            link = self.gizmo.parent / "nested"
            link.symlink_to(self.gadget.parent if directory else self.gadget, target_is_directory=directory)
            policy = self.policy()
            with self.subTest(directory=directory):
                self.assert_reason("files-symlink", lambda: self.selection(policy))
            link.unlink()

    def test_an_exact_file_under_an_allowed_directory_alias_is_read_through_its_physical_path(self):
        policy = self.policy()
        selection = policy.expand_selected(((0, "GizmoSuite/settings.conf"),))
        self.assertIs(type(selection), p._ExpandedSelection)
        self.assertEqual(selection.paths, (str(self.gizmo),))                      # the physical file, never the alias spelling
        self.assertEqual(len(selection.aliases), 1)
        result = p.read_selected(policy, selection)
        self.assertEqual([item["content"] for item in result["files"]], ['mode = "basic"'])
        self.assertNotIn("GizmoSuite", json.dumps(result))

    def test_a_file_link_and_other_generic_readers_still_do_not_follow_links(self):
        policy = self.policy()
        logical_file = str(self.alias / self.gizmo.name)
        self.assert_reason("files-symlink", lambda: p.read_bounded(policy, logical_file, 4096))   # material/project reader
        link = self.gizmo.parent / "file-link"
        link.symlink_to(self.gizmo)                                                  # a link to a FILE never gets the exception
        for relative in ("GizmoSuite/file-link", "gizmo/file-link"):         # expansion is lexical; the open refuses
            selection = policy.expand_selected(((0, relative),))
            self.assert_reason("files-symlink", lambda: p.read_selected(policy, selection))

    def test_write_workspace_and_generic_directory_open_stay_nofollow(self):
        policy = self.policy(write=("~/.config/GizmoSuite/**",))
        self.assert_reason("policy-directory", lambda: policy.write_workspace(0))
        with self.assertRaises(ValueError):
            with p.open_directory(str(self.alias), self.uid):
                self.fail("generic opener followed the alias")

    def test_v1_keeps_the_original_no_alias_contract(self):
        policy = self.policy(version=1)
        self.assert_reason("files-symlink", lambda: self.selection(policy))

    def test_group_writable_target_and_parent_are_refused(self):
        for path in (self.gizmo.parent, self.alias.parent):
            policy = self.policy()
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(mode | 0o020)
            try:
                # The parent also contains the policy: its stronger existing
                # secure-directory check rejects it before alias resolution.
                reason = "policy-directory" if path == self.alias.parent else "files-permission"
                self.assert_reason(reason, lambda: self.selection(policy))
            finally:
                path.chmod(mode)

    def test_foreign_owned_alias_or_target_is_refused(self):
        for blocked in ("GizmoSuite", "gizmo"):
            real_stat = os.stat
            def guarded(name, *args, **kwargs):
                info = real_stat(name, *args, **kwargs)
                if name == blocked:
                    return SimpleNamespace(**{key: (self.uid + 1 if key == "st_uid" else getattr(info, key))
                        for key in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                                    "st_size", "st_mtime_ns", "st_ctime_ns")})
                return info
            policy = self.policy()
            with self.subTest(blocked=blocked), patch.object(p.os, "stat", side_effect=guarded):
                self.assert_reason("files-owner", lambda: self.selection(policy))

    def test_retargeted_or_replaced_alias_is_detected_before_any_file_read(self):
        self.file(".config/other/settings.conf", "OTHER")
        for replacement in ("other", "directory"):
            policy = self.policy()
            paths = self.selection(policy)
            self.alias.unlink()
            if replacement == "directory":
                self.alias.mkdir()
            else:
                self.alias.symlink_to(replacement, target_is_directory=True)
            with self.subTest(replacement=replacement), \
                    patch.object(p, "_read", side_effect=AssertionError("stale alias reached file content")):
                self.assert_reason("files-changed", lambda: p.read_selected(policy, paths))
            if replacement == "directory":
                self.alias.rmdir()
            else:
                self.alias.unlink()
            self.alias.symlink_to("gizmo", target_is_directory=True)

    def test_replaced_target_directory_and_parent_chain_are_detected(self):
        policy = self.policy()
        paths = self.selection(policy)
        self.gizmo.parent.rename(self.root / ".config/gizmo-old")
        self.file(".config/gizmo/settings.conf", "REPLACED")
        self.assert_reason("files-changed", lambda: p.recheck_selection(policy, paths))
        paths = self.selection(policy)
        parent = self.root / ".config"
        parent.rename(self.root / "config-old")
        self.file(".config/gizmo/settings.conf", "REPLACED AGAIN")
        (self.root / ".config/GizmoSuite").symlink_to("gizmo", target_is_directory=True)
        self.assert_reason("files-changed", lambda: p.recheck_selection(policy, paths))

    def test_alias_change_after_read_discards_the_collected_context(self):
        self.file(".config/other/settings.conf", "OTHER")
        policy = self.policy()
        paths = self.selection(policy)
        real_read = p._read
        def changed(path, *args, **kwargs):
            result = real_read(path, *args, **kwargs)
            if path == str(self.gizmo):
                self.alias.unlink()
                self.alias.symlink_to("other", target_is_directory=True)
            return result
        with patch.object(p, "_read", side_effect=changed):
            self.assert_reason("files-changed", lambda: p.read_selected(policy, paths))

    def test_alias_change_between_readlink_and_capture_is_detected(self):
        self.file(".config/other/settings.conf", "OTHER")
        policy = self.policy()
        real_link = os.readlink
        changed = False
        def retarget(name, *args, **kwargs):
            nonlocal changed
            target = real_link(name, *args, **kwargs)
            if name == "GizmoSuite" and not changed:
                changed = True
                self.alias.unlink()
                self.alias.symlink_to("other", target_is_directory=True)
            return target
        with patch.object(p.os, "readlink", side_effect=retarget):
            self.assert_reason("files-changed", lambda: self.selection(policy))

    def test_guards_cannot_be_transplanted_to_another_policy_object(self):
        policy = self.policy()
        paths = self.selection(policy)
        other = p.load_policy()
        with patch.object(p.os, "open", side_effect=AssertionError("wrong policy performed I/O")):
            self.assert_reason("files-selection", lambda: p.recheck_selection(other, paths))


if __name__ == "__main__":
    unittest.main()
