"""The finite, owner-budgeted discovery flow of one `ask` question.

A ledger belongs to exactly one question. It owns the budgets (rounds, corrections, scanned entries, wire bytes),
executes local list/find steps through `directory_context`, remembers which names were actually observed, and refuses a
content read whose selectors were neither observed nor named by the user (`discovery-required`). It never decides what the
model may read: the captured policy still does that. Nothing here retries silently or sends a partial selection.

The wire document is a window, not the whole task (the S3 engine, docs/WORKSPACE_ENGINE.md): when a new step does not
fit `discovery_bytes`, the oldest steps leave it and one `cleared` step names what they were, so the model can read a
file again; its own `notes` are kept by the program across every step and never cleared. The rounds stay the owner's
budget of the question; the window only decides what one request carries.
"""
from dataclasses import replace
import json
import re
import unicodedata

from . import directory_context, permissions
from .skill_runtime import MAX_KNOWLEDGE_BYTES, observe_command
from .discovery_contract import (CEILING_TOTAL_ENTRIES, CLEARED_ITEMS, CLEARED_OPS, CLEARED_TEXT, NOTES_BYTES,
                                 REFUSAL_CODES, MAX_SELECTORS, MAX_STEPS, clean_path, document_bytes, diagnostic_bytes, skill_bytes,
                                 validate_discovery, web_data)

REFUSAL_RESERVE = 2400
WEB_MINIMUM = 512          # bytes of a shortened web answer below which it is not worth sending
OVERHEAD = 1024            # envelope of a document with its counters; leaves room for the remaining steps' framing
MAX_PAGES = 8              # public pages opened in one question (S10 stage A); a program bound, not a setting
MAX_FILE_STEPS = 16        # downloaded files and archive inspections in one question (S10 stages B and C)
MAX_EXTENSIONS = 7         # further portions of a step budget one question takes on its own (8 portions in all)
EXTENDABLE = ("discovery_rounds", "command_rounds", "pages", "downloads")
SHOWN_BYTES = 128 * 1024   # results shown during one question; the final answer adds its own
ROUND_ROOM = 8 * 1024      # window bytes a list, find or command step may need; older steps are cleared to make it


class BudgetExhausted(Exception):
    """Owner budget used up. `name` is the permissions.toml key; the text is built from it by the caller."""

    def __init__(self, name):
        self.name = name
        super().__init__("discovery budget")


class WindowFull(Exception):
    """The steps of this round fill the window and none of them has reached the model yet: send them first."""


class Recoverable(Exception):
    """A selection problem the model may correct once per correction budget (never a security refusal)."""

    def __init__(self, code, selectors, limit=None):
        self.code, self.selectors, self.limit = code, selectors, limit
        super().__init__(code)


def _split(selection):
    """(scope id, relative path without terminal wildcard, whether the path ends in a wildcard)."""
    if type(selection) is str:
        return selection, "", True
    path = selection["path"]
    for suffix in ("/**", "/*"):
        if path.endswith(suffix):
            return selection["id"], path[:-len(suffix)], True
    return selection["id"], path, False


class DiscoveryLedger:
    def __init__(self, policy, question, hints=""):
        self.policy = policy
        self.limits = policy.limits() if policy is not None else permissions.Limits(discovery_rounds=0, plan_corrections=0, command_rounds=0)
        self.question = question
        # The agent's own instructions (role text and Skills) are the owner's data, never the model's: a path they name
        # counts like one typed in the question. It never widens what the policy allows and never beats deny.
        self.hints = hints if type(hints) is str else ""
        try:
            self.scopes = {"f" + str(item.index): item for item in policy.scopes()}
        except (TypeError, AttributeError):
            self.scopes = {}
        self.home = getattr(policy, "home", "") if type(getattr(policy, "home", "")) is str else ""
        self.steps, self.guards = [], ()
        self.delivered = []            # (content step, guard) of every excerpt this question sent, cleared ones too
        self.cleared = {"count": 0, "items": []}
        self.notes = ""
        self._sent = set()             # steps that already reached the model: only these may leave the window
        self.shown = []                # results the model showed the user during the question (kept before its answer)
        self.scan = directory_context.Scan(self.limits.search_scan)
        self.rounds_left = self.limits.discovery_rounds
        self.corrections_left = self.limits.plan_corrections
        self.commands_left = self.limits.command_rounds
        self.listed, self.files, self.directories = set(), set(), set()
        # Addresses the program may open without asking: typed by the user, found by the search, linked by an opened page.
        from .web_broker import urls_in
        self.known_urls = set(urls_in(question))
        self.pages_left = MAX_PAGES
        self.files_left = MAX_FILE_STEPS
        self.extensions = 0

    # -- state ---------------------------------------------------------------------------------------------------
    def document(self):
        head = ([{"op": "cleared", "count": self.cleared["count"], "items": [dict(i) for i in self.cleared["items"]]}]
                if self.cleared["count"] else []) + ([{"op": "notes", "text": self.notes}] if self.notes else [])
        return {"steps": head + [dict(step) for step in self.steps],
                "remaining": {"rounds": self.rounds_left, "corrections": self.corrections_left,
                                  "commands": self.commands_left}}

    @property
    def contents(self):
        """Guards of the excerpts still in the window (the transfer rechecks exactly these)."""
        return tuple(guard for step, guard in self.delivered if any(step is kept for kept in self.steps))

    @property
    def content_bytes(self):
        """File text in the window: read_total_size bounds one request, not the whole task."""
        return sum(len(item["text"].encode("utf-8")) for step, _ in self.delivered
                   if any(step is kept for kept in self.steps) for item in step["fragments"])

    def snapshot(self):
        document = validate_discovery(self.document())
        if self._diagnostic_bytes(document) > self.limits.discovery_bytes:
            raise ValueError("discovery_bytes")
        self._sent = {id(step) for step in self.steps}
        return directory_context.DiscoverySnapshot(self.policy, document, self.guards, self.contents)

    def unsent(self):
        return any(id(step) not in self._sent for step in self.steps)

    # -- the window ------------------------------------------------------------------------------------------------
    def note(self, text):
        """The model's working memory for this question (findings, what is left), replaced whole by each plan that
        carries it. Data, never authority; it stays in every request of the question and is never cleared."""
        if text is None:
            return
        if type(text) is not str or len(text.encode("utf-8")) > NOTES_BYTES:
            raise ValueError("notes")
        limit = min(NOTES_BYTES, self.limits.discovery_bytes // 4)     # a small window still has room for steps
        raw = text.encode("utf-8")
        if len(raw) > limit:
            marker = " …[notes shortened: keep them shorter]"
            text = raw[:limit - len(marker.encode("utf-8"))].decode("utf-8", "ignore") + marker
        self.notes = text if text.strip() else ""

    def show(self, text):
        """Results the model shows the user now (a long task's batch of findings): kept in order and put before the
        final answer, so they are never lost when their sources leave the window. False past SHOWN_BYTES."""
        if type(text) is not str or not text.strip():
            return False
        if sum(len(item.encode("utf-8")) for item in self.shown) + len(text.encode("utf-8")) > SHOWN_BYTES:
            return False
        self.shown.append(text.strip("\n"))
        return True

    def with_shown(self, answer):
        """The final answer of the question: what was shown during it, then the answer itself."""
        if not self.shown:
            return answer
        return "".join(item + "\n\n" for item in self.shown) + (answer if type(answer) is str else "")

    @staticmethod
    def _describe(step):
        op = step["op"]
        if op == "content":
            spans = ", ".join("%d-%d" % (item["first"], item["last"]) for item in step["fragments"]) or "no match"
            what = "%s:%s lines %s of %d" % (step["id"], step["path"] or ".", spans, step["lines"])
            if step["search"] is not None:
                what += " (search %s)" % json.dumps(step["search"]["needle"], ensure_ascii=False)
        elif op == "list":
            what = "list " + ", ".join("%s:%s" % (d["id"], d["path"] or ".") for d in step["directories"])
        elif op == "find":
            what = "find %s in %s:%s" % (" ".join(step["queries"]), step["id"], step["path"] or ".")
        elif op == "run":
            what = " ".join(step["argv"])
        elif op == "manual":
            what = "%s(%s)" % (step["page"], step["section"])
        elif op == "refusal":
            what = step["code"]
        else:
            what = str(step.get("url") or step.get("archive") or step.get("name") or op)
        what = "".join(c for c in what if c not in "\n\t" and unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Zl", "Zp"))
        raw = what.encode("utf-8")
        return raw[:CLEARED_TEXT - 4].decode("utf-8", "ignore") + " …" if len(raw) > CLEARED_TEXT else what or op

    def _clear_oldest(self, ops=CLEARED_OPS):
        for index, step in enumerate(self.steps):
            if (step["op"] in ops and id(step) in self._sent
                    and not (step["op"] == "refusal" and step["code"] == "discovery-budget")):
                del self.steps[index]
                self.cleared["count"] += 1
                self.cleared["items"] = (self.cleared["items"] + [{"op": step["op"], "what": self._describe(step)}])[-CLEARED_ITEMS:]
                return True
        return False

    def make_room(self, need):
        """Clear the oldest steps until `need` bytes fit the window (and its entries); False when nothing is left to
        clear. A cleared step stays readable again: what it named is still known to grounding."""
        while self._budget() < need or self._entries_left() <= 0 or len(self.steps) >= MAX_STEPS - 40:
            ops = ("list", "find") if self._budget() >= need and self._entries_left() <= 0 else CLEARED_OPS
            if not self._clear_oldest(ops):
                return self._budget() >= need and self._entries_left() > 0
        return True

    @staticmethod
    def _diagnostic_bytes(document):
        return diagnostic_bytes(document)

    def _budget(self):
        # Room for the refusal steps the correction budget may still add (4 selectors of ~560 bytes + framing each).
        reserve = min(self.corrections_left * REFUSAL_RESERVE, self.limits.discovery_bytes // 4)
        return self.limits.discovery_bytes - (self._diagnostic_bytes(self.document()) if self.steps else 0) - OVERHEAD - reserve

    def _entries_left(self):
        used = sum(len(d["entries"]) for s in self.steps if s["op"] == "list" for d in s["directories"])
        used += sum(len(s["matches"]) for s in self.steps if s["op"] == "find")
        return CEILING_TOTAL_ENTRIES - used

    def _record(self, step):
        if step["op"] == "list":
            for directory in step["directories"]:
                self.listed.add((directory["id"], directory["path"]))
                self.directories.add((directory["id"], directory["path"]))
                for entry in directory["entries"]:
                    self._see(directory["id"], entry)
        elif step["op"] == "find":
            self.directories.add((step["id"], step["path"]))
            for entry in step["matches"]:
                self._see(step["id"], entry)

    def _see(self, ident, entry):
        (self.files if entry["type"] == "file" else self.directories).add((ident, entry["path"]))

    # -- steps -----------------------------------------------------------------------------------------------------
    def can_extend(self, name):
        # A question whose discovery is closed (a byte or scan budget, or its last portion) never opens again.
        return name in EXTENDABLE and self.extensions < MAX_EXTENSIONS and not self.budget_closed() and (
            name != "command_rounds" or self.limits.command_rounds > 0)

    def extend(self, name):
        """One more portion of the exhausted step budget (the question goes on by itself); byte budgets never grow."""
        if not self.can_extend(name):
            return False
        self.extensions += 1
        if name == "discovery_rounds":
            self.rounds_left += self.limits.discovery_rounds
        elif name == "command_rounds":
            self.commands_left += self.limits.command_rounds
        elif name == "pages":
            self.pages_left += MAX_PAGES
        else:
            self.files_left += MAX_FILE_STEPS
        return True

    def take_round(self):
        if self.rounds_left <= 0:
            raise BudgetExhausted("discovery_rounds")
        if self.scan.left <= 0:
            raise BudgetExhausted("search_scan")
        self.make_room(ROUND_ROOM)
        if self._budget() <= 0:
            raise BudgetExhausted("discovery_bytes")
        if self._entries_left() <= 0:
            raise BudgetExhausted("discovery_bytes")
        self.rounds_left -= 1

    def take_command(self):
        """One command round of the owner's budget (0 means the assistant may not run anything)."""
        if self.commands_left <= 0:
            raise BudgetExhausted("command_rounds")
        self.commands_left -= 1

    def output_room(self):
        """Bytes of command output that still fit the discovery budget; BudgetExhausted when almost nothing does."""
        # JSON can double a byte (newline, quote), so the raw output gets at most half of what is left.
        self.make_room(2 * self.limits.command_output + 600)
        room = min(self.limits.command_output, (self._budget() - 600) // 2)
        if room < 256:
            raise BudgetExhausted("discovery_bytes")
        return room

    def can_afford(self, steps):
        """True when `steps` more commands each still get a usable slice of output and a command round."""
        self.make_room(600 + 512 * steps)
        return self.commands_left >= steps and (self._budget() - 600) // 2 >= 256 * steps

    def manual(self, plan):
        """One installed manual page as data for the next round; a missing page is recorded, never an error."""
        from . import manuals
        self.take_round()
        try:
            found = manuals.excerpt(plan["page"], plan["section"], plan.get("needle"))
            step = {"op": "manual", "page": plan["page"], "section": plan["section"], "status": "ok",
                    "lines": found["lines"], "fragments": found["fragments"], "complete": found["complete"]}
        except ValueError:
            step = {"op": "manual", "page": plan["page"], "section": plan["section"], "status": "missing",
                    "lines": 0, "fragments": [], "complete": False}
        for character in "".join(item["text"] for item in step["fragments"]):
            if character not in "\n\t" and unicodedata.category(character) in ("Cc", "Cf", "Cs"):
                step["fragments"] = [dict(item, text="".join(c for c in item["text"] if c in "\n\t"
                                     or unicodedata.category(c) not in ("Cc", "Cf", "Cs"))) for item in step["fragments"]]
                break
        need = document_bytes({"steps": [step], "remaining": self.document()["remaining"]})
        if not self.make_room(need) or need > self._budget():
            raise BudgetExhausted("discovery_bytes")
        self.steps.append(step)
        return step

    def add_skill(self, name, text):
        """A Skill the model asked for, as the owner's data in the next round. A path it names counts like a typed one."""
        status = "missing" if text is None else "ok"
        document = self.document()
        step = {"op": "skill", "name": name, "status": status, "text": text or ""}
        document["steps"].append(step)
        if skill_bytes(document) > MAX_KNOWLEDGE_BYTES:
            raise BudgetExhausted("skills_bytes")
        self.steps.append(step)
        if text and "/" not in name:
            self.hints += "\n" + text

    def add_page(self, step):
        """One page step (opened, declined, refused or failed) as data for the next round; its links become known."""
        if self.pages_left <= 0:
            raise BudgetExhausted("pages")
        self._fit(step)
        self.pages_left -= 1
        self.steps.append(step)
        self.known_urls.update(step["links"])
        return step

    def _fit(self, step):
        """Make room for one more step, clearing the oldest ones; BudgetExhausted when even an empty window is too small."""
        self.make_room(document_bytes({"steps": [step], "remaining": self.document()["remaining"]}) + OVERHEAD)
        document = self.document()
        document["steps"].append(step)
        if self._diagnostic_bytes(validate_discovery(document)) > self.limits.discovery_bytes:
            raise BudgetExhausted("discovery_bytes")

    def add_archive(self, step):
        return self.add_download(step)

    def add_download(self, step):
        """One downloaded (or declined, refused, failed, cancelled) file, or one archive inspection, as data for the next
        round; bounded per question like pages."""
        if self.files_left <= 0:
            raise BudgetExhausted("downloads")
        self._fit(step)
        self.files_left -= 1
        self.steps.append(step)
        return step

    def add_web(self, text):
        """The checked answer of this question's web search, as data for the next round (at most once)."""
        if any(step["op"] == "web" for step in self.steps):
            raise ValueError("web")
        # Normalized and bounded like the contract requires (separators, MAX_WEB_TEXT). When the owner's discovery
        # budget is smaller, a visibly shortened answer is kept rather than stopping the question; the full answer
        # was already shown. Only when not even a short part fits is the budget reported.
        original = web_data(text)
        if original is None:
            raise ValueError("web")
        from .web_broker import urls_in
        from .discovery_contract import WEB_SOURCES
        # Only the program-made source list counts, never an address the model wrote into the answer text.
        self.known_urls.update(urls_in(original.rpartition(WEB_SOURCES)[2]))
        limit = len(original.encode("utf-8"))
        self.make_room(2 * limit + OVERHEAD)
        for _ in range(8):
            shortened = web_data(original, limit)
            if shortened is None:
                break
            step = {"op": "web", "text": shortened}
            document = self.document()
            document["steps"].append(step)
            over = self._diagnostic_bytes(document) + (OVERHEAD if shortened != original else 0) - self.limits.discovery_bytes
            if over <= 0:
                self.steps.append(step)
                return step
            limit -= over + 64
            if limit < WEB_MINIMUM:
                break
        raise BudgetExhausted("discovery_bytes")

    def add_run(self, step):
        # Test and undo must finish after an approved live change. Keep their status even if command
        # output cannot fit on the wire. snapshot() refuses a transfer if the statuses alone overflow.
        self.make_room(document_bytes({"steps": [step], "remaining": self.document()["remaining"]}) + OVERHEAD)
        self.steps.append(step)
        observe_command(step)
        if step["output"] and self._diagnostic_bytes(self.document()) > self.limits.discovery_bytes:
            step["output"] = ""
            step["truncated"] = True
        return step

    def run(self, plan, permit):
        """Execute one validated list/find plan. Raises ValueError with a finite code on any local refusal."""
        self.take_round()
        left = self._entries_left()
        limits = replace(self.limits, listing_entries=min(self.limits.listing_entries, left),
                         search_results=min(self.limits.search_results, left))
        if plan["action"] == "list":
            step, guards = directory_context.list_step(self.policy, plan["files"], plan.get("after"), limits,
                                                       self.scan, self._budget(), permit)
        else:
            # One broad search may use at most a quarter of the question's scan budget, so a wide scope cannot
            # starve the narrower follow-up the model is told to make when a result is incomplete.
            # A text find is the content search the question is about: it may use what is left, since the reads that
            # follow it use content operations, not the scan budget.
            cap = self.scan.left if plan["kind"] == "text" else min(self.scan.left, max(1000, self.limits.search_scan // 4))
            local = directory_context.Scan(cap)
            step, guards = directory_context.find_step(self.policy, plan["files"][0], plan["queries"], plan["kind"],
                                                       limits, local, self._budget(), permit)
            self.scan.left -= cap - local.left
        self.steps.append(step)
        self.guards += guards
        self._record(step)
        return step

    def content(self, plan, permit, *, whole=False):
        """One grounded file, local large-file scan, bounded excerpts on the wire. Older steps leave the window to make
        room. An excerpt that still does not fit what is left of the request is a correction (files-total) for a range
        the model chose, and the used-up byte budget for a whole small file read as a step."""
        from . import ask_content
        self.ground([plan["file"]])
        self.take_round()
        selection = plan["file"]
        key = (selection, "") if type(selection) is str else (selection["id"], selection["path"])
        owner_room = min(ask_content.MAX_FRAGMENT_BYTES, self.limits.read_file_size)
        self.make_room(2 * owner_room + 2048)
        while True:                      # the owner's per-request limits count only the files still in the window
            distinct = {(guard.ident, guard.relative) for guard in self.contents}
            room_total = self.limits.read_total_size - self.content_bytes
            if (key in distinct or len(distinct) < self.limits.read_files) and room_total >= owner_room:
                break
            if not self._clear_oldest(("content",)):
                break
        if key not in distinct and len(distinct) >= self.limits.read_files and not (whole and self.unsent()):
            raise ValueError("files-count")
        owner_room = min(owner_room, self.limits.read_total_size - self.content_bytes)
        budget_room = max(0, (self._budget() - 2048) // 2)
        room = min(owner_room, budget_room)
        if whole and (budget_room < owner_room or key not in distinct and len(distinct) >= self.limits.read_files
                      ) and self.unsent():
            raise WindowFull()       # this round's files fill the window: they go to the model first
        if room < 1:
            raise BudgetExhausted("discovery_bytes")
        try:
            step, guard, amount = ask_content.collect(self.policy, plan, permit, room=room, page=whole)
        except ValueError as error:
            if error.args not in (("files-room",), ("files-total",)):
                raise
            if whole and budget_room < owner_room:      # the request's byte budget, not an owner file limit
                raise BudgetExhausted("discovery_bytes") from None
            raise ValueError("files-total") from None
        if document_bytes({"steps": [step], "remaining": self.document()["remaining"]}) > self._budget():
            raise BudgetExhausted("discovery_bytes")
        self.steps.append(step)
        self.delivered.append((step, guard))
        return step

    def whole(self, selection, permit):
        """One file read as a discovery step, like a tool result in an agent loop: the model may read more files before
        it answers, so a file-by-file task does not end after its first file. A file larger than one excerpt comes as
        its first page (complete=false; the model reads the rest with read-range). None when the file is not plain
        text or cannot be read (the caller then makes the ordinary, final read), and nothing of this attempt is kept.
        A used-up step or byte budget raises BudgetExhausted like any other step, so the owner is asked to continue,
        or the model answers from what it has with the limit shown."""
        if self.budget_closed():
            return None
        saved = (self.rounds_left, list(self.steps), list(self.delivered),
                 {"count": self.cleared["count"], "items": list(self.cleared["items"])})
        plan = {"action": "read-range", "file": selection, "first": 1, "last": 64 * 1024 * 1024 + 1}
        try:
            return self.content(plan, permit, whole=True)
        except (ValueError, OSError, BudgetExhausted, Recoverable, WindowFull) as error:
            self.rounds_left, self.steps, self.delivered, self.cleared = saved
            if type(error) in (BudgetExhausted, WindowFull):
                raise
            return None

    def excerpts(self):
        """{absolute path: {"version", "size", "fragments"}} of the file excerpts this question already delivered, for
        an edit of a file too large to send whole (S3). Only excerpts of the newest version read are kept; an excerpt
        cleared from the window still counts, it was delivered."""
        found = {}
        for step, guard in self.delivered:
            entry = found.get(guard.path)
            if entry is None or entry["version"] != step["version"]:
                entry = found[guard.path] = {"version": step["version"], "size": step["size"], "fragments": []}
            for fragment in step["fragments"]:
                if all((fragment["start"], fragment["end"]) != (item["start"], item["end"]) for item in entry["fragments"]):
                    entry["fragments"].append(fragment)
        return found

    # -- grounding ---------------------------------------------------------------------------------------------------
    def _typed(self, ident, base):
        """Did the user spell this path? Only the full path below the scope root, or that path with the scope's own
        prefix, counts; the match is case-sensitive and bounded, so 'logs.conf' is not found inside 'nested/logs.conf'."""
        forms = {base}
        scope = self.scopes.get(ident)
        if scope is not None:
            prefix = scope.base
            forms.add(prefix + "/" + base)
            home = self.home
            if home and (prefix == home or prefix.startswith(home + "/")):
                forms.add("~" + prefix[len(home):] + "/" + base)
        return any(re.search(r"(?<![\w./~-])" + re.escape(form) + r"(?![\w-]|\.\w)", text)
                   for form in forms for text in (self.question, self.hints))

    def ground(self, selections):
        """Every read must be backed by a list/find observation or by a path the user spelled out."""
        missing = []
        for selection in selections:
            if type(selection) is str:
                scope = self.scopes.get(selection)
                # A directory scope is the owner's own literal rule, but what it contains is unknown until it was
                # listed; an exact-file scope is fully described by its own pattern.
                if scope is not None and scope.mode in ("tree", "children") and (selection, "") not in self.directories:
                    missing.append(selection)
                continue
            ident, base, _ = _split(selection)
            if (ident, base) in self.files or (ident, base) in self.directories:
                continue
            if len(base) >= 3 and self._typed(ident, base):
                continue
            missing.append(selection)
        if missing:
            raise Recoverable("discovery-required", self.selectors(missing))

    @staticmethod
    def selectors(selections):
        result = []
        for selection in selections[:MAX_SELECTORS]:
            ident, path = (selection, "") if type(selection) is str else (selection["id"], selection["path"])
            try:
                result.append({"id": ident, "path": clean_path(path, empty=True, wildcard=True)})
            except ValueError:
                continue
        return result

    def soft_stop(self, name):
        """Scan/byte budgets close further discovery but do not abort the question: the model is told, once,
        that discovery is over (rounds -> 0) and may still read observed paths or answer from what it has."""
        if name not in ("search_scan", "discovery_bytes") + EXTENDABLE or any(
                step["op"] == "refusal" and step["code"] == "discovery-budget" for step in self.steps):
            return False
        limit = {"pages": MAX_PAGES, "downloads": MAX_FILE_STEPS, "discovery_rounds": self.limits.discovery_rounds,
                 "command_rounds": self.limits.command_rounds}.get(name)
        self.steps.append({"op": "refusal", "code": "discovery-budget", "selectors": [],
                           "limit": getattr(self.limits, name) if limit is None else limit})
        self.rounds_left = 0
        if name == "discovery_bytes":
            self.commands_left = 0          # no room for more output either: the model answers from what it has
        return True

    def budget_closed(self):
        """True once soft_stop has told the model that discovery is over."""
        return any(step["op"] == "refusal" and step["code"] == "discovery-budget" for step in self.steps)

    # -- corrections ---------------------------------------------------------------------------------------------------
    def correction(self, problem, *, round_spent=False):
        """Record a recoverable refusal; returns False when the owner's correction budget is used up.

        A path that does not exist is a discovery outcome, not a malformed plan: while discovery rounds are left it
        costs one of them (a list or find step has already spent its round), so the model can still find the right
        place instead of running out of corrections."""
        missing = problem.code == "files-missing" and (round_spent or self.rounds_left > 0)
        if self.corrections_left <= 0 and not missing:
            return False
        step = {"op": "refusal", "code": problem.code, "selectors": problem.selectors, "limit": problem.limit}
        self.steps.append(step)
        if self._diagnostic_bytes(self.document()) > self.limits.discovery_bytes:
            step["selectors"] = []                     # the owner's byte budget wins over naming the selectors
            if self._diagnostic_bytes(self.document()) > self.limits.discovery_bytes:
                self.steps.pop()
                return False
        if missing:
            self.rounds_left -= 0 if round_spent else 1
        else:
            self.corrections_left -= 1
        return True

    def limit_for(self, code):
        return {"files-count": self.limits.read_files, "files-size": self.limits.read_file_size,
                "files-total": self.limits.read_total_size, "files-entries": permissions.MAX_DIRECTORY_ENTRIES}.get(code)


RECOVERABLE = frozenset(REFUSAL_CODES) - {"discovery-required", "discovery-budget"}
