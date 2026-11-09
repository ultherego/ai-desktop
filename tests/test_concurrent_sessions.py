"""Several terminals at once: requests share the service profile; only account changes take it alone, and they wait."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from ai_desktop import analysis_sandbox, siwc_auth
from ai_desktop.analysis_contract import AnalysisDenied


class ProfileLockTests(unittest.TestCase):
    def setUp(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(analysis_sandbox, "HOME", home))

    def test_requests_from_several_terminals_hold_the_profile_together(self):
        with analysis_sandbox.profile_lock(shared=True), analysis_sandbox.profile_lock(shared=True, wait=0):
            pass                                                     # the second one did not wait at all

    def test_an_account_change_waits_for_running_requests_and_then_proceeds(self):
        released = threading.Event()

        def request():
            with analysis_sandbox.profile_lock(shared=True):
                released.wait(5)
        thread = threading.Thread(target=request)
        thread.start()
        time.sleep(0.1)
        threading.Timer(0.3, released.set).start()
        started = time.monotonic()
        with analysis_sandbox.profile_lock(wait=5):
            waited = time.monotonic() - started
        thread.join(5)
        self.assertGreaterEqual(waited, 0.2)                         # it waited instead of refusing at once

    def test_waiting_has_a_limit_and_then_says_busy(self):
        with analysis_sandbox.profile_lock():
            with self.assertRaises(AnalysisDenied) as caught:
                with analysis_sandbox.profile_lock(shared=True, wait=0.2):
                    pass
        self.assertEqual(caught.exception.reason, "busy")


class ServerTests(unittest.TestCase):
    """The activated worker serves several terminals' connections at the same time."""

    def setUp(self):
        import json, queue, socket
        from unittest.mock import Mock
        from ai_desktop import analysis_service as service
        from ai_desktop.analysis_framing import receive_message, send_message
        self.service, self.json, self.socket = service, json, socket
        self.receive, self.send = receive_message, send_message
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
        for target, value in (("activation_listener", lambda uid: listener), ("supervisor_ready", lambda: None)):
            self.enterContext(patch.object(service, target, side_effect=value))
        self.enterContext(patch.object(service.select, "select", side_effect=select))
        self.enterContext(patch.object(service.e2_ipc, "_peer_uid"))
        self.enterContext(patch.object(service.os, "geteuid", return_value=2000))

    def connect(self, worker=None):
        """A terminal connects (the first one also starts the worker) and gets READY."""
        server, client = self.socket.socketpair(self.socket.AF_UNIX, self.socket.SOCK_SEQPACKET)
        self.addCleanup(client.close)
        self.pending.put(server)
        if worker is not None:
            worker.start()
        self.assertEqual(self.service.packet(client), self.service.READY)
        return client

    def ask(self, client, results, mark):
        from ai_desktop.analysis_contract import AnalysisConsent
        gate = AnalysisConsent(None, question=mark, web=True)
        auth = gate.authorize(None, question=mark, web=True, sha256=gate.sha256, nonce=gate.nonce)
        self.send(client, auth.payload.encode(), kind="request")
        results.append(self.json.loads(self.receive(client, kind="response", timeout=10,
                                                    partial=lambda body: None))["text"])

    def test_one_terminal_is_answered_while_another_still_waits_for_its_slow_answer(self):
        slow_may_finish = threading.Event()

        def run(action, body, **kwargs):
            if b"slow" in body:
                slow_may_finish.wait(5)
                return "slow answer"
            return "quick answer"
        self.enterContext(patch.object(self.service, "run_client", side_effect=run))
        self.enterContext(patch.object(self.service, "SERVER_IDLE_SECONDS", 0.5))
        worker = threading.Thread(target=self.service.serve, args=(1000,), daemon=True)
        first = self.connect(worker)
        second = self.connect()
        slow, quick = [], []
        slow_thread = threading.Thread(target=self.ask, args=(first, slow, "slow"), daemon=True)
        slow_thread.start()
        time.sleep(0.2)
        self.ask(second, quick, "quick")                             # answered although the slow one still runs
        self.assertEqual((quick, slow), (["quick answer"], []))
        slow_may_finish.set()
        slow_thread.join(5)
        self.assertEqual(slow, ["slow answer"])
        first.close()
        second.close()
        worker.join(5)
        self.assertFalse(worker.is_alive())                          # idle with no connection: the worker left

    def test_a_request_beyond_the_parallel_limit_waits_for_a_place_instead_of_busy(self):
        order, first_running, may_finish = [], threading.Event(), threading.Event()

        def run(action, body, **kwargs):
            if b"first" in body:
                first_running.set()
                may_finish.wait(5)
            order.append(b"first" in body and "first" or "second")
            return "done"
        self.enterContext(patch.object(self.service, "run_client", side_effect=run))
        self.enterContext(patch.object(self.service, "_running", threading.BoundedSemaphore(1)))
        self.enterContext(patch.object(self.service, "SERVER_IDLE_SECONDS", 0.5))
        worker = threading.Thread(target=self.service.serve, args=(1000,), daemon=True)
        one, two = self.connect(worker), self.connect()
        results = []
        threading.Thread(target=self.ask, args=(one, results, "first"), daemon=True).start()
        first_running.wait(5)
        waiting = threading.Thread(target=self.ask, args=(two, results, "second"), daemon=True)
        waiting.start()
        time.sleep(0.4)
        self.assertEqual(order, [])                                  # the second waits, it is not refused
        may_finish.set()
        waiting.join(5)
        self.assertEqual((order, results.count("done")), (["first", "second"], 2))
        one.close()
        two.close()
        worker.join(5)


class TokenLockTests(unittest.TestCase):
    def test_a_second_terminal_waits_for_the_token_instead_of_failing(self):
        store = siwc_auth.AuthStore(Path(self.enterContext(tempfile.TemporaryDirectory())) / "codex")
        with store.locked(create=True):
            pass
        released = threading.Event()

        def first():
            with store.locked():
                released.wait(5)
        thread = threading.Thread(target=first)
        thread.start()
        time.sleep(0.1)
        threading.Timer(0.3, released.set).start()
        with store.locked():
            pass
        thread.join(5)
        with patch.object(siwc_auth, "LOCK_WAIT_SECONDS", 0.2):
            holder = threading.Event()
            done = threading.Event()

            def hold():
                with store.locked():
                    holder.set()
                    done.wait(5)
            other = threading.Thread(target=hold)
            other.start()
            holder.wait(2)
            with self.assertRaises(siwc_auth.AuthError):
                with store.locked():
                    pass
            done.set()
            other.join(5)


if __name__ == "__main__":
    unittest.main()
