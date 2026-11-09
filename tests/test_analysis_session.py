"""Daily lifecycle guards, real pipe teardown, ownership and consent ordering."""
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_desktop import analysis_cli as ui, analysis_session as client, analysis_session_admin as admin, cli
from ai_desktop.analysis_contract import AnalysisConsent, AnalysisDenied
from ai_desktop.terminal_consent import PROMPTS
from test_report_policy import report_fixture


class SessionClientTests(unittest.TestCase):
    def test_status_and_default_entry_do_not_collect_activate_or_access_auth(self):
        for argv in ([], ["status"]):
            out = io.StringIO()
            with patch.object(client, "installation_ready"), patch.object(client, "identity", return_value=1000), \
                    patch.object(client, "properties", return_value={"ActiveState": "inactive"}), \
                    patch.object(client, "authenticate") as authenticate, patch.object(ui.diagnose, "collect") as collect, \
                    patch.object(ui, "send_once") as send, redirect_stdout(out), \
                    patch("ai_desktop.agent_panel.permissions.load_policy", return_value=None):
                # The owner's real permissions.toml (possibly being edited) must not decide this test.
                self.assertEqual(cli.main(argv), 0)
            authenticate.assert_not_called()
            collect.assert_not_called()
            send.assert_not_called()
            self.assertIn("auth=unchecked network=unused", out.getvalue())

    def test_status_failure_is_redacted_and_never_claims_auth_ready(self):
        out = io.StringIO()
        with patch.object(client, "installation_ready", side_effect=ValueError("PRIVATE")), redirect_stdout(out):
            self.assertEqual(client.status(), 2)
        self.assertNotIn("PRIVATE", out.getvalue())
        self.assertNotIn("STATUS: PASS", out.getvalue())

    def test_identity_and_login_flag_refuse_before_sudo(self):
        with patch.object(client.subprocess, "run") as run, patch.object(client, "trusted_file") as trusted, redirect_stdout(io.StringIO()):
            for uid, real, tty in ((0, 0, True), (999, 999, True), (1000, 1001, True), (1000, 1000, False)):
                with patch.object(client.os, "geteuid", return_value=uid), patch.object(client.os, "getuid", return_value=real), \
                        patch.object(client.os, "isatty", return_value=tty):
                    with self.assertRaises(AnalysisDenied):
                        client.authenticate()
            self.assertEqual(client.administrative("login"), 2)
            run.assert_not_called()
            trusted.assert_not_called()

    def test_helper_accepts_only_fixed_actions_and_auth_denial_has_no_child(self):
        with patch.object(client, "trusted_file"):
            for action in ("sh", "../open", "open --uid 0", "", None):
                with self.assertRaises(AnalysisDenied):
                    client.helper_command(action)
            command = client.helper_command("open")
            self.assertEqual(command, ["/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-B",
                                       "/usr/lib/ai-desktop/analysis-session.py", "open"])
        with patch.object(client, "authenticate", side_effect=AnalysisDenied("authorization")), \
                patch.object(client.subprocess, "Popen") as opened, self.assertRaises(AnalysisDenied):
            with client.managed_session():
                self.fail("entered")
        opened.assert_not_called()

    def test_real_pipe_eof_waits_for_cleanup_and_rejects_bad_frames(self):
        for ready, stopped, code, succeeds in (("SESSION1 READY", "SESSION1 STOPPED", 0, True),
                                                ("SESSION1 READY", "SESSION1 STOPPED", 2, False),
                                                ("SESSION1 READY", "PRIVATE", 0, False),
                                                ("PRIVATE", "SESSION1 STOPPED", 0, False),
                                                ("SESSION1 REFUSED busy", "", 2, False)):
            script = "import sys; print(" + repr(ready) + ",flush=True); sys.stdin.buffer.read(); print(" + repr(stopped) + ",flush=True); sys.exit(" + str(code) + ")"
            process = subprocess.Popen([sys.executable, "-B", "-c", script], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            session = client.Session(process)
            try:
                if succeeds:
                    session.start()
                    self.assertIsNone(process.poll())
                    session.close()
                    self.assertEqual(process.returncode, 0)
                else:
                    with self.assertRaises(AnalysisDenied):
                        try:
                            session.start()
                        finally:
                            session.close()
            finally:
                if not process.stdin.closed:
                    process.stdin.close()
                process.wait(timeout=3)
                process.stdout.close()

    def test_sudo_failure_or_timeout_is_redacted_before_session_start(self):
        failures = (subprocess.TimeoutExpired("PRIVATE", 120), OSError("PRIVATE"),
                    subprocess.CompletedProcess([], 1))
        for failure in failures:
            with patch.object(client, "identity", return_value=1000), patch.object(client, "trusted_file"), \
                    patch.object(client.subprocess, "run") as run, patch.object(client.subprocess, "Popen") as opened, \
                    redirect_stdout(io.StringIO()), self.assertRaises(AnalysisDenied) as caught:
                if isinstance(failure, BaseException):
                    run.side_effect = failure
                else:
                    run.return_value = failure
                with client.managed_session():
                    self.fail("entered")
            self.assertEqual(caught.exception.reason, "authorization")
            self.assertNotIn("PRIVATE", str(caught.exception))
            opened.assert_not_called()

    def test_keyboard_interrupt_closes_real_helper_pipe(self):
        script = "import sys; print('SESSION1 READY',flush=True); sys.stdin.buffer.read(); print('SESSION1 STOPPED',flush=True)"
        with patch.object(client, "authenticate"), patch.object(client, "helper_command", return_value=[sys.executable, "-B", "-c", script]), \
                self.assertRaises(KeyboardInterrupt):
            with client.managed_session():
                raise KeyboardInterrupt()

    def test_quiet_authentication_hides_only_explanation_and_keeps_sudo_on_tty(self):
        for quiet in (False, True):
            output = io.StringIO()
            with patch.object(client, "identity", return_value=1000), patch.object(client, "trusted_file"), \
                    patch.object(client.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                    redirect_stdout(output):
                client.authenticate(quiet=quiet)
            self.assertEqual("Administrator permissions" in output.getvalue(), not quiet)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/sudo", "-v"])
            self.assertFalse(any(key in run.call_args.kwargs for key in ("stdin", "stdout", "stderr", "capture_output")))

    def test_quiet_managed_session_still_requires_cleanup_acknowledgment(self):
        for code in (0, 2):
            script = "import sys; print('SESSION1 READY',flush=True); sys.stdin.buffer.read(); print('SESSION1 STOPPED',flush=True); sys.exit(" + str(code) + ")"
            output = io.StringIO()
            with patch.object(client, "authenticate") as authenticate, \
                    patch.object(client, "helper_command", return_value=[sys.executable, "-B", "-c", script]), \
                    redirect_stdout(output):
                if code:
                    with self.assertRaisesRegex(AnalysisDenied, "reason=cleanup$"):
                        with client.managed_session(quiet=True):
                            pass
                else:
                    with client.managed_session(quiet=True):
                        pass
            authenticate.assert_called_once_with(quiet=True)
            self.assertEqual(output.getvalue(), "")

    def test_invalid_quiet_flag_never_starts_authentication_or_process(self):
        for value in (None, 0, "true"):
            with patch.object(client, "authenticate") as authenticate, \
                    patch.object(client.subprocess, "Popen") as process:
                with self.assertRaisesRegex(AnalysisDenied, "reason=input$"):
                    with client.managed_session(quiet=value):
                        pass
                authenticate.assert_not_called()
                process.assert_not_called()


class DailyConsentTests(unittest.TestCase):
    def run_ui(self, answer, *, session=None, send=None, gate=None, report=None):
        out = io.StringIO()
        out.isatty = lambda: True
        with ExitStack() as stack:
            stack.enter_context(patch.object(ui.os, "geteuid", return_value=1000))
            stack.enter_context(patch.object(ui.os, "getuid", return_value=1000))
            stack.enter_context(patch.object(ui.sys.stdin, "isatty", return_value=True))
            stack.enter_context(patch.object(ui.sys.stdout, "isatty", return_value=True))
            stack.enter_context(patch.object(ui, "installation_ready"))
            stack.enter_context(patch.object(ui.diagnose, "collect", return_value=report if report is not None else report_fixture()))
            started = stack.enter_context(patch.object(ui, "managed_session", side_effect=session or nullcontext))
            transferred = stack.enter_context(patch.object(ui, "send_once", side_effect=send or (lambda _: "wynik")))
            if gate:
                stack.enter_context(patch.object(ui, "AnalysisConsent", return_value=gate))
            stack.enter_context(redirect_stdout(out))
            result = ui.main(details=True, reader=answer if callable(answer) else lambda _: answer)
        return result, out.getvalue(), started, transferred

    def test_enter_unknown_answer_and_refusal_never_escalate_or_send(self):
        for answer in ("", "nie", "tak", "t", "ANALIZUJ", "ANALIZUJ " + "0" * 64):
            answers = iter([answer, "no"])
            code, output, started, sent = self.run_ui(lambda _: next(answers))
            self.assertEqual(code, 2)
            started.assert_not_called()
            sent.assert_not_called()
            self.assertIn("type yes", output)

    def test_short_confirmation_keeps_exact_payload_and_is_not_reusable(self):
        report = report_fixture()
        gate = AnalysisConsent(report)
        code, _, started, sent = self.run_ui("YES", gate=gate, report=report)
        self.assertEqual(code, 0)
        self.assertEqual(sent.call_args.args[0].sha256, gate.sha256)
        self.assertEqual(sent.call_args.args[0].payload, gate.payload)
        code, _, started, sent = self.run_ui("yes", gate=gate, report=report)
        self.assertEqual(code, 2)
        started.assert_not_called()
        sent.assert_not_called()

    def test_changed_report_after_preview_refuses_before_session(self):
        report = report_fixture()
        def reader(prompt):
            self.assertEqual(prompt, PROMPTS["transfer"])
            report["kernel"] = "6.9.2-cachyos"
            return "yes"
        code, _, started, sent = self.run_ui(reader, report=report)
        self.assertEqual(code, 2)
        started.assert_not_called()
        sent.assert_not_called()

    def test_eof_interrupt_or_reader_failure_never_escalates(self):
        for failure in (EOFError, KeyboardInterrupt, OSError):
            def reader(_):
                raise failure()
            code, _, started, sent = self.run_ui(reader)
            self.assertEqual(code, 2)
            started.assert_not_called()
            sent.assert_not_called()

    def test_repeated_invalid_answers_do_not_refresh_expired_preview(self):
        clock = [0]
        gate = AnalysisConsent(report_fixture(), clock=lambda: clock[0])
        answers = iter(["", "?", "tak", "unknown", "yes"])
        def reader(_):
            clock[0] += 40
            return next(answers)
        code, _, started, sent = self.run_ui(reader, gate=gate)
        self.assertEqual(code, 2)
        started.assert_not_called()
        sent.assert_not_called()

    def test_expiry_during_sudo_rechecks_consent_and_still_cleans_up(self):
        clock, events = [0], []
        gate = AnalysisConsent(report_fixture(), clock=lambda: clock[0])
        @contextmanager
        def session():
            clock[0] = 181
            try:
                yield
            finally:
                events.append("cleanup")
        code, _, _, sent = self.run_ui("yes", session=session, gate=gate)
        self.assertEqual(code, 2)
        sent.assert_not_called()
        self.assertEqual(events, ["cleanup"])

    def test_failures_cancel_and_success_all_close_once_without_retry(self):
        for failure in (None, AnalysisDenied("auth-required"), AnalysisDenied("client"),
                        AnalysisDenied("timeout"), KeyboardInterrupt()):
            events = []
            @contextmanager
            def session():
                events.append("start")
                try:
                    yield
                finally:
                    events.append("cleanup")
            def send(_):
                events.append("send")
                if failure:
                    raise failure
                return "wynik"
            code, output, started, sent = self.run_ui("yes", session=session, send=send)
            self.assertEqual(code, 0 if failure is None else 2)
            self.assertEqual(events, ["start", "send", "cleanup"])
            self.assertEqual(sent.call_count, 1)
            self.assertEqual(started.call_count, 1)
            self.assertEqual("ANALYSIS: PASS" in output, failure is None)

    def test_cleanup_failure_cannot_report_analysis_success(self):
        @contextmanager
        def session():
            yield
            raise AnalysisDenied("cleanup")
        code, output, _, _ = self.run_ui("yes", session=session)
        self.assertEqual(code, 2)
        self.assertNotIn("ANALYSIS: PASS", output)
        self.assertIn("ai-desktop stop", output)


class FakeSystemd:
    """Unit states and the commands the root helper runs, without systemd."""

    def __init__(self):
        self.active, self.calls = set(), []

    def properties(self, unit):
        return {"ActiveState": "active" if unit in self.active else "inactive", "MainPID": "0", "LoadState": "loaded"}

    def command(self, *args):
        self.calls.append(args[1:])
        if args[1] == "start":
            self.active.update(args[2:])
        elif args[1] == "stop":
            self.active.difference_update(args[2:])
        return b""


class MultiSessionTests(unittest.TestCase):
    """Every open terminal has its own session; the first starts the user's socket and the last one stops it."""

    def setUp(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = temp / "sessions"
        self.state.mkdir(mode=0o700)
        self.systemd = FakeSystemd()
        for target, value in (("STATE", self.state), ("_OWNER", (os.getuid(), os.getgid())),
                              ("state_directory", lambda: None), ("unit_policy", lambda uid, inactive=False: None),
                              ("endpoint", lambda uid: None), ("cleaned", lambda uid: None),
                              ("properties", self.systemd.properties), ("command", self.systemd.command)):
            self.enterContext(patch.object(admin, target, value))
        self.written = []
        real_write = os.write

        def write(fd, data):                                          # messages on stdout are recorded, files written
            if fd == 1:
                self.written.append(data)
                return len(data)
            return real_write(fd, data)
        self.enterContext(patch.object(admin.os, "write", side_effect=write))
        self.enterContext(patch.object(admin.select, "select", return_value=([0], [], [])))

    def hold(self, nonce):
        """A terminal's helper holding its session: its unit runs, then its pipe closes."""
        self.systemd.active.add(admin.manager_unit(1000, nonce))
        real_read = os.read
        with patch.object(admin.os, "read", side_effect=lambda fd, n: b"" if fd == 0 else real_read(fd, n)):
            admin.hold(1000, nonce)                                   # its terminal closed the pipe (EOF on fd 0)

    def end(self, nonce):
        self.systemd.active.discard(admin.manager_unit(1000, nonce))
        admin.cleanup(1000, nonce)

    def test_the_first_session_starts_the_socket_and_only_the_last_one_stops_it(self):
        socket_unit, service_unit = admin.units(1000)
        one, two = "a" * 32, "b" * 32
        self.hold(one)
        self.assertEqual(self.systemd.calls, [("start", socket_unit)])
        self.hold(two)                                                # a second terminal: no second start
        self.assertEqual(self.systemd.calls, [("start", socket_unit)])
        self.assertEqual(sorted(admin.claims(1000)), [one, two])
        self.end(one)
        self.assertIn(socket_unit, self.systemd.active)               # the other terminal still works
        self.assertEqual(admin.claims(1000), [two])
        self.end(two)
        self.assertEqual(self.systemd.calls[-1], ("stop", socket_unit, service_unit))
        self.assertEqual(admin.claims(1000), [])
        self.assertEqual(self.written.count(admin.STOPPED), 2)

    def test_a_claim_whose_helper_is_gone_is_pruned_and_never_keeps_the_socket(self):
        stale, fresh = "c" * 32, "d" * 32
        admin.create_claim(1000, stale)                               # its helper died without cleanup
        self.hold(fresh)
        self.assertEqual(admin.claims(1000), [fresh])
        self.assertEqual(self.systemd.calls, [("start", admin.units(1000)[0])])

    def test_open_refuses_only_beyond_the_session_limit(self):
        for index in range(admin.MAX_SESSIONS):
            nonce = "%032x" % (index + 1)
            admin.create_claim(1000, nonce)
            self.systemd.active.add(admin.manager_unit(1000, nonce))
        with patch.object(admin.subprocess, "run") as run, self.assertRaises(AnalysisDenied) as caught:
            admin.open_session(1000)
        self.assertEqual(caught.exception.reason, "busy")
        run.assert_not_called()

    def test_a_session_of_the_previous_design_keeps_the_socket_until_it_ends(self):
        admin.legacy_claim(1000).write_text("e" * 32)
        with self.assertRaises(AnalysisDenied):
            admin.hold(1000, "f" * 32)
        self.assertEqual(self.systemd.calls, [])
        admin.cleanup(1000, "f" * 32)
        self.assertEqual(self.systemd.calls, [])                      # never stops what the old session owns

    def test_stop_ends_every_session_of_the_user(self):
        one, two = "1" * 32, "2" * 32
        self.hold(one)
        self.hold(two)

        def stopped(*args):
            self.systemd.command(*args)
            if args[1] == "stop" and args[2].startswith("ai-desktop-session-"):
                nonce = next(n for n in (one, two) if admin.manager_unit(1000, n) == args[2])
                admin.cleanup(1000, nonce)                            # that helper's own ExecStopPost
        with patch.object(admin, "command", side_effect=stopped):
            admin.stop_session(1000)
        self.assertEqual(admin.claims(1000), [])
        self.assertNotIn(admin.units(1000)[0], self.systemd.active)

    def test_names_and_claims_come_only_from_our_nonces(self):
        self.assertEqual(admin.manager_unit(1000, "a" * 32), "ai-desktop-session-1000-" + "a" * 16 + ".service")
        for bad in ("a" * 31, "A" * 32, "../" + "a" * 29, None):
            with self.subTest(bad=bad), self.assertRaises(AnalysisDenied):
                admin.manager_unit(1000, bad)
        directory = admin.claims_directory(1000)
        target = self.state / "target"
        target.write_text("a" * 32)
        target.chmod(0o600)
        for make in ("symlink", "fifo", "hardlink", "mode", "name"):
            path = directory / ("a" * 32 if make != "name" else "b" * 32)
            if make == "symlink":
                path.symlink_to(target)
            elif make == "fifo":
                os.mkfifo(path, 0o600)
            elif make == "hardlink":
                os.link(target, path)
            else:
                path.write_text("a" * 32)
                path.chmod(0o666 if make == "mode" else 0o600)
            with self.subTest(make=make), self.assertRaises((OSError, AnalysisDenied)):
                admin.claims(1000)
            path.unlink()
        (directory / "not-a-nonce").write_text("x")
        with self.assertRaises(AnalysisDenied):
            admin.claims(1000)


class RootLifecycleTests(unittest.TestCase):
    def test_root_caller_and_argument_guards_precede_all_operations(self):
        with patch.object(admin, "open_session") as opened, patch.object(admin, "hold") as hold, \
                patch.object(admin, "stop_session") as stop, patch.object(admin.os, "write"):
            with patch.object(admin.os, "getuid", return_value=1000), patch.object(admin.os, "geteuid", return_value=1000):
                self.assertEqual(admin.main(["open"]), 2)
            with patch.object(admin.os, "getuid", return_value=0), patch.object(admin.os, "geteuid", return_value=0):
                for args in ([], ["open", "1001"], ["stop", "sh"], ["hold", "1000", "../x"], ["sh"]):
                    self.assertEqual(admin.main(args), 2)
                for value in (None, "0", "01000", "-1", "1000;sh", "4294967295"):
                    with patch.dict(os.environ, {} if value is None else {"SUDO_UID": value}, clear=True):
                        self.assertEqual(admin.main(["open"]), 2)
            opened.assert_not_called(); hold.assert_not_called(); stop.assert_not_called()

    def test_unit_policy_rejects_overrides_and_existing_session_without_mutation(self):
        def good(unit):
            return {"LoadState": "loaded", "FragmentPath": "/usr/lib/systemd/system/ai-desktop-analysis@." + unit.rsplit(".", 1)[1],
                    "DropInPaths": "", "NeedDaemonReload": "no", "UnitFileState": "static", "ActiveState": "inactive", "MainPID": "0"}
        for key, value in (("DropInPaths", "/etc/override"), ("NeedDaemonReload", "yes"),
                            ("UnitFileState", "enabled"), ("ActiveState", "active"), ("FragmentPath", "/tmp/unit")):
            with patch.object(admin, "cloud_transport_ready"), patch.object(admin, "trusted_file"), \
                    patch.object(admin, "properties", side_effect=lambda unit: dict(good(unit), **{key: value})), \
                    patch.object(admin, "create_claim") as claim, patch.object(admin, "command") as command:
                with self.assertRaises(AnalysisDenied):
                    admin.unit_policy(1000, inactive=True)
                claim.assert_not_called(); command.assert_not_called()

    def test_missing_unit_is_valid_metadata_but_command_failure_is_not(self):
        for code, data, valid in ((1, b"LoadState=not-found\nActiveState=inactive\n", True),
                                  (1, b"LoadState=loaded\nActiveState=inactive\n", False),
                                  (0, b"LoadState=loaded\nLoadState=not-found\n", False)):
            with patch.object(admin, "bounded_process", return_value=(code, data, b"PRIVATE")):
                if valid:
                    self.assertEqual(admin.properties("x")["LoadState"], "not-found")
                else:
                    with self.assertRaises(AnalysisDenied):
                        admin.properties("x")
