"""One conversation keeps its privileged session and service connection between questions."""
from contextlib import contextmanager
import signal
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from ai_desktop import analysis_session as daily
from ai_desktop import analysis_contract as contract, analysis_service as service, conversation_link as link_module
from ai_desktop.analysis_contract import AnalysisConsent, AnalysisDenied
from ai_desktop.conversation_link import Link, MARGIN, held

IDLE = contract.CONVERSATION_IDLE_SECONDS
LIFETIME = contract.CONVERSATION_SECONDS


class FakeSession:
    def __init__(self):
        self.living = True

    def alive(self):
        return self.living


class Opener:
    """A stand-in for managed_session that records openings, closings and the quiet flag."""

    def __init__(self, *, fail_close=False, fail_open=None):
        self.events, self.sessions, self.fail_close, self.fail_open = [], [], fail_close, fail_open

    @contextmanager
    def __call__(self, **kwargs):
        self.events.append(("open", kwargs))
        if self.fail_open is not None:
            raise self.fail_open
        session = FakeSession()
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.events.append(("close",))
            if self.fail_close:
                raise AnalysisDenied("cleanup")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def opened(opener):
    return [event for event in opener.events if event[0] == "open"]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.opener, self.clock = Opener(), Clock()
        self.link = Link(self.opener, clock=self.clock)

    def use(self, **kwargs):
        with self.link.session(**kwargs):
            pass

    def test_the_session_opens_once_for_the_questions_of_a_conversation(self):
        for _ in range(3):
            self.use(quiet=True)
            self.clock.now += 60
        self.assertEqual(self.opener.events, [("open", {"quiet": True})])
        self.link.close()
        self.assertEqual(self.opener.events[-1], ("close",))
        self.assertFalse(self.link.is_open)

    def test_the_sudo_wait_wraps_only_the_opening(self):
        waits = []

        @contextmanager
        def waiting():
            waits.append("start")
            yield
            waits.append("end")
        self.use(waiting=waiting)
        self.use(waiting=waiting)
        self.assertEqual(waits, ["start", "end"])

    def test_an_idle_conversation_opens_a_new_session(self):
        self.use()
        self.clock.now += IDLE - MARGIN - 1
        self.use()
        self.assertEqual(len(opened(self.opener)), 1)
        self.clock.now += IDLE - MARGIN
        self.use()
        self.assertEqual([event[0] for event in self.opener.events], ["open", "close", "open"])

    def test_idle_time_counts_from_the_end_of_the_last_request(self):
        with self.link.session():
            self.clock.now += IDLE                 # a long request is not idle time
        self.clock.now += 60
        self.use()
        self.assertEqual(len(opened(self.opener)), 1)

    def test_a_long_conversation_opens_a_new_session_before_the_helper_limit(self):
        self.use()
        for _ in range(int((LIFETIME - MARGIN) // 600) + 1):
            self.clock.now += 600
            self.use()
        self.assertEqual(len(opened(self.opener)), 2)
        self.assertLess(LIFETIME, contract.HOLD_SECONDS - contract.SESSION_SECONDS + 1)

    def test_a_helper_that_ended_is_replaced_and_its_unconfirmed_cleanup_reported_at_the_end(self):
        self.opener.fail_close = True
        self.use()
        self.opener.sessions[0].living = False      # e.g. `ai-desktop stop` elsewhere, or the helper failed
        self.use()                                  # its close fails; the question still goes on, with a new session
        self.opener.fail_close = False
        self.assertEqual([event[0] for event in self.opener.events], ["open", "close", "open"])
        with self.assertRaises(AnalysisDenied) as raised:
            self.link.close()
        self.assertEqual(raised.exception.reason, "cleanup")
        self.assertEqual(self.opener.events[-1], ("close",))   # the new session was still closed
        self.link.close()                                       # reported once

    def test_a_helper_that_ended_with_a_confirmed_cleanup_is_not_reported(self):
        self.use()
        self.opener.sessions[0].living = False
        self.use()
        self.link.close()
        self.assertEqual([event[0] for event in self.opener.events], ["open", "close", "open", "close"])

    def test_a_failed_cleanup_of_a_living_session_is_reported(self):
        self.opener.fail_close = True
        self.use()
        with self.assertRaises(AnalysisDenied) as raised:
            self.link.close()
        self.assertEqual(raised.exception.reason, "cleanup")
        self.assertFalse(self.link.is_open)

    def test_a_failed_opening_keeps_nothing(self):
        self.opener.fail_open = AnalysisDenied("authorization")
        with self.assertRaises(AnalysisDenied):
            self.use()
        self.assertFalse(self.link.is_open)
        self.opener.fail_open = None
        self.use()
        self.assertTrue(self.link.is_open)

    def test_an_interrupted_sudo_prompt_keeps_nothing(self):
        self.opener.fail_open = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.use()
        self.assertFalse(self.link.is_open)

    def test_a_failed_request_keeps_the_session(self):
        with self.assertRaises(AnalysisDenied):
            with self.link.session():
                raise AnalysisDenied("timeout")
        self.use()
        self.assertEqual(len(opened(self.opener)), 1)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.opener, self.clock = Opener(), Clock()
        self.link = Link(self.opener, clock=self.clock)
        with self.link.session():
            pass
        self.desktop, self.peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(self.desktop.close)
        self.addCleanup(self.peer.close)

    def test_a_kept_connection_is_given_back(self):
        self.link.keep(self.desktop, "endpoint")
        self.assertIs(self.link.take("endpoint"), self.desktop)
        self.assertIsNone(self.link.take("endpoint"))        # it belongs to the caller until kept again

    def test_a_connection_the_service_closed_is_not_reused(self):
        self.link.keep(self.desktop, "endpoint")
        self.peer.close()
        self.assertIsNone(self.link.take("endpoint"))
        self.assertEqual(self.desktop.fileno(), -1)

    def test_a_connection_with_unexpected_data_is_not_reused(self):
        self.link.keep(self.desktop, "endpoint")
        self.peer.send(b"x")
        self.assertIsNone(self.link.take("endpoint"))
        self.assertEqual(self.desktop.fileno(), -1)

    def test_a_replaced_endpoint_is_not_reused(self):
        self.link.keep(self.desktop, "endpoint")
        self.assertIsNone(self.link.take("another endpoint"))
        self.assertEqual(self.desktop.fileno(), -1)

    def test_an_idle_connection_is_not_reused_near_the_service_limit(self):
        self.link.keep(self.desktop, "endpoint")
        self.clock.now += IDLE - MARGIN
        self.assertIsNone(self.link.take("endpoint"))

    def test_without_a_session_nothing_is_kept(self):
        self.link.close()
        self.link.keep(self.desktop, "endpoint")
        self.assertEqual(self.desktop.fileno(), -1)
        self.assertIsNone(self.link.take("endpoint"))

    def test_a_dead_helper_ends_the_connection_too(self):
        self.link.keep(self.desktop, "endpoint")
        self.opener.sessions[0].living = False
        self.assertIsNone(self.link.take("endpoint"))

    def test_closing_the_conversation_closes_the_connection(self):
        self.link.keep(self.desktop, "endpoint")
        self.link.close()
        self.assertEqual(self.desktop.fileno(), -1)


class RealHelperTests(unittest.TestCase):
    """The real `Session` on a real process and pipes: a helper that ends without `SESSION1 STOPPED` is a cleanup
    failure, never a quiet success (regression)."""

    def opener(self, script):
        @contextmanager
        def open_session(**kwargs):
            process = subprocess.Popen([sys.executable, "-I", "-c", script], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            session = daily.Session(process)
            try:
                session.start()
                yield session
            finally:
                session.close()
        return open_session

    READY = "import os, sys; os.write(1, b'SESSION1 READY\\n'); "

    def test_a_helper_that_exits_without_stopped_is_reported(self):
        with self.assertRaises(AnalysisDenied) as raised:
            with held(self.opener(self.READY + "sys.exit(7)")) as link:
                with link.session():
                    pass
                link._session.process.wait(timeout=10)       # it has ended: no proof of its cleanup
        self.assertEqual(raised.exception.reason, "cleanup")

    def test_a_helper_that_confirms_its_cleanup_ends_quietly(self):
        script = self.READY + "sys.stdin.buffer.read(); os.write(1, b'SESSION1 STOPPED\\n')"
        with held(self.opener(script)) as link:
            with link.session():
                pass


class HeldTests(unittest.TestCase):
    def test_requests_inside_find_the_link_and_it_closes_with_ctrl_c_ignored(self):
        opener, seen = Opener(), []
        original = Link.close

        def close(link):
            if link.is_open:                       # the closing that ends a session, not the empty one before opening
                seen.append(signal.getsignal(signal.SIGINT))
            original(link)
        self.assertIsNone(link_module.current())
        with patch.object(Link, "close", close):
            with held(opener) as link:
                self.assertIs(link_module.current(), link)
                with link.session():
                    pass
        self.assertIsNone(link_module.current())
        self.assertEqual(seen, [signal.SIG_IGN])
        self.assertEqual(opener.events[-1], ("close",))
        self.assertIsNot(signal.getsignal(signal.SIGINT), signal.SIG_IGN)


def approval():
    gate = AnalysisConsent(None, question="Pytanie", web=True)
    return gate.authorize(None, question="Pytanie", web=True, sha256=gate.sha256, nonce=gate.nonce)


class SendOnceTests(unittest.TestCase):
    """`send_once` reuses the conversation's connection and never repeats a request the service may have taken."""

    def setUp(self):
        self.opener, self.clock = Opener(), Clock()
        self.link = Link(self.opener, clock=self.clock)
        self.token = link_module._CURRENT.set(self.link)
        self.addCleanup(link_module._CURRENT.reset, self.token)
        with self.link.session():
            pass
        self.connections = []
        for name, value in (("account_ready", True),):
            self.enterContext(patch.object(service.e2_account, name, return_value=value))
        self.enterContext(patch.object(service, "endpoint", return_value=(1, 2)))
        self.enterContext(patch.object(service, "_open_connection", side_effect=self.connect))
        self.sent = self.enterContext(patch.object(service, "send_message"))
        self.received = self.enterContext(patch.object(service, "receive_message", side_effect=self.respond))
        self.failure = None

    def connect(self, uid, before):
        desktop, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(desktop.close)
        self.addCleanup(peer.close)
        self.connections.append((desktop, peer))
        return desktop

    def respond(self, conn, **kwargs):
        if self.failure is not None:
            raise self.failure
        return contract.result_packet(self.current.sha256, text="Odpowiedź", mode="web")

    def send(self):
        self.current = approval()
        return service.send_once(self.current)

    def test_the_second_request_uses_the_same_connection(self):
        self.assertEqual(self.send(), "Odpowiedź")
        self.assertEqual(self.send(), "Odpowiedź")
        self.assertEqual(len(self.connections), 1)
        self.assertEqual([call.args[0] for call in self.sent.call_args_list], [self.connections[0][0]] * 2)
        self.assertEqual(self.sent.call_args.args[1], self.current.payload.encode())

    def test_without_a_conversation_every_request_has_its_own_connection(self):
        link_module._CURRENT.set(None)
        self.send()
        self.send()
        self.assertEqual(len(self.connections), 2)
        self.assertTrue(all(desktop.fileno() == -1 for desktop, _ in self.connections))

    def test_a_failed_response_closes_the_connection_and_the_next_request_opens_one(self):
        self.send()
        self.failure = AnalysisDenied("timeout")
        with self.assertRaises(AnalysisDenied):
            self.send()
        self.assertEqual(self.connections[0][0].fileno(), -1)     # closing it cancels the request in the service
        self.failure = None
        self.send()
        self.assertEqual(len(self.connections), 2)

    def test_an_interrupt_closes_the_connection(self):
        self.send()
        self.failure = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.send()
        self.assertEqual(self.connections[0][0].fileno(), -1)
        self.assertIsNone(self.link.take((1, 2)))

    def test_a_refusal_in_a_whole_response_keeps_the_connection(self):
        self.send()
        packet = contract.result_packet
        self.received.side_effect = lambda conn, **kwargs: packet(self.current.sha256, reason="rate-limit", mode="web")
        with self.assertRaises(AnalysisDenied) as raised:
            self.send()
        self.assertEqual(raised.exception.reason, "rate-limit")
        self.received.side_effect = self.respond
        self.send()
        self.assertEqual(len(self.connections), 1)

    def test_a_kept_connection_closed_before_sending_is_replaced_once(self):
        self.send()
        calls = []

        def send_message(conn, body, **kwargs):
            calls.append(conn)
            if len(calls) == 1:
                raise AnalysisDenied("endpoint")      # the service closed it: nothing was taken
        self.sent.side_effect = send_message
        self.assertEqual(self.send(), "Odpowiedź")
        self.assertEqual(len(self.connections), 2)
        self.assertEqual(calls, [self.connections[0][0], self.connections[1][0]])
        self.assertEqual(self.received.call_count, 2)

    def test_a_send_timeout_on_a_kept_connection_is_not_repeated(self):
        self.send()
        self.sent.side_effect = AnalysisDenied("timeout")
        with self.assertRaises(AnalysisDenied) as raised:
            self.send()
        self.assertEqual(raised.exception.reason, "timeout")
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.sent.call_count, 2)

    def test_a_failure_on_a_new_connection_is_not_repeated(self):
        self.sent.side_effect = AnalysisDenied("endpoint")
        with self.assertRaises(AnalysisDenied):
            self.send()
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.connections[0][0].fileno(), -1)


if __name__ == "__main__":
    unittest.main()
