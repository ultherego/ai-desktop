"""Bounded one-line TTY editor, without history, readline or user init files.

Only known editing keys become terminal operations. User bytes are decoded and
validated before display; pasted controls never become terminal output or a
later confirmation. Consent continues to use its separate canonical reader.
"""
from bisect import bisect_left, bisect_right
import os
import re
import select
import sys
import termios
import time
import unicodedata

from .analysis_contract import MAX_MESSAGE_BYTES, MAX_QUESTION_BYTES, validate_question
from . import terminal_frame
from .terminal_style import bar, greeting, paint


PROMPT = "How can I help?\n> "
PASTE_TEXT_BYTES = 32 * 1024          # one pasted block kept as an attachment (project mode)
PASTES_TOTAL_BYTES = 32 * 1024        # all attachments of one prompt
MAX_PASTES = 9
ATTACH_MIN_BYTES = 400                # shorter single-line pastes simply become typed text
HISTORY_LIMIT = 50
CTRL_C_HINT = "(Press Ctrl+C again to exit)"
FOOTER = "enter send \u00b7 \\ enter new line \u00b7 / commands \u00b7 ctrl+c clear"


def footer_text(session, attach):
    """The key hint under the input: only what works at this prompt (a line break only where the message may have
    several lines, the `/` list only where there is one)."""
    return " \u00b7 ".join(("enter send",) + (("\\ enter new line",) if attach else ()) +
                           (("/ commands",) if session is not None and session.commands else ("/help",)) +
                           ("ctrl+c clear",))
FOLLOWUP_PROMPT = "> "
_PASTE_START = b"[200~"
_PASTE_END = b"\x1b[201~"
# The one table of plain editing keys (escape sequences without their ESC), shared by the idle editor and the input
# that stays live during a turn (terminal_input). Up/Down are "ignore" here: each editor gives them their meaning
# (the `/` list, a line of a multi-line message, the message history).
_KEYS = {b"[D": "left", b"OD": "left", b"[C": "right", b"OC": "right",
         b"[H": "home", b"OH": "home", b"[1~": "home", b"[7~": "home",
         b"[F": "end", b"OF": "end", b"[4~": "end", b"[8~": "end",
         b"[3~": "delete", b"[A": "ignore", b"[B": "ignore",
         b"OA": "ignore", b"OB": "ignore"}
UP_DOWN = (b"[A", b"OA", b"[B", b"OB")


_TOKEN = r"\[paste #\d: \d+ lines?, [0-9. ]+(?:B|KiB)\]"
_TOKEN_END = re.compile(_TOKEN + r"\Z")
_TOKEN_START = re.compile(_TOKEN)


class QuestionInputError(ValueError):
    """A finite input refusal; never contains the input itself."""


def paste_token(number, text):
    size = len(text.encode("utf-8"))
    lines = text.count("\n") + 1
    return "[paste #%d: %d %s, %s]" % (number, lines, "line" if lines == 1 else "lines",
                                       "%.1f KiB" % (size / 1024) if size >= 1024 else "%d B" % size)


class PromptSession:
    """What one interactive command shares between its prompts: input history, the double Ctrl+C rule,
    and the pasted blocks of the prompt being edited. Nothing is written to disk."""

    def __init__(self):
        self.history, self._armed_until, self._pastes = [], 0.0, []
        self._clock = time.monotonic
        self.commands, self.pick, self.shown = (), 0, 0     # the `/` list: (name, description) pairs set by the caller
        self.paste_bytes = PASTES_TOTAL_BYTES               # `ask` allows more: a pasted text is what it is about
        self.prefill = ""                                   # what the next prompt starts with (e.g. "/model " to finish)
        self.with_text = frozenset()                        # `/` commands that need text after them (`/arch question`)

    ARM_SECONDS = 3.0

    @property
    def armed(self):
        """True while a second Ctrl+C (at the prompt, a consent question or a wait) ends the whole command."""
        return self._clock() < self._armed_until

    @armed.setter
    def armed(self, value):
        self._armed_until = self._clock() + self.ARM_SECONDS if value else 0.0

    def remember(self, text):
        if text.strip() and (not self.history or self.history[-1] != text):
            self.history.append(text)
            del self.history[:-HISTORY_LIMIT]

    def _add_paste(self, text):
        used = sum(len(t.encode("utf-8")) for _, t in self._pastes)
        size = len(text.encode("utf-8"))
        if len(self._pastes) >= MAX_PASTES or size > self.paste_bytes or used + size > self.paste_bytes:
            raise QuestionInputError("question paste")
        number = max((n for n, _ in self._pastes), default=0) + 1
        self._pastes.append((number, text))
        return number

    def reset_pastes(self, keep=""):
        """Forget the pasted blocks, except those whose marker is in `keep` (a draft carried to the next prompt)."""
        self._pastes = [(n, t) for n, t in self._pastes if keep and paste_token(n, t) in keep]

    def attach(self, block):
        """A pasted block as it goes into the line: a marker for a multi-line or large one (kept here until the message
        is sent), the text itself otherwise. QuestionInputError when it does not fit."""
        if "\n" in block or len(block.encode("utf-8")) >= ATTACH_MIN_BYTES:
            return paste_token(self._add_paste(block), block)
        return block

    def expand(self, text, consume=True):
        """The message with every paste marker replaced by its block; the blocks are forgotten unless `consume` is
        False (to measure the message first)."""
        kept = self.take_pastes(text) if consume else [(n, t) for n, t in self._pastes if paste_token(n, t) in text]
        for number, block in kept:
            text = text.replace(paste_token(number, block), block.strip("\n"))
        return text

    def take_pastes(self, line):
        """Pastes whose marker is still in the submitted text (deleting the marker drops the block)."""
        kept = [(n, t) for n, t in self._pastes if paste_token(n, t) in line]
        self._pastes = []
        return kept


def _width(text):
    return sum(0 if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me")
               else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _safe_text(raw, newlines=False):
    try:
        text = raw.decode("utf-8", errors="strict")
        if newlines:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        else:
            validate_question(text, MAX_MESSAGE_BYTES)
        if any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") and not (newlines and char in "\n\t")
               for char in text):
            raise ValueError()
        return text
    except (UnicodeError, ValueError):
        raise QuestionInputError("question text") from None


def _window(shown, cursor, available):
    """The part of one row that fits `available` cells around the cursor, and the cursor's offset inside it."""
    widths = [0]
    for char in shown:
        widths.append(widths[-1] + _width(char))
    start = bisect_left(widths, max(0, widths[cursor] - available), 0, cursor + 1)
    end = bisect_right(widths, widths[start] + available) - 1
    return shown[start:end], widths[cursor] - widths[start]


_WORD = re.compile(r"\w", re.UNICODE)
EDITS = ("left", "right", "home", "end", "text-start", "text-end", "backspace", "delete", "up", "down",
         "word-left", "word-right", "kill-word-back", "kill-word-forward", "kill-start", "kill-end", "undo")
MAX_UNDO = 200


def edit_key(key):
    """The editing action of an escape sequence (without its ESC), or None. Covers the forms terminals send for
    Ctrl/Alt with arrows, Home/End and Delete, Alt+letter, and the kitty keyboard protocol."""
    alt = {b"b": "word-left", b"f": "word-right", b"d": "kill-word-forward", b"\x7f": "kill-word-back",
           b"\x08": "kill-word-back", b"B": "word-left", b"F": "word-right", b"D": "kill-word-forward"}
    if key in alt:
        return alt[key]
    match = re.fullmatch(rb"\[1;(\d+)([ABCDHF])", key) or re.fullmatch(rb"O(\d)([ABCDHF])", key)
    if match:
        held = (int(match.group(1)) - 1) & ~(64 | 128)
        if not held & (2 | 4):
            return None                                              # plain or Shift: the ordinary key decoding
        return {b"C": "word-right", b"D": "word-left", b"A": "up", b"B": "down",
                b"H": "text-start", b"F": "text-end"}[match.group(2)]
    match = re.fullmatch(rb"\[3;(\d+)~", key)
    if match:
        return "kill-word-forward" if ((int(match.group(1)) - 1) & (2 | 4)) else None
    match = re.fullmatch(rb"\[(\d+);(\d+)u", key)                   # kitty: [code;modifiers u
    if match:
        code, held = int(match.group(1)), (int(match.group(2)) - 1) & ~(64 | 128)
        if code in (127, 8) and held & (2 | 4):
            return "kill-word-back"
        if held == 4:
            return {119: "kill-word-back", 117: "kill-start", 107: "kill-end", 122: "undo", 95: "undo"}.get(code)
        if held == 2:
            return alt.get(bytes([code])) if code < 128 else None
    return None


CONTROL_EDITS = {b"\x17": "kill-word-back", b"\x15": "kill-start", b"\x0b": "kill-end", b"\x08": "kill-word-back",
                 b"\x1f": "undo", b"\x1a": "undo"}


class _Line:
    def __init__(self, limit):
        self.text, self.cursor, self.limit = "", 0, limit
        self.rows_total, self.row_drawn = 1, 0         # what is on the screen now (for redrawing a multi-row prompt)
        self._undo = []

    def _remember(self):
        if not self._undo or self._undo[-1] != (self.text, self.cursor):
            self._undo.append((self.text, self.cursor))
            del self._undo[:-MAX_UNDO]

    def insert(self, text):
        result = self.text[:self.cursor] + text + self.text[self.cursor:]
        if len(result.encode("utf-8")) > self.limit:
            raise QuestionInputError("question limit")
        if not text.isalnum():                       # a word typed is undone at once, not letter by letter
            self._remember()
        self.text = result
        self.cursor += len(text)

    def _word_start(self, position):
        while position and not _WORD.match(self.text[position - 1]):
            position -= 1
        while position and _WORD.match(self.text[position - 1]):
            position -= 1
        return position

    def _word_end(self, position):
        length = len(self.text)
        while position < length and not _WORD.match(self.text[position]):
            position += 1
        while position < length and _WORD.match(self.text[position]):
            position += 1
        return position

    def _line_bounds(self):
        start = self.text.rfind("\n", 0, self.cursor) + 1
        end = self.text.find("\n", self.cursor)
        return start, len(self.text) if end == -1 else end

    def _cut(self, start, end):
        """Delete text[start:end]; a paste marker it touches goes as one unit."""
        for marker in _TOKEN_START.finditer(self.text):
            if marker.start() < end and marker.end() > start:
                start, end = min(start, marker.start()), max(end, marker.end())
        if start < end:
            self._remember()
            self.text = self.text[:start] + self.text[end:]
        self.cursor = start

    def edit(self, key):
        if key == "word-left":
            self.cursor = self._word_start(self.cursor)
        elif key == "word-right":
            self.cursor = self._word_end(self.cursor)
        elif key == "kill-word-back":
            self._cut(self._word_start(self.cursor), self.cursor)
        elif key == "kill-word-forward":
            self._cut(self.cursor, self._word_end(self.cursor))
        elif key == "kill-start":
            start, _ = self._line_bounds()
            self._cut(start if start < self.cursor else max(0, start - 1), self.cursor)
        elif key == "kill-end":
            _, end = self._line_bounds()
            self._cut(self.cursor, end if end > self.cursor else min(len(self.text), end + 1))
        elif key == "undo":
            if self._undo:
                self.text, self.cursor = self._undo.pop()
        elif key == "text-start":
            self.cursor = 0
        elif key == "text-end":
            self.cursor = len(self.text)
        elif key in ("home", "end") and "\n" in self.text:
            start, end = self._line_bounds()                  # in a multi-line message: the current line
            self.cursor = start if key == "home" else end
        elif key in ("backspace", "delete"):
            self._remember()
            self._plain(key)
        else:
            self._plain(key)

    def _plain(self, key):
        if key == "left":
            self.cursor = max(0, self.cursor - 1)
        elif key == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(self.text)
        elif key == "backspace" and self.cursor:
            marker = _TOKEN_END.search(self.text[:self.cursor])
            start = marker.start() if marker else self.cursor - 1
            self.text = self.text[:start] + self.text[self.cursor:]      # a paste marker is deleted as one unit
            self.cursor = start
        elif key == "delete":
            marker = _TOKEN_START.match(self.text, self.cursor)
            end = marker.end() if marker else self.cursor + 1
            self.text = self.text[:self.cursor] + self.text[end:]
        elif key in ("up", "down"):
            before = self.text[:self.cursor]
            start = before.rfind("\n") + 1
            column = self.cursor - start
            if key == "up" and start:
                previous = before.rfind("\n", 0, start - 1) + 1
                self.cursor = previous + min(column, start - 1 - previous)
            elif key == "down":
                following = self.text.find("\n", self.cursor)
                if following != -1:
                    end = self.text.find("\n", following + 1)
                    end = len(self.text) if end == -1 else end
                    self.cursor = following + 1 + min(column, end - following - 1)

    def view(self, columns):
        # Keep the prompt, content and cursor inside one physical terminal row.
        # The remaining byte limit still applies to the complete uncut value.
        shown = self.text.replace("\n", "⏎")             # single-row view: a line break is one visible cell
        visible, offset = _window(shown, self.cursor, max(1, columns - 3))
        return visible, 2 + offset

    def layout(self, columns):
        """Rows to draw for a multi-line prompt: (rows, cursor_row, cursor_cell). Each row is cut around its own cursor."""
        available = max(1, columns - 3)
        before = self.text[:self.cursor]
        row = before.count("\n")
        column = len(before) - (before.rfind("\n") + 1)
        rows, cell = [], 2
        for index, logical in enumerate(self.text.split("\n")):
            if index == row:
                visible, offset = _window(logical, column, available)
                cell = 2 + offset
            else:
                visible, _ = _window(logical, 0, available)
            rows.append(visible)
        return rows, row, cell


def _byte(fd):
    value = os.read(fd, 1)
    if not value:
        raise EOFError()
    if value == b"\x03":
        raise KeyboardInterrupt()
    return value


def _escape(fd):
    result = b""
    for _ in range(6):
        # A lone/unrecognised Escape must not leave the editor waiting forever.
        if not select.select([fd], [], [], .15)[0]:
            break
        result += _byte(fd)
        if result in _KEYS or result == _PASTE_START:
            return result
        if not any(key.startswith(result) for key in (*_KEYS, _PASTE_START)):
            break
    raise QuestionInputError("question key")


_CTRL_LETTERS = {97: b"[H", 98: b"[D", 99: b"ctrl-c", 100: b"ctrl-d", 101: b"[F", 102: b"[C"}
_KITTY = None          # whether this terminal answered the keyboard-protocol query (asked once per process)


def _kitty_query(fd, output):
    """Ask once whether the terminal speaks the kitty keyboard protocol (then Shift+Enter is distinguishable)."""
    global _KITTY
    if _KITTY is None:
        output.write("\x1b[?u")
        output.flush()
        reply = b""
        while len(reply) < 32 and select.select([fd], [], [], .15 if not reply else .03)[0]:
            reply += os.read(fd, 1)
            if reply.endswith(b"u") and reply.startswith(b"\x1b[?"):
                break
        _KITTY = reply.startswith(b"\x1b[?") and reply.endswith(b"u")
    return _KITTY


_LOCKS = 64 | 128                                              # Caps Lock and Num Lock bits of the modifier field


def _without_locks(key):
    """`ESC[1;129A` (Up with Num Lock on) is plain Up: keys with only lock bits set are normalised to their bare form."""
    match = re.fullmatch(rb"\[(?:\d+;)?(\d+)([ABCDHF])", key)
    if match:
        return b"[" + match.group(2) if ((int(match.group(1)) - 1) & ~_LOCKS) == 0 else b"[ignored"
    match = re.fullmatch(rb"\[(\d+);(\d+)~", key)
    if match and match.group(1) != b"27":
        return b"[" + match.group(1) + b"~" if ((int(match.group(2)) - 1) & ~_LOCKS) == 0 else b"[ignored"
    return key


SCROLL_KEYS = (b"page-up", b"page-down", b"wheel-up", b"wheel-down", b"scroll-end")
_MOUSE = re.compile(rb"\[<(\d{1,3});\d{1,5};\d{1,5}[Mm]")


def scroll_key(key):
    """PgUp/PgDn (also with Shift) and the mouse wheel (SGR mouse reports) move the full-screen view; any other mouse
    report is ignored. None for every other key."""
    if key in (b"[5~", b"[5;2~"):
        return b"page-up"
    if key in (b"[6~", b"[6;2~"):
        return b"page-down"
    if key in (b"[1;2F",):
        return b"scroll-end"
    match = _MOUSE.fullmatch(key)
    if match:
        button = int(match.group(1)) & ~(4 | 8 | 16)              # without Shift/Alt/Ctrl
        return b"wheel-up" if button == 64 else b"wheel-down" if button == 65 else b"ignore"
    return None


class HistoryWalk:
    """Up/Down through the session's sent messages, from the text being edited (kept to come back to)."""

    def __init__(self, session):
        self.session, self.position, self.draft = session, None, ""

    def step(self, line, older):
        history = self.session.history if self.session is not None else []
        if not history:
            return
        if self.position is None:
            self.position, self.draft = len(history), line.text
        self.position = max(0, self.position - 1) if older else min(len(history), self.position + 1)
        line.text = self.draft if self.position == len(history) else history[self.position]
        line.cursor = len(line.text)


def modified_key(key):
    """An escape sequence (without its ESC) of a key reported with modifiers by the keyboard protocols (kitty CSI u,
    xterm modifyOtherKeys): b"newline" for Shift/Alt/Ctrl+Enter, the plain form of Ctrl+A/B/C/D/E/F, else None."""
    match = re.fullmatch(rb"\[(\d+)(?:;(\d+))?u", key) or re.fullmatch(rb"\[27;(\d+);(\d+)~", key)
    if not match:
        return None
    code, mods = (int(match.group(1)), int(match.group(2) or 1)) if key.endswith(b"u") else (int(match.group(2)), int(match.group(1)))
    held = (mods - 1) & ~(64 | 128)                            # Shift 1, Alt 2, Ctrl 4, Super 8 ... without Caps/Num Lock
    if code == 13 and held:
        return b"newline"
    if held == 4 and code in _CTRL_LETTERS:
        return _CTRL_LETTERS[code]
    return None


def enter_choice(session, text, found, pick):
    """Enter on a `/` word: (line text, send). A highlighted or typed command runs; one that needs text after it
    (a Skill: `/arch question`) is only completed with a space, so the question can follow (as Tab does)."""
    if not found or opens_list(text, pick):
        return text, True
    chosen = text if text in (item[0] for item in found) else found[pick][0]
    if chosen in getattr(session, "with_text", ()):
        return chosen + " ", False
    return chosen, True


def opens_list(text, pick):
    """A bare `/` sent with the first entry highlighted opens the full commands menu instead of running that entry."""
    return text == "/" and pick == 0


def _escape_ext(fd):
    """One escape sequence for a PromptSession: known keys, `newline` (Alt/Shift/Ctrl+Enter), `ctrl-c`/`ctrl-d`
    reported by the keyboard protocol, otherwise `ignore`. Unknown keys never abort the prompt."""
    if not select.select([fd], [], [], .15)[0]:
        return b"ignore"                                       # a lone Escape
    first = os.read(fd, 1)
    if first in (b"\r", b"\n"):
        return b"newline"                                      # Alt+Enter
    if first == b"O":
        second = os.read(fd, 1) if select.select([fd], [], [], .15)[0] else b""
        return first + second if first + second in _KEYS else b"ignore"
    if first != b"[":
        named = edit_key(first)                                # Alt+B/F/D/Backspace
        return b"edit:" + named.encode() if named else b"ignore"
    body = b""
    while len(body) < 24 and select.select([fd], [], [], .15)[0]:
        byte = os.read(fd, 1)
        if not byte:
            raise EOFError()
        body += byte
        if 0x40 <= byte[0] <= 0x7E:
            break
    key = b"[" + body
    scroll = scroll_key(key)
    if scroll == b"ignore":
        return b"mouse:" + key                                  # a click or drag: the full-screen view's selection
    if scroll is not None:
        return scroll
    named = edit_key(key)
    if named is not None:
        return b"edit:" + named.encode()
    key = _without_locks(key)
    if key in _KEYS or key == _PASTE_START:
        return key
    return modified_key(key) or b"ignore"                      # Shift/Alt/Ctrl+Enter, Ctrl+A..F via the protocols


def _discard_paste(fd, *, idle=0.3, limit=64 * 1024 * 1024):
    """A refused paste is thrown away whole. What is left of it must not reach the next prompt, where one of its lines
    would be read as a message. Ends at the paste's end marker, or when the terminal stops sending (no marker came)."""
    tail, total = b"", 0
    while total < limit and select.select([fd], [], [], idle)[0]:
        chunk = os.read(fd, 65536)
        if not chunk:
            return
        total += len(chunk)
        if _PASTE_END in tail + chunk:
            return
        tail = chunk[-(len(_PASTE_END) - 1):]


def _paste(fd, remaining, newlines=False):
    value = bytearray()
    while len(value) <= remaining + len(_PASTE_END):
        value.extend(_byte(fd))
        if value.endswith(_PASTE_END):
            raw = bytes(value[:-len(_PASTE_END)])
            if len(raw) > remaining:
                raise QuestionInputError("question paste")       # complete, and over the limit: nothing is left to read
            return _safe_text(raw, newlines)
        # ESC is permitted only as the prefix of the final paste delimiter.
        escape = value.find(b"\x1b")
        if escape >= 0 and not _PASTE_END.startswith(bytes(value[escape:])):
            break
        if value[-1] < 32 and value[-1] != 27 or value[-1] == 127:
            if not (newlines and value[-1] in (9, 10, 13)):
                break
    _discard_paste(fd)
    raise QuestionInputError("question paste")


def _character(fd, first):
    number = first[0]
    length = 1 if number < 128 else 2 if 0xC2 <= number <= 0xDF else 3 if 0xE0 <= number <= 0xEF else 4 if 0xF0 <= number <= 0xF4 else 0
    if not length:
        raise QuestionInputError("question encoding")
    return _safe_text(first + b"".join(_byte(fd) for _ in range(length - 1)))


def _matches(session, text):
    """The `/` commands offered for this text: only for one word at the start, so a path or a question is left alone."""
    if session is None or not session.commands or not text.startswith("/") or " " in text or "\n" in text:
        return ()
    return tuple(item for item in session.commands if item[0].startswith(text))


def _suggest(line, session, output):
    """Draw the list under the prompt and return the cursor to where it was. Every byte here is generated by us."""
    if session is None or not session.commands:
        return
    found = _matches(session, line.text) if line.rows_total == 1 else ()
    if not found and not session.shown:
        return
    try:
        columns = os.get_terminal_size(output.fileno()).columns
    except OSError:
        columns = 80
    width = columns or 80                                  # a terminal that reports no size still shows the list
    columns = max(4, columns)
    session.pick = min(session.pick, max(0, len(found) - 1))
    rows = []
    for index, (name, description) in enumerate(found):
        text = ("\u203a " if index == session.pick else "  ") + "%-14s %s" % (name, description)
        rows.append(text[:width - 1])
    _, cursor = line.view(columns)
    output.write("\r\n\x1b[J" + "\r\n".join(rows) + "\x1b[%dA\r\x1b[%dC" % (max(1, len(rows)), cursor))
    output.flush()
    session.shown = len(rows)


def _hide_suggestions(session, output):
    if session is not None and session.shown:
        output.write("\r\n\x1b[J\x1b[1A")
        output.flush()
        session.shown = 0


def _draw(line, output):
    try:
        columns = os.get_terminal_size(output.fileno()).columns
    except OSError:
        columns = 80
    columns = max(4, columns)
    if "\n" not in line.text and line.rows_total == 1:
        visible, cursor = line.view(columns)
        # Every escape here is generated by us. No escape from user input survives.
        output.write("\r\x1b[2K" + _row(output, visible, True) + "\r\x1b[" + str(cursor) + "C")
        output.flush()
        return
    rows, cursor_row, cursor_cell = line.layout(columns)
    parts = ["\x1b[%dA" % line.row_drawn] if line.row_drawn else []
    parts.append("\r\x1b[J")                                 # from the first row down: clear the whole old block
    for index, visible in enumerate(rows):
        parts.append(_row(output, visible, index == 0) + ("\r\n" if index < len(rows) - 1 else ""))
    if len(rows) - 1 - cursor_row:
        parts.append("\x1b[%dA" % (len(rows) - 1 - cursor_row))
    parts.append("\r\x1b[%dC" % cursor_cell)
    output.write("".join(parts))
    output.flush()
    line.rows_total, line.row_drawn = len(rows), cursor_row


def _wait(fd, idle):
    """Call the idle callback once if no key arrives before the caller's deadline; then block normally."""
    if idle is None:
        return None
    seconds, callback = idle
    left = seconds()
    if left is not None and left > 0 and not select.select([fd], [], [], left)[0]:
        callback()
        return None
    return idle


def _pointer(output):
    return paint(">", "accent", "bold", stream=output) + " "


def _row(output, visible, first):
    """One input row. In a colour terminal it is a band across the whole width (the same two cells before the text, so
    cursor positions do not change); the text is already safe, every escape here is ours."""
    shade = bar(output)
    if not shade:
        return (_pointer(output) if first else "  ") + visible
    head = shade + paint("\u203a", "accent", "bold", stream=output) + shade + " " if first else shade + "  "
    return head + visible + "\x1b[K\x1b[0m"


def _footer(line, session, output, attach=True):
    """The muted key hint under the input band, then the cursor goes back where it was. Colour terminals only, and never
    over the `/` command list."""
    if session is None or session.shown or not bar(output):
        return
    try:
        columns = max(4, os.get_terminal_size(output.fileno()).columns)
    except OSError:
        columns = 80
    if line.rows_total == 1 and "\n" not in line.text:
        below, cell = 0, line.view(columns)[1]
    else:
        rows, cursor_row, cell = line.layout(columns)
        below = len(rows) - 1 - cursor_row
    hint = "  " + footer_text(session, attach)[:max(0, columns - 3)]
    output.write(("\x1b[%dB" % below if below else "") + "\r\n\x1b[2K" + paint(hint, "muted", stream=output)
                 + "\x1b[%dA\r" % (below + 1) + ("\x1b[%dC" % cell if cell else ""))
    output.flush()


def _styled_prompt(prompt, output):
    """Colour only: the visible cells of the input row stay the two of "> ", so cursor positions do not change."""
    head, tail = prompt[:-2], prompt[-2:]
    if tail != "> ":
        return prompt
    first, newline, rest = head.partition("\n")
    return (greeting(first, stream=output) + newline + rest if first else head) + _row(output, "", True)


def read_question(*, max_bytes=MAX_QUESTION_BYTES, followup=False, prompt=None, session=None, attach=False, idle=None,
                  lf_breaks=True, draft=""):
    """Read a fresh bounded UTF-8 question and restore the original TTY state.

    With a `PromptSession`: Up/Down walk the history of this command, a first Ctrl+C clears the line (or, on an empty
    line, asks for a second one) and only the second ends the command. With `attach=True` a multi-line or large paste
    becomes a marked attachment instead of being refused; the caller collects it with `session.take_pastes`."""
    if type(followup) is not bool or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_MESSAGE_BYTES:
        raise QuestionInputError("question limit")
    if ((session is not None and type(session) is not PromptSession) or type(attach) is not bool
            or (attach and session is None) or prompt not in (None, FOLLOWUP_PROMPT, PROMPT)):
        raise QuestionInputError("question limit")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise QuestionInputError("question terminal")
    fd, output = sys.stdin.fileno(), sys.stdout
    original = termios.tcgetattr(fd)
    if not original[3] & termios.ICANON:
        raise QuestionInputError("question terminal")
    edited = original[:]
    edited[6] = original[6][:]
    edited[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR | termios.IXON | termios.IXOFF)
    edited[3] &= ~(termios.ICANON | termios.ECHO | termios.IEXTEN | termios.ISIG)
    edited[6][termios.VMIN], edited[6][termios.VTIME] = 1, 0
    termios.tcsetattr(fd, termios.TCSANOW, edited)
    pushed, line, result = None, None, None
    frame = terminal_frame.current() if session is not None else None

    def redraw():
        if frame is None:
            _draw(line, output)
            _suggest(line, session, output)
            _footer(line, session, output, attach)
            return
        found = _matches(session, line.text) if "\n" not in line.text else ()
        if found:
            session.pick = min(session.pick, len(found) - 1)
        frame.show_commands(found, session.pick if found else 0)
        if not session.armed:
            frame.note = ""
        frame.draw(line.text, line.cursor)          # the band grows with the text, like Codex and Claude Code

    try:
        termios.tcflush(fd, termios.TCIFLUSH)
        line = _Line(max_bytes)
        if session is not None and not draft and session.prefill:
            draft = session.prefill                         # a command to finish, e.g. "/model " for an ID to type
        if session is not None:
            session.prefill = ""
        if draft:
            line.insert(_safe_text(draft.encode("utf-8"), newlines=True))    # typed during the previous turn
        if session is not None:
            session.reset_pastes(keep=draft)                # a paste in that draft keeps its block
            # Asked before the prompt is drawn (and only once per process) so that no typed key can be mistaken for the reply.
            if _kitty_query(fd, output):
                output.write("\x1b[>1u")
                pushed = "kitty"
            else:
                output.write("\x1b[>4;2m")                          # xterm modifyOtherKeys; terminals without it ignore this
                pushed = "xterm"
        if frame is not None:
            frame.enter()
            output.write("\x1b[?2004h")
            terminal_frame._call(frame, "mouse", True)
            redraw()
        else:
            output.write("\x1b[?2004h" + _styled_prompt(prompt if prompt is not None else FOLLOWUP_PROMPT if followup else PROMPT, output))
            output.flush()
            _footer(line, session, output, attach)
        walk = HistoryWalk(session)
        while True:
            idle = _wait(fd, idle)
            try:
                value = _byte(fd)
                extended = None
                if value == b"\x1b" and session is not None:
                    extended = _escape_ext(fd)
                    if extended == b"ctrl-c":
                        raise KeyboardInterrupt()
                if extended is not None and extended.startswith(b"mouse:"):
                    if frame is not None:
                        terminal_frame._call(frame, "mouse_event", extended[6:])
                    continue
                if extended in SCROLL_KEYS:
                    if frame is not None:
                        terminal_frame._call(frame, "scroll", extended)                      # the full-screen view's history; the text stays
                    continue
                if frame is not None and extended != b"ignore":
                    terminal_frame._call(frame, "scroll", b"scroll-end")                     # typing returns to the live conversation
            except KeyboardInterrupt:
                if session is None:
                    raise
                if line.text:
                    line.text, line.cursor, session.armed = "", 0, False
                    session.reset_pastes()
                    redraw()
                    continue
                if session.armed:
                    session.armed = False
                    raise
                session.armed = True
                if frame is not None:
                    frame.note = CTRL_C_HINT
                    redraw()
                    continue
                output.write("\r\x1b[2K" + CTRL_C_HINT + "\r\n" + _row(output, "", True))
                output.flush()
                _footer(line, session, output, attach)
                continue
            if session is not None and value != b"\x1b":
                session.armed = False
            if value == b"\n" and attach and lf_breaks:
                line.insert("\n")                                   # Ctrl+J / a terminal binding that sends LF: break the line
                redraw()
                continue
            if value == b"\t" and _matches(session, line.text):
                line.text = _matches(session, line.text)[session.pick][0] + " "
                line.cursor = len(line.text)
                session.pick = 0
                redraw()
                continue
            if value in (b"\r", b"\n"):
                found = _matches(session, line.text)
                if found:
                    # `/mo` + Enter runs what the list has highlighted; `/ar` + Enter completes `/arch ` and waits
                    line.text, send = enter_choice(session, line.text, found, session.pick)
                    line.cursor = len(line.text)
                    session.pick = 0
                    if not send:
                        redraw()
                        continue
                _hide_suggestions(session, output)
                if attach and line.text.endswith("\\") and line.cursor == len(line.text):
                    line.text = line.text[:-1]                      # backslash + Enter: a line break instead of sending
                    line.cursor -= 1
                    line.insert("\n")
                    redraw()
                    continue
                text = validate_question(line.text, MAX_MESSAGE_BYTES)
                if session is not None:
                    session.armed = False
                    session.remember(text)
                result = text
                return text
            if value == b"\x04":
                if not line.text:
                    raise EOFError()
                line.edit("delete")
            elif value == b"\x7f":
                line.edit("backspace")
            elif value in CONTROL_EDITS:
                line.edit(CONTROL_EDITS[value])                     # Ctrl+W/U/K, Ctrl+Backspace, Ctrl+Z
            elif value in (b"\x01", b"\x05", b"\x02", b"\x06"):
                line.edit({b"\x01": "home", b"\x05": "end", b"\x02": "left", b"\x06": "right"}[value])
            elif value == b"\x1b":
                key = extended if session is not None else _escape(fd)
                if session is not None:
                    session.armed = False
                if key == b"ctrl-d":
                    if not line.text:
                        raise EOFError()
                    line.edit("delete")
                elif key == b"newline":
                    if attach:
                        line.insert("\n")
                elif key == b"ignore":
                    pass
                elif key.startswith(b"edit:"):
                    line.edit(key[5:].decode())
                elif key == _PASTE_START and attach:
                    line.insert(session.attach(_paste(fd, session.paste_bytes + 1024, newlines=True)))
                elif key == _PASTE_START:
                    line.insert(_paste(fd, max_bytes - len(line.text.encode("utf-8"))))
                elif key in (b"[A", b"OA", b"[B", b"OB") and _matches(session, line.text):
                    count = len(_matches(session, line.text))
                    session.pick = (session.pick + (-1 if key in (b"[A", b"OA") else 1)) % count
                elif key in (b"[A", b"OA", b"[B", b"OB") and session is not None and "\n" in line.text:
                    line.edit("up" if key in (b"[A", b"OA") else "down")
                elif key in UP_DOWN and session is not None:
                    walk.step(line, key in (b"[A", b"OA"))
                else:
                    line.edit(_KEYS[key])
            else:
                line.insert(_character(fd, value))
            redraw()
    finally:
        if frame is None:
            _hide_suggestions(session, output)
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, original)
            finally:
                keys = "\x1b[<u" if pushed == "kitty" else "\x1b[>4;0m" if pushed == "xterm" else ""
                if frame is not None:
                    terminal_frame._call(frame, "mouse", False)
                    terminal_frame._call(frame, "scroll", b"scroll-end")
                    output.write(keys + "\x1b[?2004l")
                    if result is not None:
                        frame.submitted(result)
                    else:
                        frame.leave()
                else:
                    below = max(0, line.rows_total - 1 - line.row_drawn) if line is not None else 0
                    output.write(("\x1b[%dB" % below if below else "") + keys
                                 + "\x1b[?2004l" + ("\r\n\x1b[J" if bar(output) else "\n"))
                    output.flush()
