"""Accounts and home directories as this computer defines them, discovered instead of assumed.

Users may live outside /home (e.g. /var/home on image-based systems, /srv/home on servers) and a distribution may start
regular UIDs at another number than 1000. The regular UID range comes from the root-owned /etc/login.defs (UID_MIN,
UID_MAX; 1000..60000 when unset), the home directories from the account database (pwd), so the read sandbox can hide
every user's home and no check depends on one layout.
"""
import functools
import os
import pwd
import re
import stat

LOGIN_DEFS = "/etc/login.defs"
DEFAULT_UID_MIN, DEFAULT_UID_MAX = 1000, 60000
# Never hidden as a "home": hiding these would hide the system itself (an account whose home is / or /usr).
_SYSTEM = ("/", "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/opt", "/proc", "/run", "/sbin", "/sys", "/usr", "/var")


def uid_range(path=LOGIN_DEFS):
    """(UID_MIN, UID_MAX) of regular users from a root-owned, not group/other-writable login.defs; defaults otherwise.
    The system file is read once per process."""
    return _uid_range(path) if path != LOGIN_DEFS else _system_uid_range()


@functools.lru_cache(maxsize=1)
def _system_uid_range():
    return _uid_range(LOGIN_DEFS)


def _uid_range(path):
    low, high = DEFAULT_UID_MIN, DEFAULT_UID_MAX
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except Exception:                       # unreadable for any reason: the documented defaults
        return low, high
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 256 * 1024:
            return low, high
        text = os.read(fd, 256 * 1024).decode("utf-8", "replace")
    finally:
        os.close(fd)
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(UID_MIN|UID_MAX)\s+(\d{1,9})\s*(?:#.*)?", line)
        if match:
            if match.group(1) == "UID_MIN":
                low = int(match.group(2))
            else:
                high = int(match.group(2))
    if not 1 <= low <= high:
        return DEFAULT_UID_MIN, DEFAULT_UID_MAX
    return low, high


def is_regular_uid(uid):
    """A regular (non-system) user: from this system's UID_MIN up. No upper cut at UID_MAX, because systemd-homed and
    directory-service accounts live above it; only the invalid 2**32-1 is excluded."""
    return type(uid) is int and not isinstance(uid, bool) and uid_range()[0] <= uid < 2**32 - 1


def _usable(directory):
    if type(directory) is not str or not directory.startswith("/"):
        return None
    real = os.path.realpath(directory)
    if real in _SYSTEM or not os.path.isdir(real):
        return None
    return real


def home_roots(entries=None):
    """Directories that hold users' homes and must be hidden from read commands: /home and /root when present, every
    regular account's home (wherever it is) and the caller's own. Nested entries are covered by their parent. The
    system's account list is read once per process (policy checks call this for every path)."""
    return _home_roots(entries) if entries is not None else _system_home_roots()


@functools.lru_cache(maxsize=1)
def _system_home_roots():
    return _home_roots(None)


def _home_roots(entries):
    found = set()
    for fixed in ("/home", "/root"):
        usable = _usable(fixed)
        if usable:
            found.add(usable)
    low, high = uid_range()
    try:
        accounts = pwd.getpwall() if entries is None else entries
    except (OSError, KeyError):
        accounts = []
    for account in accounts:
        if low <= account.pw_uid <= high or account.pw_uid == os.getuid():
            usable = _usable(account.pw_dir)
            if usable:
                found.add(usable)
    try:
        own = _usable(pwd.getpwuid(os.getuid()).pw_dir)
        if own:
            found.add(own)
    except KeyError:
        pass
    ordered = sorted(found, key=len)
    return tuple(path for index, path in enumerate(ordered)
                 if not any(path.startswith(parent + "/") for parent in ordered[:index]))
