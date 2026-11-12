"""Fixed root-only session lifecycle. Never receives reports or credentials.

sudo authenticates the caller; PID1 owns a bounded helper with ExecStopPost.
Every open terminal of a user has its own session: its own root-owned claim (named by a random nonce), created
before any activation, and its own helper unit. The first session starts the user's analysis socket, the last one
stops it; these decisions are made under one root-owned lock per user, and claims whose helper unit is gone are
pruned. No user-supplied command, path, property, unit name or UID is accepted by open.
"""
from contextlib import contextmanager
import fcntl
import time
import os
from pathlib import Path
import pwd
import re
import secrets
import select
import signal
import stat
import subprocess
import sys

from .analysis_contract import AnalysisDenied, HOLD_SECONDS
from .analysis_sandbox import INSTALL, cloud_transport_ready, trusted_file
from .analysis_service import endpoint, socket_path, UID
from .process_io import bounded_process
from .host_accounts import is_regular_uid

BOOTSTRAP = INSTALL / "analysis-session.py"
STATE = Path("/run/ai-desktop-sessions")
ENV = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "TERM": "dumb"}
NONCE = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
READY = b"SESSION1 READY\n"
STOPPED = b"SESSION1 STOPPED\n"
REFUSED = b"SESSION1 REFUSED "
PROPERTIES = "LoadState,ActiveState,MainPID,FragmentPath,DropInPaths,NeedDaemonReload,UnitFileState"


def desktop_uid(value):
    if type(value) is not str or not UID.fullmatch(value):
        raise AnalysisDenied("identity")
    uid = int(value)
    socket_path(uid)
    user = pwd.getpwuid(uid)
    if (not is_regular_uid(uid) or user.pw_name == "ai-desktop"
            or user.pw_shell in ("/usr/bin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false")):
        raise AnalysisDenied("identity")
    return uid


def units(uid):
    socket_path(uid)
    return ["ai-desktop-analysis@" + str(uid) + suffix for suffix in (".socket", ".service")]


MAX_SESSIONS = 64                  # open terminals of one user with a session at the same time
_OWNER = (0, 0)                    # every claim, lock and claims directory belongs to root:root
LIVE = ("active", "activating", "deactivating", "reloading")


def manager_unit(uid, nonce):
    """The helper unit of one terminal's session; its name comes only from the UID and our own random nonce."""
    socket_path(uid)
    if type(nonce) is not str or not NONCE.fullmatch(nonce):
        raise AnalysisDenied("input")
    return "ai-desktop-session-%d-%s.service" % (uid, nonce[:16])


def command(*args):
    code, out, _ = bounded_process(list(args), env=ENV, timeout=10, limit=16384)
    if code != 0:
        raise AnalysisDenied("endpoint")
    return out


def properties(unit):
    code, raw, _ = bounded_process(["/usr/bin/systemctl", "show", unit, "--property=" + PROPERTIES],
                                  env=ENV, timeout=10, limit=16384)
    result = {}
    for line in raw.decode("utf-8", "strict").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise AnalysisDenied("endpoint")
        result[key] = value
    if code != 0 and not (code == 1 and result.get("LoadState") == "not-found"
                          and result.get("ActiveState") == "inactive"):
        raise AnalysisDenied("endpoint")
    return result


def unit_policy(uid, *, inactive=False):
    """Read-only metadata, also used by the unprivileged status command."""
    cloud_transport_ready()
    trusted_file(BOOTSTRAP)
    for unit in units(uid):
        suffix = unit.rsplit(".", 1)[1]
        fragment = "/usr/lib/systemd/system/ai-desktop-analysis@." + suffix
        trusted_file(fragment)
        values = properties(unit)
        if (values.get("LoadState") != "loaded" or values.get("FragmentPath") != fragment
                or values.get("DropInPaths") != "" or values.get("NeedDaemonReload") != "no"
                or values.get("UnitFileState") != "static"):
            raise AnalysisDenied("runtime")
        if inactive and (values.get("ActiveState") not in ("inactive", "failed")
                         or (suffix == "service" and values.get("MainPID") != "0")):
            raise AnalysisDenied("busy")
    if inactive and os.path.lexists(socket_path(uid)):
        raise AnalysisDenied("busy")


def state_directory():
    for parent in (Path("/"), Path("/run")):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise AnalysisDenied("identity")
    STATE.mkdir(mode=0o700, exist_ok=True)
    info = STATE.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise AnalysisDenied("identity")


def legacy_claim(uid):
    """The single claim of the previous one-session layout (up to candidate 131); while that session runs it owns
    the socket. An upgrade can happen while it runs: its own cleanup then comes from this version."""
    socket_path(uid)
    return STATE / str(uid)


def legacy_unit(uid):
    socket_path(uid)
    return "ai-desktop-session-%d.service" % uid


def read_legacy_claim(uid):
    """The nonce in the previous layout's claim, or None; the same ownership checks as every claim."""
    path = legacy_claim(uid)
    if not os.path.lexists(path):
        return None
    return read_claim(path, name=None)


def claims_directory(uid):
    socket_path(uid)
    path = STATE / ("%d.d" % uid)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != _OWNER
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise AnalysisDenied("identity")
    return path


def claim_path(uid, nonce):
    if type(nonce) is not str or not NONCE.fullmatch(nonce):
        raise AnalysisDenied("input")
    return claims_directory(uid) / nonce


@contextmanager
def session_lock(uid, *, seconds=30):
    """One root-owned lock per user orders the start of the first session and the stop after the last one."""
    socket_path(uid)
    fd = os.open(STATE / ("%d.lock" % uid), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != _OWNER
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise AnalysisDenied("identity")
        deadline = time.monotonic() + seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise AnalysisDenied("busy") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def read_claim(path, *, name=True):
    """The nonce in a root-owned claim. A per-terminal claim is named by its nonce; the legacy one is not."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid) != _OWNER
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise AnalysisDenied("identity")
        nonce = os.read(fd, 33).decode("ascii", "strict")
        if not NONCE.fullmatch(nonce) or (name and nonce != path.name):
            raise AnalysisDenied("identity")
        return nonce
    finally:
        os.close(fd)


def claims(uid):
    """The nonces of this user's sessions; anything else in the directory is refused."""
    found = []
    for name in sorted(os.listdir(claims_directory(uid))):
        if not NONCE.fullmatch(name):
            raise AnalysisDenied("identity")
        found.append(read_claim(claims_directory(uid) / name))
    if len(found) > MAX_SESSIONS:
        raise AnalysisDenied("identity")
    return found


def create_claim(uid, nonce):
    fd = os.open(claim_path(uid, nonce), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if os.write(fd, nonce.encode("ascii")) != 32:
            raise AnalysisDenied("cleanup")
    finally:
        os.close(fd)


def prune(uid):
    """Forget claims whose helper unit no longer runs (its cleanup was interrupted), the previous layout's included;
    call under session_lock."""
    for nonce in claims(uid):
        if properties(manager_unit(uid, nonce)).get("ActiveState") not in LIVE:
            claim_path(uid, nonce).unlink()
    if read_legacy_claim(uid) is not None and properties(legacy_unit(uid)).get("ActiveState") not in LIVE:
        legacy_claim(uid).unlink()


def cleaned(uid):
    if os.path.lexists(socket_path(uid)):
        raise AnalysisDenied("cleanup")
    for unit in units(uid):
        values = properties(unit)
        if (values.get("ActiveState") not in ("inactive", "failed")
                or (unit.endswith(".service") and values.get("MainPID") != "0")):
            raise AnalysisDenied("cleanup")


def stop_unclaimed(uid):
    """Call under session_lock when no session claims the user's units: they must not run. A cleanup whose stop failed
    or timed out leaves them active with no claim; without this every later open would be refused as busy and
    `ai-desktop stop` could not end them. Stops them when any runs, then refuses unless they really ended."""
    if claims(uid) or os.path.lexists(legacy_claim(uid)):
        raise AnalysisDenied("busy")
    for unit in units(uid):
        values = properties(unit)
        if values.get("ActiveState") not in ("inactive", "failed") or values.get("MainPID", "0") != "0":
            command("/usr/bin/systemctl", "stop", *units(uid))
            break
    cleaned(uid)


def cleanup(uid, nonce):
    """ExecStopPost of one session's helper: forget its claim; after the last session, stop the user's units."""
    state_directory()
    with session_lock(uid):
        path = claim_path(uid, nonce)
        if os.path.lexists(path):
            read_claim(path)
            path.unlink()
        if read_legacy_claim(uid) == nonce:
            legacy_claim(uid).unlink()                 # a session of the previous layout ending after an upgrade
        prune(uid)
        if not claims(uid) and not os.path.lexists(legacy_claim(uid)):
            try:
                command("/usr/bin/systemctl", "stop", *units(uid))
            except AnalysisDenied:
                stop_unclaimed(uid)            # once more; if it still fails, the next open or stop recovers it
            cleaned(uid)
    os.write(1, STOPPED)


def hold(uid, nonce):
    state_directory()
    with session_lock(uid):
        prune(uid)
        if os.path.lexists(legacy_claim(uid)):
            raise AnalysisDenied("busy")           # a running session of the previous layout still owns the socket
        if len(claims(uid)) >= MAX_SESSIONS:
            raise AnalysisDenied("busy")           # rechecked under the lock: two opens may pass the first look
        first = not claims(uid)
        if first:
            # No session claims the units: anything still running is left over from a cleanup that could not stop
            # it. Stop it first instead of refusing this and every later terminal.
            unit_policy(uid)
            stop_unclaimed(uid)
        unit_policy(uid, inactive=first)
        create_claim(uid, nonce)  # Before any activation, including a partial start.
        if first:
            # Inactive units may be garbage-collected between show and reset-failed;
            # reset-failed then refuses an unloaded unit. Starting is sufficient and
            # must not silently reset the installed activation rate limits.
            command("/usr/bin/systemctl", "start", units(uid)[0])
        endpoint(uid)
    os.write(1, READY)
    # The UI owns the write end. EOF, including SIGKILL of the UI, releases the
    # session. PID1's RuntimeMaxSec and ExecStopPost also cover this helper dying.
    if not select.select([0], [], [], HOLD_SECONDS)[0]:
        raise AnalysisDenied("timeout")
    if os.read(0, 1) != b"":
        raise AnalysisDenied("input")


def session_command(uid, nonce):
    stop = "/usr/bin/python3 -I -B " + str(BOOTSTRAP) + " cleanup " + str(uid) + " " + nonce
    return ["/usr/bin/systemd-run", "--quiet", "--wait", "--collect", "--pipe", "--service-type=exec",
            "--unit=" + manager_unit(uid, nonce), "--property=RuntimeMaxSec=%d" % (HOLD_SECONDS + 20),
            "--property=TimeoutStopSec=60",
            "--property=ExecStopPost=" + stop, "--property=KillMode=control-group",
            "--property=User=root", "--property=Group=root", "--property=UMask=0077",
            "--property=NoNewPrivileges=yes", "--property=CapabilityBoundingSet=",
            "--property=AmbientCapabilities=", "--property=ProtectSystem=strict",
            "--property=ProtectHome=yes", "--property=PrivateTmp=yes", "--property=PrivateDevices=yes",
            "--property=RestrictAddressFamilies=AF_UNIX", "--property=RestrictNamespaces=yes",
            "--property=RestrictSUIDSGID=yes", "--property=MemoryMax=96M", "--property=TasksMax=16",
            "--property=ReadWritePaths=" + str(STATE),
            "/usr/bin/python3", "-I", "-B", str(BOOTSTRAP), "hold", str(uid), nonce]


def open_session(uid):
    unit_policy(uid)
    state_directory()
    with session_lock(uid):
        prune(uid)
        if len(claims(uid)) >= MAX_SESSIONS:
            raise AnalysisDenied("busy")
    nonce = secrets.token_hex(16)
    try:
        # Its own unit name: PID1 never mixes this session with another terminal's.
        result = subprocess.run(session_command(uid, nonce), env=ENV, check=False,
                                close_fds=True, timeout=HOLD_SECONDS + 60, stderr=subprocess.DEVNULL)
        return 0 if result.returncode == 0 else 2
    finally:
        # ExecStopPost owns normal cleanup; this path only handles an interrupted launcher with our own claim.
        if os.path.lexists(claim_path(uid, nonce)):
            command("/usr/bin/systemctl", "stop", manager_unit(uid, nonce))
            if os.path.lexists(claim_path(uid, nonce)):
                raise AnalysisDenied("cleanup")


def stop_session(uid):
    """`ai-desktop stop`: end every session of this user (each helper's own cleanup runs), then check the units."""
    state_directory()
    with session_lock(uid):
        prune(uid)
        running = [manager_unit(uid, nonce) for nonce in claims(uid)]
        if read_legacy_claim(uid) is not None:
            running.append(legacy_unit(uid))        # a session of the previous layout: its cleanup is ours now
    for unit in running:
        command("/usr/bin/systemctl", "stop", unit)
    with session_lock(uid):
        prune(uid)
        if claims(uid) or os.path.lexists(legacy_claim(uid)):
            raise AnalysisDenied("busy")           # a terminal opened a new session meanwhile
        stop_unclaimed(uid)                        # also units left running by a failed cleanup, with no claim
    os.write(1, STOPPED)


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    try:
        if os.getuid() != 0 or os.geteuid() != 0:
            raise AnalysisDenied("identity")
        if args in (["open"], ["stop"], ["login"], ["login-verbose"]):
            uid = desktop_uid(os.environ.get("SUDO_UID"))
            if args in (["login"], ["login-verbose"]):
                from .analysis_admin import main as login
                return login(allow=True, details=True) if args == ["login-verbose"] else login(allow=True)
            if args == ["open"]:
                # Keep sudo's controlling TTY/timestamp. The UI handles Ctrl+C
                # and closes its control pipe; this wrapper must finish cleanup.
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                return open_session(uid)
            stop_session(uid)
        elif (len(args) == 3 and args[0] in ("hold", "cleanup")
              and NONCE.fullmatch(args[2])):
            uid = desktop_uid(args[1])
            (hold if args[0] == "hold" else cleanup)(uid, args[2])
        else:
            raise AnalysisDenied("input")
        return 0
    except (AnalysisDenied, OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        reason = exc.reason if isinstance(exc, AnalysisDenied) else "cancelled" if isinstance(exc, KeyboardInterrupt) else "runtime"
        try:
            os.write(1, REFUSED + reason.encode("ascii") + b"\n")
        except OSError:
            pass
        return 2
