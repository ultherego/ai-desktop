"""Inspect a downloaded archive and install its plain files into a write scope (S10, stage C).

Nothing in an archive is run. The format is recognised from the content (zip; tar plain or with gzip, bzip2, xz, and
zstd where Python has it), never from the name. Every entry is checked before anything is written: no absolute paths,
no `..`, no links, devices or FIFOs, no duplicate names or a file where a folder is needed, bounded depth, entry count
and unpacked size, and no extreme compression ratio (a decompression bomb). Executable content (programs, libraries,
scripts) is named as such; nothing is called safe. Unpacking counts the real bytes and stops at the declared size.

Installing is a copy from outside the workspace: the files are unpacked into a hidden sibling of the destination
(`.ai-desktop-copy-<plan>`) through opened, no-follow folder handles and published with one no-replace rename, so an
interruption never leaves half an installation under the real name and nothing is overwritten. The journal is the one
of moves and copies (`workspace_transfer`), so `ai-desktop task restore` undoes an installation by moving it into the
task's private state, not by deleting it. Installed files are 0644 and folders 0755: nothing becomes executable.
"""
import fcntl
import gzip
import lzma
import os
import re
import secrets
import stat
import tarfile
import time
import unicodedata
import zipfile
import zlib

try:                                            # Python 3.14 reads zstd; a corrupt stream raises its own error type
    from compression.zstd import ZstdError as _ZstdError
except ImportError:
    class _ZstdError(Exception):
        pass

# What a damaged or unusual archive may raise while it is read: always a refusal, never an unexpected error.
READ_ERRORS = (zipfile.BadZipFile, tarfile.TarError, EOFError, lzma.LZMAError, zlib.error, gzip.BadGzipFile,
               NotImplementedError, _ZstdError)

from . import task_workspace as tw
from .fs_publish import rename_noreplace
from .workspace_executor import ExecutionDenied, _parent
from . import workspace_transfer as transfer

MAX_ENTRIES = 20000
MAX_BYTES = 4 * 1024 ** 3                  # unpacked size of one archive
MAX_DEPTH = 32
MAX_RATIO = 200                            # unpacked/packed above this (and above BOMB_FLOOR) is a decompression bomb
BOMB_FLOOR = 64 * 1024 ** 2
EXECUTABLE = (".exe", ".dll", ".so", ".dylib", ".jar", ".sh", ".bash", ".zsh", ".fish", ".py", ".pl", ".rb", ".php",
              ".ps1", ".bat", ".cmd", ".vbs", ".js", ".mjs", ".lua", ".appimage", ".run", ".bin", ".msi", ".com",
              ".scr", ".apk", ".deb", ".rpm", ".desktop", ".service")
REASONS = ("format", "encrypted", "path", "link", "type", "duplicate", "count", "depth", "size", "bomb", "space",
           "exists", "missing", "workspace", "archive", "changed", "denied", "mount", "full", "top")
_LABEL = re.compile(r"~/\.local/state/ai-desktop/downloads/[0-9]{8}-[0-9]{6}-[a-f0-9]{8}/[^/\x00]{1,120}\Z")
_WRITTEN = re.compile(r"(?:[^\x00]{0,1024}/)?([0-9]{8}-[0-9]{6}-[a-f0-9]{8}/[^/\x00]{1,120})\Z")


def label(value):
    """The quarantine label of an archive however the model wrote it (`~/...`, the full path the user saw, or just
    `<download id>/<name>`); None when it does not end in a download id and a file name. Whatever comes before the id
    is ignored: only this account's own quarantine is ever read."""
    if type(value) is not str or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value):
        return None
    match = _WRITTEN.fullmatch(value)
    if match and len(match.group(1).rsplit("/", 1)[1].encode("utf-8", "surrogatepass")) > 120:
        return None
    candidate = "~/.local/state/ai-desktop/downloads/" + match.group(1) if match else None
    return candidate if candidate and _LABEL.fullmatch(candidate) else None


class ArchiveDenied(ExecutionDenied):
    def __init__(self, reason):
        super().__init__("ARCHIVE: REFUSED " + reason)
        self.reason = reason


# -- the archive -------------------------------------------------------------------------------------------------------

def resolve(label, home):
    """The quarantined archive a label (`~/.local/state/ai-desktop/downloads/<id>/<name>`) names, as an absolute path;
    only files the download step created are accepted."""
    if type(label) is not str or not _LABEL.fullmatch(label) or label.endswith((".part", "/")) \
            or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in label):
        raise ArchiveDenied("archive")
    return home + label[1:]


def _open(path):
    """The archive file, opened without following a link; must be a private regular file of this user."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise ArchiveDenied("archive") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        os.close(fd)
        raise ArchiveDenied("archive")
    return os.fdopen(fd, "rb")


def _digest(path):
    import hashlib
    digest = hashlib.sha256()
    with _open(path) as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def kind(head):
    """The format from the first bytes: zip, tar, tar.gz, tar.bz2, tar.xz, tar.zst; None when unknown."""
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "tar.gz"
    if head[:3] == b"BZh":
        return "tar.bz2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "tar.xz"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "tar.zst"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    return None


def clean_name(raw):
    """One entry's path inside the archive as plain relative segments, or ArchiveDenied("path")."""
    if type(raw) is not str or not raw or "\\" in raw or raw.startswith("/") or "\x00" in raw:
        raise ArchiveDenied("path")
    name = raw[2:] if raw.startswith("./") else raw
    name = name.rstrip("/")
    parts = name.split("/")
    if not name or len(parts) > MAX_DEPTH:
        raise ArchiveDenied("depth" if len(parts) > MAX_DEPTH else "path")
    for part in parts:
        if (part in ("", ".", "..") or len(part.encode("utf-8", "surrogatepass")) > 255
                or part.casefold().startswith((".ai-desktop", ".ai-task-"))
                or any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp") for c in part)):
            raise ArchiveDenied("path")
    return name


MAX_ZIP_DIRECTORY = 32 * 1024 * 1024     # bytes of a zip central directory: 20000 entries with long names fit easily


def _zip_directory(handle):
    """(entries, central directory bytes) from the end record (ZIP64 when present), read BEFORE zipfile parses the
    directory, so an archive that announces too many entries or a huge directory is refused without building it."""
    import struct
    handle.seek(0, os.SEEK_END)
    end = handle.tell()
    handle.seek(max(0, end - 65557))
    tail = handle.read()
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < 22:
        raise ArchiveDenied("format")
    entries, size = struct.unpack("<HI", tail[at + 10:at + 16])
    if entries == 0xFFFF or size == 0xFFFFFFFF:
        locator = tail.rfind(b"PK\x06\x07", 0, at)
        if locator < 0 or at - locator < 20:
            raise ArchiveDenied("format")
        (record,) = struct.unpack("<Q", tail[locator + 8:locator + 16])
        handle.seek(record)
        zip64 = handle.read(56)
        if len(zip64) < 56 or zip64[:4] != b"PK\x06\x06":
            raise ArchiveDenied("format")
        entries, size = struct.unpack("<QQ", zip64[32:48])
    handle.seek(0)
    return entries, size


def _entries(handle, form, packed=0):
    """[(name, is_dir, size, mode, source)] of the archive, each name checked; `source` opens the entry's data. The
    declared size is summed while listing, so a tar bomb is refused before its whole stream is decompressed."""
    found = []
    declared = [0]

    def account(size):
        declared[0] += size
        if declared[0] > MAX_BYTES:
            raise ArchiveDenied("size")
        if declared[0] > BOMB_FLOOR and declared[0] > MAX_RATIO * max(packed, 1):
            raise ArchiveDenied("bomb")
    if form == "zip":
        entries, directory = _zip_directory(handle)
        if entries > MAX_ENTRIES:
            raise ArchiveDenied("count")
        if directory > MAX_ZIP_DIRECTORY:
            raise ArchiveDenied("size")
        try:
            archive = zipfile.ZipFile(handle)
        except (zipfile.BadZipFile, OSError, ValueError):
            raise ArchiveDenied("format") from None
        for info in archive.infolist():
            if info.flag_bits & 0x1:
                raise ArchiveDenied("encrypted")
            # The Unix mode is meaningful only from a Unix system; type bits 0 mean a plain entry (Python's writestr).
            unix = info.external_attr >> 16 if info.create_system == 3 else 0
            if stat.S_IFMT(unix) and (stat.S_ISLNK(unix) or not (stat.S_ISREG(unix) or stat.S_ISDIR(unix))):
                raise ArchiveDenied("link" if stat.S_ISLNK(unix) else "type")
            directory = info.is_dir()
            account(0 if directory else info.file_size)
            found.append((clean_name(info.filename), directory, 0 if directory else info.file_size,
                          stat.S_IMODE(unix), (lambda info=info: archive.open(info))))
        return found, archive
    try:
        archive = tarfile.open(fileobj=handle, mode="r:*")
    except (OSError, ValueError) + READ_ERRORS:
        raise ArchiveDenied("format") from None
    try:
        for member in archive:
            if member.issym() or member.islnk():
                raise ArchiveDenied("link")
            if not (member.isfile() or member.isdir()):
                raise ArchiveDenied("type")
            if member.name in ("", ".", "./"):
                continue
            account(0 if member.isdir() else member.size)
            found.append((clean_name(member.name), member.isdir(), 0 if member.isdir() else member.size,
                          member.mode & 0o7777, (lambda member=member: archive.extractfile(member))))
            if len(found) > MAX_ENTRIES:
                raise ArchiveDenied("count")
    except (OSError,) + READ_ERRORS:
        raise ArchiveDenied("format") from None
    return found, archive


def _checked(entries, packed):
    """The whole listing at once: no duplicates, no file where a folder is needed, bounded count, size and ratio."""
    if len(entries) > MAX_ENTRIES:
        raise ArchiveDenied("count")
    files, folders, explicit = {}, set(), set()
    for name, directory, size, _, _ in entries:
        # A folder entry may come after its own contents (many tools write it that way); only a repeated folder entry,
        # a name used twice for files, or a file where a folder is needed is a duplicate.
        if name in files or (directory and name in explicit) or (not directory and name in folders):
            raise ArchiveDenied("duplicate")
        parents = name.split("/")[:-1]
        for index in range(1, len(parents) + 1):
            prefix = "/".join(parents[:index])
            if prefix in files:
                raise ArchiveDenied("duplicate")
            folders.add(prefix)
        if directory:
            folders.add(name)
            explicit.add(name)
        else:
            if any(other.startswith(name + "/") for other in folders):
                raise ArchiveDenied("duplicate")
            files[name] = size
    total = sum(files.values())
    if total > MAX_BYTES:
        raise ArchiveDenied("size")
    if total > BOMB_FLOOR and total > MAX_RATIO * max(packed, 1):
        raise ArchiveDenied("bomb")
    return files, folders, total


PROGRAM_MAGIC = (b"#!", b"\x7fELF", b"MZ")


def _executable(name, mode, source):
    """A program or script: a known extension, or an executable bit on a file that starts like one (#!, ELF, MZ).
    Archives made on some systems mark every file executable, so the bit alone does not make an image a program."""
    if name.casefold().endswith(EXECUTABLE):
        return True
    if not mode & 0o111:
        return False
    try:
        with source() as data:
            return data.read(4).startswith(PROGRAM_MAGIC)
    except (OSError, ValueError) + READ_ERRORS:
        return True                                       # unreadable: say so rather than call it a plain file


def inspect(path):
    """What the archive holds, without unpacking it: {"format", "files", "folders", "bytes", "top", "executables",
    "examples"}; ArchiveDenied(reason) for anything the installer would refuse."""
    with _open(path) as handle:
        packed = os.fstat(handle.fileno()).st_size
        form = kind(handle.read(512))
        handle.seek(0)
        if form is None:
            raise ArchiveDenied("format")
        if form == "tar.zst" and not hasattr(tarfile.TarFile, "zstopen"):
            raise ArchiveDenied("format")                   # this Python cannot read zstd (it can from 3.14)
        entries, archive = _entries(handle, form, packed)
        try:
            files, folders, total = _checked(entries, packed)
            risky = sorted(name for name, directory, _, mode, source in entries
                           if not directory and _executable(name, mode, source))
        finally:
            archive.close()
    if not files and not folders:
        raise ArchiveDenied("format")
    top = sorted({name.split("/")[0] for name in list(files) + list(folders)})
    names = sorted(set(files) | folders)
    return {"format": form, "files": len(files), "folders": len(folders), "bytes": total, "top": top[:32],
            "top_count": len(top), "single": len(top) == 1 and top[0] in folders,
            "executables": len(risky), "examples": risky[:8], "names": names}


def _unpack(path, parent_fd, staging, expected):
    """Unpack into the new private folder `staging` in parent_fd through no-follow handles, one folder handle open at a
    time; real bytes are counted against each declared size. Returns the staging folder's identity from creation."""
    os.mkdir(staging, 0o700, dir_fd=parent_fd)
    made = tw.identity(os.stat(staging, dir_fd=parent_fd, follow_symlinks=False))
    root = os.open(staging, tw.DIR, dir_fd=parent_fd)
    current = ["", None]

    def folder(relative):
        """The folder `relative` below the staging root, created when missing; the previous one is closed."""
        if current[0] == relative and current[1] is not None:
            return current[1]
        if current[1] is not None:
            os.close(current[1])
            current[1] = None
        fd = os.dup(root)
        try:
            for part in [p for p in relative.split("/") if p]:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, tw.DIR, dir_fd=fd)
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        current[0], current[1] = relative, fd
        return fd
    try:
        if tw.identity(os.fstat(root)) != made:
            raise ArchiveDenied("changed")
        with _open(path) as handle:
            form = kind(handle.read(512))
            handle.seek(0)
            packed = os.fstat(handle.fileno()).st_size
            entries, archive = _entries(handle, form, packed)
            try:
                files, _, total = _checked(entries, packed)
                if total != expected["bytes"] or len(files) != expected["files"]:
                    raise ArchiveDenied("changed")
                for name, directory, size, _, source in entries:
                    if directory:
                        folder(name)
                        continue
                    head, _, leaf = name.rpartition("/")
                    holder = folder(head)
                    out = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644,
                                  dir_fd=holder)
                    try:
                        count = 0
                        with source() as data:
                            while True:
                                block = data.read(1024 * 1024)
                                if not block:
                                    break
                                count += len(block)
                                if count > size:
                                    raise ArchiveDenied("size")       # more than the entry declared
                                view = memoryview(block)
                                while view:
                                    view = view[os.write(out, view):]
                        if count != size:
                            raise ArchiveDenied("format")
                        os.fchmod(out, 0o644)
                        os.fsync(out)
                    finally:
                        os.close(out)
            finally:
                archive.close()
        os.fchmod(root, 0o755)
    except READ_ERRORS:
        raise ArchiveDenied("format") from None
    finally:
        if current[1] is not None:
            os.close(current[1])
        os.close(root)
    return made


# -- the plan and the installation -------------------------------------------------------------------------------------

def prepare(workspace, label, archive_path, to, *, clock=time.monotonic, policy=None, index=None):
    """Check the archive and the destination BEFORE the owner's question; nothing is written. With one top-level
    folder the archive creates `to/<that folder>`; otherwise it creates the new folder `to` itself. With `policy`,
    every path the archive would create is checked against the write rule `index`, deny rules and the default
    protection."""
    facts = inspect(archive_path)
    if to != ".":
        to = transfer.path(to)
    single = facts["single"]
    if not single and to == ".":
        raise ArchiveDenied("top")                          # several top entries need a new folder of their own
    target = (facts["top"][0] if to == "." else to + "/" + facts["top"][0]) if single else to
    target = transfer.path(target)
    if policy is not None:
        inside = [name.split("/", 1)[1] if single and "/" in name else ("" if single else name)
                  for name in facts["names"]]
        for relative in {""} | set(inside):
            destination = workspace + "/" + target + ("/" + relative if relative else "")
            if not policy.can_write(destination, index=index) or policy.excludes(destination):
                raise ArchiveDenied("denied")
    root = tw._root(workspace)
    try:
        try:
            parent, leaf = _parent(root, target)
        except FileNotFoundError:
            raise ArchiveDenied("missing") from None
        try:
            try:
                os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                raise ArchiveDenied("exists")                       # nothing is overwritten
            except FileNotFoundError:
                pass
            holder = os.fstat(parent)
            if holder.st_uid != os.geteuid():
                raise ArchiveDenied("workspace")
            if holder.st_dev != os.fstat(root).st_dev:
                raise ArchiveDenied("mount")                        # undo keeps the installation in the workspace state
            free = os.statvfs(parent)
            if facts["bytes"] + transfer.SPACE_MARGIN > free.f_bavail * free.f_frsize:
                raise ArchiveDenied("space")
            archive = os.stat(archive_path, follow_symlinks=False)
            inspection = {key: value for key, value in facts.items() if key != "names"}
            data = {"workspace": workspace, "root": tw.identity(os.fstat(root)), "operation": "install",
                    "from": label, "to": target, "kind": "folder", "entry": tw.identity(archive),
                    "files": facts["files"], "folders": facts["folders"], "bytes": facts["bytes"],
                    "target_parent": tw.identity(holder), "plan": secrets.token_hex(16),
                    "archive": archive_path, "single": single, "inspection": inspection,
                    "sha256": _digest(archive_path)}
            return transfer.TransferPlan(data, clock=clock)
        finally:
            os.close(parent)
    except ExecutionDenied:
        raise
    except (OSError, ValueError):
        raise ArchiveDenied("workspace") from None
    finally:
        os.close(root)


def execute(prepared, authorization):
    """Consume the owner's yes and install exactly the shown archive; returns the plan ID for undo."""
    if type(prepared) is not transfer.TransferPlan:
        raise ArchiveDenied("changed")
    before = prepared.consume(authorization)
    if before.get("operation") != "install":
        raise ArchiveDenied("changed")
    workspace, plan = before["workspace"], before["plan"]
    root = tw._root(workspace)
    try:
        fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if tw.identity(os.fstat(root)) != before["root"]:
            raise ArchiveDenied("changed")
        if _digest(before["archive"]) != before["sha256"]:
            raise ArchiveDenied("changed")                          # the exact bytes the owner saw described
        parent, leaf = _parent(root, before["to"])
        try:
            if tw.identity(os.fstat(parent)) != before["target_parent"]:
                raise ArchiveDenied("changed")
            state = tw._state_dir(root, create=True)
            try:
                try:
                    transfer._journal_room(state)
                except transfer.TransferDenied:
                    raise ArchiveDenied("full") from None
                name = transfer._journal_name(plan)
                journal = {"schema": 1, "plan": plan, "workspace": workspace, "operation": "install",
                           "from": before["from"], "to": before["to"], "kind": "folder", "entry": before["entry"],
                           "files": before["files"], "bytes": before["bytes"], "status": "prepared"}
                tw._save(state, name, journal, first=True)
                staging = ".ai-desktop-copy-" + plan
                made, published = None, False
                try:
                    try:
                        made = _unpack(before["archive"], parent, staging, before["inspection"])
                        if before["single"]:
                            # The archive's one top folder becomes the destination. The private staging folder (0700,
                            # checked by identity) is then empty and removed.
                            inner = os.open(staging, tw.DIR, dir_fd=parent)
                            try:
                                if tw.identity(os.fstat(inner)) != made:
                                    raise ArchiveDenied("changed")
                                top = before["inspection"]["top"][0]
                                entry = tw.identity(os.stat(top, dir_fd=inner, follow_symlinks=False))
                                rename_noreplace(inner, top, parent, leaf)
                                published = tw.identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) == entry
                            finally:
                                os.close(inner)
                            if not published:
                                raise ArchiveDenied("changed")
                            transfer._discard_staging(parent, staging, made)
                        else:
                            published = transfer.publish(parent, staging, leaf, made)
                            if not published:
                                raise ArchiveDenied("changed")
                        os.fsync(parent)
                    except BaseException:
                        if not published:
                            transfer._discard_staging(parent, staging, made)
                        raise
                    journal["entry"] = tw.identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False))
                    transfer.finish(state, name, journal, True)
                except BaseException as error:
                    transfer.finish(state, name, journal, published, error)
                    if isinstance(error, OSError) and getattr(error, "errno", None) == 17:
                        raise ArchiveDenied("exists") from None
                    if isinstance(error, OSError) and getattr(error, "errno", None) == 28:
                        raise ArchiveDenied("space") from None
                    if isinstance(error, READ_ERRORS):
                        raise ArchiveDenied("format") from None
                    raise
            finally:
                os.close(state)
        finally:
            os.close(parent)
    except ExecutionDenied:
        raise
    except (OSError, ValueError):
        raise ArchiveDenied("workspace") from None
    finally:
        os.close(root)
    return plan
