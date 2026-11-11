"""`ai-desktop commands`: the operations permissions.toml allows, in a form a person and a program can read (S5).

Each entry is one template with its class (read: runs without asking; change: asks every time), whether its program
is installed in the fixed system directories, and the installed package and version that own it. Nothing is run
except a local `pacman -Qo` query for the owning package; no model, no network.
"""
import argparse
import json
import subprocess

from . import command_ops, permissions
from .command_presets import PRESETS


def _owner(path, cache):
    if path in cache:
        return cache[path]
    result = None
    try:
        done = subprocess.run(["/usr/bin/pacman", "-Qo", path], capture_output=True, text=True, timeout=10,
                              env={"PATH": "/usr/bin", "LANG": "C"}, check=False)
        words = done.stdout.strip().split()
        if done.returncode == 0 and len(words) >= 2:
            result = {"package": words[-2], "version": words[-1]}
    except (OSError, subprocess.SubprocessError):
        pass
    cache[path] = result
    return result


def catalog(policy):
    preset = {template for name in PRESETS for template in PRESETS[name]}
    entries, cache = [], {}
    for kind, templates in (("read", policy.commands()), ("change", policy.changes())):
        for tokens in templates:
            text = " ".join(tokens)
            path = command_ops.resolve(tokens[0])
            entry = {"template": text, "class": kind, "program": tokens[0], "installed": path is not None,
                     "in_preset": kind == "read" and text in preset}
            if path is not None:
                owner = _owner(path, cache)
                if owner:
                    entry.update(owner)
            entries.append(entry)
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ai-desktop commands",
                                     description="The commands permissions.toml allows, read or change, with the installed package versions")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)
    try:
        policy = permissions.load_policy(optional=True)
    except ValueError as exc:
        print("permissions.toml is not valid: " + str(exc))
        return 2
    if policy is None:
        print("No permissions.toml: no commands are allowed.")
        return 0
    entries = catalog(policy)
    if args.json:
        print(json.dumps({"commands": entries}, ensure_ascii=False, indent=2))
        return 0
    for kind, title in (("read", "Run without asking (read)"), ("change", "Ask every time (change)")):
        rows = [entry for entry in entries if entry["class"] == kind]
        print("%s: %d" % (title, len(rows)))
        for entry in rows:
            status = ("%s %s" % (entry.get("package", "?"), entry.get("version", "")) if entry["installed"]
                      else "not installed")
            print("  %-48s %s%s" % (entry["template"], status, " · also in preset diagnostics" if entry["in_preset"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
