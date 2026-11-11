"""Install the application code and the isolated account ONLY in the disposable Ubuntu CI VM.

No model, OAuth, real report, pacman, enable or changes to the developer host.
Normal verify.py never runs this driver. The explicit flag AND CI preflight
must pass before any sudo. Existing accounts/files are refused, not repaired.
Later CI drivers (ci_analysis_systemd.py, ci_codex_quota.py) install their own units on top.
"""
import grp
import os
from pathlib import Path
import pwd
import subprocess
import sys

import installed_system

ROOT = Path(__file__).resolve().parents[1]
DEST = Path("/usr/lib/ai-desktop")
UNITS = Path("/usr/lib/systemd/system")


def _require_ci():
    uid = os.geteuid()
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Linux"
            or uid <= 0 or uid != os.getuid()):
        raise ValueError("disposable-ci-only")
    release = Path("/etc/os-release").read_text().splitlines()
    if ("ID=ubuntu" not in release or 'VERSION_ID="24.04"' not in release
            or Path("/proc/1/comm").read_text().strip() != "systemd"):
        raise ValueError("ci-platform")
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup("ai-desktop")
        except KeyError:
            pass
        else:
            raise ValueError("existing-account")
    for path in (DEST, Path("/var/lib/ai-desktop")):
        if os.path.lexists(path):
            raise ValueError("existing-installation")
    return uid


def _run(*args):
    subprocess.run(args, check=True, timeout=30,
                   env={"PATH": "/usr/bin", "LANG": "C.UTF-8"})


def _install():
    _run("sudo", "-n", "/usr/bin/install", "-d", "-m755", str(DEST / "ai_desktop"))
    sources = sorted((ROOT / "ai_desktop").glob("*.py"))
    if not sources or any(p.is_symlink() or not p.is_file() for p in sources):
        raise ValueError("source-code")
    _run("sudo", "-n", "/usr/bin/install", "-m644", *(str(p) for p in sources),
         str(DEST / "ai_desktop"))
    _run("sudo", "-n", "/usr/bin/systemd-sysusers", str(ROOT / "packaging/sysusers.d/ai-desktop.conf"))
    _run("sudo", "-n", "/usr/bin/systemd-tmpfiles", "--create", str(ROOT / "packaging/tmpfiles.d/ai-desktop.conf"))
    _run("sudo", "-n", "/usr/bin/systemctl", "daemon-reload")


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv) != ["--allow-disposable-systemd-test"]:
        print("CI PROVISION: REFUSED (explicit disposable-CI flag required)")
        return 2
    stage = "preflight"
    try:
        _require_ci()
        stage = "install"
        _install()
        stage = "verify"
        installed_system._installed_matches()
    except (OSError, ValueError, subprocess.SubprocessError):
        print("CI PROVISION: FAIL stage=" + stage)
        return 2
    print("CI PROVISION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
