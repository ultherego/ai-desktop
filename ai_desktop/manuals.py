"""Installed manual pages as task evidence (S4): the documentation of the version that is actually installed.

The controller reads the page file itself from /usr/share/man (never runs `man`, never uses the network), converts
the roff source to plain text and returns bounded excerpts around a literal phrase, or the start of the page. A page
is public documentation of an installed package, not user data, so it needs no files.read rule; nothing outside the
fixed manual tree, no link and nothing writable by others is read.
"""
import gzip
import os
import re
import stat

ROOT = "/usr/share/man"
PAGE = re.compile(r"[a-z0-9][a-z0-9._+-]{0,63}\Z")
SECTION = re.compile(r"[1-9][a-z]{0,3}\Z")
MAX_SOURCE = 4 * 1024 * 1024           # a compressed page read at most
MAX_TEXT = 8 * 1024 * 1024
MAX_FRAGMENTS = 4
CONTEXT_LINES = 6
MAX_FRAGMENT_BYTES = 4096
_ESCAPES = (("\\-", "-"), ("\\&", ""), ("\\(aq", "'"), ("\\(lq", '"'), ("\\(rq", '"'), ("\\(em", "—"), ("\\(en", "–"),
            ("\\(bu", "•"), ("\\e", "\\"), ("\\ ", " "), ("\\~", " "), ("\\|", ""), ("\\^", ""), ("\\*(Aq", "'"))
_FONT = re.compile(r"\\f(?:\[[A-Z]*\]|\(..|.)")


def _read(page, section):
    if type(page) is not str or not PAGE.fullmatch(page) or type(section) is not str or not SECTION.fullmatch(section):
        raise ValueError("manual-name")
    directory = os.path.join(ROOT, "man" + section[0])
    for name, compressed in ((page + "." + section + ".gz", True), (page + "." + section, False)):
        path = os.path.join(directory, name)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            continue
        except OSError:
            raise ValueError("manual-unreadable") from None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or info.st_size > MAX_SOURCE):
                raise ValueError("manual-unreadable")
            raw = os.read(fd, MAX_SOURCE + 1)
        finally:
            os.close(fd)
        if compressed:
            try:
                with gzip.GzipFile(fileobj=__import__("io").BytesIO(raw)) as stream:
                    raw = stream.read(MAX_TEXT + 1)
            except (OSError, EOFError):
                raise ValueError("manual-unreadable") from None
        if len(raw) > MAX_TEXT:
            raise ValueError("manual-unreadable")
        return path, raw.decode("utf-8", "replace")
    raise ValueError("manual-missing")


def plain(source):
    """Readable text from man(7) roff: headings kept, macros and font escapes dropped. Approximate, never executed."""
    lines = []
    for line in source.splitlines():
        if line.startswith(('.\\"', "'\\\"")):
            continue
        if line.startswith("."):
            macro, _, rest = line[1:].partition(" ")
            if macro in ("SH", "SS"):
                lines.append("")
                line = rest.strip().strip('"')
            elif macro in ("TP", "PP", "LP", "P", "sp", "br", "IP"):
                lines.append("")
                if macro != "IP" or not rest.strip():
                    continue
                line = rest.strip().strip('"')
            elif macro in ("B", "I", "BR", "IR", "RB", "RI", "BI", "IB"):
                line = rest.replace('"', "")
            else:
                continue
        for old, new in _ESCAPES:
            line = line.replace(old, new)
        line = _FONT.sub("", line)
        lines.append(line.rstrip())
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def excerpt(page, section, needle=None):
    """{"path", "lines", "fragments": [{"first","last","text"}], "complete"} for one installed page."""
    path, source = _read(page, section)
    lines = plain(source).splitlines()
    fragments = []
    if needle:
        if type(needle) is not str or not 2 <= len(needle) <= 64 or "\n" in needle:
            raise ValueError("manual-needle")
        wanted = needle.casefold()
        hits = [number for number, line in enumerate(lines) if wanted in line.casefold()]
        for number in hits:
            if fragments and number <= fragments[-1][1]:
                continue
            first, last = max(0, number - 1), min(len(lines) - 1, number + CONTEXT_LINES)
            fragments.append((first, last))
            if len(fragments) >= MAX_FRAGMENTS:
                break
        complete = len(fragments) < MAX_FRAGMENTS
    else:
        fragments, complete = [(0, min(len(lines) - 1, 40))], len(lines) <= 41
    result = []
    for first, last in fragments:
        text = "\n".join(lines[first:last + 1]).encode("utf-8")[:MAX_FRAGMENT_BYTES].decode("utf-8", "ignore")
        result.append({"first": first + 1, "last": last + 1, "text": text})
    return {"path": path, "lines": len(lines), "fragments": result, "complete": complete}
