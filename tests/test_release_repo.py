"""The signed pacman repository published on GitHub Releases (scripts/release_repo.sh, .github/workflows/release.yml)."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release_repo.sh"
WORKFLOW = ROOT / ".github/workflows/release.yml"


def run(*args, env=None):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)


class ScriptTests(unittest.TestCase):
    def test_bad_arguments_and_a_used_directory_are_refused(self):
        self.assertEqual(run().returncode, 2)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "old").write_text("x")
            self.assertEqual(run(temp, "KEY", "a.pkg.tar.zst").returncode, 2)
            out = Path(temp) / "out"
            refused = run(str(out), "KEY", str(Path(temp) / "old"))
            self.assertEqual(refused.returncode, 2)
            self.assertIn("not a package", refused.stderr)

    @unittest.skipUnless(shutil.which("repo-add") and shutil.which("gpg") and shutil.which("zstd"), "needs pacman tools")
    def test_a_signed_repository_with_plain_database_copies_is_built(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            home = temp / "gnupg"
            home.mkdir(mode=0o700)
            env = dict(os.environ, GNUPGHOME=str(home))
            subprocess.run(["gpg", "--batch", "--passphrase", "", "--quick-gen-key", "synthetic signing key", "ed25519",
                            "sign", "1d"], env=env, check=True, capture_output=True)
            listing = subprocess.run(["gpg", "--list-secret-keys", "--with-colons"], env=env, check=True,
                                     capture_output=True, text=True).stdout
            fingerprint = re.search(r"^fpr:+([0-9A-F]{40}):", listing, re.M).group(1)
            info = temp / ".PKGINFO"
            info.write_text("pkgname = ai-desktop\npkgbase = ai-desktop\npkgver = 1.0.0-1\npkgdesc = synthetic\n"
                            "arch = x86_64\nsize = 1\n")
            plain = temp / "package.tar"
            with tarfile.open(plain, "w") as archive:
                archive.add(info, arcname=".PKGINFO")
            package = temp / "ai-desktop-1.0.0-1-x86_64.pkg.tar.zst"
            subprocess.run(["zstd", "-q", str(plain), "-o", str(package)], check=True)
            done = run(str(temp / "repo"), fingerprint, str(package), env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            repo = temp / "repo"
            for name in ("ai-desktop.db", "ai-desktop.db.sig", "ai-desktop.files", "ai-desktop.files.sig",
                         package.name, package.name + ".sig"):
                self.assertTrue((repo / name).is_file() and not (repo / name).is_symlink(), name)
            subprocess.run(["gpg", "--batch", "--verify", str(repo / "ai-desktop.db.sig"), str(repo / "ai-desktop.db")],
                           env=env, check=True, capture_output=True)
            subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, capture_output=True)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_it_runs_only_for_version_tags_after_the_verification_gate(self):
        self.assertRegex(self.text, r"on:\n  push:\n    tags: \[\"v\*\"\]\n")
        self.assertIn("needs: verify", self.text)
        self.assertIn("scripts/verify.py --mode ci --fail-fast", self.text)
        self.assertIn('"$TAG" != "v$pkgver" && "$TAG" != "v$pkgver-$pkgrel"', self.text)

    def test_only_the_committed_project_key_signs_and_actions_are_pinned(self):
        self.assertIn("packaging/signing-key.asc", self.text)
        self.assertIn('"$expected" == "$imported"', self.text)
        self.assertIn("scripts/release_repo.sh", self.text)
        for action in re.findall(r"uses: (\S+)", self.text):
            self.assertRegex(action, r"@[0-9a-f]{40}\Z")                       # a commit, never a moving tag
        self.assertNotRegex(self.text, r"echo[^\n]*\$\{?SIGNING_(KEY|PASSPHRASE)")      # a secret is never printed

    def test_the_database_goes_up_after_its_package_and_old_packages_only_after_both(self):
        upload_package = self.text.index('gh release upload repo "$repo/$package"')
        upload_database = self.text.index('"$repo/ai-desktop.db"')
        delete_old = self.text.index("gh release delete-asset repo")
        self.assertLess(upload_package, upload_database)
        self.assertLess(upload_database, delete_old)


if __name__ == "__main__":
    unittest.main()
