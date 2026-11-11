"""Match references to request-local observations from the command broker.

This module never executes a command or treats model/user prose as a version
probe. Its caller must supply an actual completed broker result. Package epochs
and release suffixes describe packaging; git/post-release suffixes remain part
of the upstream version and must not silently match a release manual.
"""
import re

_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+_.-]{0,127}\Z")
_EXACT = r"[A-Za-z0-9][A-Za-z0-9.+:_-]{0,63}"
_BOUND = r"[A-Za-z0-9][A-Za-z0-9.+_~]{0,63}"
# A reference's version: "rolling", one exact upstream version, or a range ">=LOW" / ">=LOW,<HIGH" compared as pacman does.
VERSION_FIELD = re.compile(r"rolling|" + _EXACT + r"|>=" + _BOUND + r"(?:,<" + _BOUND + r")?\Z")
_VERSION = re.compile(r"(?:[0-9]+:)?([A-Za-z0-9][A-Za-z0-9.+_~]{0,127})-[0-9]+(?:\.[0-9]+)?\Z")


def trusted_version_observation(argv, result):
    """Accept one explicit pacman -Q PACKAGE result; otherwise return no fact."""
    if (type(argv) not in (list, tuple) or len(argv) != 3
            or argv[0] not in ("pacman", "/usr/bin/pacman") or argv[1] != "-Q"
            or type(argv[2]) is not str or not _PACKAGE.fullmatch(argv[2])
            or type(result) is not dict or result.get("op") != "run"
            or result.get("argv") != list(argv) or result.get("role", "read") != "read"
            or result.get("status") != "ok" or type(result.get("exit")) is not int
            or result["exit"] != 0 or result.get("truncated") is not False):
        return {}
    output = result.get("output")
    if type(output) is not str:
        return {}
    try:
        if len(output.encode("utf-8")) > 512:
            return {}
    except UnicodeError:
        return {}
    # No warnings, additional package rows, terminal escapes or embedded lines.
    match = re.fullmatch(re.escape(argv[2]) + r" ([^\s]+)\n?", output)
    if not match:
        return {}
    version = _VERSION.fullmatch(match.group(1))
    return {argv[2]: version.group(1)} if version else {}


def _alnum(c):
    return c.isascii() and c.isalnum()


def rpmvercmp(a, b):
    """-1, 0 or 1 exactly as pacman's rpmvercmp (libalpm) orders two version strings without epoch or release."""
    if a == b:
        return 0
    one = ptr1 = two = ptr2 = 0
    n1, n2 = len(a), len(b)
    while one < n1 and two < n2:
        while one < n1 and not _alnum(a[one]):
            one += 1
        while two < n2 and not _alnum(b[two]):
            two += 1
        if not (one < n1 and two < n2):
            break
        if (one - ptr1) != (two - ptr2):                      # different separator lengths
            return -1 if (one - ptr1) < (two - ptr2) else 1
        ptr1, ptr2 = one, two
        if a[ptr1].isdigit():
            while ptr1 < n1 and a[ptr1].isascii() and a[ptr1].isdigit():
                ptr1 += 1
            while ptr2 < n2 and b[ptr2].isascii() and b[ptr2].isdigit():
                ptr2 += 1
            isnum = True
        else:
            while ptr1 < n1 and a[ptr1].isascii() and a[ptr1].isalpha():
                ptr1 += 1
            while ptr2 < n2 and b[ptr2].isascii() and b[ptr2].isalpha():
                ptr2 += 1
            isnum = False
        if two == ptr2:                                        # numeric beats alpha (an empty segment of the other type)
            return 1 if isnum else -1
        seg1, seg2 = a[one:ptr1], b[two:ptr2]
        if isnum:
            seg1, seg2 = seg1.lstrip("0"), seg2.lstrip("0")
            if len(seg1) != len(seg2):
                return 1 if len(seg1) > len(seg2) else -1
        if seg1 != seg2:
            return -1 if seg1 < seg2 else 1
        one, two = ptr1, ptr2
    if one >= n1 and two >= n2:
        return 0
    if (one >= n1 and not (two < n2 and b[two].isascii() and b[two].isalpha())) or (one < n1 and a[one].isascii() and a[one].isalpha()):
        return -1
    return 1


def _in_range(expected, observed):
    low, _, high = expected[2:].partition(",<")
    return rpmvercmp(observed, low) >= 0 and (not high or rpmvercmp(observed, high) < 0)


def reference_compatibility(metadata, versions):
    """Return matched/mismatch/unknown/rolling without guessing compatibility."""
    if type(metadata) is not dict or type(versions) is not dict:
        return "unknown"
    expected, names = metadata.get("version"), metadata.get("tools")
    if expected == "rolling":
        return "rolling"
    if (type(expected) is not str or not expected or type(names) is not list
            or not names or any(type(name) is not str for name in names)):
        return "unknown"
    observed = [versions.get(name) for name in names]
    fits = (lambda value: _in_range(expected, value)) if expected.startswith(">=") else (lambda value: value == expected)
    if any(type(value) is str and not fits(value) for value in observed):
        return "mismatch"
    if all(type(value) is str and fits(value) for value in observed):
        return "matched"
    return "unknown"
