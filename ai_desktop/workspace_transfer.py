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


def _tree(parent, leaf, *, read=False):
    """(files, folders, bytes) of a file or folder, refusing links and special files; folders are walked by FD."""
    info, kind = _held(parent, leaf)
    if kind == "file":
        if read and not os.access(leaf, os.R_OK, dir_fd=parent, follow_symlinks=False):
            raise TransferDenied("type")
        return 1, 0, info.st_size
    files = folders = size = 0
    pending = [os.open(leaf, tw.DIR, dir_fd=parent)]
    try:
        while pending:
            fd = pending.pop()
            try:
                folders += 1
                for name in os.listdir(fd):
                    child = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(child.st_mode):
                        pending.append(os.open(name, tw.DIR, dir_fd=fd))
                    elif stat.S_ISREG(child.st_mode):
                        files += 1
                        size += child.st_size
                    else:
                        raise TransferDenied("type")             # a link, device, socket or FIFO inside
                    if files + folders > MAX_FILES:
                        raise TransferDenied("size")
            finally:
                os.close(fd)
    finally:
        for fd in pending:
            os.close(fd)
    return files, folders, size


def _inside(source, target):
    return target == source or target.startswith(source + "/")


def prepare(workspace, operation, source, target, *, clock=time.monotonic):
    """Check everything BEFORE the owner's question; nothing is written. Paths are relative to the workspace."""
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
            if operation == "move":
                if info.st_uid != os.geteuid():
                    raise TransferDenied("type")
                if os.fstat(src_parent).st_dev != target_dir.st_dev:
                    raise TransferDenied("device")                  # one filesystem only; copy works across
                files, folders, size = _tree(src_parent, src_leaf)
            else:
                files, folders, size = _tree(src_parent, src_leaf, read=True)
                free = os.statvfs(dst_parent)
                if size + SPACE_MARGIN > free.f_bavail * free.f_frsize:
                    raise TransferDenied("space")
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


def _copy_tree(src_parent, src_leaf, dst_parent, staging):
    """Recreate the file or folder under `staging` in dst_parent; links and special files refuse the copy."""
    info, kind = _held(src_parent, src_leaf)
    if kind == "file":
        _copy_file(src_parent, src_leaf, dst_parent, staging)
        return
    os.mkdir(staging, stat.S_IMODE(info.st_mode) & 0o755 | 0o700, dir_fd=dst_parent)
    pending = [(os.open(src_leaf, tw.DIR, dir_fd=src_parent), os.open(staging, tw.DIR, dir_fd=dst_parent))]
    try:
        while pending:
            source, target = pending.pop()
            try:
                for name in sorted(os.listdir(source)):
                    child = os.stat(name, dir_fd=source, follow_symlinks=False)
                    if stat.S_ISDIR(child.st_mode):
                        os.mkdir(name, stat.S_IMODE(child.st_mode) & 0o755 | 0o700, dir_fd=target)
                        pending.append((os.open(name, tw.DIR, dir_fd=source), os.open(name, tw.DIR, dir_fd=target)))
                    elif stat.S_ISREG(child.st_mode):
                        _copy_file(source, name, target, name)
                    else:
                        raise TransferDenied("type")
                os.fsync(target)
            finally:
                os.close(source)
                os.close(target)
    finally:
        for source, target in pending:
            os.close(source)
            os.close(target)


def _discard_staging(parent, name):
    """Remove only the hidden staging tree this copy created (never the destination or the source)."""
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent)
        return
    fd = os.open(name, tw.DIR, dir_fd=parent)
    try:
        for child in os.listdir(fd):
            _discard_staging(fd, child)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


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
                journal = {"schema": 1, "plan": plan, "workspace": workspace, "operation": before["operation"],
                           "from": before["from"], "to": before["to"], "kind": before["kind"], "entry": before["entry"],
                           "files": before["files"], "bytes": before["bytes"], "status": "prepared"}
                tw._save(state, _journal_name(plan), journal, first=True)   # intent is durable before anything moves
                try:
                    if before["operation"] == "move":
                        rename_noreplace(src_parent, src_leaf, dst_parent, dst_leaf)
                        os.fsync(src_parent)
                        os.fsync(dst_parent)
                        moved = os.stat(dst_leaf, dir_fd=dst_parent, follow_symlinks=False)
                        journal["entry"] = tw.identity(moved)
                    else:
                        staging = ".ai-desktop-copy-" + plan
                        try:
                            _copy_tree(src_parent, src_leaf, dst_parent, staging)
                            rename_noreplace(dst_parent, staging, dst_parent, dst_leaf)
                            os.fsync(dst_parent)
                        except BaseException:
                            _discard_staging(dst_parent, staging)
                            raise
                        journal["entry"] = tw.identity(os.stat(dst_leaf, dir_fd=dst_parent, follow_symlinks=False))
                    journal["status"] = "done"
                    tw._save(state, _journal_name(plan), journal)
                except (OSError, ValueError, ExecutionDenied) as error:
                    journal["status"] = "failed"
                    tw._save(state, _journal_name(plan), journal)
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
            or _journal_name(value["plan"]) != name or value["operation"] not in ("move", "copy")
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
                    journal["status"] = "conflict"
                    tw._save(state, name, journal)
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
