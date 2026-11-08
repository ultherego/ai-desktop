"""The read-only D-Bus view of `allow` commands: only listed read methods pass, and no proxy means no D-Bus."""
import functools
import os
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from ai_desktop import command_ops, dbus_filter
from sandbox_requirement import requires, skip_or_fail

PROXY_READY = os.path.exists(dbus_filter.PROXY) and dbus_filter.usable() and os.path.exists("/run/dbus/system_bus_socket") \
    and shutil.which("bwrap") is not None and shutil.which("busctl") is not None


class RuleTests(unittest.TestCase):
    def test_every_rule_is_one_method_on_one_service(self):
        rules = dbus_filter.arguments()
        self.assertEqual(rules[0], "--filter")
        for rule in rules[1:]:
            self.assertRegex(rule, r"\A--call=[A-Za-z0-9.]+=[A-Za-z0-9.]+\.[A-Za-z0-9]+@/\*\Z")
        self.assertNotIn("--talk", " ".join(rules))
        self.assertNotIn("--own", " ".join(rules))
        self.assertFalse(any(re.search(r"=\*@|\.\*@", rule) for rule in rules), "no wildcard methods")

    def test_no_method_that_changes_state_resolves_names_or_reads_secrets(self):
        methods = [rule.split("=", 2)[2].split("@")[0] for rule in dbus_filter.arguments()[1:]]
        forbidden = re.compile(r"\.(Set|Start|Stop|Restart|Reload|Kill|Enable|Disable|Mask|Unmask|Reset|Activate|"
                               r"Deactivate|Add|Delete|Remove|Create|Update|Connect|Disconnect|Power|Reboot|Suspend|"
                               r"Hibernate|Lock|Terminate|Resolve|GetSecrets|Authenticate|Mount|Unmount|Format|"
                               r"Attach|Detach|Link|Unlink|Preset|Revert|Freeze|Thaw|Clean|Subscribe)[A-Za-z]*\Z")
        self.assertEqual([method for method in methods if forbidden.search(method)], [])
        self.assertIn("org.freedesktop.DBus.Properties.Get", methods)
        self.assertIn("org.opensuse.Snapper.GetPluginsReport", methods)      # snapper asks for it after every command
        self.assertNotIn("org.freedesktop.DBus.Properties.Set", methods)
        self.assertNotIn("org.freedesktop.PolicyKit1", " ".join(dbus_filter.arguments()))


class SandboxWiringTests(unittest.TestCase):
    def sandbox(self):
        with patch.object(command_ops.shutil, "which", return_value="/usr/bin/bwrap"):
            return command_ops.sandbox_command("/usr/bin/hostnamectl", ["hostnamectl"])

    def test_without_a_proxy_the_command_gets_no_dbus_at_all(self):
        command = self.sandbox()
        self.assertFalse(any("dbus" in item for item in command))

    def test_with_a_proxy_only_its_socket_takes_the_place_of_the_system_bus(self):
        token = dbus_filter._bus.set("/tmp/ai-desktop-bus-synthetic/bus")
        try:
            command = self.sandbox()
        finally:
            dbus_filter._bus.reset(token)
        start = command.index("/tmp/ai-desktop-bus-synthetic/bus") - 1
        self.assertEqual(command[start:start + 3],
                         ["--ro-bind", "/tmp/ai-desktop-bus-synthetic/bus", "/run/dbus/system_bus_socket"])
        self.assertGreater(start, command.index("/run") - 1)            # after the empty /run, so it is not covered
        self.assertNotIn("/run/dbus/system_bus_socket", command[:start])

    def test_a_missing_or_foreign_proxy_yields_no_bus(self):
        with patch.object(dbus_filter.shutil, "which", return_value=None):
            with dbus_filter.session() as bus:
                self.assertIsNone(bus)
                self.assertIsNone(dbus_filter.current())
        with patch.object(dbus_filter.shutil, "which", return_value="/home/example/xdg-dbus-proxy"):
            with dbus_filter.session() as bus:
                self.assertIsNone(bus)

    def test_a_proxy_older_than_the_filter_fixes_gives_no_bus(self):
        for output, usable in (("xdg-dbus-proxy 0.1.5\n", False), ("xdg-dbus-proxy 0.1.8\n", False),
                               ("xdg-dbus-proxy 0.1.9\n", True), ("xdg-dbus-proxy 0.2\n", True),
                               ("something else 9.9\n", False), ("", False)):
            with self.subTest(output=output), patch.dict(dbus_filter._checked, clear=True), \
                    patch.object(dbus_filter.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)):
                self.assertIs(dbus_filter.usable("/usr/bin/synthetic-proxy"), usable)
        with patch.dict(dbus_filter._checked, {dbus_filter.PROXY: (0, 1, 5)}), \
                patch.object(dbus_filter.shutil, "which", return_value=dbus_filter.PROXY), \
                patch.object(dbus_filter.os.path, "exists", return_value=True):
            with dbus_filter.session() as bus:
                self.assertIsNone(bus)                                        # never the old filter, never the raw bus

    def test_only_sandboxed_read_commands_start_the_proxy(self):
        calls = []

        class Session:
            def __enter__(self):
                calls.append("start")

            def __exit__(self, *unused):
                return False

        step = {"op": "run", "argv": ["x"], "status": "ok", "exit": 0, "output": "", "truncated": False, "role": "read"}
        with patch.object(dbus_filter, "session", Session), patch.object(command_ops, "_execute", return_value=step):
            command_ops.execute("/usr/bin/x", ["x"], output_limit=10, timeout=1)
            command_ops.execute("/usr/bin/x", ["x"], output_limit=10, timeout=1,
                                command=functools.partial(command_ops.sandbox_command, host_sockets=("/run/a.sock",)))
            command_ops.execute("/usr/bin/x", ["x"], output_limit=10, timeout=1, host=True, role="change")
            command_ops.execute("/usr/bin/x", ["x"], output_limit=10, timeout=1, command=lambda path, argv: [path])
        self.assertEqual(calls, ["start", "start"])


@requires(PROXY_READY, "xdg-dbus-proxy, a system bus, bwrap and busctl are needed")
class RealProxyTests(unittest.TestCase):
    def busctl(self, *args):
        with dbus_filter.session() as bus:
            self.assertIsNotNone(bus)
            command = command_ops.sandbox_command(shutil.which("busctl"), ["busctl", "--system", *args])
            return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def test_a_property_read_passes(self):
        result = self.busctl("get-property", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                             "org.freedesktop.systemd1.Manager", "Version")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_unlisted_method_and_a_name_lookup_are_refused_by_the_proxy(self):
        for args in (("call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                      "NoSuchMethodSynthetic"),
                     ("call", "org.freedesktop.resolve1", "/org/freedesktop/resolve1", "org.freedesktop.resolve1.Manager",
                      "ResolveHostname", "isit", "0", "synthetic.invalid", "0", "0")):
            with self.subTest(method=args[4]):
                result = self.busctl(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Access denied", result.stderr)

    def test_the_snapper_tool_reads_its_configurations_through_the_filter(self):
        # Checked here, not at import: discovering the tests must never run a host program.
        snapper = shutil.which("snapper", path="/usr/bin")
        if snapper is None:
            self.skipTest("snapper is not installed")
        try:
            direct = subprocess.run([snapper, "list-configs"], capture_output=True, stdin=subprocess.DEVNULL, timeout=10)
        except subprocess.TimeoutExpired:
            self.skipTest("snapper did not answer in time")
        if direct.returncode != 0:
            self.skipTest("snapper has no configuration readable by this user")
        with dbus_filter.session():
            command = command_ops.sandbox_command(shutil.which("snapper"), ["snapper", "list-configs"])
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_proxy_stops_and_leaves_nothing_behind(self):
        with dbus_filter.session() as bus:
            directory = os.path.dirname(bus)
        self.assertFalse(os.path.exists(directory))


if __name__ == "__main__":
    unittest.main()
