"""Small local choices of the user in ~/.config/ai-desktop (no data, no authority).

`save`: whether interactive conversations save themselves ("on", the default, or "off"). With "off" a conversation is
saved only when asked for with `--session new`. Reads never follow a link and fall back to the default; writes are
atomic, private (0600) and never through a link.
"""
import os
import pwd
import secrets

SAVE_VALUES = ("on", "off")


def _directory(home=None):
    base = home if home is not None else pwd.getpwuid(os.geteuid()).pw_dir
    return os.path.join(base, ".config", "ai-desktop")


def _read(name, allowed, default, home=None):
    try:
        descriptor = os.open(os.path.join(_directory(home), name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            value = os.read(descriptor, 64).decode("ascii").strip()
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        return default
    return value if value in allowed else default


def _write(name, value, home=None):
    directory = _directory(home)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if os.path.islink(directory):
        raise OSError("settings directory is a link")
    temporary = os.path.join(directory, ".%s.%s" % (name, secrets.token_hex(8)))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(descriptor, (value + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, os.path.join(directory, name))
    except OSError:
        os.unlink(temporary)
        raise


def saving(home=None):
    """True unless the user turned automatic saving off (`ai-desktop settings save off`)."""
    return _read("save", SAVE_VALUES, "on", home) == "on"


def set_saving(enabled, home=None):
    _write("save", "on" if enabled else "off", home)
