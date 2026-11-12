"""The distribution this computer runs, read from os-release (systemd's standard file), never assumed.

AI Desktop targets the Arch Linux family. CachyOS-specific procedures are offered only where /etc/os-release says
CachyOS; plain Arch or another Arch-based system gets the general Arch procedures only.
"""
import functools
import os
import re
import stat

PATHS = ("/etc/os-release", "/usr/lib/os-release")
_LINE = re.compile(r"([A-Z][A-Z0-9_]*)=(.*)")


def _parse(text):
    values = {}
    for line in text.splitlines():
        match = _LINE.fullmatch(line.strip())
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def read(paths=PATHS):
    """{ID, ID_LIKE, PRETTY_NAME, ...} from the first readable os-release that root owns; {} when there is none."""
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except Exception:
            continue
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 64 * 1024:
                continue
            return _parse(os.read(fd, 64 * 1024).decode("utf-8", "replace"))
        finally:
            os.close(fd)
    return {}


@functools.lru_cache(maxsize=1)
def current():
    return read()


def ids(values=None):
    """The distribution and the ones it declares itself like, lower case (ID first)."""
    values = current() if values is None else values
    return tuple(item.lower() for item in [values.get("ID", "")] + values.get("ID_LIKE", "").split() if item)


def is_cachyos(values=None):
    return "cachyos" in ids(values)
