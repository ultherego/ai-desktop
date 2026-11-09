"""A small model of what the program has drawn on the terminal, for the full-screen conversation view.

In the full-screen view (the terminal's alternate screen) the terminal keeps no history of its own. Everything the
program writes is fed here as well: the model follows the same rules as the terminal for the bytes this program emits
(printable text, colours, carriage return, newline, erase line/screen, cursor moves and absolute positions, the
scroll region, saving and restoring the cursor), and a row that scrolls off the top of the region is kept in a
bounded history. The view can then show earlier rows and later draw the live screen again exactly.

Only sequences this program writes need to be understood; anything else is skipped (never echoed into the text).
Every character counts as one cell, as in the rest of the terminal code.
"""
from collections import deque
import re

MAX_HISTORY = 20000                  # rows kept above the screen
_CSI = re.compile(r"\x1b\[([0-9;?<>=]*)([ -/]*)([@-~])")
_RESET = "\x1b[0m"


class ScreenModel:
    def __init__(self, rows, columns):
        self.rows, self.columns = max(1, rows), max(1, columns)
        self.grid = [self._blank_row() for _ in range(self.rows)]
        self.history = deque(maxlen=MAX_HISTORY)
        self.row = self.column = 0
        self.top, self.bottom = 0, self.rows - 1          # the scroll region (0-based, inclusive)
        self.saved = (0, 0, "")
        self.style = ""                                    # the current SGR state as one sequence ("" = default)
        self.wrap_pending = False
        self._pending = ""                                 # an escape sequence cut between two writes
        self.dropped = 0                                   # history rows that fell off the bounded history

    def _blank_row(self):
        return [(" ", "")] * self.columns

    # -- feeding -------------------------------------------------------------------------------------------------------
    def feed(self, text):
        text = self._pending + text
        self._pending = ""
        index, length = 0, len(text)
        while index < length:
            char = text[index]
            if char == "\x1b":
                consumed = self._escape(text, index)
                if consumed is None:                       # incomplete: wait for the rest
                    self._pending = text[index:]
                    if len(self._pending) > 64:
                        self._pending = ""                 # never hold an unknown sequence forever
                    return
                index = consumed
                continue
            if char == "\n":
                self.column = 0                            # the terminal driver turns "\n" into "\r\n" (ONLCR)
                self._line_feed()
            elif char == "\r":
                self.column, self.wrap_pending = 0, False
            elif char == "\b":
                self.column, self.wrap_pending = max(0, self.column - 1), False
            elif char == "\t":
                self.column = min(self.columns - 1, (self.column // 8 + 1) * 8)
            elif ord(char) >= 0x20 and char != "\x7f":
                self._put(char)
            index += 1

    def _escape(self, text, index):
        if index + 1 >= len(text):
            return None
        kind = text[index + 1]
        if kind == "[":
            match = _CSI.match(text, index)
            if match is None:
                rest = text[index + 2:]
                return None if re.fullmatch(r"[0-9;?<>=]*[ -/]*", rest) else index + 2
            self._csi(match.group(1), match.group(2), match.group(3))
            return match.end()
        if kind == "]":                                    # an OSC string: skip to BEL or ST
            for end in range(index + 2, len(text)):
                if text[end] == "\x07":
                    return end + 1
                if text[end] == "\x1b" and end + 1 < len(text) and text[end + 1] == "\\":
                    return end + 2
            return None
        if kind == "7":
            self.saved = (self.row, self.column, self.style)
        elif kind == "8":
            self.row, self.column, self.style = self.saved
            self.wrap_pending = False
        elif kind == "M":                                  # reverse index
            if self.row == self.top:
                self._scroll_down()
            else:
                self.row = max(0, self.row - 1)
        elif kind == "D":
            self._line_feed()
        elif kind in "()":
            return min(len(text), index + 3)
        return index + 2

    def _numbers(self, params, default):
        values = []
        for part in params.split(";"):
            values.append(int(part) if part.isdigit() else default)
        return values or [default]

    def _csi(self, params, intermediate, final):
        if params.startswith(("?", "<", ">", "=")) or intermediate:
            return                                         # private modes (cursor, alternate screen, mouse, keys)
        numbers = self._numbers(params, 0)
        first = numbers[0]
        self.wrap_pending = False if final != "m" else self.wrap_pending
        if final == "m":
            self._sgr(params)
        elif final in "Hf":
            row = (numbers[0] or 1) - 1
            column = ((numbers[1] if len(numbers) > 1 else 1) or 1) - 1
            self.row, self.column = min(max(row, 0), self.rows - 1), min(max(column, 0), self.columns - 1)
        elif final == "A":
            self.row = max(0, self.row - (first or 1))
        elif final == "B":
            self.row = min(self.rows - 1, self.row + (first or 1))
        elif final == "C":
            self.column = min(self.columns - 1, self.column + (first or 1))
        elif final == "D":
            self.column = max(0, self.column - (first or 1))
        elif final == "G":
            self.column = min(max((first or 1) - 1, 0), self.columns - 1)
        elif final == "d":
            self.row = min(max((first or 1) - 1, 0), self.rows - 1)
        elif final == "K":
            line = self.grid[self.row]
            blank = (" ", "")
            if first == 0:
                self.grid[self.row] = line[:self.column] + [blank] * (self.columns - self.column)
            elif first == 1:
                self.grid[self.row] = [blank] * (self.column + 1) + line[self.column + 1:]
            else:
                self.grid[self.row] = self._blank_row()
        elif final == "J":
            if first == 0:
                self._erase_line_from_cursor()
                for row in range(self.row + 1, self.rows):
                    self.grid[row] = self._blank_row()
            elif first == 1:
                for row in range(0, self.row):
                    self.grid[row] = self._blank_row()
            else:
                self.grid = [self._blank_row() for _ in range(self.rows)]
        elif final == "r":
            top = (numbers[0] or 1) - 1
            bottom = ((numbers[1] if len(numbers) > 1 else self.rows) or self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.top, self.bottom = top, bottom
            else:
                self.top, self.bottom = 0, self.rows - 1
            self.row, self.column = 0, 0                   # DECSTBM homes the cursor
        elif final == "s":
            self.saved = (self.row, self.column, self.style)
        elif final == "u":
            self.row, self.column, self.style = self.saved

    def _erase_line_from_cursor(self):
        line = self.grid[self.row]
        self.grid[self.row] = line[:self.column] + [(" ", "")] * (self.columns - self.column)

    def _sgr(self, params):
        if params in ("", "0"):
            self.style = ""
            return
        parts = params.split(";")
        if parts[0] in ("0", ""):
            self.style = "\x1b[" + ";".join(parts[1:]) + "m" if len(parts) > 1 else ""
        else:
            self.style = self.style + "\x1b[" + params + "m"
            if len(self.style) > 96:                       # keep the state bounded: the last sequences decide
                self.style = self.style[-96:]
                self.style = self.style[self.style.find("\x1b"):] if "\x1b" in self.style else ""

    def _put(self, char):
        if self.wrap_pending:
            self.column, self.wrap_pending = 0, False
            self._line_feed()
        line = self.grid[self.row]
        line[self.column] = (char, self.style)
        if self.column == self.columns - 1:
            self.wrap_pending = True
        else:
            self.column += 1

    def _line_feed(self):
        self.wrap_pending = False
        if self.row == self.bottom:
            self._scroll_up()
        elif self.row < self.rows - 1:
            self.row += 1

    def _scroll_up(self):
        gone = self.grid.pop(self.top)
        if self.top == 0:
            self._keep(gone)
        self.grid.insert(self.bottom, self._blank_row())

    def _keep(self, row):
        if len(self.history) == self.history.maxlen:
            self.dropped += 1
        self.history.append(row)

    def _scroll_down(self):
        self.grid.pop(self.bottom)
        self.grid.insert(self.top, self._blank_row())

    # -- size ----------------------------------------------------------------------------------------------------------
    def resize(self, rows, columns):
        rows, columns = max(1, rows), max(1, columns)
        if columns != self.columns:
            fit = lambda line: (line + [(" ", "")] * (columns - len(line)))[:columns]
            self.grid = [fit(line) for line in self.grid]
            self.history = deque((fit(line) for line in self.history), maxlen=MAX_HISTORY)
            self.columns = columns
        while len(self.grid) > rows:                       # rows that no longer fit go to the history
            self._keep(self.grid.pop(0))
            self.row = max(0, self.row - 1)
        while len(self.grid) < rows:
            self.grid.append(self._blank_row())
        self.rows = rows
        self.top, self.bottom = 0, rows - 1
        self.row, self.column = min(self.row, rows - 1), min(self.column, columns - 1)

    # -- drawing -------------------------------------------------------------------------------------------------------
    @staticmethod
    def render(line):
        """One row as text with its colours, trailing blanks removed."""
        cells = list(line)
        while cells and cells[-1] == (" ", ""):
            cells.pop()
        out, style = [], ""
        for char, wanted in cells:
            if wanted != style:
                out.append(_RESET + wanted)
                style = wanted
            out.append(char)
        if style:
            out.append(_RESET)
        return "".join(out)

    def region_rows(self, top_rows, offset):
        """The rows of the conversation area (the first `top_rows` rows) as seen `offset` rows above the live screen:
        history rows above, then the live rows. Fewer rows come back at the very start of the history."""
        combined = list(self.history) + [self.grid[row] for row in range(min(top_rows, self.rows))]
        end = len(combined) - min(max(0, offset), len(self.history))
        return combined[max(0, end - top_rows):max(0, end)]

    def view(self, top_rows, offset):
        """The escape string that draws the conversation area at this history offset (0 = live)."""
        rows = self.region_rows(top_rows, offset)
        rows += [self._blank_row()] * (top_rows - len(rows))
        return "".join("\x1b[%d;1H\x1b[0m\x1b[2K%s" % (index + 1, self.render(line)) for index, line in enumerate(rows))

    def repaint(self, *, region=True):
        """The escape string that draws the whole live screen again and puts the cursor and colour back."""
        body = "".join("\x1b[%d;1H\x1b[0m\x1b[2K%s" % (index + 1, self.render(line))
                       for index, line in enumerate(self.grid))
        if region:
            body += "\x1b[%d;%dr" % (self.top + 1, self.bottom + 1)
        return body + "\x1b[%d;%dH" % (self.row + 1, self.column + 1) + _RESET + self.style

    def transcript(self, top_rows, since=0):
        """The conversation as rows with their colours (history, then the live conversation area), from the absolute
        row `since` (as counted by `total`) on."""
        rows = list(self.history) + self.grid[:min(top_rows, self.rows)]
        lines = [self.render(line) for line in rows[max(0, since - self.dropped):]]
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    def total(self, top_rows):
        """Rows written so far up to the end of the conversation area, counting those the history let go."""
        rows = list(self.history) + self.grid[:min(top_rows, self.rows)]
        while rows and not self.render(rows[-1]).strip():
            rows.pop()
        return self.dropped + len(rows)
