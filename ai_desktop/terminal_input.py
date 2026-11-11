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

The draft is edited like the idle prompt (question_input), with the same key table: Shift/Alt+Enter and backslash +
Enter break the line, Up/Down walk the sent messages while the draft has one line, a multi-line or large paste becomes
the same `[paste #N]` marker (expanded when the message is queued), Tab completes a `/` command with a space after it,
and a bare `/` + Enter opens the commands menu. Ctrl+D deletes the character under the cursor; on an empty draft it is
ignored, because ending the conversation in the middle of an answer would lose it (Ctrl+C stops the turn).
"""
from contextlib import contextmanager
import os
import re
import select
import signal
import sys
import termios
import contextvars
import threading
import time

from .analysis_contract import MAX_MESSAGE_BYTES
from . import question_input
from .question_input import (_without_locks, _KEYS, _PASTE_END, _PASTE_START, CONTROL_EDITS, UP_DOWN, HistoryWalk, QuestionInputError,
                             _Line, _safe_text, edit_key, enter_choice, modified_key, opens_list, scroll_key,
                             waits_for_text)

_ANSI = re.compile(r"\x1b\[[0-9;?<>]*[A-Za-z]")
_current = None
_SLASH = "/"
_CTRL_C_REPORTED = (b"\x1b[99;5u", b"\x1b[27;5;99~")      # Ctrl+C under the kitty / modifyOtherKeys protocols


class Prefill(str):
    """What a `/` menu gives back to the input instead of queueing: the start of a command for the owner to finish
    (e.g. "/model " after "Other ID", for the model ID to type)."""


def _keyboard_modes():
    """(push, pop): the keyboard protocol the idle prompt uses, so Shift+Enter is told apart from Enter."""
    if question_input._KITTY:
        return "\x1b[>1u", "\x1b[<u"
    return "\x1b[>4;2m", "\x1b[>4;0m"                         # xterm modifyOtherKeys; others ignore it


def _view(frame, name, *args):
    """The full-screen view's scrolling and mouse, when the frame has them (an inline frame or a test stand-in does not)."""
    method = getattr(frame, name, None)
    if callable(method):
        method(*args)


class _Output:
    """sys.stdout during a turn: program text goes to the end of the conversation, then the cursor returns to the band.

    The terminal's saved-cursor slot holds the end of the conversation for the whole turn."""

    def __init__(self, stream, owner):
        self._stream, self._owner = stream, owner
        self._redrawn = False          # the last write was a line redrawn in place (the waiting indicator)

    def write(self, text):
        with self._owner.lock:
            prefix = ""
            if text and self._redrawn and not text.startswith("\r"):
                # A message printed while the waiting indicator's line is on screen starts on a clean line instead
                # of being glued after it; the indicator draws itself again on its next tick.
                prefix = "\r\x1b[2K"
            if text:
                self._redrawn = text.startswith("\r") and not text.endswith("\n")
            self._stream.write("\x1b8" + prefix + text + "\x1b7" + self._owner.band_cursor())
            return len(text)

    def flush(self):
        with self._owner.lock:
            self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class TypeAhead:
    def __init__(self, frame, stream, draft="", resolver=None, commands=(), session=None):
        self.resolver = resolver                   # turns a `/` command into what waits for the answer
        self.commands, self.pick = tuple(commands), 0   # the `/` list offered above the band, as when idle
        self.session = session                     # the idle prompt's PromptSession: its history and paste blocks
        self.walk = HistoryWalk(session)
        self.frame, self.stream = frame, stream
        self.lock = threading.RLock()
        self.line = _Line(MAX_MESSAGE_BYTES)
        self.line.insert(draft)
        self.queue = []
        self._modes = None                         # the pushed keyboard protocol, popped whenever others read keys
        self.fd = sys.stdin.fileno()
        self._original = termios.tcgetattr(self.fd)
        self._stop, self._pause, self._paused = threading.Event(), threading.Event(), threading.Event()
        self._question, self._answer, self._answered = None, None, threading.Event()
        # Input generation: a consent question starts a new one. A key sequence (an escape, a paste, a multi-byte
        # character) begun in an earlier generation is never completed into the answer.
        self._epoch, self._sequence_epoch = 0, 0
        # A bracketed paste whose end marker has not arrived yet. It outlives the quiet timeout of the sequence that
        # collects it: a paste stalled for a moment is still an earlier paste when a consent question appears.
        self._paste_open = False
        self._picker = None                        # a `/` menu open in the list above the band
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

    def _push_keys(self):
        if self._modes is None:
            self._modes = _keyboard_modes()
            self.stream.write(self._modes[0])

    def _pop_keys(self):
        """Before anyone else reads the keyboard (sudo, a menu) and at the end: they expect plain keys."""
        if self._modes is not None:
            self.stream.write(self._modes[1])
            self._modes = None

    # -- drawing -------------------------------------------------------------------------------------------------------
    def band_cursor(self):
        row, column = self.frame.cursor_at
        return "\x1b[%d;%dH" % (row, column)

    def _note(self):
        if self._question is not None and getattr(self, "_sequence", None) is not None and self._sequence[0] == "stale":
            return "an earlier paste is still arriving and is not your answer · ctrl+c stops the turn"
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
            if self._picker is not None and self._question is None:
                self.frame.show_commands(self._picker.rows(), self._picker.pick)
                self.frame.note = self._picker.note()
            else:
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
        self._push_keys()
        _view(self.frame, "mouse", True)
        self.draw()
        # The turn's context (the selected service, model and role) goes with the reader, so a `/` menu opened during
        # the answer offers that service's choices, not the defaults of an empty context.
        context = contextvars.copy_context()
        self._thread = threading.Thread(target=context.run, args=(self._run,), name="ai-desktop-typeahead", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            # The loop checks the flag every round, but a `/` menu opened during the turn reads the keyboard itself
            # until it is closed. The terminal is given back only after the reader has ended: a menu that closes
            # later would otherwise restore its own (raw) mode over the next prompt and read keys meant for it.
            while self._thread.is_alive():
                self._thread.join(timeout=0.1)
        with self.lock:
            self.frame.busy = None
            _view(self.frame, "mouse", False)
            _view(self.frame, "scroll", b"scroll-end")
            sys.stdout = self.stream
            try:
                self._cooked()
            except termios.error:
                pass
            self.frame.note = ""
            self._pop_keys()
            self.stream.write("\x1b[?2004l\x1b8")        # back to the end of the conversation
            self.stream.flush()
        return self.line.text, list(self.queue)

    def run_picker(self, picker):
        """A `/` menu in the list above the band, driven by this reader (called from it through the resolver). It
        yields the keyboard like the reader itself: to a lease (sudo) and to a consent question in the band, and comes
        back afterwards. The end of the turn waits for it (the owner decided an open menu stays usable)."""
        with self.lock:
            self._picker = picker
            self._pop_keys()                           # the menu reads plain keys
        self.draw()
        try:
            while True:
                if self._pause.is_set():
                    self._paused.set()
                    while self._pause.is_set():
                        self._stop.wait(0.02)
                    self._paused.clear()
                    self.draw()
                    continue
                if not select.select([self.fd], [], [], 0.02)[0] or self._pause.is_set():
                    continue
                value = os.read(self.fd, 1)
                if not value:
                    return None
                if self._question is not None:         # the answer to a consent question goes to the band
                    try:
                        self._feed(value)
                    except (QuestionInputError, KeyboardInterrupt, EOFError, UnicodeError, ValueError):
                        self._sequence = None
                    self.draw()
                    continue
                from .overlay_menu import key_of
                finished, answer = picker.key(key_of(self.fd, value))
                if finished:
                    return answer
                self.draw()
        finally:
            with self.lock:
                self._picker = None
                if not self._stop.is_set():
                    self._push_keys()
            self.draw()

    @contextmanager
    def lease(self):
        """The keyboard and the terminal for someone else (sudo, a menu): no key reaches the draft meanwhile.
        An open `/` menu pauses too and comes back after it."""
        self._pause.set()
        alive = self._thread is not None and self._thread.is_alive()
        if alive and not self._paused.wait(timeout=2):
            self._pause.clear()                        # never hand over a keyboard someone else still reads
            raise OSError("the live input did not pause")
        with self.lock:
            _view(self.frame, "scroll", b"scroll-end")
            _view(self.frame, "mouse", False)                     # sudo or a menu reads keys itself: no wheel reports for it
            sys.stdout = self.stream
            self._pop_keys()
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
                if self._picker is None:
                    self._push_keys()
                _view(self.frame, "mouse", True)
                sys.stdout = self._output
            self._pause.clear()
            self.draw()

    def _open_question(self, plain, max_bytes):
        """Nothing typed or pasted before the question counts as its answer: the kernel's queue is flushed, and a
        sequence the reader already began (a paste whose end is still to come) is cut off by the new generation."""
        with self.lock:
            self._epoch += 1                           # first, so the reader sees it before it sees the question
            termios.tcflush(self.fd, termios.TCIFLUSH)
            sequence = getattr(self, "_sequence", None)
            if self._paste_open or (sequence is not None and (sequence[0] in ("paste", "discard", "stale") or (
                    sequence[0] == "escape" and _PASTE_START.startswith(bytes(sequence[1]))))):
                # Cut off now, not at its next byte: whatever of that paste still arrives, however late, is dropped
                # up to its end marker and never read as the answer.
                self._sequence, self._sequence_at = ("stale", bytearray()), time.monotonic()
                self._sequence_epoch = self._epoch
            self._answer, self._question = _Line(max_bytes), plain
            self._answered.clear()

    def ask(self, prompt, max_bytes):
        """A consent question in the band; returns the line typed after it appeared."""
        self._open_question(_ANSI.sub("", prompt).rstrip(), max_bytes)
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
    # "stale" is a paste begun before a consent question: while the question is open it ends only at its end marker
    # (or Ctrl+C); the quiet time below applies only once no question is open.
    _QUIET = {"escape": 0.15, "char": 0.15, "paste": 2.0, "discard": 2.0, "stale": 30.0}

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
            self._lapse(time.monotonic())
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

    def _lapse(self, now):
        """Drop a sequence that has been quiet too long: a lone Esc, a cut character or an unfinished paste. An
        earlier paste is never timed out into a consent answer: while a question is open only its end marker (or
        Ctrl+C, which stops the turn) ends it. Without a question it may lapse."""
        sequence = self._sequence
        if sequence is None or now - self._sequence_at <= self._QUIET[sequence[0]]:
            return
        if sequence[0] == "stale":
            if self._question is not None:
                return
            self._paste_open = False
        self._sequence = None

    def _feed(self, value):
        """One byte from the terminal."""
        target = self._answer if self._question is not None else self.line
        sequence = getattr(self, "_sequence", None)
        if sequence is not None and self._sequence_epoch != self._epoch:
            # Begun before a consent question appeared: never completed into the answer. A paste (or an escape that
            # may still become one) is dropped up to its end marker; a cut key or character is just abandoned.
            pasting = sequence[0] in ("paste", "discard", "stale") or (
                sequence[0] == "escape" and _PASTE_START.startswith(bytes(sequence[1])))
            sequence = self._sequence = ("stale", bytearray()) if pasting else None
            self._sequence_epoch = self._epoch
        if sequence is not None:
            kind, buffer = sequence[0], sequence[1]
            self._sequence_at = time.monotonic()
            if kind == "stale" and value == b"\x03":   # Ctrl+C still stops the turn; the paste is abandoned
                self._sequence, self._paste_open = None, False
                self._key(value)
                return
            if kind in ("discard", "stale"):          # an oversized or an earlier paste: wait for its end, keep nothing
                if kind == "stale" and (buffer + value).endswith(_CTRL_C_REPORTED):
                    self._sequence, self._paste_open = None, False   # Ctrl+C as the keyboard protocol reports it
                    self._key(b"\x03")
                    return
                tail = (buffer + value)[-max(len(_PASTE_END), *map(len, _CTRL_C_REPORTED)):]
                if tail.endswith(_PASTE_END):
                    self._sequence, self._paste_open = None, False
                else:
                    self._sequence = (kind, tail)
                return
            buffer.extend(value)
            if kind == "paste":
                if buffer.endswith(_PASTE_END):
                    self._sequence, self._paste_open = None, False
                    raw = bytes(buffer[:-len(_PASTE_END)])
                    if target is self.line and self.session is not None:
                        try:                            # a marker, as at the idle prompt; the block waits in the session
                            target.insert(self.session.attach(_safe_text(raw, newlines=True)))
                        except QuestionInputError:
                            print(_note("the paste is too large for one message (at most %d KiB)"
                                        % (self.session.paste_bytes // 1024)))
                    elif len(raw) <= MAX_MESSAGE_BYTES - len(target.text.encode("utf-8")):
                        target.insert(_safe_text(raw, newlines=True))
                elif len(buffer) > MAX_MESSAGE_BYTES + len(_PASTE_END):
                    self._sequence = ("discard", bytearray(buffer[-len(_PASTE_END):]))
                return
            if kind == "escape":
                key = bytes(buffer)
                if key[:1] == b"[" and len(key) > 1 and 0x40 <= key[-1] <= 0x7E:
                    plain = _without_locks(key)
                    if plain in _KEYS:
                        # An arrow or Home/End in the keyboard protocol's long form (ESC[1;1B, lock bits) is the
                        # plain key, as at the idle prompt (CachyOS check of candidate 156, a terminal inside a multiplexer).
                        key = plain
                if key == _PASTE_START:
                    self._sequence, self._paste_open = ("paste", bytearray()), True
                elif key == _PASTE_END[1:]:
                    self._sequence, self._paste_open = None, False   # the end of a paste whose sequence timed out
                elif key in (b"\r", b"\n"):                   # Alt+Enter: a line break in the draft
                    self._sequence = None
                    if self._question is None:
                        target.insert("\n")
                elif key in _KEYS:
                    self._sequence = None
                    _view(self.frame, "scroll", b"scroll-end")      # a key: back to the live conversation
                    if key in UP_DOWN:
                        self._up_down(key in (b"[A", b"OA"))
                        return
                    if _KEYS[key] != "ignore":
                        target.edit(_KEYS[key])
                elif key[:1] == b"[" and not 0x40 <= key[-1] <= 0x7E and len(key) < 32:
                    return                              # a longer sequence (a mouse report, PgUp) is still arriving
                elif key[:1] == b"O" and len(key) == 2 and key[1:].isdigit():
                    return                              # O5C: a modified arrow in application mode, one more byte
                elif not any(known.startswith(key) for known in (*_KEYS, _PASTE_START, _PASTE_END[1:])):
                    self._sequence = None
                    scroll = scroll_key(key)
                    named = edit_key(key) if scroll is None else None
                    modified = modified_key(key) if scroll is None and named is None else None
                    if modified == b"newline":
                        if self._question is None:
                            target.insert("\n")                   # Shift/Ctrl+Enter, as at the idle prompt
                    elif modified in (b"ctrl-c", b"ctrl-d"):
                        self._key(b"\x03" if modified == b"ctrl-c" else b"\x04")
                    elif modified is not None:
                        target.edit(_KEYS[modified])          # Ctrl+A/B/E/F reported by the keyboard protocol
                    elif scroll is not None and scroll != b"ignore":
                        _view(self.frame, "scroll", scroll)       # the full-screen view's history; the draft stays
                    elif scroll is not None:
                        _view(self.frame, "mouse_event", key)     # a click or drag: the view's own selection
                    elif named is not None:
                        _view(self.frame, "scroll", b"scroll-end")
                        target.edit(named)                        # Ctrl/Alt with arrows, Delete, Backspace
                return
            if kind == "char" and len(buffer) == sequence[2]:
                self._sequence = None
                target.insert(_safe_text(bytes(buffer)))
            return
        if value == b"\x1b":
            self._sequence, self._sequence_at = ("escape", bytearray()), time.monotonic()
            self._sequence_epoch = self._epoch
            return
        _view(self.frame, "scroll", b"scroll-end")               # typing returns to the live conversation
        if value[0] >= 0x80:
            number = value[0]
            length = 2 if 0xC2 <= number <= 0xDF else 3 if 0xE0 <= number <= 0xEF else 4 if 0xF0 <= number <= 0xF4 else 0
            if length:
                self._sequence, self._sequence_at = ("char", bytearray(value), length), time.monotonic()
                self._sequence_epoch = self._epoch
            return
        self._key(value)

    def _up_down(self, older):
        """As at the idle prompt: the `/` list, a line of a multi-line draft, or the sent messages."""
        if self._question is not None:
            return
        found = self._matches()
        if found:
            self.pick = (self.pick + (-1 if older else 1)) % len(found)
        elif "\n" in self.line.text:
            self.line.edit("up" if older else "down")
        else:
            self.walk.step(self.line, older)

    def _send(self):
        """Enter on the draft: queue it (a `/` command goes through the resolver first)."""
        found = self._matches()
        if found:
            # `/mo` + Enter runs what the list has highlighted; `/ar` + Enter completes `/arch ` and waits for the text
            self.line.text, send = enter_choice(self.session, self.line.text, found, self.pick)
            self.line.cursor = len(self.line.text)
            if not send:
                self.pick = 0
                return
        self.pick = 0
        if waits_for_text(self.session, self.line.text):
            self.line.text = self.line.text.strip() + " "      # `/arch ` + Enter: wait for the question, queue nothing
            self.line.cursor = len(self.line.text)
            return
        text = self.line.text
        if self.line.text.endswith("\\") and self.line.cursor == len(self.line.text):
            self.line.text = self.line.text[:-1]       # backslash + Enter: a line break instead of sending
            self.line.cursor -= 1
            self.line.insert("\n")
            return
        if self.session is not None:
            sent = self.session.expand(text, consume=False)   # paste markers become their blocks, as when idle
            if len(sent.encode("utf-8")) > MAX_MESSAGE_BYTES:
                print(_note("with the paste the message is over %d KiB; shorten it" % (MAX_MESSAGE_BYTES // 1024)))
                return                                 # nothing is lost: the draft and its blocks stay
            self.session.remember(text)                # the history is shared with the idle prompt
            text = self.session.expand(text)
        self.walk = HistoryWalk(self.session)
        self.line.text, self.line.cursor = "", 0
        items = [text]
        if self.resolver is not None and text[:1] == "/":
            try:
                items = self.resolver(text)      # a menu opens above the band; its choice, or the command, waits
            except Exception:               # a failing menu must not lose what was typed
                items = [text]
            prefill = next((item for item in items if isinstance(item, Prefill)), None)
            items = [item for item in items if not isinstance(item, Prefill)]
            if prefill is not None:
                self.line.text, self.line.cursor = str(prefill), len(prefill)
            for item in items:
                print(_note("%s \u00b7 after the answer" % getattr(item, "label", item)))
        self.queue.extend(items)

    def _key(self, value):
        target = self._answer if self._question is not None else self.line
        if value == b"\x03":
            if self._question is None and self.line.text:
                self.line.text, self.line.cursor = "", 0
            else:
                os.kill(os.getpid(), signal.SIGINT)    # like Ctrl+C while waiting: stops the turn
            return
        if value == b"\x04":
            if target.text:
                target.edit("delete")                  # on an empty draft ignored: see the module notes
            return
        if value == b"\t" and self._question is None and self._matches():
            self.line.text = self._matches()[self.pick][0] + " "
            self.line.cursor, self.pick = len(self.line.text), 0
            return
        if value in (b"\r", b"\n"):
            if self._question is not None:
                self._answered.set()
            elif self.line.text.strip():
                self._send()
            return
        if value == b"\x7f":
            target.edit("backspace")
        elif value in CONTROL_EDITS:
            target.edit(CONTROL_EDITS[value])           # Ctrl+W/U/K, Ctrl+Backspace, Ctrl+Z
        elif value in (b"\x01", b"\x05", b"\x02", b"\x06"):
            target.edit({b"\x01": "home", b"\x05": "end", b"\x02": "left", b"\x06": "right"}[value])
        elif b" " <= value < b"\x7f":
            target.insert(value.decode("ascii"))


def _note(text):
    from .terminal_style import paint
    return paint("  \u203a " + text, "muted")


def current():
    """The live input of the running turn, or None."""
    return _current


def start(frame, stream, resolver=None, commands=(), session=None):
    """Keep the input live for this turn; returns the owner or None when there is no pinned input. `session`: the idle
    prompt's PromptSession, so queued messages join its history and pastes become the same markers."""
    global _current
    if _current is not None or frame is None:
        return _current
    try:
        if not (stream.isatty() and sys.stdin.isatty()):
            return None
        owner = TypeAhead(frame, stream, draft=getattr(frame, "draft", ""), resolver=resolver, commands=commands,
                          session=session)
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
