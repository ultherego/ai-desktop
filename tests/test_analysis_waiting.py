"""Many terminals at once: a wait for a free place, the service's pre-work, the worker's lifetime and the session
cleanup never cost a terminal its answer. Fakes only: no systemd, sudo or model."""
import os as _os
_os.environ.setdefault("AI_DESKTOP_MODEL_CATALOG", "off")
import io
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, Mock, patch

from ai_desktop import (analysis_contract as contract, analysis_framing as framing, analysis_service as service,
                        analysis_session as client, analysis_session_admin as admin, answer_stream,
                        terminal_progress as progress)
from ai_desktop.analysis_contract import AnalysisConsent, AnalysisDenied

ROOT = Path(__file__).resolve().parents[1]


def approval(question="Pytanie"):
    gate = AnalysisConsent(None, question=question, web=True)
    return gate.authorize(None, question=question, web=True, sha256=gate.sha256, nonce=gate.nonce)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def pair():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    left.settimeout(15)                 # a broken test fails instead of hanging
    right.settimeout(15)
    return left, right


class RenewedDeadlineTests(unittest.TestCase):
    """Item 1: status frames renew the terminal's wait, within a finite ceiling."""

    def exchange(self, frames, *, ceiling, renew=10, step=8):
        left, right = pair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        for _ in range(frames):
            framing.send_partial(left, b"Squeued:0")
        framing.send_message(left, b"final", kind="response")
        clock = Clock()

        def seen(body):
            clock.now += step                      # each frame arrives `step` seconds later
            return renew
        return framing.receive_message(right, kind="response", timeout=10, clock=clock, partial=seen, ceiling=ceiling)

    def test_status_frames_move_the_deadline_while_the_request_waits(self):
        self.assertEqual(self.exchange(6, ceiling=100), b"final")     # 48 s in all, beyond the first 10 s

    def test_without_a_ceiling_the_single_deadline_stays(self):
        with self.assertRaises(AnalysisDenied) as caught:
            self.exchange(6, ceiling=None)
        self.assertEqual(caught.exception.reason, "timeout")

    def test_the_ceiling_bounds_every_renewal(self):
        with self.assertRaises(AnalysisDenied) as caught:
            self.exchange(6, ceiling=30)
        self.assertEqual(caught.exception.reason, "timeout")

    def test_the_ceiling_is_validated(self):
        left, right = pair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        for kwargs in ({"ceiling": 5}, {"ceiling": framing.MAX_CEILING + 1}, {"ceiling": float("inf")},
                       {"ceiling": 100, "partial": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AnalysisDenied):
                framing.receive_message(right, kind="response", timeout=10,
                                        **{"partial": lambda body: None, **kwargs})

    def test_the_longest_wait_fits_the_framing_limits(self):
        for effort in (None, "high", "max"):
            self.assertLessEqual(service.answer_seconds(effort), 1200)
            self.assertLessEqual(service.wait_ceiling(effort), framing.MAX_CEILING)
            self.assertGreater(service.wait_ceiling(effort), service.answer_seconds(effort) + service.QUEUE_SECONDS)
        framing._settings("response", service.answer_seconds("max"))


class PartialLimitTests(unittest.TestCase):
    """Item 6: the frame cap covers the longest answer and wait; their bytes are bounded on their own."""

    def test_the_frame_cap_covers_the_longest_wait_at_the_live_rate(self):
        heartbeats = service.QUEUE_SECONDS / service.QUEUE_HEARTBEAT
        texts = service.wait_ceiling("max") / answer_stream.INTERVAL
        self.assertGreater(framing.MAX_PARTIALS, 2 * (heartbeats + texts))
        # every frame of that many at the full size would still pass only within the byte bound
        self.assertGreaterEqual(framing.MAX_PARTIAL_STREAM, answer_stream.MAX_PARTIAL_TOTAL * 4)

    def test_the_byte_bound_ends_a_flood_of_frames(self):
        left, right = pair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        with patch.object(framing, "MAX_PARTIAL_STREAM", 3 * (framing.HEADER.size + 100)):
            for _ in range(4):
                framing.send_partial(left, b"T" * 100)
            framing.send_message(left, b"final", kind="response")
            with self.assertRaises(AnalysisDenied) as caught:
                framing.receive_message(right, kind="response", partial=lambda body: None)
        self.assertEqual(caught.exception.reason, "input")

    def test_a_long_answer_with_many_frames_still_arrives(self):
        left, right = pair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        count = 5000                                             # beyond the old cap of 4096 frames

        def sender():
            for _ in range(count):
                framing.send_partial(left, b"Tx", timeout=10)
            framing.send_message(left, b"final", kind="response")
        thread = threading.Thread(target=sender, daemon=True)
        thread.start()
        frames = []
        self.assertEqual(framing.receive_message(right, kind="response", partial=frames.append, timeout=30), b"final")
        thread.join(5)
        self.assertEqual(len(frames), count)


class PreWorkTests(unittest.TestCase):
    """Item 2: the terminal's wait covers the service's work before its own model-call budget."""

    def test_the_wait_covers_the_profile_lock_compatibility_and_login_checks(self):
        from ai_desktop import analysis_sandbox
        pre_work = analysis_sandbox.PROFILE_WAIT_SECONDS + 20 + 20          # profile lock, compatibility, login status
        self.assertGreater(service.PREPARE_SECONDS, pre_work)
        for effort in (None, "high", "xhigh", "max"):
            self.assertEqual(service.answer_seconds(effort),
                             contract.request_seconds(effort) + contract.SERVICE_MARGIN + service.PREPARE_SECONDS)


class LiveTextTests(unittest.TestCase):
    def test_queued_and_started_renew_and_restart_is_remembered_even_without_a_display(self):
        live = service._live_text(None, None, 77)
        self.assertEqual(live(b"Squeued:0"), 77)
        self.assertEqual(live(b"Sstarted:0"), 77)
        self.assertIsNone(live(b"Ssearch:1"))
        self.assertFalse(live.restart)
        self.assertIsNone(live(b"Srestart:0"))
        self.assertTrue(live.restart)

    def test_the_display_gets_the_fixed_codes_and_a_bad_frame_never_renews(self):
        steps = []
        live = service._live_text(None, lambda code, number: steps.append((code, number)), 5)
        self.assertEqual(live(b"Squeued:0"), 5)
        self.assertIsNone(live(b"Smodel text:0"))
        self.assertEqual(steps, [("queued", 0)])

    def test_a_display_failure_still_renews(self):
        live = service._live_text(None, lambda code, number: 1 / 0, 5)
        self.assertEqual(live(b"Squeued:0"), 5)
        self.assertEqual(live(b"Squeued:0"), 5)


class IndicatorTests(unittest.TestCase):
    def test_waiting_for_a_place_is_shown_and_started_brings_the_phase_back(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True))
        stack.enter_context(patch.object(progress.os, "isatty", return_value=True))
        stack.enter_context(patch.object(progress.os, "get_terminal_size", return_value=os.terminal_size((120, 24))))
        stack.enter_context(patch.object(progress.threading, "Thread"))
        stack.enter_context(patch.object(progress.time, "monotonic", return_value=10.0))

        class TTY(io.StringIO):
            def isatty(self):
                return True

            def fileno(self):
                return 1
        out = TTY()
        indicator = progress.WaitingIndicator(stream=out)
        indicator.phase("assistant")
        indicator.status("queued", 0)
        self.assertIn("Waiting for a free place", out.getvalue().rsplit(progress.CLEAR_LINE, 1)[1])
        indicator.status("started", 0)
        self.assertIn("Analyzing the question", out.getvalue().rsplit(progress.CLEAR_LINE, 1)[1])
        indicator.status("restart", 0)
        self.assertIn("Waiting for a free place", out.getvalue().rsplit(progress.CLEAR_LINE, 1)[1])
        with self.assertRaises(ValueError):
            indicator.status("model text", 0)
        indicator.stop()

    def test_every_service_code_is_known_to_the_indicator(self):
        for code in answer_stream.STATUS:
            self.assertTrue(code in progress.STATUS_LABELS or code in progress.PHASE_STATUS, code)


class QueueTests(unittest.TestCase):
    """Item 1, service side: a request beyond PARALLEL_REQUESTS reports its wait and then runs."""

    def setUp(self):
        self.enterContext(patch.object(service, "supervisor_ready"))
        self.enterContext(patch.object(service, "QUEUE_HEARTBEAT", 0.1))
        self.slots = threading.BoundedSemaphore(1)
        self.enterContext(patch.object(service, "_running", self.slots))
        self.enterContext(patch.object(service, "run_client", return_value="Odpowiedź"))
        self.desktop, self.server = pair()
        self.addCleanup(self.desktop.close)
        self.addCleanup(self.server.close)
        self.worker = service._Worker()
        self.addCleanup(self.worker.close)

    def serve(self):
        result = []
        thread = threading.Thread(target=lambda: result.append(service._serve(self.server, 1000, self.worker)),
                                  daemon=True)
        self.current = approval()
        framing.send_message(self.desktop, self.current.payload.encode(), kind="request")
        thread.start()
        return thread, result

    def test_a_waiting_request_tells_the_terminal_and_runs_when_a_place_frees(self):
        self.slots.acquire()                                     # four other terminals' requests run
        thread, result = self.serve()
        threading.Timer(0.5, self.slots.release).start()
        frames = []
        response = framing.receive_message(self.desktop, kind="response", timeout=10, partial=frames.append)
        thread.join(5)
        self.assertEqual(json.loads(response)["text"], "Odpowiedź")
        self.assertIn(b"Squeued:0", frames)
        self.assertGreaterEqual(frames.count(b"Squeued:0"), 2)  # repeated while it waits
        self.assertEqual(frames[-1], b"Sstarted:0")
        self.assertEqual(result, [True])

    def test_a_request_that_got_a_place_at_once_sends_no_status(self):
        thread, _ = self.serve()
        frames = []
        framing.receive_message(self.desktop, kind="response", timeout=10, partial=frames.append)
        thread.join(5)
        self.assertEqual(frames, [])

    def test_the_wait_has_a_limit(self):
        self.slots.acquire()
        self.addCleanup(self.slots.release)
        with patch.object(service, "QUEUE_SECONDS", 0.3):
            thread, _ = self.serve()
            response = framing.receive_message(self.desktop, kind="response", timeout=10, partial=lambda body: None)
            thread.join(5)
        with self.assertRaises(AnalysisDenied) as caught:
            contract.read_result(response, self.current.sha256, mode="web")
        self.assertEqual(caught.exception.reason, "busy")

    def test_an_ending_worker_declines_a_waiting_request_unstarted(self):
        self.slots.acquire()
        self.addCleanup(self.slots.release)
        thread, result = self.serve()
        time.sleep(0.3)
        self.worker.drain()
        thread.join(5)
        self.server.close()                                       # _connection closes it after a False
        frames = []
        with self.assertRaises(AnalysisDenied) as caught:
            framing.receive_message(self.desktop, kind="response", timeout=10, partial=frames.append)
        self.assertEqual(caught.exception.reason, "endpoint")
        self.assertEqual(frames[-1], b"Srestart:0")
        self.assertEqual(result, [False])
        service.run_client.assert_not_called()


class SendOnceTests(unittest.TestCase):
    """The terminal renews its wait and sends a request declined unstarted to the next worker."""

    def setUp(self):
        self.enterContext(patch.object(service.e2_account, "account_ready", return_value=True))
        self.enterContext(patch.object(service, "endpoint", return_value=(1, 2)))
        self.connections = []
        self.enterContext(patch.object(service, "_open_connection", side_effect=self.connect))
        self.sent = self.enterContext(patch.object(service, "send_message"))
        self.replies = []
        self.received = self.enterContext(patch.object(service, "receive_message", side_effect=self.respond))
        from ai_desktop import conversation_link
        token = conversation_link._CURRENT.set(None)
        self.addCleanup(conversation_link._CURRENT.reset, token)

    def connect(self, uid, before):
        desktop, peer = pair()
        self.addCleanup(desktop.close)
        self.addCleanup(peer.close)
        self.connections.append(desktop)
        return desktop

    def respond(self, conn, **kwargs):
        reply = self.replies.pop(0)
        return reply(kwargs)

    def answer(self, kwargs):
        return contract.result_packet(self.current.sha256, text="Odpowiedź", mode="web")

    def test_the_wait_is_renewable_and_bounded(self):
        self.current = approval()
        self.replies = [self.answer]
        self.assertEqual(service.send_once(self.current), "Odpowiedź")
        kwargs = self.received.call_args.kwargs
        self.assertEqual(kwargs["timeout"], service.answer_seconds(None))
        self.assertEqual(kwargs["ceiling"], service.wait_ceiling(None))
        self.assertEqual(kwargs["partial"](b"Squeued:0"), service.answer_seconds(None))

    def test_a_request_declined_by_an_ending_worker_is_sent_again_to_the_next(self):
        self.current = approval()

        def declined(kwargs):
            kwargs["partial"](b"Srestart:0")
            raise AnalysisDenied("endpoint")
        self.replies = [declined, self.answer]
        steps = []
        self.assertEqual(service.send_once(self.current, on_status=lambda *step: steps.append(step)), "Odpowiedź")
        self.assertEqual(len(self.connections), 2)
        self.assertEqual(self.sent.call_count, 2)
        self.assertEqual(steps, [("restart", 0)])
        self.assertEqual(self.connections[0].fileno(), -1)

    def test_a_closed_connection_without_restart_is_never_repeated(self):
        self.current = approval()

        def closed(kwargs):
            raise AnalysisDenied("endpoint")
        self.replies = [closed, self.answer]
        with self.assertRaises(AnalysisDenied) as caught:
            service.send_once(self.current)
        self.assertEqual(caught.exception.reason, "endpoint")
        self.assertEqual(self.sent.call_count, 1)

    def test_resending_is_bounded(self):
        self.current = approval()

        def declined(kwargs):
            kwargs["partial"](b"Srestart:0")
            raise AnalysisDenied("endpoint")
        self.replies = [declined] * (service.MAX_RESENDS + 1) + [self.answer]
        with self.assertRaises(AnalysisDenied):
            service.send_once(self.current)
        self.assertEqual(self.sent.call_count, service.MAX_RESENDS + 1)


class ConnectTests(unittest.TestCase):
    """Items 3 and 5: the terminal waits for the next worker and retries a full backlog."""

    def test_while_the_worker_is_replaced_the_terminal_waits_and_shows_it(self):
        conn = object()
        attempts = [service._Restart(), service._Restart(), conn]

        def opened(uid, before):
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        steps, pauses = [], []
        with patch.object(service, "_open_connection", side_effect=opened):
            self.assertIs(service._connect(1000, "e", lambda *step: steps.append(step), sleep=pauses.append), conn)
        self.assertEqual(steps, [("restart", 0)])
        self.assertEqual(pauses, [service.RESTART_PAUSE] * 2)

    def test_the_wait_for_the_next_worker_is_bounded(self):
        clock = Clock()

        def opened(uid, before):
            clock.now += 100
            raise service._Restart()
        with patch.object(service, "_open_connection", side_effect=opened), self.assertRaises(AnalysisDenied) as caught:
            service._connect(1000, "e", None, clock=clock, sleep=lambda seconds: None)
        self.assertEqual(caught.exception.reason, "timeout")

    def fake_socket(self, failures):
        made = []

        class Fake:
            def __init__(self, *args):
                made.append(self)
                self.closed = False

            def settimeout(self, value):
                pass

            def connect(self, path):
                if failures:
                    failures.pop()
                    raise BlockingIOError(11, "full backlog")

            def close(self):
                self.closed = True
        return Fake, made

    def test_a_full_backlog_is_retried_within_the_connect_window(self):
        Fake, made = self.fake_socket([1, 1, 1])
        with patch.object(service.socket, "socket", Fake), patch.object(service, "manager_peer"), \
                patch.object(service, "endpoint", return_value="e"), \
                patch.object(service, "packet", return_value=service.READY):
            conn = service._open_connection(1000, "e", sleep=lambda seconds: None)
        self.assertIs(conn, made[-1])
        self.assertEqual(len(made), 4)
        self.assertTrue(all(fake.closed for fake in made[:-1]))

    def test_a_backlog_that_stays_full_ends_as_endpoint(self):
        Fake, made = self.fake_socket([1] * 1000)
        clock = Clock()

        def sleep(seconds):
            clock.now += 1
        with patch.object(service.socket, "socket", Fake), self.assertRaises(AnalysisDenied) as caught:
            service._open_connection(1000, "e", clock=clock, sleep=sleep)
        self.assertEqual(caught.exception.reason, "endpoint")
        self.assertLessEqual(len(made), service.CONNECT_SECONDS + 1)
        self.assertTrue(all(fake.closed for fake in made))

    def test_restart_from_the_worker_is_its_own_signal(self):
        Fake, _ = self.fake_socket([])
        with patch.object(service.socket, "socket", Fake), patch.object(service, "manager_peer"), \
                patch.object(service, "endpoint", return_value="e"), \
                patch.object(service, "packet", return_value=service.RESTART), self.assertRaises(service._Restart):
            service._open_connection(1000, "e")

    def test_the_socket_backlog_takes_many_terminals(self):
        unit = (ROOT / "packaging/systemd/ai-desktop-analysis@.socket").read_text()
        self.assertGreaterEqual(int(re.search(r"^Backlog=(\d+)$", unit, re.M).group(1)), 64)


class WorkerLifetimeTests(unittest.TestCase):
    """Item 3: the worker leaves before RuntimeMaxSec without cutting off an answer."""

    def test_the_lifetime_matches_the_unit_and_leaves_room_for_the_longest_answer(self):
        unit = (ROOT / "packaging/systemd/ai-desktop-analysis@.service").read_text()
        self.assertEqual(int(re.search(r"^RuntimeMaxSec=(\d+)$", unit, re.M).group(1)), service.WORKER_SECONDS)
        self.assertGreaterEqual(service.HARD_DRAIN_SECONDS, service.answer_seconds("max") + 60)
        self.assertGreater(service.SOFT_DRAIN_SECONDS, service.HARD_DRAIN_SECONDS)
        self.assertGreater(service.WORKER_SECONDS, service.SOFT_DRAIN_SECONDS)
        self.assertGreater(service.RESTART_WAIT_SECONDS, service.HARD_DRAIN_SECONDS)

    def test_an_idle_connection_ends_when_the_worker_drains(self):
        worker = service._Worker()
        self.addCleanup(worker.close)
        desktop, server = pair()
        self.addCleanup(desktop.close)
        self.addCleanup(server.close)
        result = []
        thread = threading.Thread(target=lambda: result.append(worker.next_request(server, 60)), daemon=True)
        thread.start()
        time.sleep(0.2)
        started = time.monotonic()
        worker.drain()
        thread.join(5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result, [False])
        with self.assertRaises(OSError):
            desktop.send(b"x")                                    # a later request fails at once: the desktop reconnects

    def test_a_request_that_arrived_as_the_worker_ends_is_answered_with_restart(self):
        worker = service._Worker()
        self.addCleanup(worker.close)
        desktop, server = pair()
        self.addCleanup(desktop.close)
        self.addCleanup(server.close)
        worker.drain()
        desktop.send(b"request header")
        self.assertFalse(worker.next_request(server, 1))
        self.assertEqual(worker.busy, 0)
        frames = []
        with self.assertRaises(AnalysisDenied):
            framing.receive_message(desktop, kind="response", timeout=2, partial=frames.append)
        self.assertEqual(frames, [b"Srestart:0"])

    def test_a_request_is_counted_as_busy_until_its_response(self):
        worker = service._Worker()
        self.addCleanup(worker.close)
        desktop, server = pair()
        self.addCleanup(desktop.close)
        self.addCleanup(server.close)
        desktop.send(b"request header")
        self.assertTrue(worker.next_request(server, 1))
        self.assertFalse(worker.leave_if_idle())                  # never leaves under a request
        worker.done()
        self.assertTrue(worker.leave_if_idle())


class ServeLifetimeTests(unittest.TestCase):
    """serve() itself: an idle moment near the end lets it leave; at the hard limit new connections come back later."""

    def setUp(self):
        self.pending = queue.Queue()
        listener = Mock()
        listener.__enter__ = Mock(return_value=listener)
        listener.__exit__ = Mock(return_value=False)
        listener.accept = Mock(side_effect=lambda: (self.pending.get_nowait(), None))
        self.listener = listener
        real_select = service.select.select

        def select(readers, writers, errors, timeout=None):
            if listener in readers:
                if self.pending.empty():
                    time.sleep(min(timeout or 0, 0.05))
                    return [], [], []
                return [listener], [], []
            return real_select(readers, writers, errors, timeout)
        self.enterContext(patch.object(service, "activation_listener", side_effect=lambda uid: listener))
        self.enterContext(patch.object(service, "supervisor_ready"))
        self.enterContext(patch.object(service.select, "select", side_effect=select))
        self.enterContext(patch.object(service.e2_ipc, "_peer_uid"))
        self.enterContext(patch.object(service.os, "geteuid", return_value=2000))
        self.enterContext(patch.object(service, "SERVER_IDLE_SECONDS", 30))

    def connect(self):
        server, desktop = pair()
        self.addCleanup(desktop.close)
        self.pending.put(server)
        return desktop

    def test_near_the_end_the_worker_leaves_after_the_running_answer_and_closes_idle_connections(self):
        may_finish = threading.Event()

        def run(action, body, **kwargs):
            may_finish.wait(5)
            return "Odpowiedź"
        self.enterContext(patch.object(service, "run_client", side_effect=run))
        self.enterContext(patch.object(service, "WORKER_SECONDS", service.SOFT_DRAIN_SECONDS + 0.5))
        desktop = self.connect()
        worker = threading.Thread(target=service.serve, args=(1000,), daemon=True)
        worker.start()
        self.assertEqual(service.packet(desktop), service.READY)
        current = approval()
        framing.send_message(desktop, current.payload.encode(), kind="request")
        time.sleep(1)                                             # the soft limit passes while the answer runs
        self.assertTrue(worker.is_alive())
        may_finish.set()
        response = framing.receive_message(desktop, kind="response", timeout=10, partial=lambda body: None)
        self.assertEqual(json.loads(response)["text"], "Odpowiedź")  # the answer was not cut off
        worker.join(5)
        self.assertFalse(worker.is_alive())                         # then it left at once
        self.assertEqual(desktop.recv(10), b"")                     # its kept connection ended: the next one reconnects

    def test_at_the_hard_limit_a_new_connection_is_told_to_come_back(self):
        may_finish = threading.Event()

        def run(action, body, **kwargs):
            may_finish.wait(5)
            return "Odpowiedź"
        self.enterContext(patch.object(service, "run_client", side_effect=run))
        self.enterContext(patch.object(service, "SOFT_DRAIN_SECONDS", service.HARD_DRAIN_SECONDS))
        self.enterContext(patch.object(service, "WORKER_SECONDS", service.HARD_DRAIN_SECONDS + 0.5))
        busy = self.connect()
        worker = threading.Thread(target=service.serve, args=(1000,), daemon=True)
        worker.start()
        self.assertEqual(service.packet(busy), service.READY)
        current = approval()
        framing.send_message(busy, current.payload.encode(), kind="request")
        time.sleep(1)                                             # the hard limit passes while the answer runs
        late = self.connect()
        self.assertEqual(service.packet(late), service.RESTART)  # the terminal waits for the next worker
        may_finish.set()
        response = framing.receive_message(busy, kind="response", timeout=10, partial=lambda body: None)
        self.assertEqual(json.loads(response)["text"], "Odpowiedź")
        worker.join(5)
        self.assertFalse(worker.is_alive())


class LimitTests(unittest.TestCase):
    """Item 9: many terminals keep a session and a connection for hours."""

    def test_connections_and_sessions_are_many_but_bounded(self):
        self.assertEqual(service.MAX_CONNECTIONS, 64)
        self.assertEqual(admin.MAX_SESSIONS, 64)
        self.assertGreaterEqual(service.MAX_CONNECTIONS, admin.MAX_SESSIONS)  # every session's terminal is served
        unit = (ROOT / "packaging/systemd/ai-desktop-analysis@.service").read_text()
        tasks = int(re.search(r"^TasksMax=(\d+)$", unit, re.M).group(1))
        self.assertGreaterEqual(tasks, 384 + service.MAX_CONNECTIONS)    # one thread per connection on top


class FakeSystemd:
    def __init__(self, active=()):
        self.active, self.calls, self.fail_stop = set(active), [], 0

    def properties(self, unit):
        return {"ActiveState": "active" if unit in self.active else "inactive", "MainPID": "0", "LoadState": "loaded"}

    def command(self, *args):
        self.calls.append(args[1:])
        if args[1] == "start":
            self.active.update(args[2:])
        elif args[1] == "stop":
            if self.fail_stop:
                self.fail_stop -= 1
                raise AnalysisDenied("endpoint")
            self.active.difference_update(args[2:])
        return b""


class UnclaimedUnitsTests(unittest.TestCase):
    """Item 4: units left running with no claim never make every later terminal busy."""

    def setUp(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = temp / "sessions"
        self.state.mkdir(mode=0o700)
        self.systemd = FakeSystemd(admin.units(1000))         # a failed cleanup left both units active, no claim

        def cleaned(uid):
            if any(unit in self.systemd.active for unit in admin.units(uid)):
                raise AnalysisDenied("cleanup")

        def unit_policy(uid, inactive=False):
            if inactive and any(unit in self.systemd.active for unit in admin.units(uid)):
                raise AnalysisDenied("busy")
        for target, value in (("STATE", self.state), ("_OWNER", (os.getuid(), os.getgid())),
                              ("state_directory", lambda: None), ("unit_policy", unit_policy),
                              ("endpoint", lambda uid: None), ("cleaned", cleaned),
                              ("properties", self.systemd.properties), ("command", self.systemd.command)):
            self.enterContext(patch.object(admin, target, value))
        self.written = []
        real_write = os.write

        def write(fd, data):
            if fd == 1:
                self.written.append(data)
                return len(data)
            return real_write(fd, data)
        self.enterContext(patch.object(admin.os, "write", side_effect=write))
        self.enterContext(patch.object(admin.select, "select", return_value=([0], [], [])))

    def test_the_next_terminal_stops_the_leftovers_and_opens_its_session(self):
        nonce = "a" * 32
        real_read = os.read
        with patch.object(admin.os, "read", side_effect=lambda fd, n: b"" if fd == 0 else real_read(fd, n)):
            admin.hold(1000, nonce)
        self.assertEqual(self.systemd.calls[:2], [("stop",) + tuple(admin.units(1000)), ("start", admin.units(1000)[0])])
        self.assertEqual(self.written, [admin.READY])

    def test_stop_ends_the_leftovers_without_any_claim(self):
        admin.stop_session(1000)
        self.assertEqual(self.systemd.calls, [("stop",) + tuple(admin.units(1000))])
        self.assertEqual(self.systemd.active, set())
        self.assertEqual(self.written, [admin.STOPPED])

    def test_leftovers_that_cannot_be_stopped_are_still_refused(self):
        self.systemd.fail_stop = 99
        with self.assertRaises(AnalysisDenied):
            admin.stop_session(1000)
        with self.assertRaises(AnalysisDenied):
            admin.hold(1000, "b" * 32)
        self.assertEqual(admin.claims(1000), [])                   # fail-closed: no claim, no start
        self.assertNotIn(("start", admin.units(1000)[0]), self.systemd.calls)

    def test_a_stop_that_fails_once_during_cleanup_is_tried_again(self):
        nonce = "c" * 32
        admin.create_claim(1000, nonce)
        self.systemd.fail_stop = 1
        admin.cleanup(1000, nonce)
        self.assertEqual(self.systemd.active, set())
        self.assertEqual(self.written, [admin.STOPPED])

    def test_running_sessions_are_never_stopped_as_leftovers(self):
        nonce = "d" * 32
        admin.create_claim(1000, nonce)
        self.systemd.active.add(admin.manager_unit(1000, nonce))
        with self.assertRaises(AnalysisDenied) as caught:
            with admin.session_lock(1000):
                admin.stop_unclaimed(1000)
        self.assertEqual(caught.exception.reason, "busy")
        self.assertEqual(self.systemd.calls, [])


class SessionCloseTests(unittest.TestCase):
    """Item 7: the cleanup wait matches the helper's real bound and the sudo process is always reaped."""

    def test_the_wait_outlives_the_helpers_own_cleanup_bound(self):
        command = " ".join(admin.session_command(1000, "a" * 32))
        stop = int(re.search(r"TimeoutStopSec=(\d+)", command).group(1))
        self.assertGreater(client.CLEANUP_SECONDS, stop)
        self.assertGreaterEqual(client.CLEANUP_SECONDS, 30 + 10 + 10)   # lock wait, stop, check

    def test_a_helper_that_never_confirms_is_killed_and_reaped(self):
        script = "import sys, time; print('SESSION1 READY', flush=True); sys.stdin.buffer.read(); time.sleep(60)"
        process = subprocess.Popen([sys.executable, "-B", "-c", script], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        session = client.Session(process)
        session.start()
        with patch.object(client, "CLEANUP_SECONDS", 0.5), self.assertRaises(AnalysisDenied) as caught:
            session.close()
        self.assertEqual(caught.exception.reason, "cleanup")
        self.assertIsNotNone(process.poll())                       # reaped, not left behind
        self.assertTrue(process.stdout.closed)

    def test_a_helper_that_refused_at_start_is_reaped_too(self):
        process = MagicMock()
        process.poll.side_effect = [None, None, 0]
        process.wait.side_effect = subprocess.TimeoutExpired("x", 1)
        session = client.Session(process)
        session.ready = False
        with self.assertRaises(AnalysisDenied):
            session.close()
        process.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class ExcusedWaitTests(unittest.TestCase):
    """Independent review of PR #50: an answer that arrived after a wait for a free place was thrown away because the
    question's approval had expired meanwhile. Time before the model call starts is excused, like a sudo password."""

    def test_the_started_frame_marks_the_start_of_the_answer_time(self):
        from ai_desktop import analysis_service as service
        marks = {"work_from": 0.0}
        receive = service._live_text(None, None, 240, marks)
        receive(b"Squeued:0")
        self.assertEqual(marks["work_from"], 0.0)
        receive(b"Sstarted:0")
        self.assertGreater(marks["work_from"], 0.0)

    def test_the_claude_client_reports_started_right_before_the_model_call(self):
        from test_check_memory import ClaudeRequestTests
        from ai_desktop import analysis_sandbox as sandbox, check_memory
        check_memory.clear()
        self.addCleanup(check_memory.clear)
        seen = []
        case = ClaudeRequestTests()
        case._outcome = None
        with case.fixture() as fixture:
            fixture.bound.fingerprint = "fp-wait"
            sandbox.run_client("analyze", b"synthetic request",
                               status=lambda code, number=0: seen.append((code, list(fixture.events))))
        self.assertEqual(seen[0][0], "started")
        self.assertEqual(seen[0][1][-1], "status")              # after the login check, before the answer call

    def test_the_approval_moves_forward_by_the_excused_wait_within_a_bound(self):
        from unittest.mock import Mock
        from ai_desktop.analysis_contract import QUEUE_EXCUSE_SECONDS, TTL
        from ai_desktop.assistant_flow import AccessSnapshot
        from ai_desktop.permissions import Limits
        from ai_desktop.question_consent import QuestionConsent
        now = [100.0]
        policy = Mock(spec=("recheck", "select", "limits"))
        policy.limits.return_value = Limits()
        snapshot = AccessSnapshot(policy, {"claude": True, "systemd": [], "weather": False},
                                  {"files": [], "systemd": [], "weather": False})
        permit = QuestionConsent("Pytanie", snapshot, clock=lambda: now[0])
        permit.approve()
        now[0] += TTL + 600                                     # 10 minutes in a queue, then a normal answer
        permit.excuse(600)
        permit.check()
        permit.excuse(10 ** 9)                                   # never more than the longest accepted wait
        self.assertLessEqual(permit._expires - now[0], TTL + QUEUE_EXCUSE_SECONDS)
        permit.close()
        before = permit._expires
        permit.excuse(100)                                       # a closed question is never revived
        self.assertEqual(permit._expires, before)
