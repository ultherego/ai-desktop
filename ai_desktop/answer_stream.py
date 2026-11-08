"""Live answer text: bounded, display-only, never authoritative.

The service splits the Claude client's `stream-json` output: partial-message events feed the live text and are dropped,
every other line is kept for the unchanged final checks. Only the decoded `text` of an `answer` action (or the plain text
of a question without a JSON contract) is forwarded. The final, validated answer always decides what is kept; the
terminal shows the live text block by block and prints only what the final answer adds.
"""
import json
import time

from .analysis_contract import AnalysisDenied

MAX_LINE = 4 * 1024 * 1024          # one stream-json line; a partial-message event is far smaller
MAX_RAW_TEXT = 512 * 1024           # model text gathered for the preview; beyond it the preview just stops
MAX_PARTIAL_TOTAL = 256 * 1024      # live text sent to the terminal in one request
INTERVAL = 0.15                     # seconds between live updates
STATUS = ("search", "results")     # web progress: a fixed vocabulary with a count, never model text


def _string_prefix(raw, start):
    """Decode a JSON string body from `start` (just after the quote) as far as it is complete.
    Returns (text, closed). An escape cut by the end of the data waits for the next update."""
    out, index, length = [], start, len(raw)
    while index < length:
        char = raw[index]
        if char == '"':
            return "".join(out), True
        if char != "\\":
            if ord(char) < 0x20:
                raise ValueError("control")
            out.append(char)
            index += 1
            continue
        if index + 1 >= length:
            break
        code = raw[index + 1]
        simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
        if code in simple:
            out.append(simple[code])
            index += 2
            continue
        if code != "u":
            raise ValueError("escape")
        if index + 6 > length:
            break
        value = int(raw[index + 2:index + 6], 16)
        if 0xD800 <= value < 0xDC00:
            if index + 12 > length:
                break
            if raw[index + 6:index + 8] != "\\u":
                raise ValueError("surrogate")
            low = int(raw[index + 8:index + 12], 16)
            if not 0xDC00 <= low < 0xE000:
                raise ValueError("surrogate")
            out.append(chr(0x10000 + ((value - 0xD800) << 10) + (low - 0xDC00)))
            index += 12
            continue
        if 0xDC00 <= value < 0xE000:
            raise ValueError("surrogate")
        out.append(chr(value))
        index += 6
    return "".join(out), False


def _skip_string(raw, start):
    """Index after the closing quote of a string body starting at `start`, or None when it is not complete yet."""
    index = start
    while index < len(raw):
        if raw[index] == "\\":
            index += 2
        elif raw[index] == '"':
            return index + 1
        else:
            index += 1
    return None


def _skip_value(raw, index):
    """Index after one complete JSON value, or None while it is still arriving."""
    if index >= len(raw):
        return None
    if raw[index] == '"':
        return _skip_string(raw, index + 1)
    if raw[index] in "{[":
        depth = 0
        while index < len(raw):
            char = raw[index]
            if char == '"':
                index = _skip_string(raw, index + 1)
                if index is None:
                    return None
                continue
            if char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return None
    while index < len(raw) and raw[index] not in ",}":
        index += 1
    return index if index < len(raw) else None


def _blank(raw, index):
    while index < len(raw) and raw[index] in " \t\r\n":
        index += 1
    return index


def answer_prefix(raw):
    """The `text` of `{"action":"answer","text":...}` decoded as far as it has arrived, or "" for anything else.

    `action` must come first, as the request asks: a value that might turn out to be a command never reaches the screen.
    Any malformed data ends the preview (the final answer is still shown)."""
    try:
        index = _blank(raw, 0)
        if index >= len(raw) or raw[index] != "{":
            return ""
        index, action = _blank(raw, index + 1), None
        while index < len(raw):
            if raw[index] != '"':
                return ""
            end = _skip_string(raw, index + 1)
            if end is None:
                return ""
            key = json.loads(raw[index:end])
            index = _blank(raw, end)
            if index >= len(raw) or raw[index] != ":":
                return ""
            index = _blank(raw, index + 1)
            if action is None:
                if key != "action":
                    return ""
                end = _skip_value(raw, index)
                if end is None:
                    return ""
                action = json.loads(raw[index:end])
                if action != "answer":
                    return ""
            elif key == "text":
                if index >= len(raw) or raw[index] != '"':
                    return ""
                return _string_prefix(raw, index + 1)[0]
            else:
                end = _skip_value(raw, index)
                if end is None:
                    return ""
            index = _blank(raw, end)
            if index >= len(raw) or raw[index] != ",":
                return ""
            index = _blank(raw, index + 1)
        return ""
    except (ValueError, TypeError, RecursionError):
        return ""


class ClaudeStream:
    """A `stdout_filter` for the client's output: keeps every line except partial-message events, whose text deltas
    build the live preview. `json_answer` selects the answer-action contract; otherwise the text is shown as written."""

    def __init__(self, on_text=None, *, json_answer=True, on_status=None, clock=time.monotonic):
        self._on_text, self._json, self._clock = on_text, json_answer, clock
        self._pending = bytearray()
        self._parts, self._size, self._sent, self._last = [], 0, 0, None
        self._stopped = on_text is None
        self._on_status, self._searches, self._results = on_status, 0, 0

    def __call__(self, block):
        if block is None:                       # end of output: the last line may lack its newline
            kept = self._line(bytes(self._pending)) if self._pending else b""
            self._pending.clear()
            self._emit(force=True)
            return kept
        self._pending.extend(block)
        kept = bytearray()
        while True:
            end = self._pending.find(b"\n")
            if end < 0:
                break
            line = bytes(self._pending[:end])
            del self._pending[:end + 1]
            kept.extend(self._line(line))
        if len(self._pending) > MAX_LINE:
            raise AnalysisDenied("result", diagnostic="claude-stream-size")
        self._emit()
        return bytes(kept)

    def _status(self, code, number=0):
        if self._on_status is not None:
            self._on_status(code, min(number, 99))

    def _observe(self, line):
        """Progress of a web search from the kept events: counts only, never their content."""
        if self._on_status is None or (b'"tool_use"' not in line and b'"tool_result"' not in line):
            return
        try:
            value = json.loads(line)
            content = value.get("message", {}).get("content") if type(value) is dict else None
        except (ValueError, UnicodeError, RecursionError, AttributeError):
            return
        if type(content) is not list:
            return
        blocks = [block for block in content if type(block) is dict]
        if value.get("type") == "assistant":
            calls = sum(1 for block in blocks if block.get("type") == "tool_use" and block.get("name") == "WebSearch")
            if calls:
                self._searches += calls
                self._status("search", self._searches)
        elif value.get("type") == "user" and any(block.get("type") == "tool_result" for block in blocks):
            self._results += 1
            self._status("results", self._results)

    def _line(self, line):
        if b'"stream_event"' not in line:
            self._observe(line)
            return line + b"\n"
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            return line + b"\n"                 # the final checks refuse it as before
        if type(value) is not dict or value.get("type") != "stream_event":
            return line + b"\n"
        event = value.get("event")
        delta = event.get("delta") if type(event) is dict and event.get("type") == "content_block_delta" else None
        if type(delta) is dict and delta.get("type") == "text_delta" and type(delta.get("text")) is str:
            self._add(delta["text"])
        return b""

    def _add(self, text):
        if self._stopped:
            return
        self._size += len(text)
        if self._size > MAX_RAW_TEXT:
            self._stopped = True
            return
        self._parts.append(text)

    def _emit(self, force=False):
        if self._on_text is None or not self._parts:
            return
        now = self._clock()
        if not force and self._last is not None and now - self._last < INTERVAL:
            return
        self._last = now
        raw = "".join(self._parts)
        self._parts = [raw]
        visible = answer_prefix(raw) if self._json else raw
        if len(visible) > self._sent:
            piece = visible[self._sent:]
            self._sent = len(visible)
            self._on_text(piece)


def complete_blocks(text):
    """Split live text into Markdown blocks that are finished (followed by a blank line, outside a code fence).
    Returns (blocks, consumed): the blocks and how many characters of `text` they cover."""
    blocks, start, position, fence = [], 0, 0, None
    for line in text.split("\n")[:-1]:                 # the last piece may still grow
        stripped = line.strip()
        if fence is None and stripped[:3] in ("```", "~~~"):
            fence = stripped[:3]
        elif fence is not None and stripped.startswith(fence):
            fence = None
        position += len(line) + 1
        if fence is None and stripped == "":
            block = text[start:position].strip("\n")
            if block.strip():
                blocks.append(block)
            start = position
    return blocks, start
