"""The DankBar widget belongs to DMS: it is linked into DMS's plugin directory only where DMS is installed.

The package ships the widget at PACKAGED and never changes a user's DMS configuration on its own. `ai-desktop widget
install`, run by that user, detects DMS and creates one link in DMS's plugin directory; without DMS it does nothing.
An existing plugin of the same name is never replaced, and `remove` deletes only this package's own link.
"""
import os
import shutil

PACKAGED = "/usr/share/ai-desktop/dms/AiDesktop"
NAME = "AiDesktop"
RELOAD = "dms ipc call plugins reload aiDesktop"


def dms_present():
    """DMS (DankMaterialShell) installs its `dms` command in /usr/bin; nothing else counts as DMS."""
    return shutil.which("dms", path="/usr/bin") is not None


def plugins_dir():
    """The directory DMS scans for plugins: $XDG_CONFIG_HOME/dms/plugins, or ~/.config/dms/plugins."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not base or not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "dms", "plugins")


def _ours(path):
    return os.path.islink(path) and os.readlink(path) == PACKAGED


def status():
    """One of: no-dms, not-packaged, linked, absent, occupied."""
    if not dms_present():
        return "no-dms"
    if not os.path.isdir(PACKAGED) or os.path.islink(PACKAGED):
        return "not-packaged"
    target = os.path.join(plugins_dir(), NAME)
    if _ours(target):
        return "linked"
    return "occupied" if os.path.lexists(target) else "absent"


def install():
    """Link the widget when DMS is installed. Returns the resulting state; never replaces anything."""
    state = status()
    if state != "absent":
        return state
    directory = plugins_dir()
    os.makedirs(directory, mode=0o755, exist_ok=True)
    try:
        os.symlink(PACKAGED, os.path.join(directory, NAME))
    except FileExistsError:
        return "occupied"
    return "installed"


def remove():
    """Delete only this package's own link."""
    target = os.path.join(plugins_dir(), NAME)
    if not _ours(target):
        return "not-linked"
    os.unlink(target)
    return "removed"


MESSAGES = {
    "no-dms": "DMS is not installed (no /usr/bin/dms): the DankBar widget is not used, nothing was changed.",
    "not-packaged": "The packaged widget is missing from %s. Reinstall AI Desktop." % PACKAGED,
    "linked": "The DankBar widget is linked into DMS. Enable AI Desktop in DMS Settings -> Plugins if it is off.",
    "absent": "DMS is installed and the widget is not linked yet: run ai-desktop widget install.",
    "occupied": "Another plugin named AiDesktop is already in the DMS plugin directory; it was left as it is.",
    "installed": "Linked the DankBar widget into DMS. Enable AI Desktop in DMS Settings -> Plugins, add it in the "
                 "DankBar editor, then run: " + RELOAD,
    "removed": "Removed the widget link from the DMS plugin directory.",
    "not-linked": "The widget is not linked by AI Desktop; nothing was removed.",
}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="ai-desktop widget",
                                     description="Link the DankBar widget into DMS, only where DMS is installed")
    parser.add_argument("action", nargs="?", choices=("status", "install", "remove"), default="status")
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        print("Run this as the desktop user, not as root.")
        return 2
    try:
        state = {"status": status, "install": install, "remove": remove}[args.action]()
    except OSError:
        print("Could not change the DMS plugin directory. Check its permissions.")
        return 2
    print(MESSAGES[state])
    return 0 if state in ("linked", "installed", "removed", "absent", "no-dms") else 1
