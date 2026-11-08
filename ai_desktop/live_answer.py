"""The answer as it is written: finished Markdown blocks appear while the model works, the rest when it is done.

Display only. The final, validated answer decides what the conversation keeps; when it does not begin with what was
already shown, the terminal says so and prints the whole final answer. A memory line (`@@MEMORY`) is never shown.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from .answer_stream import block_ends
from .conversation_memory import MARKER

_CURRENT = ContextVar("ai_desktop_live_answer", default=None)


def current():
    return _CURRENT.get()


@contextmanager
def turn(**kwargs):
    """One conversation turn may show live text; requests made inside it find this object with `current()`."""
    live = LiveAnswer(**kwargs)
    token = _CURRENT.set(live)
    try:
        yield live
    finally:
        _CURRENT.reset(token)


class LiveAnswer:
    def __init__(self, *, render, rule, write=print):
        self._render, self._rule, self._write = render, rule, write
        self._text, self._shown, self._blocked = "", 0, False
        self._before = None
        self.started = False

    def attach(self, before):
        """`before()` runs once, just before the first block (it stops the waiting indicator)."""
        self._before = before

    def feed(self, piece):
        if self._blocked or type(piece) is not str or not piece:
            return
        self._text += piece
        base = self._shown
        for block, end in block_ends(self._text[base:]):
            if any(line.startswith(MARKER.rstrip()) for line in block.split("\n")):
                self._blocked = True             # the memory line comes last: nothing after it is shown live
                return
            self._show(block)
            self._shown = base + end             # counted per block, so stopping later never shows it twice

    def _show(self, block):
        if not self.started:
            self.started = True
            if self._before is not None:
                self._before()
            self._write("\n" + self._rule() + "\n")
        else:
            self._write("")
        self._write(self._render(block))

    def finish(self, visible):
        """Print what the final answer adds. False when nothing was shown live (the caller prints the answer)."""
        if not self.started:
            return False
        # Blank lines around the shown blocks are not text: the final answer is trimmed (e.g. before a memory line).
        shown, final = self._text[:self._shown].strip("\n"), visible.lstrip("\n")
        if final.startswith(shown):
            rest = final[len(shown):].strip("\n")
            if rest.strip():
                self._write("")
                self._write(self._render(rest))
        else:
            self._write("\n(The final answer differs from the text shown while it was written. The final answer:)\n")
            if visible.strip():
                self._write(self._render(visible))
        return True
