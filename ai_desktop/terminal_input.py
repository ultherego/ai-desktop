"""Typing while the model works.

During a turn the pinned input keeps taking keys: you can prepare the next message and press Enter to queue it; the
queue is sent after the current answer, in order, `/` commands included (so a setting chosen meanwhile applies only
after the answer). A background thread owns the keyboard; everything the program prints goes through a wrapper that
puts it at the end of the conversation and returns the cursor to the input.

Anything else that needs the keyboard takes it over completely:
- a consent question (`fresh_input`) is asked IN the input band, covering your draft until you answer; only keys
  typed after the question appeared count as the answer;
- `sudo` and menus take a lease: the thread stops reading and the terminal goes back to its normal mode, so a password
  never reaches the draft.
Colour terminals with the pinned input only; elsewhere the turn runs as before.
"""
from contextlib import contextmanager
import os
import re
import select
import signal
import sys
import termios
import threading

from .analysis_contract import MAX_MESSAGE_BYTES
from .question_input import QuestionInputError, _Line, _PASTE_START, _character, _escape, _paste

_ANSI = re.compile(r"\x1b\[[0-9;?<>]*[A-Za-z]")
_current = None
_SLASH = "/"


def current():
    return _current


class _Output:
    """sys.stdout during a turn: program text goes to the end of the conversation, then the cursor returns to the band.

    The terminal's saved-cursor slot holds the end of the conversation for the whole turn."""

    def __init__(self, stream, owner):
        self._stream, self._owner = stream, owner

    def write(self, text):
        with self._owner.lock:
            self._stream.write("\x1b8" + text + "\x1b7" + self._owner.band_cursor())
            return len(text)

    def flush(self):
        with self._owner.lock:
            self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class TypeAhead:
    def __init__(self, frame, stream, draft="", resolver=None):
        self.resolver = resolver                   # turns a `/` command into what waits for the answer
        self.frame, self.stream = frame, stream
        self.lock = threading.RLock()
        self.line = _Line(MAX_MESSAGE_BYTES)
        self.line.insert(draft)
        self.queue = []
        self.fd = sys.stdin.fileno()
        self._original = termios.tcgetattr(self.fd)
        self._stop, self._pause, self._paused = threading.Event(), threading.Event(), threading.Event()
        self._question, self._answer, self._answered = None, None, threading.Event()
        self._thread = None
        self._output = None

    # -- terminal modes ------------------------------------------------------------------------------------------------
    def _raw(self):
        raw = self._original[:]
        raw[6] = self._original[6][:]
        raw[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR | termios.IXON | termios.IXOFF)
        raw[3] &= ~(termios.ICANON | termios.ECHO | termios.IEXTEN | termios.ISIG)
        raw[6][termios.VMIN], raw[6][termios.VTIME] = 1, 0
        termios.tcsetattr(self.fd, termios.TCSANOW, raw)

    def _cooked(self):
        termios.tcsetattr(self.fd, termios.TCSANOW, self._original)

    # -- drawing -------------------------------------------------------------------------------------------------------
    def band_cursor(self):
        row, column = self.frame.cursor_at
        return "\x1b[%d;%dH" % (row, column)

    def _note(self):
        if self._question is not None:
            return "answer the question · your draft comes back after it"
        if self.queue:
            return "%d queued · sent after the answer, in order" % len(self.queue)
        return "the model is working · enter queues a message · ctrl+c stops the turn"

    def draw(self):
        with self.lock:
            if self._question is not None:
                text = self._question + " " + self._answer.text
                cursor = len(self._question) + 1 + self._answer.cursor
            else:
                text, cursor = self.line.text, self.line.cursor
            self.frame.note = self._note()
            self.frame.draw(text, cursor, typing=False)

    # -- lifecycle -----------------------------------------------------------------------------------------------------
    def start(self):
        self.stream.write("\x1b7")                      # the end of the conversation
        self.frame.busy = self
        self._output = _Output(self.stream, self)
        sys.stdout = self._output
        self._raw()
        self.stream.write("\x1b[?2004h")               # a pasted block arrives marked, never as Enter
        self.draw()
        self._thread = threading.Thread(target=self._run, name="ai-desktop-typeahead", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self.lock:
            self.frame.busy = None
            sys.stdout = self.stream
            try:
                self._cooked()
            except termios.error:
                pass
            self.frame.note = ""
            self.stream.write("\x1b[?2004l\x1b8")        # back to the end of the conversation
            self.stream.flush()
        return self.line.text, list(self.queue)

    @contextmanager
    def lease(self):
        """The keyboard and the terminal for someone else (sudo, a menu): no key reaches the draft meanwhile."""
        self._pause.set()
        self._paused.wait(timeout=1)
        with self.lock:
            sys.stdout = self.stream
            self.stream.write("\x1b8")
            self.stream.flush()
            self._cooked()
        try:
            yield
        finally:
            with self.lock:
                self.stream.write("\x1b7")
                self._raw()
                sys.stdout = self._output
            self._pause.clear()
            self.draw()

    def ask(self, prompt, max_bytes):
        """A consent question in the band; returns the line typed after it appeared."""
        plain = _ANSI.sub("", prompt).rstrip()
        with self.lock:
            termios.tcflush(self.fd, termios.TCIFLUSH)  # nothing typed before the question counts as its answer
            self._answer, self._question = _Line(max_bytes), plain
            self._answered.clear()
        self.draw()
        try:
            while not self._answered.wait(timeout=0.1):
                if self._stop.is_set():
                    raise EOFError()
            with self.lock:
                answer = self._answer.text
        finally:
            with self.lock:
                self._question = None              # an interrupted question gives the band back to the draft
        print(prompt + answer)                         # the question and the answer stay in the conversation
        self.draw()
        return answer

    # -- keys ----------------------------------------------------------------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            if self._pause.is_set():
                self._paused.set()
                while self._pause.is_set() and not self._stop.is_set():
                    self._stop.wait(0.05)
                self._paused.clear()
                continue
            if not select.select([self.fd], [], [], 0.05)[0]:
                continue
            try:
                value = os.read(self.fd, 1)
            except OSError:
                return
            if not value:
                return
            try:
                self._key(value)
            except (QuestionInputError, KeyboardInterrupt, EOFError, UnicodeError):
                pass
            self.draw()

    def _key(self, value):
        target = self._answer if self._question is not None else self.line
        if value == b"\x03":
            if self._question is None and self.line.text:
                self.line.text, self.line.cursor = "", 0
            else:
                os.kill(os.getpid(), signal.SIGINT)    # like Ctrl+C while waiting: stops the turn
            return
        if value in (b"\r", b"\n"):
            if self._question is not None:
                self._answered.set()
            elif self.line.text.strip():
                text = self.line.text
                self.line.text, self.line.cursor = "", 0
                items = [text]
                if self.resolver is not None and text[:1] == "/":
                    try:
                        items = self.resolver(text)  # a menu opens now; its choice, or the whole command, waits
                    except Exception:           # a failing menu must not lose what was typed
                        items = [text]
                    for item in items:
                        print(_note("%s \u00b7 after the answer" % getattr(item, "label", item)))
                self.queue.extend(items)
            return
        if value in (b"\x08", b"\x7f"):
            target.edit("backspace")
        elif value in (b"\x01", b"\x05", b"\x02", b"\x06"):
            target.edit({b"\x01": "home", b"\x05": "end", b"\x02": "left", b"\x06": "right"}[value])
        elif value == b"\x1b":
            key = _escape(self.fd)
            if key == _PASTE_START:
                target.insert(_paste(self.fd, MAX_MESSAGE_BYTES - len(target.text.encode("utf-8")), newlines=True))
            elif key in (b"[D", b"OD", b"[C", b"OC", b"[H", b"OH", b"[F", b"OF", b"[3~"):
                target.edit({b"[D": "left", b"OD": "left", b"[C": "right", b"OC": "right", b"[H": "home",
                             b"OH": "home", b"[F": "end", b"OF": "end", b"[3~": "delete"}[key])
        elif value >= b" " or value == b"\t":
            if value != b"\t":
                target.insert(_character(self.fd, value))


def _note(text):
    from .terminal_style import paint
    return paint("  \u203a " + text, "muted")


def start(frame, stream, resolver=None):
    """Keep the input live for this turn; returns the owner or None when there is no pinned input."""
    global _current
    if _current is not None or frame is None:
        return _current
    try:
        if not (stream.isatty() and sys.stdin.isatty()):
            return None
        owner = TypeAhead(frame, stream, draft=getattr(frame, "draft", ""), resolver=resolver)
        owner.start()
    except (OSError, ValueError, termios.error):
        return None
    _current = owner
    return owner


def stop():
    """End of the turn: the draft and the queued messages, in order."""
    global _current
    owner, _current = _current, None
    if owner is None:
        return "", []
    return owner.stop()


@contextmanager
def lease():
    """Give the keyboard to a foreground reader (sudo, a menu) for a while; a no-op outside a live turn."""
    owner = _current
    if owner is None:
        yield
        return
    with owner.lease():
        yield
