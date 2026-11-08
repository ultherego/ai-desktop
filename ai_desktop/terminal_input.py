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
import time

from .analysis_contract import MAX_MESSAGE_BYTES
from .question_input import _KEYS, _PASTE_END, _PASTE_START, QuestionInputError, _Line, _safe_text

_ANSI = re.compile(r"\x1b\[[0-9;?<>]*[A-Za-z]")
_current = None
_EDITS = {b"[D": "left", b"OD": "left", b"[C": "right", b"OC": "right", b"[H": "home", b"OH": "home", b"[1~": "home",
          b"[7~": "home", b"[F": "end", b"OF": "end", b"[4~": "end", b"[8~": "end", b"[3~": "delete"}
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
    def __init__(self, frame, stream, draft="", resolver=None, commands=()):
        self.resolver = resolver                   # turns a `/` command into what waits for the answer
        self.commands, self.pick = tuple(commands), 0   # the `/` list offered above the band, as when idle
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
            found = self._matches() if self._question is None else ()
            self.pick = min(self.pick, max(0, len(found) - 1))
            self.frame.show_commands(found, self.pick)
            self.frame.note = self._note()
            self.frame.draw(text, cursor, typing=False)

    def _matches(self):
        """The `/` commands for one word at the start of the line, as the idle prompt offers them."""
        text = self.line.text
        if not self.commands or not text.startswith("/") or " " in text or "\n" in text:
            return ()
        return tuple(item for item in self.commands if item[0].startswith(text))

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
            self._thread.join(timeout=2)           # the loop checks the flag every round and never blocks in a read
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
        alive = self._thread is not None and self._thread.is_alive()
        if alive and not self._paused.wait(timeout=2):
            self._pause.clear()                        # never hand over a keyboard someone else still reads
            raise OSError("the live input did not pause")
        with self.lock:
            sys.stdout = self.stream
            self.stream.write("\x1b8")
            self.stream.flush()
            self._cooked()
            termios.tcflush(self.fd, termios.TCIFLUSH)  # the new reader starts with keys typed after the handover
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
    # One byte at a time, never a blocking read of "the rest": an escape sequence, a paste or a multi-byte character
    # is collected across loop rounds, so a pause or stop is honoured within one round. Whatever is unfinished when
    # the keyboard is handed over is dropped, never completed with keys typed for someone else.
    _QUIET = {"escape": 0.15, "char": 0.15, "paste": 2.0, "discard": 2.0}

    def _run(self):
        self._sequence, self._sequence_at = None, 0.0
        while not self._stop.is_set():
            if self._pause.is_set():
                self._sequence = None
                self._paused.set()
                while self._pause.is_set() and not self._stop.is_set():
                    self._stop.wait(0.02)
                self._paused.clear()
                continue
            if self._sequence is not None and time.monotonic() - self._sequence_at > self._QUIET[self._sequence[0]]:
                self._sequence = None                  # a lone Esc, a cut character or an unfinished paste
            if not select.select([self.fd], [], [], 0.02)[0] or self._pause.is_set() or self._stop.is_set():
                continue
            try:
                value = os.read(self.fd, 1)
            except OSError:
                return
            if not value:
                return
            try:
                self._feed(value)
            except (QuestionInputError, KeyboardInterrupt, EOFError, UnicodeError, ValueError):
                self._sequence = None
            self.draw()

    def _feed(self, value):
        """One byte from the terminal."""
        target = self._answer if self._question is not None else self.line
        sequence = getattr(self, "_sequence", None)
        if sequence is not None:
            kind, buffer = sequence[0], sequence[1]
            self._sequence_at = time.monotonic()
            if kind == "discard":                      # an oversized paste: wait for its end, keep nothing
                tail = (buffer + value)[-len(_PASTE_END):]
                self._sequence = None if tail == _PASTE_END else ("discard", tail)
                return
            buffer.extend(value)
            if kind == "paste":
                if buffer.endswith(_PASTE_END):
                    self._sequence = None
                    raw = bytes(buffer[:-len(_PASTE_END)])
                    if len(raw) <= MAX_MESSAGE_BYTES - len(target.text.encode("utf-8")):
                        target.insert(_safe_text(raw, newlines=True))
                elif len(buffer) > MAX_MESSAGE_BYTES + len(_PASTE_END):
                    self._sequence = ("discard", bytearray(buffer[-len(_PASTE_END):]))
                return
            if kind == "escape":
                key = bytes(buffer)
                if key == _PASTE_START:
                    self._sequence = ("paste", bytearray())
                elif key in _KEYS:
                    self._sequence = None
                    found = self._matches() if self._question is None else ()
                    if found and key in (b"[A", b"OA", b"[B", b"OB"):
                        self.pick = (self.pick + (-1 if key in (b"[A", b"OA") else 1)) % len(found)
                        return
                    edit = _EDITS.get(key)
                    if edit:
                        target.edit(edit)
                elif not any(known.startswith(key) for known in (*_KEYS, _PASTE_START)):
                    self._sequence = None
                return
            if kind == "char" and len(buffer) == sequence[2]:
                self._sequence = None
                target.insert(_safe_text(bytes(buffer)))
            return
        if value == b"\x1b":
            self._sequence, self._sequence_at = ("escape", bytearray()), time.monotonic()
            return
        if value[0] >= 0x80:
            number = value[0]
            length = 2 if 0xC2 <= number <= 0xDF else 3 if 0xE0 <= number <= 0xEF else 4 if 0xF0 <= number <= 0xF4 else 0
            if length:
                self._sequence, self._sequence_at = ("char", bytearray(value), length), time.monotonic()
            return
        self._key(value)

    def _key(self, value):
        target = self._answer if self._question is not None else self.line
        if value == b"\x03":
            if self._question is None and self.line.text:
                self.line.text, self.line.cursor = "", 0
            else:
                os.kill(os.getpid(), signal.SIGINT)    # like Ctrl+C while waiting: stops the turn
            return
        if value == b"\t" and self._question is None and self._matches():
            self.line.text = self._matches()[self.pick][0]
            self.line.cursor, self.pick = len(self.line.text), 0
            return
        if value in (b"\r", b"\n"):
            if self._question is not None:
                self._answered.set()
            elif self.line.text.strip():
                found = self._matches()
                if found and self.line.text not in (item[0] for item in found):
                    self.line.text = found[self.pick][0]   # `/mo` + Enter runs what the list has highlighted
                self.pick = 0
                text = self.line.text
                self.line.text, self.line.cursor = "", 0
                items = [text]
                if self.resolver is not None and text[:1] == "/":
                    try:
                        items = self.resolver(text)  # a menu opens now; its choice, or the whole command, waits
                    except Exception:           # a failing menu must not lose what was typed
                        items = [text]
                    sys.stdout.write("\x1b[?2004h")    # a menu turns bracketed paste off when it closes
                    for item in items:
                        print(_note("%s \u00b7 after the answer" % getattr(item, "label", item)))
                self.queue.extend(items)
            return
        if value in (b"\x08", b"\x7f"):
            target.edit("backspace")
        elif value in (b"\x01", b"\x05", b"\x02", b"\x06"):
            target.edit({b"\x01": "home", b"\x05": "end", b"\x02": "left", b"\x06": "right"}[value])
        elif b" " <= value < b"\x7f":
            target.insert(value.decode("ascii"))


def _note(text):
    from .terminal_style import paint
    return paint("  \u203a " + text, "muted")


def start(frame, stream, resolver=None, commands=()):
    """Keep the input live for this turn; returns the owner or None when there is no pinned input."""
    global _current
    if _current is not None or frame is None:
        return _current
    try:
        if not (stream.isatty() and sys.stdin.isatty()):
            return None
        owner = TypeAhead(frame, stream, draft=getattr(frame, "draft", ""), resolver=resolver, commands=commands)
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
