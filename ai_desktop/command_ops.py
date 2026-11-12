"""Operations the assistant may run for you - only the commands YOU list in permissions.toml, only after YOU say yes.

The owner's `[commands] allow` is a list of argv *templates*. A model proposal is an argv list (never a shell string);
it is matched token by token against the templates, resolved to an absolute, root-owned program, shown to the user
verbatim, and run only after a fresh consent for that exact command. It runs without a shell, with a clean environment,
no stdin, a time limit and an output limit, inside a read-only, network-less `bwrap` view of the system. The result is
sanitised, bounded data that goes back to the model; it is never authority for anything.

Nothing about a particular program is built in: the only code-level refusals are interpreters, shells, privilege
escalation, network clients and obvious writers as the FIRST word of a template, because those turn "run this command"
into "run anything".
"""
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time
import unicodedata

# No count limit on templates (owner's request): the policy file size (permissions.MAX_POLICY_BYTES) bounds them.
MAX_TOKENS = 10
MAX_TOKEN_BYTES = 100
MAX_TEMPLATE_BYTES = 120
SEARCH_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
STDERR_BYTES = 2048
FORBIDDEN_PROGRAMS = frozenset((
    "sh", "bash", "dash", "zsh", "fish", "ksh", "csh", "tcsh", "ash", "busybox", "env", "xargs", "find", "nice", "nohup",
    "timeout", "watch", "script", "flock", "stdbuf", "setsid", "chroot", "unshare", "nsenter", "bwrap", "strace", "ltrace",
    "gdb", "python", "python3", "perl", "ruby", "node", "nodejs", "php", "lua", "tclsh", "awk", "gawk", "mawk", "sed",
    "sudo", "su", "doas", "pkexec", "run0", "machinectl", "ssh", "scp", "sftp", "nc", "ncat", "netcat", "socat", "telnet",
    "curl", "wget", "ftp", "rsync", "rm", "rmdir", "mv", "cp", "dd", "tee", "chmod", "chown", "chgrp", "ln", "truncate",
    "shred", "mount", "umount", "kill", "pkill", "killall", "reboot", "poweroff", "shutdown", "halt", "install",
    "systemd-run", "systemd-nspawn", "systemd-cat", "busctl", "loginctl", "dbus-send", "gdbus", "runuser", "login",
    "ionice", "taskset", "chrt", "setpriv", "capsh", "time", "git", "less", "more", "man", "tar", "vi", "vim", "nano", "ed",
    "ex", "view", "emacs", "pager", "ld", "ld.so", "ld-linux", "exec", "eval", "command", "builtin", "source",
))
_PROGRAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
_LITERAL = re.compile(r"[A-Za-z0-9._+=:,@/%-]{1,100}\Z")
_PLACEHOLDERS = {
    # A unit name with or without its type, as systemctl takes it ("ufw" means "ufw.service"); never an option.
    "<unit>": re.compile(r"[A-Za-z0-9:_@][A-Za-z0-9:_.@-]{0,99}"
                         r"(?:\.(?:service|socket|timer|target|mount|path|slice|scope|device|swap))?\Z"),
    "<int>": re.compile(r"(?:[1-9][0-9]{0,4}|0)\Z"),
    "<word>": re.compile(r"[A-Za-z0-9._:][A-Za-z0-9._:-]{0,63}\Z"),
}
# Tools whose first non-option word is a SUBCOMMAND: a placeholder must never stand there, or the model could pick
# `restart` for `systemctl --user <word> x`.
SUBCOMMAND_TOOLS = frozenset(("systemctl", "docker", "nmcli", "btrfs", "snapper", "bluetoothctl", "resolvectl",
                              "powerprofilesctl", "pactl", "wpctl", "networkctl", "timedatectl", "hostnamectl",
                              "localectl", "journalctl"))
# Options of those tools that never take a value. Short forms mean different things across tools (`-f` forces in
# systemctl and names fields in nmcli), so only the unambiguous ones are listed.
_STANDALONE_OPTIONS = frozenset(("--user", "--system", "--global", "--runtime", "--no-pager", "--no-legend",
                                 "--no-ask-password", "--no-block", "--no-reload", "--no-wall", "--now", "--all", "--full",
                                 "--quiet", "-q", "--force", "--failed", "--plain"))
# These CLIs dispatch through several levels and may add plugins or accept command abbreviations. For a template
# with placeholders, require a complete, known command path before the first variable argument. Unsupported commands
# can still be configured verbatim. A pinned verb does not make it read-only: the owner chooses allow or change.
_COMMAND_PATHS = {
    "docker": {
        "": "ps stats logs top inspect images history events pull push start stop restart kill pause unpause rm rmi rename wait",
        "container": "ls stats logs top inspect start stop restart kill pause unpause rm rename wait",
        "image": "ls inspect history pull push rm tag",
        "volume": "ls inspect create rm",
        "network": "ls inspect connect disconnect create rm",
        "compose": "ps images logs top start stop restart pause unpause",
    },
    "btrfs": {
        "filesystem": "show usage df du sync resize label defragment",
        "subvolume": "show list get-default set-default snapshot create delete find-new sync",
        "device": "stats usage scan ready add remove",
        "balance": "status start pause resume cancel",
        "scrub": "status start resume cancel",
        "property": "get list set",
        "qgroup": "show limit create destroy assign remove",
    },
    "nmcli": {
        "general": "status hostname permissions logging reload",
        "connection": "show up down add modify clone delete reload load import export",
        "device": "status show set connect disconnect reapply modify delete",
        "device wifi": "list connect rescan hotspot",
    },
}
_GLOBAL_SWITCHES = {"docker": {"--debug", "-D", "--tls", "--tlsverify"},
                    "btrfs": {"--verbose", "-v", "--quiet", "-q", "--raw", "--human-readable"},
                    "nmcli": {"-t", "--terse", "-p", "--pretty", "-a", "--ask", "-s", "--show-secrets", "-o", "--overview"}}
_GLOBAL_VALUES = {"docker": {"-c", "--context", "-H", "--host", "--config", "--log-level", "--tlskey", "--tlscert", "--tlscacert"},
                  "btrfs": {"--format", "--log"},
                  "nmcli": {"-f", "--fields", "-g", "--get-values", "-m", "--mode", "-w", "--wait",
                            "-c", "--colors", "-e", "--escape"}}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")


def _require_command_path(program, arguments):
    """Match an exact command path, accounting for global options before it."""
    words = iter(arguments)
    for word in words:
        if word in _GLOBAL_SWITCHES[program]:
            continue
        option, equals, value = word.partition("=")
        if option in _GLOBAL_VALUES[program]:
            if not equals:
                value = next(words, "")
            if not value or value.startswith("-") or value in _PLACEHOLDERS:
                raise ValueError("command-template")
            continue
        break
    else:
        raise ValueError("command-template")
    group = ""
    paths = _COMMAND_PATHS[program]
    while word not in paths.get(group, "").split():
        group = (group + " " + word).lstrip()
        if group not in paths:
            raise ValueError("command-template")
        word = next(words, "")
        if not word:
            raise ValueError("command-template")


# A read template may end with "...": the fixed words, then up to the argv limit of further words, each a plain word.
# Paths among them are checked against the files policy by the caller (permissions.Policy.allows_command).
ELLIPSIS = "..."
_EXTRA = re.compile(r"[A-Za-z0-9._:=,@%+/-]{1,100}\Z")
# Verbs that change state. A read template that leaves its arguments open with "..." must not start with one: "docker
# run --rm ..." in allow would run any container without asking. Such a template belongs in change.
STATE_VERBS = frozenset(("run", "exec", "start", "stop", "restart", "kill", "rm", "rmi", "build", "pull", "push", "create",
                         "delete", "remove", "install", "uninstall", "enable", "disable", "mask", "unmask", "set", "apply",
                         "edit", "cp", "commit", "prune", "update", "upgrade", "mount", "umount", "load", "import", "tag",
                         "reload", "reset", "connect", "disconnect", "up", "down", "add", "del", "modify", "write"))


def opens_a_change(tokens):
    """True for an open read template whose fixed words include a state-changing verb."""
    return tokens[-1] == ELLIPSIS and any(token in STATE_VERBS for token in tokens[1:-1])
_GROUP = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")


def valid_group(name):
    return type(name) is str and bool(_GROUP.fullmatch(name))


def _require_literal_verb(tokens):
    """No placeholder may stand where the verb could be. After an option not known to stand alone, the next word may be
    its value (`systemctl -H host <word>`), so the verb is looked for one word further on."""
    maybe_value = False
    for token in tokens[1:]:
        if token in _PLACEHOLDERS or token == ELLIPSIS:
            raise ValueError("command-template")
        if token.startswith("-"):
            maybe_value = "=" not in token and token not in _STANDALONE_OPTIONS
        elif maybe_value:
            maybe_value = False
        else:
            return


def parse_template(text, *, strict=False):
    """A validated template as a tuple of tokens, or ValueError. Called while loading the owner's policy."""
    if type(text) is not str or not text.strip() or len(text.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise ValueError("command-template")
    tokens = tuple(text.split())
    if not 1 <= len(tokens) <= MAX_TOKENS or " ".join(tokens) != text:
        raise ValueError("command-template")
    program = tokens[0]
    if not _PROGRAM.fullmatch(program) or program.casefold() in FORBIDDEN_PROGRAMS:
        raise ValueError("command-template")
    if ELLIPSIS in tokens[:-1] or (tokens[-1] == ELLIPSIS and len(tokens) < 2):
        raise ValueError("command-template")      # only at the end, never alone
    for token in tokens[1:]:
        if token not in _PLACEHOLDERS and not _LITERAL.fullmatch(token):
            raise ValueError("command-template")
    if len(tokens) > 1 and tokens[1] in _PLACEHOLDERS:
        raise ValueError("command-template")      # the first argument (usually the verb) is always the owner's literal
    if program in _COMMAND_PATHS and any(token in _PLACEHOLDERS for token in tokens):
        _require_command_path(program, tokens[1:])
    elif strict or (program in SUBCOMMAND_TOOLS and program != "journalctl"):
        _require_literal_verb(tokens)
    return tokens


def parse_templates(items, *, strict=False):
    if type(items) is not list:
        raise ValueError("command-template")
    result = tuple(parse_template(item, strict=strict) for item in items)
    if len(set(result)) != len(result):
        raise ValueError("command-template")
    return result


def clean_argv(value):
    """A model-proposed argv: a short list of plain words. No interpretation, no expansion."""
    if (type(value) is not list or not 1 <= len(value) <= MAX_TOKENS
            or any(type(item) is not str or not item or len(item.encode("utf-8")) > MAX_TOKEN_BYTES
                   or any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp", "Zs") for char in item)
                   for item in value)):
        raise ValueError("command-argv")
    return list(value)


def _matches(template, argv):
    fixed = template[:-1] if template[-1] == ELLIPSIS else template
    if len(argv) < len(fixed) or (template[-1] != ELLIPSIS and len(argv) != len(fixed)):
        return False
    if not all((_PLACEHOLDERS[token].fullmatch(word) if token in _PLACEHOLDERS else token == word)
               for token, word in zip(fixed, argv)):
        return False
    return all(_EXTRA.fullmatch(word) for word in argv[len(fixed):])


def extra_words(templates, argv):
    """The words a matching "..." template left open (an exact match leaves none). None when nothing matches."""
    exact = None
    for template in templates:
        if _matches(template, argv):
            if template[-1] != ELLIPSIS:
                return ()
            exact = tuple(argv[len(template) - 1:]) if exact is None else exact
    return exact


def parse_deny(items):
    """Commands the owner never wants run, as word prefixes: "docker rm" refuses every docker rm call."""
    if type(items) is not list:
        raise ValueError("command-template")
    result = []
    for item in items:
        if type(item) is not str or len(item.encode("utf-8")) > MAX_TEMPLATE_BYTES:
            raise ValueError("command-template")
        tokens = tuple(item.split())
        if (not 1 <= len(tokens) <= MAX_TOKENS or " ".join(tokens) != item or not _PROGRAM.fullmatch(tokens[0])
                or any(token in _PLACEHOLDERS or token == ELLIPSIS or not _LITERAL.fullmatch(token)
                       for token in tokens[1:])):
            raise ValueError("command-template")
        result.append(tokens)
    if len(set(result)) != len(result):
        raise ValueError("command-template")
    return tuple(result)


def denied(denies, argv):
    return any(tuple(argv[:len(prefix)]) == prefix for prefix in denies)


def exact_match(templates, argv):
    """Like match, but a "..." template never counts: for commands that run outside the read sandbox (test, undo)."""
    return any(_matches(template, argv) for template in templates if template[-1] != ELLIPSIS)


def match(templates, argv):
    """True when argv fits one of the owner's templates exactly (literals equal, placeholders validated)."""
    return any(_matches(template, argv) for template in templates)


def resolve(program, search=SEARCH_PATH):
    """Absolute path of a bare program name in fixed system directories, or None. The file (after symlinks) and
    every directory above it must be root-owned and not writable by group/other: a user-writable path never runs."""
    if type(program) is not str or not _PROGRAM.fullmatch(program):
        return None
    for directory in search:
        candidate = os.path.join(directory, program)
        try:
            real = os.path.realpath(candidate, strict=True)
            if os.path.basename(real).casefold() in FORBIDDEN_PROGRAMS:
                continue
            info = os.stat(real)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                    or not os.access(real, os.X_OK)):
                continue
            parts = real.split("/")[1:-1]
            current = ""
            for part in parts:
                current += "/" + part
                parent = os.stat(current)
                if parent.st_uid != 0 or parent.st_mode & 0o022:
                    raise OSError()
            return real
        except OSError:
            continue
    return None


def sanitize(raw, limit):
    """(text, truncated): decoded leniently, terminal escapes and control characters removed, cut at a byte limit."""
    text = _ANSI.sub("", raw.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text
                   if char in "\n\t" or unicodedata.category(char) not in ("Cc", "Cf", "Cs", "Zl", "Zp"))
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", "ignore"), True


# What a read command still sees of /run, which is otherwise an empty tmpfs. A read-only bind does not stop connect() on a
# Unix socket, so every daemon socket under /run (Docker, containerd, systemd varlink, the DNS resolver, the display manager,
# ssh, polkit agents) would be reachable. Kept: plain state files that read-only tools need; missing entries are skipped.
# The system D-Bus is not among them: its place is taken by a filtered proxy that passes only read methods
# (dbus_filter), and without the proxy there is no D-Bus at all.
RUN_VISIBLE = ("/run/systemd/system", "/run/systemd/sessions", "/run/systemd/seats",
               "/run/systemd/users", "/run/systemd/netif", "/run/systemd/timesync", "/run/log", "/run/udev/data",
               "/run/udev/tags", "/run/mount", "/run/utmp", "/run/NetworkManager")
# Not /run/user (session sockets of every login) and not /run/dbus (the system bus goes only through the read filter).
_HOST_SOCKET = re.compile(r"/run/(?!(?:user|dbus)(?:/|\Z))[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,3}\Z")
MAX_HOST_SOCKETS = 8


def valid_host_socket(value):
    """An extra socket the owner chose to share with read commands (for example /run/docker.sock)."""
    return (type(value) is str and bool(_HOST_SOCKET.fullmatch(value))
            and not any(part in (".", "..") for part in value.split("/")))


def is_socket(path):
    """True only for a Unix socket itself (not a link to one, not a directory): what may be bound for a command."""
    try:
        return stat.S_ISSOCK(os.lstat(path).st_mode)
    except OSError:
        return False


def sandbox_command(path, argv, *, share_processes=False, host_sockets=(), binds=()):
    """Read-only, network-less view of the system for ONE command; no shell, clean environment.
    share_processes (owner opt-in) keeps the host's process table visible: the read-only bind of / already carries the
    host /proc, so the private PID namespace and its own /proc are simply not created."""
    bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
    if bwrap is None:
        return None
    isolation = [] if share_processes else ["--proc", "/proc"]
    if any(not valid_host_socket(item) for item in host_sockets):
        return None
    run = ["--tmpfs", "/run"]
    for item in RUN_VISIBLE:
        run += ["--ro-bind-try", item, item]
    for item in host_sockets:
        if is_socket(item):                        # a directory or a link in its place is never shared
            run += ["--ro-bind-try", item, item]
    for item in binds:                             # policy-checked paths (Policy.sandbox_binds), read-only over the tmpfs
        if type(item) is not str or not item.startswith("/") or os.path.realpath(item) != item:
            return None
        run += ["--ro-bind", item, item]
    from . import dbus_filter
    bus = dbus_filter.current()
    if bus is not None:
        run += ["--ro-bind", bus, "/run/dbus/system_bus_socket"]
    # Every user's home is hidden wherever this system keeps it (host_accounts), not only under /home.
    from .host_accounts import home_roots
    homes = [item for root in home_roots() for item in ("--tmpfs", root)]
    return [bwrap, "--ro-bind", "/", "/", *isolation, "--dev", "/dev", "--tmpfs", "/tmp",
            *homes, *run,
            "--unshare-net", "--unshare-ipc", *([] if share_processes else ["--unshare-pid"]),
            "--unshare-uts", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8",
            "--setenv", "LC_ALL", "C.UTF-8", "--setenv", "TERM", "dumb", "--setenv", "NO_COLOR", "1",
            "--setenv", "SYSTEMD_COLORS", "0", "--setenv", "SYSTEMD_PAGER", "", "--setenv", "PAGER", "cat",
            "--setenv", "HOME", "/tmp",
            "--chdir", "/", path, *argv[1:]]


def host_environment():
    """Clean environment for a change/test/undo command that must reach the user's own services: no secrets, no HOME."""
    uid = os.geteuid()
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb", "NO_COLOR": "1",
            "SYSTEMD_COLORS": "0", "SYSTEMD_PAGER": "", "PAGER": "cat", "XDG_RUNTIME_DIR": "/run/user/%d" % uid,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/%d/bus" % uid}


def host_command(path, argv):
    return [path, *argv[1:]]


def execute(path, argv, *, output_limit, timeout, command=None, host=False, role="read"):
    """Run one approved command. Returns the closed result step; never raises for a failing command. A sandboxed read
    command gets the read-only D-Bus proxy for exactly its own run."""
    builder = command or (host_command if host else sandbox_command)
    if host or getattr(builder, "func", builder) is not sandbox_command:
        return _execute(path, argv, output_limit=output_limit, timeout=timeout, command=command, host=host, role=role)
    from . import dbus_filter
    with dbus_filter.session():
        return _execute(path, argv, output_limit=output_limit, timeout=timeout, command=command, host=host, role=role)


def _execute(path, argv, *, output_limit, timeout, command=None, host=False, role="read"):
    argv_list = list(argv)
    command = command or (host_command if host else sandbox_command)
    full = command(path, argv_list)
    if full is None:
        return {"op": "run", "argv": argv_list, "status": "unavailable", "exit": None, "output": "", "truncated": False,
                "role": role}
    started = time.monotonic()
    try:
        process = subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=host_environment() if host else {"PATH": "/usr/bin:/bin"},
                                   start_new_session=True, close_fds=True, cwd="/")
    except OSError:
        return {"op": "run", "argv": argv_list, "status": "unavailable", "exit": None, "output": "", "truncated": False,
                "role": role}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    buffers = {"out": bytearray(), "err": bytearray()}
    caps = {"out": output_limit + 1, "err": STDERR_BYTES + 1}
    status, deadline, stopped, capped = "ok", started + timeout, False, False
    code = None
    try:
        while selector.get_map() and not stopped:
            left = deadline - time.monotonic()
            if left <= 0:
                status = "timeout"
                break
            for key, _ in selector.select(min(left, 0.5)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                room = caps[key.data] - len(buffers[key.data])
                buffers[key.data].extend(chunk[:max(room, 0)])
                if len(buffers[key.data]) >= caps[key.data] and key.data == "out":
                    if host:
                        capped = True      # a change is never killed halfway for being talkative: drain, keep the cap
                    else:
                        stopped = True     # enough: more would only be cut off, so the command is stopped
                        break
        if status == "ok" and not stopped:
            # Both pipes are closed; a command may still be running (it closed its output on purpose): give it the time
            # it has left instead of calling a late success a failure.
            try:
                code = process.wait(timeout=max(deadline - time.monotonic(), 0.01))
            except subprocess.TimeoutExpired:
                status = "timeout"
    finally:
        selector.close()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        for stream in (process.stdout, process.stderr):
            stream.close()
        waited = process.wait()
        code = waited if code is None else code
    text, cut = sanitize(bytes(buffers["out"]), output_limit)
    error, _ = sanitize(bytes(buffers["err"]), STDERR_BYTES)
    cut = cut or stopped or capped                    # a stopped command produced more than was kept, whatever sanitising removed
    if status == "ok" and not stopped and code != 0:
        status = "failed"
    if status in ("ok", "failed") and not stopped and code != 0 and error.strip():
        joined = text + ("\n" if text and not text.endswith("\n") else "") + "[stderr] " + error.strip()
        text, more = sanitize(joined.encode("utf-8"), output_limit)           # cut by BYTES, never by characters
        cut = cut or more
    exit_code = code if status in ("ok", "failed") and not stopped and -255 <= code <= 255 else None
    return {"op": "run", "argv": argv_list, "status": status, "exit": exit_code, "output": text, "truncated": cut,
            "role": role}
