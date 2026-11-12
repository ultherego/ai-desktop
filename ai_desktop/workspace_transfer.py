"""Move or copy a file or a whole folder inside one write workspace, with a journal and undo (S10).

Nothing is ever overwritten or unlinked:
- **move** is one `renameat2(RENAME_NOREPLACE)` on one filesystem; undo renames it back when the moved entry is still
  the one recorded and its old name is free;
- **copy** recreates the tree in a hidden sibling of the destination (`.ai-desktop-copy-<plan>`) and publishes it with
  one no-replace rename at the end, so an interruption never leaves a half copy under the destination name; undo moves
  the copy into the task's private state directory (`.ai-desktop-tasks/transfer-<plan>`), it does not delete it.
The journal (`<plan>.transfer.json`) is written before anything moves. Symlinks, devices, sockets and FIFOs are refused,
as for the other task executors; same-UID hostile renames stay outside the boundary.
"""
from dataclasses import dataclass
import errno
import fcntl
import json
import os
import re
import secrets
import stat
import time

from . import task_workspace as tw
from .analysis_contract import canonical
from .e2_ipc import _unique_pairs
from .fs_publish import rename_noreplace
from .workspace_executor import ExecutionDenied, _parent

MAX_FILES = 20000                          # entries of one copied tree (files and folders)
SPACE_MARGIN = 64 * 1024 * 1024            # free space a copy leaves on the target filesystem
APPROVAL_SECONDS = 600
_PLAN = re.compile(r"[a-f0-9]{32}\Z")
_STATUSES = ("prepared", "done", "failed", "undone", "conflict")
_COPY_CHUNK = 1024 * 1024


MAX_PATH_BYTES = 4096
MAX_DEPTH = 32


def path(value):
    """A path relative to the workspace for a move or copy: real folder names (spaces, any language, deep trees) are
    fine; empty, `.`/`..` and control characters are not, nor the task's private state. Wider than the edit paths of
    task_contract on purpose: a move or copy writes no content of its own."""
    import unicodedata
    if (type(value) is not str or not value or len(value.encode("utf-8", "surrogatepass")) > MAX_PATH_BYTES
            or value.startswith("/") or value.endswith("/")):
        raise ValueError("path")
    parts = value.split("/")
    if len(parts) > MAX_DEPTH:
        raise ValueError("path")
    for part in parts:
        if (part in ("", ".", "..") or len(part.encode("utf-8", "surrogatepass")) > 255
                or part.casefold() == ".ai-desktop-tasks" or part.casefold().startswith((".ai-task-", ".ai-desktop-copy-"))
                or any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp") for c in part)):
            raise ValueError("path")
    return value


class TransferDenied(ExecutionDenied):
    """A refusal with a short reason: missing, exists, inside, device, type, size, space, changed, workspace."""

    def __init__(self, reason):
        super().__init__("TRANSFER: REFUSED " + reason)
        self.reason = reason


@dataclass(frozen=True)
class TransferAuthorization:
    plan: str
    token: str


class TransferPlan:
    """What the owner sees before the yes; `execute` acts only on exactly this, still unchanged."""

    def __init__(self, data, clock=time.monotonic):
        self._data, self._clock = data, clock
        self._created = clock()
        self._token = secrets.token_hex(16)
        self._spent = False

    def data(self):
        return json.loads(json.dumps(self._data))

    def authorize(self):
        return TransferAuthorization(self._data["plan"], self._token)

    def consume(self, authorization):
        if (self._spent or type(authorization) is not TransferAuthorization or authorization.plan != self._data["plan"]
                or authorization.token != self._token or self._clock() - self._created > APPROVAL_SECONDS):
            raise TransferDenied("approval")
        self._spent = True
        return self.data()


def _journal_name(plan):
    return plan + ".transfer.json"


def _held(parent, leaf):
    info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISDIR(info.st_mode):
        kind = "folder"
    else:
        raise TransferDenied("type")
    return info, kind


def _open_relative(base, relative):
    """A folder below `base` opened component by component without following links."""
    fd = os.dup(base)
    try:
        for part in [p for p in relative.split("/") if p]:
            child = os.open(part, tw.DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _tree(parent, leaf, *, read=False, names=None):
    """(files, folders, bytes) of a file or folder, refusing links and special files. Folders are walked by name from
    one opened handle (one descriptor at a time, however wide the tree); `names` collects every relative path."""
    info, kind = _held(parent, leaf)
    if kind == "file":
        if read and not os.access(leaf, os.R_OK, dir_fd=parent, follow_symlinks=False):
            raise TransferDenied("type")
        return 1, 0, info.st_size
    files = folders = size = 0
    top = os.open(leaf, tw.DIR, dir_fd=parent)
    try:
        pending = [""]
        while pending:
            relative = pending.pop()
            fd = _open_relative(top, relative)
            try:
                folders += 1
                for name in sorted(os.listdir(fd)):
                    child = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    inner = relative + "/" + name if relative else name
                    if stat.S_ISDIR(child.st_mode):
                        pending.append(inner)
                    elif stat.S_ISREG(child.st_mode):
                        files += 1
                        size += child.st_size
                    else:
                        raise TransferDenied("type")             # a link, device, socket or FIFO inside
                    if names is not None:
                        names.append(inner)
                    if files + folders > MAX_FILES:
                        raise TransferDenied("size")
            finally:
                os.close(fd)
    finally:
        os.close(top)
    return files, folders, size


def _inside(source, target):
    return target == source or target.startswith(source + "/")


def _authorized(policy, index, workspace, source, target, names, operation):
    """Every entry of the tree, not only its top: the source readable (deny and default protection win) and, for a
    move, writable; the destination inside the chosen write rule and not denied. None of it is ever skipped."""
    if policy is None:
        return
    for relative in [""] + names:
        tail = "/" + relative if relative else ""
        source_path, target_path = workspace + "/" + source + tail, workspace + "/" + target + tail
        if not policy.allows(source_path) or policy.excludes(source_path) or not policy.can_write(target_path, index=index):
            raise TransferDenied("denied")
        if operation == "move" and not policy.can_write(source_path, index=index):
            raise TransferDenied("denied")


def _journal_room(state):
    """Refused BEFORE the first journal is written: past MAX_JOURNALS the history could no longer be listed."""
    count = sum(1 for name in os.listdir(state) if re.fullmatch(r"[a-f0-9]{32}\.transfer\.json", name))
    if count >= tw.MAX_JOURNALS:
        raise TransferDenied("full")


def prepare(workspace, operation, source, target, *, clock=time.monotonic, policy=None, index=None):
    """Check everything BEFORE the owner's question; nothing is written. Paths are relative to the workspace. With
    `policy`, every entry of the tree is checked against it (read for the source, the write rule `index` for the
    destination)."""
    if operation not in ("move", "copy"):
        raise TransferDenied("operation")
    try:
        source, target = path(source), path(target)
    except ValueError:
        raise TransferDenied("path") from None
    if _inside(source, target):
        raise TransferDenied("inside")
    root = tw._root(workspace)
    try:
        src_parent, src_leaf = _parent(root, source)
        try:
            dst_parent, dst_leaf = _parent(root, target)
        except FileNotFoundError:
            os.close(src_parent)
            raise TransferDenied("missing") from None
        try:
            try:
                info, kind = _held(src_parent, src_leaf)
            except FileNotFoundError:
                raise TransferDenied("missing") from None
            try:
                os.stat(dst_leaf, dir_fd=dst_parent, follow_symlinks=False)
                raise TransferDenied("exists")                      # never overwrite: the owner decides first
            except FileNotFoundError:
                pass
            target_dir = os.fstat(dst_parent)
            if target_dir.st_uid != os.geteuid():
                raise TransferDenied("workspace")
            names = []
            if operation == "move":
                if info.st_uid != os.geteuid():
                    raise TransferDenied("type")
                if os.fstat(src_parent).st_dev != target_dir.st_dev:
                    raise TransferDenied("device")                  # one filesystem only; copy works across
                files, folders, size = _tree(src_parent, src_leaf, names=names)
            else:
                if target_dir.st_dev != os.fstat(root).st_dev:
                    raise TransferDenied("mount")                   # undo keeps the copy in the workspace's own state
                files, folders, size = _tree(src_parent, src_leaf, read=True, names=names)
                free = os.statvfs(dst_parent)
                if size + SPACE_MARGIN > free.f_bavail * free.f_frsize:
                    raise TransferDenied("space")
            _authorized(policy, index, workspace, source, target, names, operation)
            data = {"workspace": workspace, "root": tw.identity(os.fstat(root)), "operation": operation,
                    "from": source, "to": target, "kind": kind, "entry": tw.identity(info), "files": files,
                    "folders": folders, "bytes": size, "target_parent": tw.identity(target_dir),
                    "plan": secrets.token_hex(16)}
            return TransferPlan(data, clock=clock)
        finally:
            os.close(src_parent)
            os.close(dst_parent)
    except ExecutionDenied:
        raise
    except (OSError, ValueError):
        raise TransferDenied("workspace") from None
    finally:
        os.close(root)


def _copy_file(src_dir, name, dst_dir, target):
    source = os.open(name, tw.READ, dir_fd=src_dir)
    try:
        info = os.fstat(source)
        if not stat.S_ISREG(info.st_mode):
            raise TransferDenied("type")
        os.set_blocking(source, True)
        out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                      stat.S_IMODE(info.st_mode) & 0o755 | 0o600, dir_fd=dst_dir)
        try:
            while True:
                block = os.read(source, _COPY_CHUNK)
                if not block:
                    break
                view = memoryview(block)
                while view:
                    view = view[os.write(out, view):]
            os.fsync(out)
        finally:
            os.close(out)
    finally:
        os.close(source)


def _copy_tree(src_parent, src_leaf, dst_parent, staging, *, files=None, size=None):
    """Recreate the file or folder under `staging` in dst_parent; links and special files refuse the copy. Returns the
    staging entry's identity, taken right after it was created. With `files`/`size` (the approved plan) a source that
    grew after the plan is refused rather than copied in full."""
    info, kind = _held(src_parent, src_leaf)
    if kind == "file":
        _copy_file(src_parent, src_leaf, dst_parent, staging)
        return tw.identity(os.stat(staging, dir_fd=dst_parent, follow_symlinks=False))
    os.mkdir(staging, 0o700, dir_fd=dst_parent)
    made = tw.identity(os.stat(staging, dir_fd=dst_parent, follow_symlinks=False))
    entries = copied = 0
    source_top = os.open(src_leaf, tw.DIR, dir_fd=src_parent)
    target_top = os.open(staging, tw.DIR, dir_fd=dst_parent)
    try:
        if tw.identity(os.fstat(target_top)) != made:
            raise TransferDenied("changed")
        pending = [""]
        while pending:
            relative = pending.pop()
            source = _open_relative(source_top, relative)
            try:
                target = _open_relative(target_top, relative)
                try:
                    for name in sorted(os.listdir(source)):
                        child = os.stat(name, dir_fd=source, follow_symlinks=False)
                        entries += 1
                        if files is not None and entries > files + MAX_FILES:
                            raise TransferDenied("changed")
                        if stat.S_ISDIR(child.st_mode):
                            os.mkdir(name, stat.S_IMODE(child.st_mode) & 0o755 | 0o700, dir_fd=target)
                            pending.append(relative + "/" + name if relative else name)
                        elif stat.S_ISREG(child.st_mode):
                            copied += child.st_size
                            if size is not None and copied > size + SPACE_MARGIN:
                                raise TransferDenied("changed")      # the source grew after the plan was shown
                            _copy_file(source, name, target, name)
                        else:
                            raise TransferDenied("type")
                    os.fsync(target)
                finally:
                    os.close(target)
            finally:
                os.close(source)
        os.fchmod(target_top, stat.S_IMODE(info.st_mode) & 0o755 | 0o700)   # private while it was being built
    finally:
        os.close(source_top)
        os.close(target_top)
    return made


def _discard_staging(parent, name, expected=None):
    """Remove only the hidden staging tree this run created: with `expected` (its identity from creation) anything
    else now under that name is left alone, so a folder swapped in by another account is never deleted. The tree is
    emptied through the opened handle; the final rmdir by name cannot remove a non-empty swapped-in folder."""
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if expected is not None and tw.identity(info) != expected:
        return
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent)
        return
    fd = os.open(name, tw.DIR, dir_fd=parent)
    try:
        if expected is not None and tw.identity(os.fstat(fd)) != expected:
            return
        _empty(fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


def _empty(fd):
    for child in os.listdir(fd):
        info = os.stat(child, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            inner = os.open(child, tw.DIR, dir_fd=fd)
            try:
                _empty(inner)
            finally:
                os.close(inner)
            os.rmdir(child, dir_fd=fd)
        else:
            os.unlink(child, dir_fd=fd)


def publish(parent, staging, leaf, expected):
    """One no-replace rename of the staging entry to its real name, only while it is still the entry this run made,
    and checked again after: True when the published entry is exactly that one."""
    if tw.identity(os.stat(staging, dir_fd=parent, follow_symlinks=False)) != expected:
        raise TransferDenied("changed")
    rename_noreplace(parent, staging, parent, leaf)
    return tw.identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) == expected


def finish(state, name, journal, published, error=None):
    """The journal's final word, written on every way out (also Ctrl-C or an unexpected error): `done` once the entry
    is published under its real name, otherwise `failed`. Undo and pruning then always see the truth."""
    journal["status"] = "done" if published else "failed"
    try:
        tw._save(state, name, journal)
    except (OSError, ValueError):
        if error is None:
            raise


def execute(prepared, authorization):
    """Consume the owner's yes and perform exactly the shown move or copy; returns the plan ID for undo."""
    if type(prepared) is not TransferPlan:
        raise TransferDenied("plan")
    before = prepared.consume(authorization)
    workspace, plan = before["workspace"], before["plan"]
    root = tw._root(workspace)
    try:
        fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if tw.identity(os.fstat(root)) != before["root"]:
            raise TransferDenied("changed")
        src_parent, src_leaf = _parent(root, before["from"])
        dst_parent, dst_leaf = _parent(root, before["to"])
        try:
            info, _ = _held(src_parent, src_leaf)
            if tw.identity(info) != before["entry"] or tw.identity(os.fstat(dst_parent)) != before["target_parent"]:
                raise TransferDenied("changed")
            state = tw._state_dir(root, create=True)
            try:
                _journal_room(state)
                name = _journal_name(plan)
                journal = {"schema": 1, "plan": plan, "workspace": workspace, "operation": before["operation"],
                           "from": before["from"], "to": before["to"], "kind": before["kind"], "entry": before["entry"],
                           "files": before["files"], "bytes": before["bytes"], "status": "prepared"}
                tw._save(state, name, journal, first=True)          # intent is durable before anything moves
                published = False
                try:
                    if before["operation"] == "move":
                        if tw.identity(os.stat(src_leaf, dir_fd=src_parent, follow_symlinks=False)) != before["entry"]:
                            raise TransferDenied("changed")
                        rename_noreplace(src_parent, src_leaf, dst_parent, dst_leaf)
                        published = True
                        moved = os.stat(dst_leaf, dir_fd=dst_parent, follow_symlinks=False)
                        if tw.identity(moved) != before["entry"]:
                            raise TransferDenied("changed")
                        journal["entry"] = tw.identity(moved)
                        os.fsync(src_parent)
                        os.fsync(dst_parent)
                    else:
                        staging = ".ai-desktop-copy-" + plan
                        made = None
                        try:
                            made = _copy_tree(src_parent, src_leaf, dst_parent, staging,
                                              files=before["files"] + before.get("folders", 0), size=before["bytes"])
                            published = publish(dst_parent, staging, dst_leaf, made)
                            if not published:
                                raise TransferDenied("changed")
                            os.fsync(dst_parent)
                        except BaseException:
                            if not published:
                                _discard_staging(dst_parent, staging, made)
                            raise
                        journal["entry"] = made
                    finish(state, name, journal, True)
                except BaseException as error:
                    finish(state, name, journal, published, error)
                    if isinstance(error, OSError) and error.errno == errno.EEXIST:
                        raise TransferDenied("exists") from None
                    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
                        raise TransferDenied("space") from None
                    raise
            finally:
                os.close(state)
        finally:
            os.close(src_parent)
            os.close(dst_parent)
    except ExecutionDenied:
        raise
    except (OSError, ValueError):
        raise TransferDenied("workspace") from None
    finally:
        os.close(root)
    return plan


def _load(state, name):
    fd = os.open(name, tw.READ, dir_fd=state)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1 or info.st_size > 64 * 1024):
            raise ValueError("transfer journal")
        value = json.loads(os.read(fd, 64 * 1024 + 1), object_pairs_hook=_unique_pairs)
    finally:
        os.close(fd)
    keys = {"schema", "plan", "workspace", "operation", "from", "to", "kind", "entry", "files", "bytes", "status"}
    if (type(value) is not dict or set(value) != keys or value["schema"] != 1 or not _PLAN.match(str(value["plan"]))
            or _journal_name(value["plan"]) != name or value["operation"] not in ("move", "copy", "install")
            or value["status"] not in _STATUSES or value["kind"] not in ("file", "folder")):
        raise ValueError("transfer journal")
    path(value["from"]), path(value["to"])
    tw._valid_identity(value["entry"])
    return value


def _emptied(state, value):
    """A record that holds nothing may be pruned: a failed transfer changed nothing, an undone move is back under its old
    name. An undone copy stays while its folder is kept in the task state, and a done transfer stays so it can be undone;
    pruning never deletes user files."""
    if value["status"] == "failed" or (value["status"] == "undone" and value["operation"] == "move"):
        return True
    if value["status"] != "undone":
        return False
    try:
        os.lstat("transfer-" + value["plan"], dir_fd=state)
    except FileNotFoundError:
        return True
    return False


def recovery_records(workspace):
    """Moves and copies for task status and task prune, newest state from each journal."""
    root = tw._root(workspace)
    try:
        try:
            state = tw._state_dir(root)
        except FileNotFoundError:
            return []
        try:
            names = sorted(n for n in os.listdir(state) if re.fullmatch(r"[a-f0-9]{32}\.transfer\.json", n))
            if len(names) > tw.MAX_JOURNALS:
                raise ValueError("transfer journal count")
            result = []
            for name in names:
                value = _load(state, name)
                if value["workspace"] != workspace:
                    raise ValueError("transfer workspace")
                result.append({"plan": value["plan"], "status": value["status"], "kind": "transfer",
                               "operation": value["operation"], "from": value["from"], "to": value["to"], "files": [],
                               "journal_sha256": tw.sha(canonical(value).encode("utf-8")),
                               "modified": os.stat(name, dir_fd=state).st_mtime,
                               "prunable": _emptied(state, value)})
            return result
        finally:
            os.close(state)
    finally:
        os.close(root)


def prune(workspace, approved):
    """Remove the chosen finished transfer records, re-checked under the lock against the hash the owner saw.
    One mismatch refuses all before anything is removed. Returns the number removed."""
    chosen = tw.approved_records(approved)
    if any(not _PLAN.match(plan) for plan, _ in chosen):
        raise ValueError("prune approval")
    root = tw._root(workspace)
    try:
        fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = tw._state_dir(root)
        try:
            checked = []
            for plan, expected in chosen:
                name = _journal_name(plan)
                held = os.stat(name, dir_fd=state, follow_symlinks=False)
                value = _load(state, name)
                if value["workspace"] != workspace:
                    raise ValueError("transfer workspace")
                if tw.sha(canonical(value).encode("utf-8")) != expected:
                    raise ValueError("journal changed")
                if not _emptied(state, value):
                    raise ValueError("journal unfinished")
                checked.append((name, held))
            for name, held in checked:
                tw.retire(state, name, held)
            os.fsync(state)
            return len(checked)
        finally:
            os.close(state)
    finally:
        os.close(root)


def known(workspace, plan):
    if type(plan) is not str or not _PLAN.match(plan):
        return False
    root = tw._root(workspace)
    try:
        try:
            state = tw._state_dir(root)
        except FileNotFoundError:
            return False
        try:
            os.stat(_journal_name(plan), dir_fd=state, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
        finally:
            os.close(state)
    finally:
        os.close(root)


def describe(workspace, plan):
    """The journal of one transfer (for the undo question)."""
    root = tw._root(workspace)
    try:
        state = tw._state_dir(root)
        try:
            value = _load(state, _journal_name(plan))
            if value["workspace"] != workspace:
                raise ValueError("transfer workspace")
            return value
        finally:
            os.close(state)
    finally:
        os.close(root)


def undo(workspace, plan):
    """Put things back: a move is renamed back, a copy is moved into the task's private state (kept, not deleted).
    Refuses with a conflict when the destination is no longer the entry this transfer created or the old name is taken.
    Returns True when undone."""
    if type(plan) is not str or not _PLAN.match(plan):
        raise TransferDenied("plan")
    root = tw._root(workspace)
    try:
        fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = tw._state_dir(root)
        try:
            name = _journal_name(plan)
            journal = _load(state, name)
            if journal["workspace"] != workspace or journal["status"] != "done":
                raise TransferDenied("changed")
            dst_parent, dst_leaf = _parent(root, journal["to"])
            try:
                try:
                    now = os.stat(dst_leaf, dir_fd=dst_parent, follow_symlinks=False)
                except FileNotFoundError:
                    now = None
                if now is None or tw.identity(now) != journal["entry"]:
                    journal["status"] = "conflict"
                    tw._save(state, name, journal)
                    return False
                try:
                    if journal["operation"] == "move":
                        src_parent, src_leaf = _parent(root, journal["from"])
                        try:
                            rename_noreplace(dst_parent, dst_leaf, src_parent, src_leaf)
                            os.fsync(src_parent)
                        finally:
                            os.close(src_parent)
                    else:
                        rename_noreplace(dst_parent, dst_leaf, state, "transfer-" + plan)
                        os.fsync(state)
                    os.fsync(dst_parent)
                except OSError:
                    # The old name is taken now, a folder on the way is gone, or another filesystem: nothing moved and
                    # the record stays `done`, so undo can be tried again once that is resolved.
                    return False
                journal["status"] = "undone"
                tw._save(state, name, journal)
                return True
            finally:
                os.close(dst_parent)
        finally:
            os.close(state)
    except ExecutionDenied:
        raise
    except (OSError, ValueError):
        raise TransferDenied("workspace") from None
    finally:
        os.close(root)
