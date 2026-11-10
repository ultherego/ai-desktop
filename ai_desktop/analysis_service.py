"""Separate systemd activation endpoint for the explicitly approved A+ path."""
import os
import json
import re
import select
import socket
import threading
import time

from .analysis_contract import (CONVERSATION_IDLE_SECONDS, CONVERSATION_SECONDS, SERVICE_MARGIN, request_seconds, AnalysisDenied, Approval, MAX_PACKET, REASONS,
                                digest, parse_request, read_result, result_packet)
from .analysis_sandbox import run_client, transport_supervisor_ready as supervisor_ready
from .analysis_framing import PARTIAL_BYTES, send_message, send_partial, receive_message
from .answer_stream import MAX_PARTIAL_TOTAL, STATUS
from . import activation_socket, e2_account, e2_ipc

DIRECTORY = "/run/ai-desktop-analysis"
READY = b"ANALYSIS2 READY"
RESTART = b"ANALYSIS2 RESTART"     # the worker is near the end of its lifetime: connect again to the next one
REFUSED = b"ANALYSIS2 REFUSED "
MAX_CONTROL = 128
UID = re.compile(r"[1-9][0-9]{0,9}\Z", re.ASCII)


def socket_path(uid):
    if type(uid) is not int or not 0 < uid < 2**32 - 1:
        raise AnalysisDenied("identity")
    return DIRECTORY + "/" + str(uid) + ".sock"


def endpoint(uid):
    return activation_socket.endpoint(socket_path(uid), uid)


def manager_peer(channel, uid):
    return activation_socket.manager_peer(channel, socket_path(uid))


def local_readiness():
    """Metadata-only check. No activation, profile access or native execution."""
    from .analysis_sandbox import runtime_ready
    try:
        uid = os.geteuid()
        if uid == 0 or uid != os.getuid() or not e2_account.account_ready():
            raise AnalysisDenied("identity")
        endpoint(uid)
    except (OSError, ValueError, KeyError):
        raise AnalysisDenied("endpoint") from None
    try:
        runtime_ready()
    except (OSError, ValueError):
        raise AnalysisDenied("runtime") from None


def packet(channel):
    """One small readiness/refusal record; application messages are framed."""
    body, ancillary, flags, _ = channel.recvmsg(MAX_CONTROL + 1, 0)
    if not 0 < len(body) <= MAX_CONTROL or ancillary or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise AnalysisDenied("input")
    return body


def send_packet(channel, body):
    if type(body) is not bytes or not 0 < len(body) <= MAX_CONTROL or channel.send(body) != len(body):
        raise AnalysisDenied("result")


# What the terminal waits for one answer: the model call's own budget (request_seconds) and SERVICE_MARGIN, plus the
# service's work before that budget starts: the shared profile lock (up to PROFILE_WAIT_SECONDS = 30 s), the client
# binding, the offline compatibility check (up to 20 s) and the login status check (up to 20 s), with a margin.
from .analysis_contract import PREPARE_SECONDS   # noqa: E402  (one constant for the terminal and the consent)
# A request waiting for a free place among PARALLEL_REQUESTS tells the terminal so every QUEUE_HEARTBEAT seconds, and
# the terminal's wait is renewed meanwhile; after QUEUE_SECONDS of waiting the service gives up ("busy").
QUEUE_HEARTBEAT = 10
QUEUE_SECONDS = 3600
MAX_RESENDS = 3                 # a request the ending worker declined unstarted is sent again, at most this often
RESTART_PAUSE = 1               # seconds between connection attempts while the worker is replaced
CONNECT_SECONDS = 5             # a connection and its READY; a full backlog (EAGAIN) is retried within it


def answer_seconds(effort):
    """How long the terminal waits for an answer from the moment the request has a place (or is sent)."""
    return request_seconds(effort) + SERVICE_MARGIN + PREPARE_SECONDS


def wait_ceiling(effort):
    """The longest the terminal ever waits for one answer, a wait for a free place included."""
    return answer_seconds(effort) + QUEUE_SECONDS + 60


def _live_text(on_partial, on_status=None, renew=None, marks=None):
    """Decode live frames: `T` + text, `S` + a fixed progress code and count. A bad frame or a display failure only
    ends the preview; without a display the frames are still read. `queued`/`started` return `renew` (seconds the
    terminal waits from now); `restart` is remembered in `receive.restart`."""
    state = {"total": 0, "on": on_partial is not None or on_status is not None}

    def receive(body):
        kind, rest = body[:1], body[1:]
        if kind == b"S":
            try:
                code, _, number = rest.decode("ascii").partition(":")
                if code not in STATUS or not number.isdigit() or len(number) > 2:
                    raise ValueError("status")
            except ValueError:
                state["on"] = False
                return None
            if code == "restart":
                receive.restart = True
            if code == "started" and marks is not None:
                marks["work_from"] = time.monotonic()      # the request has its place: from here the answer counts
            if state["on"] and on_status is not None:
                try:
                    on_status(code, int(number))
                except Exception:           # never lose the final answer to the preview
                    state["on"] = False
            return renew if code in ("queued", "started") else None
        if not state["on"]:
            return None
        state["total"] += len(body)
        try:
            if state["total"] > MAX_PARTIAL_TOTAL + 64 * 1024:
                raise ValueError("partial budget")
            if kind == b"T":
                if on_partial is not None:
                    on_partial(rest.decode("utf-8"))
            else:
                raise ValueError("kind")
        except Exception:                   # never lose the final answer to the preview
            state["on"] = False
        return None
    receive.restart = False
    return receive


def send_once(approval, *, on_partial=None, on_status=None):
    """Send one approved request. `on_partial(text)` may show live answer text and `on_status(code, number)` progress
    (web search, a wait for a free place); the returned answer is what counts."""
    send_once.last_meta = None
    send_once.last_wait = 0.0          # seconds spent waiting for a place or for the next worker (not the answer's time)
    began = time.monotonic()
    marks = {"work_from": began}
    try:
        uid = os.geteuid()
        if (type(approval) is not Approval or os.getuid() != uid or uid == 0
                or digest(approval.payload) != approval.sha256):
            raise AnalysisDenied("consent")
        body = approval.payload.encode("utf-8")
        model_input, _ = parse_request(body)
        parsed = json.loads(model_input)
        mode = parsed["mode"]
        selected = parsed.get("execution")
        provider = selected["provider"] if selected is not None else "claude"
        if provider == "local":
            from .local_provider import complete
            return complete(parsed, approval)
        if not e2_account.account_ready():
            raise AnalysisDenied("consent")
        effort = selected["effort"] if selected else None
        before = endpoint(uid)
        from . import conversation_link
        link = conversation_link.current()
        conn = link.take(before) if link is not None else None
        reused, whole = conn is not None, False
        try:
            for attempt in range(MAX_RESENDS + 1):
                if conn is None:
                    conn = _connect(uid, before, on_status)
                approval.recheck_sources()
                try:
                    send_message(conn, body, kind="request")
                except AnalysisDenied as failed:
                    if not reused or failed.reason != "endpoint":
                        raise
                    # The kept connection was closed in the meantime: the service never took this request (an
                    # unfinished one is discarded), so sending it once on a new connection does not repeat it.
                    conn.close()
                    conn = None
                    conn = _connect(uid, before, on_status)
                    send_message(conn, body, kind="request")
                reused = False
                if attempt:
                    marks["work_from"] = time.monotonic()   # a resend to the next worker: the earlier wait is excused
                live = _live_text(on_partial, on_status, answer_seconds(effort), marks)
                try:
                    response = receive_message(conn, kind="response", timeout=answer_seconds(effort),
                                               partial=live, ceiling=wait_ceiling(effort))
                except AnalysisDenied as failed:
                    # The worker ends its lifetime: it said it did not start this request and closed the connection.
                    # Sending it to the next worker does not repeat it.
                    if failed.reason != "endpoint" or not live.restart or attempt == MAX_RESENDS:
                        raise
                    conn.close()
                    conn = None
                    continue
                whole = True                                         # the connection carries the next request
                break
        finally:
            send_once.last_wait = max(0.0, marks["work_from"] - began)
            if conn is not None:
                if whole and link is not None:
                    link.keep(conn, before)
                else:
                    conn.close()                                     # also cancels a request still running
        result = read_result(response, approval.sha256, mode=mode, provider=provider)
        send_once.last_meta = getattr(result, "meta", None)          # closed model metadata, kept apart from the text
        if send_once.last_meta and ("quota" in send_once.last_meta or "plan" in send_once.last_meta):
            from .account_cli import remember
            remember(provider, (selected or {}).get("account", "default"), send_once.last_meta.get("quota"),
                     plan=send_once.last_meta.get("plan"))
        return str(result)                                           # callers get the plain text they always got
    except socket.timeout:
        raise AnalysisDenied("timeout") from None
    except (OSError, ValueError, TypeError, KeyError):
        raise AnalysisDenied("endpoint") from None


class _Restart(Exception):
    """The worker is ending its lifetime and takes no new connection; the next one starts when it has left."""


def _connect(uid, before, on_status=None, *, clock=time.monotonic, sleep=time.sleep):
    """A new connection; while the worker is being replaced, wait for the next one (bounded), showing the wait."""
    deadline, told = clock() + RESTART_WAIT_SECONDS, False
    while True:
        try:
            return _open_connection(uid, before)
        except _Restart:
            if not told and on_status is not None:
                told = True
                try:
                    on_status("restart", 0)
                except Exception:
                    pass
            if clock() >= deadline:
                raise AnalysisDenied("timeout") from None
            sleep(RESTART_PAUSE)


def _open_connection(uid, before, *, clock=time.monotonic, sleep=time.sleep):
    """A new authenticated connection to this user's service, after its READY. While a worker starts, a full listen
    backlog makes connect() fail at once with EAGAIN on a UNIX socket; that is retried within CONNECT_SECONDS."""
    deadline = clock() + CONNECT_SECONDS
    while True:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            conn.settimeout(CONNECT_SECONDS)
            try:
                conn.connect(socket_path(uid))
            except BlockingIOError:
                if clock() + 0.05 >= deadline:
                    raise AnalysisDenied("endpoint") from None
                conn.close()
                sleep(0.05)
                continue
            conn.settimeout(max(0.5, deadline - clock()))
            manager_peer(conn, uid)
            if endpoint(uid) != before:
                raise AnalysisDenied("endpoint")
            try:
                ready = packet(conn)
            except socket.timeout:
                # Queued in the backlog while the old worker drains and the next one starts: wait for it (bounded by
                # _connect), never report a timeout for a question that has not been sent.
                raise _Restart() from None
            if ready == RESTART:
                raise _Restart()
            if ready != READY:
                if ready.startswith(REFUSED):
                    reason = ready[len(REFUSED):].decode("ascii")
                    if reason in REASONS:
                        raise AnalysisDenied(reason)
                raise AnalysisDenied("endpoint")
            return conn
        except BaseException:
            conn.close()
            raise


PARTIAL_TIMEOUT = 2                     # seconds a live frame may wait for room before the preview stops


class _live_sender:
    """Forward live answer text and web progress in bounded frames. A slow or closed terminal ends the preview, never
    the request: closure is still noticed by `cancelled`."""

    def __init__(self, conn):
        self._conn, self._total, self._on = conn, 0, True

    def _send(self, body):
        try:
            send_partial(self._conn, body, timeout=PARTIAL_TIMEOUT)
        except AnalysisDenied:
            self._on = False                    # nothing of this frame was queued: the response still arrives whole

    def text(self, text):
        if not self._on or type(text) is not str or not text:
            return
        body = text.encode("utf-8")
        self._total += len(body)
        if self._total > MAX_PARTIAL_TOTAL:
            self._on = False
            return
        offset, room = 0, PARTIAL_BYTES - 1     # the kind byte takes one
        while offset < len(body) and self._on:
            end = min(offset + room, len(body))
            while end < len(body) and (body[end] & 0xC0) == 0x80:
                end -= 1                            # never split a UTF-8 character between frames
            self._send(b"T" + body[offset:end])
            offset = end

    def status(self, code, number=0):
        if self._on and code in STATUS and type(number) is int and 0 <= number <= 99:
            self._send(b"S" + ("%s:%d" % (code, number)).encode("ascii"))

    def status_or_start(self, code, number=0):
        """`started` marks the start of the model call (it renews the terminal's wait and ends the excused time, so it
        is sent even when the preview has stopped); other codes are display-only progress."""
        if code == "started":
            self.control(code)
        else:
            self.status(code, number)

    def control(self, code):
        """A wait-for-a-place frame: it renews the terminal's wait, so it is sent even when the preview has stopped."""
        if code in STATUS:
            try:
                send_partial(self._conn, b"S" + code.encode("ascii") + b":0", timeout=PARTIAL_TIMEOUT)
            except AnalysisDenied:
                pass


def activation_listener(uid):
    return activation_socket.listener(socket_path(uid), "analysis")


# Several terminals of one user work at the same time: each connection has its own thread, and up to this many
# model requests run at once (each in its own sandbox); a further request waits for a free place, never "busy".
MAX_CONNECTIONS = 64
PARALLEL_REQUESTS = 4
SERVER_IDLE_SECONDS = 60          # with no connection at all the worker leaves; the next connection starts it again
_running = threading.BoundedSemaphore(PARALLEL_REQUESTS)
# The worker's lifetime: RuntimeMaxSec of ai-desktop-analysis@.service, which PID1 enforces by killing it. The worker
# leaves on its own before that, so no answer is cut off: from WORKER_SECONDS - SOFT_DRAIN_SECONDS it leaves at the
# first moment no request is running or waiting (idle connections are closed; a terminal then simply connects again,
# and the connections pending meanwhile start the next worker, see FlushPending= in systemd.socket(5)); from
# WORKER_SECONDS - HARD_DRAIN_SECONDS no new request starts: a waiting one is declined unstarted (the terminal sends
# it again to the next worker), new connections are told to come back, running requests finish, then it leaves.
WORKER_SECONDS = 15480
HARD_DRAIN_SECONDS = answer_seconds("max") + 60
SOFT_DRAIN_SECONDS = HARD_DRAIN_SECONDS + 3600
RESTART_WAIT_SECONDS = HARD_DRAIN_SECONDS + 60      # the longest a terminal waits for the next worker


class _Worker:
    """What the connection threads of one worker share: requests in progress and whether the worker is ending."""

    def __init__(self):
        self.lock = threading.Lock()
        self.busy = 0               # connections between receiving a request (or being accepted) and its response
        self.draining = False       # no new request starts
        self.closing = False        # the worker leaves: idle connections end, none is accepted
        self._wake_read, self._wake_write = os.pipe()

    def _wake(self):
        try:
            os.write(self._wake_write, b"x")        # never read: it stays readable for every idle connection
        except OSError:
            pass

    def drain(self):
        with self.lock:
            self.draining = True
        self._wake()

    def leave_if_idle(self):
        with self.lock:
            if self.busy:
                return False
            self.closing = True
        self._wake()
        return True

    @property
    def ending(self):
        return self.draining or self.closing

    def done(self):
        with self.lock:
            self.busy -= 1

    def next_request(self, conn, seconds):
        """True (and counted as busy) when the desktop sent another request in time; False on closure, idle time,
        the end of the conversation's lifetime or the end of the worker."""
        if seconds <= 0:
            return False
        try:
            ready = select.select([conn, self._wake_read], [], [], seconds)[0]
        except (OSError, ValueError):
            return False
        with self.lock:
            if not self.ending:             # the wake-up is written only after `ending` is set under this lock
                try:
                    if conn not in ready or conn.recv(1, socket.MSG_PEEK) == b"":
                        return False
                except OSError:
                    return False
                self.busy += 1
                return True
        _decline(conn)
        return False

    def close(self):
        for fd in (self._wake_read, self._wake_write):
            try:
                os.close(fd)
            except OSError:
                pass


def _decline(conn, *, received=False):
    """End a connection whose next request this worker will not start. Shutting down reading first makes any further
    send of the desktop fail at once (EPIPE, so it connects again); a request that already arrived (`received`, or
    still queued in the socket) is answered with `restart` before the closure, so the desktop sends it again to the
    next worker. Nothing of it was started."""
    try:
        conn.shutdown(socket.SHUT_RD)
        pending = received or conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
    except (BlockingIOError, InterruptedError):
        pending = b""
    except OSError:
        return
    if pending:
        try:
            send_partial(conn, b"S" + b"restart:0", timeout=PARTIAL_TIMEOUT)
        except AnalysisDenied:
            pass


def receive_once(uid, *, clock=time.monotonic):
    """One authenticated desktop connection, handled to its end."""
    socket_path(uid)
    with activation_listener(uid) as listener:
        if not select.select([listener], [], [], 5)[0]:
            raise AnalysisDenied("timeout")
        worker = _Worker()
        try:
            worker.busy += 1
            _connection(listener.accept()[0], uid, clock, worker)
        finally:
            worker.close()


def serve(uid, *, clock=time.monotonic):
    """The activated worker: accept the user's connections while any is open or one arrives within
    SERVER_IDLE_SECONDS; each runs in its own thread, so one terminal never waits for another's conversation. Near
    the end of its lifetime it leaves without cutting off an answer (see WORKER_SECONDS)."""
    socket_path(uid)
    with activation_listener(uid) as listener:
        if not select.select([listener], [], [], 5)[0]:
            raise AnalysisDenied("timeout")
        worker, born = _Worker(), clock()
        threads, idle_since = [], None
        try:
            while True:
                threads = [thread for thread in threads if thread.is_alive()]
                age = clock() - born
                if not worker.draining and age >= WORKER_SECONDS - HARD_DRAIN_SECONDS:
                    worker.drain()
                if not worker.closing and age >= WORKER_SECONDS - SOFT_DRAIN_SECONDS:
                    worker.leave_if_idle()
                if worker.closing:
                    if not threads:
                        return                     # pending connections start the next worker
                    threads[0].join(0.5)           # idle connections are ending; nothing new is accepted
                    continue
                if len(threads) >= MAX_CONNECTIONS:
                    threads[0].join(0.5)               # full: wait for a connection to end, never spin
                    continue
                if select.select([listener], [], [], 0.5)[0]:
                    with worker.lock:
                        worker.busy += 1               # until its first response: the worker does not leave under it
                    thread = threading.Thread(target=_connection, args=(listener.accept()[0], uid, clock, worker),
                                              daemon=True)
                    thread.start()
                    threads.append(thread)
                    idle_since = None
                    continue
                if threads:
                    idle_since = None
                elif idle_since is None:
                    idle_since = clock()
                elif clock() - idle_since >= SERVER_IDLE_SECONDS:
                    return
        finally:
            if not threads:
                worker.close()


def _connection(conn, uid, clock, worker=None):
    """READY, then requests one after another until the desktop closes the connection, it stays idle for
    CONVERSATION_IDLE_SECONDS, CONVERSATION_SECONDS pass, a request is cancelled or the worker ends. Each request has
    its own approval digest, checks and sandbox, exactly as a connection carrying a single request. The connection
    arrives counted as busy in `worker`; it is released after each response."""
    own = worker is None
    if own:
        worker = _Worker()
        worker.busy += 1
    busy = True
    with conn:
        try:
            conn.settimeout(5)
            e2_ipc._peer_uid(conn, uid)
            if worker.ending:
                send_packet(conn, RESTART)
                return
            try:
                supervisor_ready()
                if uid == os.geteuid():
                    raise AnalysisDenied("identity")
            except (AnalysisDenied, OSError, ValueError, KeyError) as exc:
                reason = exc.reason if isinstance(exc, AnalysisDenied) else "runtime"
                send_packet(conn, REFUSED + reason.encode("ascii"))
                return
            send_packet(conn, READY)
            ends = clock() + CONVERSATION_SECONDS
            while True:
                if not busy:
                    busy = worker.next_request(conn, min(CONVERSATION_IDLE_SECONDS, ends - clock()))
                    if not busy:
                        return
                carry_on = _serve(conn, uid, worker)
                busy = False
                worker.done()
                if not carry_on:
                    return
        except (AnalysisDenied, OSError, ValueError, KeyError):
            return                     # one terminal's broken connection never ends the others
        finally:
            if busy:
                worker.done()
            if own:
                worker.close()


def _serve(conn, uid, worker=None):
    """One request on the connection. False when the connection must end (the request was cancelled, or declined
    because the worker ends)."""
    model_input, sha256 = parse_request(receive_message(conn, kind="request"))
    parsed = json.loads(model_input)
    mode = parsed["mode"]
    selected = parsed.get("execution")
    provider = selected["provider"] if selected is not None else "claude"
    stopped = []
    live = _live_sender(conn)

    def cancelled():
        # During a request, closure OR another packet cancels it, and the connection ends with it.
        if select.select([conn], [], [], 0)[0]:
            stopped.append(True)
            return True
        return False

    try:
        supervisor_ready()
        since = heartbeat = time.monotonic()
        queued = False
        while not _running.acquire(timeout=0.2):          # a free place among the parallel requests, or a cancel
            if cancelled():
                raise AnalysisDenied("cancelled")
            if worker is not None and worker.draining:
                _decline(conn, received=True)             # not started: the desktop sends it to the next worker
                return False
            now = time.monotonic()
            if now - since >= QUEUE_SECONDS:
                raise AnalysisDenied("busy")
            if not queued or now - heartbeat >= QUEUE_HEARTBEAT:
                live.control("queued")                    # the terminal shows the wait and renews its own
                queued, heartbeat = True, now
        if queued:
            live.control("started")
    except (AnalysisDenied, OSError, ValueError, KeyError) as exc:
        reason = exc.reason if isinstance(exc, AnalysisDenied) else "internal"
        return _respond(conn, result_packet(sha256, reason=reason, mode=mode, provider=provider), stopped)
    try:
        if cancelled():
            raise AnalysisDenied("cancelled")
        if provider == "local":
            # Only the owner controller can consult its policy and reach the loopback endpoint.
            raise AnalysisDenied("authorization")
        action = "web" if mode == "web" else "analyze"
        from contextlib import nullcontext
        from . import account_runtime
        account = (selected or {}).get("account")
        with (account_runtime.selected(uid, provider, account) if account else nullcontext()):
            if selected is not None and selected["provider"] == "codex":
                from .codex_runtime import run_client as run_codex
                result = run_codex(action, model_input.encode("utf-8"), cancelled=cancelled, mode=mode,
                                   partial=live.text)
            else:
                result = run_client(action, model_input.encode("utf-8"), cancelled=cancelled, mode=mode,
                                    partial=live.text, status=live.status_or_start)
        response = result_packet(sha256, text=result, mode=mode, meta=getattr(result, "meta", None))
    except (AnalysisDenied, OSError, ValueError, KeyError) as exc:
        reason = exc.reason if isinstance(exc, AnalysisDenied) else "internal"
        diagnostic = exc.diagnostic if isinstance(exc, AnalysisDenied) else None
        response = result_packet(sha256, reason=reason, mode=mode, diagnostic=diagnostic, provider=provider)
    finally:
        _running.release()
    return _respond(conn, response, stopped)


def _respond(conn, response, stopped):
    try:
        send_message(conn, response, kind="response")
    except AnalysisDenied:
        return False
    return not stopped


def worker_main(argv):
    try:
        if argv in (["login"], ["login-verbose"]):
            # Entered only by the explicit root-admin command, on a private TTY.
            if not os.isatty(0) or not os.isatty(1):
                raise AnalysisDenied("consent", stage="terminal")
            run_client("login")
            if argv == ["login-verbose"]:
                print("LOGIN: PASS auth=claude.ai")
            return 0
        if len(argv) != 1 or not UID.fullmatch(argv[0]):
            raise AnalysisDenied("input")
        serve(int(argv[0]))
        return 0
    except (AnalysisDenied, e2_ipc.HandoffDenied, OSError, ValueError, TypeError,
            KeyError, KeyboardInterrupt) as exc:
        if argv in (["login"], ["login-verbose"]):
            failure = exc if isinstance(exc, AnalysisDenied) else AnalysisDenied("internal")
            print("LOGIN: REFUSED stage=" + failure.stage + " reason=" + failure.reason, flush=True)
        return 2
