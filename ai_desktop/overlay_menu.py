"""Menus of a conversation drawn in the list above the input band, where the `/` commands are listed.

The owner decided that a `/` menu belongs to the input area, as in Codex and Claude Code, never to the conversation:
the conversation keeps scrolling while a menu is open, and nothing a menu draws mixes with it. During a turn the live
input's own reader drives the menu, so the keyboard can still be handed to `sudo` or a consent question meanwhile.
Outside a conversation frame (`ai-desktop sessions`, login) the ordinary inline menu of `terminal_menu` is used.
"""
import os
import select
import termios
import threading

from . import terminal_frame, terminal_menu
from .question_input import _byte
from .terminal_menu import MAX_OPTIONS, MenuInputError, _escape, _label

HINT = "↑/↓ choose · enter selects · esc cancels"
HINT_MARKS = "↑/↓ choose · space ticks · enter selects · esc cancels"


class Picker:
    """One menu's state: a trusted title, trusted options, the highlighted row and optional ticks."""

    def __init__(self, title, options, *, default=0, marks=None, markable=0):
        self.title = _label(title)
        if type(options) not in (list, tuple) or not 1 <= len(options) <= MAX_OPTIONS:
            raise MenuInputError("menu-options")
        self.options = tuple(_label(option) for option in options)
        if marks is not None and (type(marks) is not set or type(markable) is not int
                                  or not 0 <= markable <= len(self.options)):
            raise MenuInputError("menu-options")
        if type(default) is not int or not 0 <= default < len(self.options):
            raise MenuInputError("menu-default")
        self.pick, self.marks, self.markable = default, marks, markable

    def rows(self):
        """(label, description) rows for the list above the band."""
        return tuple(((("[x] " if index in self.marks else "[ ] ") if self.marks is not None and index < self.markable
                       else "") + option, "") for index, option in enumerate(self.options))

    def note(self):
        return self.title + " · " + (HINT_MARKS if self.marks is not None else HINT)

    def key(self, key):
        """(finished, answer): answer is the chosen index, or None when cancelled."""
        if key == "cancel":
            return True, None
        if key == "enter":
            return True, self.pick
        if key in ("up", "down"):
            self.pick = (self.pick + (-1 if key == "up" else 1)) % len(self.options)
        elif key == "space" and self.marks is not None and self.pick < self.markable:
            self.marks.symmetric_difference_update({self.pick})
        return False, None


def key_of(fd, byte):
    """One key from its first byte; an escape sequence is read to its end. Unknown input is ignored."""
    if byte in (b"\r", b"\n"):
        return "enter"
    if byte == b" ":
        return "space"
    if byte in (b"\x03", b"\x04"):
        return "cancel"
    if byte == b"\x1b":
        try:
            return _escape(fd)
        except MenuInputError:
            return "ignore"
    return "ignore"


def choose(title, options, *, default=0, marks=None, markable=0):
    """Like `terminal_menu.choose`, drawn above the input band when a conversation frame is on screen."""
    frame = terminal_frame.current()
    if frame is None:
        return terminal_menu.choose(title, options, default=default, marks=marks, markable=markable)
    picker = Picker(title, options, default=default, marks=marks, markable=markable)
    from . import terminal_input
    owner = terminal_input.current()
    if owner is not None:
        if threading.current_thread() is not owner._thread:
            raise MenuInputError("menu-terminal")   # only the live input's reader may read keys during a turn
        return owner.run_picker(picker)
    return _idle(frame, picker)


def _idle(frame, picker):
    """Between turns: the menu owns the keyboard until it is answered; the terminal mode is restored after it."""
    import sys
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        raise MenuInputError("menu-terminal")
    original = termios.tcgetattr(fd)
    raw = original[:]
    raw[6] = original[6][:]
    raw[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR | termios.IXON | termios.IXOFF)
    raw[3] &= ~(termios.ICANON | termios.ECHO | termios.IEXTEN | termios.ISIG)
    raw[6][termios.VMIN], raw[6][termios.VTIME] = 1, 0     # output processing stays on: lines still end properly
    try:
        termios.tcsetattr(fd, termios.TCSANOW, raw)
        termios.tcflush(fd, termios.TCIFLUSH)      # nothing typed before the menu appeared chooses in it
        frame.stream.write("\x1b[?2004h")          # a paste arrives marked and is ignored, never as Enter
        while True:
            frame.show_commands(picker.rows(), picker.pick)
            frame.note = picker.note()
            frame.draw("", 0, typing=False)
            if not select.select([fd], [], [], None)[0]:
                continue
            try:
                key = key_of(fd, _byte(fd))
            except (KeyboardInterrupt, EOFError):
                return None                    # Ctrl+C or Ctrl+D closes the menu, never the conversation
            finished, answer = picker.key(key)
            if finished:
                return answer
    except (OSError, termios.error, ValueError):
        raise MenuInputError("menu-input") from None
    finally:
        try:
            frame.stream.write("\x1b[?2004l")
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSANOW, original)
        except (OSError, termios.error, ValueError):
            pass
        frame.show_commands((), 0)
        frame.leave()
