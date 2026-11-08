"""Shared helper for the acceptance-script tests: a full snapshot of a directory tree."""
import os
import stat


def tree_snapshot(root):
    """Include every name, entry type, mode and file/link contents."""
    result = {}
    for path in (root, *sorted(root.rglob("*"))):
        info = path.lstat()
        data = (path.read_bytes() if stat.S_ISREG(info.st_mode) else
                os.fsencode(os.readlink(path)) if stat.S_ISLNK(info.st_mode) else None)
        result[path.relative_to(root).as_posix()] = (stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), data)
    return result
