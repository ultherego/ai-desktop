"""A read-only view of the system D-Bus for `allow` commands.

The read sandbox shows the host /run as an empty tmpfs; this module supplies its D-Bus socket. For one command,
xdg-dbus-proxy (the filter Flatpak uses) listens on a private socket and passes on only the method calls below: property
reads, introspection and listing methods of the services read tools use. Everything else, including any method that
changes state, resolves a host name or reads secrets, is refused by the proxy before it reaches the service. Without the
proxy the command gets no D-Bus at all; it never falls back to the real bus.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import select
import shutil
import subprocess
import tempfile

PROXY = "/usr/bin/xdg-dbus-proxy"
# 0.1.6 serves sd-bus clients (busctl, hostnamectl, timedatectl); 0.1.8 and 0.1.9 fix filter bypasses (broadcasts,
# forged replies, CVE-2026-94422). The filter is a security boundary, so an older proxy means no D-Bus at all.
MIN_VERSION = (0, 1, 9)
_checked = {}
SYSTEM_BUS = "unix:path=/run/dbus/system_bus_socket"
READY_SECONDS = 5

_READ = ("org.freedesktop.DBus.Properties.Get", "org.freedesktop.DBus.Properties.GetAll",
         "org.freedesktop.DBus.Introspectable.Introspect", "org.freedesktop.DBus.Peer.Ping",
         "org.freedesktop.DBus.ObjectManager.GetManagedObjects")

# name -> read-only methods beyond the generic ones above. A method is listed only if it returns information and does
# not start, stop, load configuration, resolve names, authenticate or reveal secrets.
SERVICES = {
    "org.freedesktop.systemd1": (
        "org.freedesktop.systemd1.Manager.GetUnit", "org.freedesktop.systemd1.Manager.GetUnitByPID",
        "org.freedesktop.systemd1.Manager.GetUnitByInvocationID", "org.freedesktop.systemd1.Manager.GetUnitByControlGroup",
        "org.freedesktop.systemd1.Manager.LoadUnit",
        "org.freedesktop.systemd1.Manager.ListUnits", "org.freedesktop.systemd1.Manager.ListUnitsFiltered",
        "org.freedesktop.systemd1.Manager.ListUnitsByPatterns", "org.freedesktop.systemd1.Manager.ListUnitsByNames",
        "org.freedesktop.systemd1.Manager.ListUnitFiles", "org.freedesktop.systemd1.Manager.ListUnitFilesByPatterns",
        "org.freedesktop.systemd1.Manager.GetUnitFileState", "org.freedesktop.systemd1.Manager.GetUnitFileLinks",
        "org.freedesktop.systemd1.Manager.GetDefaultTarget", "org.freedesktop.systemd1.Manager.ListJobs",
        "org.freedesktop.systemd1.Manager.GetJob", "org.freedesktop.systemd1.Manager.GetUnitProcesses",
        "org.freedesktop.systemd1.Manager.GetDynamicUsers", "org.freedesktop.systemd1.Manager.Dump",
        "org.freedesktop.systemd1.Manager.DumpByFileDescriptor", "org.freedesktop.systemd1.Manager.DumpUnitsMatchingPatterns",
        "org.freedesktop.systemd1.Manager.DumpUnitsMatchingPatternsByFileDescriptor",
        "org.freedesktop.systemd1.Unit.GetProcesses", "org.freedesktop.systemd1.Service.GetProcesses",
        "org.freedesktop.systemd1.Scope.GetProcesses", "org.freedesktop.systemd1.Slice.GetProcesses",
        "org.freedesktop.systemd1.Socket.GetProcesses", "org.freedesktop.systemd1.Mount.GetProcesses",
        "org.freedesktop.systemd1.Swap.GetProcesses",
    ),
    "org.freedesktop.login1": (
        "org.freedesktop.login1.Manager.ListSessions", "org.freedesktop.login1.Manager.ListSessionsEx",
        "org.freedesktop.login1.Manager.ListUsers", "org.freedesktop.login1.Manager.ListSeats",
        "org.freedesktop.login1.Manager.ListInhibitors", "org.freedesktop.login1.Manager.GetSession",
        "org.freedesktop.login1.Manager.GetSessionByPID", "org.freedesktop.login1.Manager.GetUser",
        "org.freedesktop.login1.Manager.GetUserByPID", "org.freedesktop.login1.Manager.GetSeat",
    ),
    "org.freedesktop.hostname1": ("org.freedesktop.hostname1.Describe",),
    "org.freedesktop.timedate1": ("org.freedesktop.timedate1.ListTimezones",),
    "org.freedesktop.locale1": (),
    "org.freedesktop.network1": (
        "org.freedesktop.network1.Manager.ListLinks", "org.freedesktop.network1.Manager.GetLinkByName",
        "org.freedesktop.network1.Manager.GetLinkByIndex", "org.freedesktop.network1.Manager.Describe",
        "org.freedesktop.network1.Manager.DescribeLink", "org.freedesktop.network1.Link.Describe",
    ),
    "org.freedesktop.resolve1": (),          # properties only: no ResolveHostname/ResolveRecord, so no DNS from a read
    "org.freedesktop.timesync1": (),
    "org.freedesktop.NetworkManager": (
        "org.freedesktop.NetworkManager.GetDevices", "org.freedesktop.NetworkManager.GetAllDevices",
        "org.freedesktop.NetworkManager.GetPermissions", "org.freedesktop.NetworkManager.GetLogging",
        "org.freedesktop.NetworkManager.state", "org.freedesktop.NetworkManager.Settings.ListConnections",
        "org.freedesktop.NetworkManager.Settings.GetConnectionByUuid",
        "org.freedesktop.NetworkManager.Settings.Connection.GetSettings",      # secrets need GetSecrets: not listed
        "org.freedesktop.NetworkManager.Device.GetAppliedConnection",
        "org.freedesktop.NetworkManager.Device.Wireless.GetAccessPoints",
        "org.freedesktop.NetworkManager.Device.Wireless.GetAllAccessPoints",
    ),
    "org.bluez": (),
    "org.freedesktop.UPower": (
        "org.freedesktop.UPower.EnumerateDevices", "org.freedesktop.UPower.GetDisplayDevice",
        "org.freedesktop.UPower.GetCriticalAction", "org.freedesktop.UPower.Device.GetHistory",
        "org.freedesktop.UPower.Device.GetStatistics",
    ),
    "org.freedesktop.UPower.PowerProfiles": (),
    "net.hadess.PowerProfiles": (),
    "org.opensuse.Snapper": (
        "org.opensuse.Snapper.ListConfigs", "org.opensuse.Snapper.GetConfig", "org.opensuse.Snapper.ListSnapshots",
        "org.opensuse.Snapper.GetSnapshot", "org.opensuse.Snapper.GetDefaultSnapshot",
        "org.opensuse.Snapper.GetActiveSnapshot", "org.opensuse.Snapper.ListSnapshotsAtTime",
        "org.opensuse.Snapper.GetUsedSpace", "org.opensuse.Snapper.GetMountPoint",
        "org.opensuse.Snapper.IsSnapshotReadOnly",
        "org.opensuse.Snapper.GetPluginsReport",     # the snapper tool asks for it after every command
    ),
    "org.freedesktop.UDisks2": (),
    "org.freedesktop.Accounts": (),
}

_bus = ContextVar("ai_desktop_read_bus", default=None)


def proxy_version(path=PROXY):
    """The installed proxy's version as a tuple, or None when it cannot be read. Asked once per process."""
    if path not in _checked:
        version = None
        try:
            text = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=5, env={"PATH": "/usr/bin"}, check=False).stdout.strip()
            name, _, number = text.partition(" ")
            parts = tuple(int(part) for part in number.split("."))
            if name == "xdg-dbus-proxy" and 2 <= len(parts) <= 4:
                version = parts
        except (OSError, ValueError, subprocess.SubprocessError):
            version = None
        _checked[path] = version
    return _checked[path]


def usable(path=PROXY):
    version = proxy_version(path)
    return version is not None and version >= MIN_VERSION


def arguments():
    """The proxy's filter rules, one --call per allowed method on any object path."""
    rules = ["--filter"]
    for name, methods in SERVICES.items():
        for method in (*_READ, *methods):
            rules.append("--call=%s=%s@/*" % (name, method))
    return rules


def current():
    """The filtered socket for the command that is running now, or None (then the command gets no D-Bus)."""
    return _bus.get()


@contextmanager
def session():
    """Run the proxy for one command. Yields the socket path, or None when the proxy is missing or not ready."""
    proxy = shutil.which("xdg-dbus-proxy", path="/usr/bin")
    if proxy != PROXY or not os.path.exists("/run/dbus/system_bus_socket") or not usable(proxy):
        token = _bus.set(None)
        try:
            yield None
        finally:
            _bus.reset(token)
        return
    directory = tempfile.mkdtemp(prefix="ai-desktop-bus-")
    socket = os.path.join(directory, "bus")
    ready_read, ready_write = os.pipe()
    process = None
    path = None
    try:
        process = subprocess.Popen([PROXY, "--fd=%d" % ready_write, SYSTEM_BUS, socket, *arguments()],
                                   pass_fds=(ready_write,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin"}, close_fds=True)
        os.close(ready_write)
        ready_write = None
        if select.select([ready_read], [], [], READY_SECONDS)[0] and os.read(ready_read, 1) and os.path.exists(socket):
            path = socket
        token = _bus.set(path)
        try:
            yield path
        finally:
            _bus.reset(token)
    finally:
        if ready_write is not None:
            os.close(ready_write)
        os.close(ready_read)               # the proxy stops when this end closes
        if process is not None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        shutil.rmtree(directory, ignore_errors=True)
