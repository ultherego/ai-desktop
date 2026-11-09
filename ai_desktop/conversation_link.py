"""One conversation keeps its privileged session and its service connection between questions.

Without it, every request ran `sudo -v`, started a session helper unit and opened a new connection (activating the
service again when it had left). Inside `held()`, the first request opens them and later requests of the same
conversation reuse them. Every request is still approved, checked and run in a fresh sandbox on its own: only the
transport is kept. The link is local to one terminal, so other terminals keep their own sessions (any number of
them work at once).

The link is let go, and the next request opens anew, when:
- the conversation ends (the session helper is closed and its cleanup is confirmed);
- it was idle for CONVERSATION_IDLE_SECONDS, or CONVERSATION_SECONDS have passed since the session opened (the
  service ends an idle connection at the same limit; a margin keeps the desktop from reusing one the service is
  closing);
- a request failed in transport (the connection is closed, which also cancels the request in the service);
- the service closed the connection, or the session helper ended (e.g. `ai-desktop stop` in another terminal).
A local recipient needs no privileged session; its requests never use the link.
"""
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
import select
import signal
import threading
import time

from .analysis_contract import AnalysisDenied, CONVERSATION_IDLE_SECONDS, CONVERSATION_SECONDS

MARGIN = 30                     # seconds before a service limit at which a kept connection or session is not reused
_CURRENT = ContextVar("ai_desktop_conversation_link", default=None)


def current():
    return _CURRENT.get()


class Link:
    def __init__(self, opener, *, clock=time.monotonic):
        self._opener, self._clock = opener, clock
        self._stack = self._session = None
        self._opened = self._used = 0.0
        self._conn = self._endpoint = None
        self._conn_used = 0.0

    # -- the privileged session ------------------------------------------------------------------------------------

    def _expired(self, since, used, now):
        return (now - used >= CONVERSATION_IDLE_SECONDS - MARGIN
                or now - since >= CONVERSATION_SECONDS - MARGIN)

    @property
    def is_open(self):
        return self._stack is not None

    def _session_usable(self):
        if self._stack is None:
            return False
        if callable(getattr(self._session, "alive", None)) and not self._session.alive():
            return False
        return not self._expired(self._opened, self._used, self._clock())

    @contextmanager
    def session(self, *, waiting=None, **kwargs):
        """The kept session for one request: opened (with `waiting` around the sudo prompt) when there is none or it
        can no longer be used. It stays open after the request."""
        if not self._session_usable():
            self.close()
            stack = ExitStack()
            try:
                with (waiting() if waiting is not None else nullcontext()):
                    self._session = stack.enter_context(self._opener(**kwargs))
            except BaseException:
                self._session = None
                stack.close()
                raise
            self._stack, self._opened = stack, self._clock()
        self._used = self._clock()
        try:
            yield
        finally:
            self._used = self._clock()

    # -- the service connection ------------------------------------------------------------------------------------

    def take(self, endpoint):
        """The kept connection, or None when there is none or it must not be reused. The caller owns it until
        `keep` gives it back; anything else closes it."""
        conn, self._conn = self._conn, None
        if conn is None:
            return None
        now = self._clock()
        reusable = (self._endpoint == endpoint and self._session_usable()
                    and now - self._conn_used < CONVERSATION_IDLE_SECONDS - MARGIN)
        if reusable:
            try:
                # Between requests the service sends nothing: anything readable is its closure.
                reusable = not select.select([conn], [], [], 0)[0]
            except (OSError, ValueError, TypeError):
                reusable = False
        if not reusable:
            _close(conn)
            return None
        return conn

    def keep(self, conn, endpoint):
        """Give back a connection whose last response arrived whole."""
        if self._conn is not None:
            _close(self._conn)
        if self._stack is None:
            _close(conn)                  # no session to go with it
            return
        self._conn, self._endpoint, self._conn_used = conn, endpoint, self._clock()

    # -- the end ---------------------------------------------------------------------------------------------------

    def close(self):
        """Close the connection, then the session; a failed confirmation of the session's cleanup is raised."""
        conn, self._conn = self._conn, None
        if conn is not None:
            _close(conn)
        stack, session, self._stack, self._session = self._stack, self._session, None, None
        if stack is None:
            return
        if session is not None and callable(getattr(session, "alive", None)) and not session.alive():
            # The helper already ended (another terminal ran `ai-desktop stop`, or its time ran out): its own cleanup
            # ran in PID1; only the local pipe is left to close.
            try:
                stack.close()
            except AnalysisDenied:
                pass
            return
        stack.close()


def _close(conn):
    try:
        conn.close()
    except OSError:
        pass


@contextmanager
def held(opener, **kwargs):
    """A conversation: requests inside it find the link with `current()`. On the way out the session is closed with
    Ctrl+C ignored, so its cleanup acknowledgment is never cut short."""
    link = Link(opener, **kwargs)
    token = _CURRENT.set(link)
    try:
        yield link
    finally:
        _CURRENT.reset(token)
        main = threading.current_thread() is threading.main_thread()
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN) if main else None
        try:
            link.close()
        finally:
            if main:
                signal.signal(signal.SIGINT, previous)
