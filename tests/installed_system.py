"""Read-only checks of a root-owned installation shared by the disposable CI and CachyOS acceptance drivers.

Never invokes sudo, pacman installation, systemctl start/enable, a shell, or a client.
"""
import hashlib
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CORE_MODULES = ("__init__.py", "e2_socket_activation.py", "e2_ipc.py", "e2_account.py",
                "report_policy.py", "transfer_consent.py")


def _command(argv):
    done = subprocess.run(argv, capture_output=True, text=True, timeout=5,
                          env={"PATH": "/usr/bin", "LANG": "C"}, check=False)
    if done.returncode:
        raise ValueError("command")
    return done.stdout.strip()


def _property(unit, name):
    return _command(["/usr/bin/systemctl", "show", unit, "--value", "--property=" + name])


def _installed_matches():
    """Root-owned, not group/other-writable directories and the exact core modules."""
    for name in ("/usr", "/usr/lib", "/usr/lib/ai-desktop",
                 "/usr/lib/ai-desktop/ai_desktop", "/usr/lib/systemd",
                 "/usr/lib/systemd/system"):
        info = os.lstat(name)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("installed-path")
    for name in CORE_MODULES:
        source, installed = ROOT / "ai_desktop" / name, Path("/usr/lib/ai-desktop/ai_desktop") / name
        info = installed.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or hashlib.sha256(source.read_bytes()).digest() !=
                hashlib.sha256(installed.read_bytes()).digest()):
            raise ValueError("installed-code")
