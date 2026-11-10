"""`ai-desktop project DIRECTORY`: one read-only project, explored by the model through the controller.

Same transport and pacing as `ai-desktop material`: one bounded consent per question, a
step-by-step visible loop (list, find, search, read), at most five read-only stages, nothing sent
before a yes, no Internet, no writes, no code execution. File NAMES, search hits and fragments are
sent as data, so the consent says so. The answer lists the places Claude really received and what
was skipped or unchecked; sources are confirmed by the controller, never taken from the model.
"""
import argparse
import collections
import os
import sys
import time

import json
import shlex

from .run_controls import recipient, failure_hint, network_hint
from . import ask_cli, ask_edit, assistant_flow, permissions, task_contract
from .analysis_cli import HINTS
from .analysis_contract import AnalysisDenied, InputBudgetExceeded, MAX_QUESTION_BYTES, validate_question
from .assistant_contract import parse_plan
from .question_consent import QuestionConsent
from .workspace_executor import ExecutionDenied
from .analysis_service import send_once
from .skill_runtime import task_skills, accept_skill_response
from .file_context import _read_hint
from .material_cli import (GRANT_HINTS, GRANT_SECONDS, SEPARATOR, Session, SessionWindow, _countdown, _countdown_end,
                           _safe, field, rule, EXIT_COMMANDS, SETTING_COMMANDS)
from .material_grant import GrantDenied, TransferSession
from .material_loop import lines_label
from .project_loop import Loop, history_turn
from .project_scope import Project, ProjectError
from .project_step import MAX_HISTORY, MAX_STAGES, MAX_STEPS, TOTAL_BYTES, parse_decision, plain_answer
from .project_task_consent import ProjectTaskConsent, TASK_BYTES, TASK_REQUESTS, TASK_SECONDS, TASK_STAGES
from .question_input import PromptSession, paste_token
from . import task_workspace, workspace_trash
from .terminal_consent import confirm

Binding = collections.namedtuple("Binding", "material_id sha256")
ROOT_HINTS = {
    "PROJECT_ROOT": "This must be a regular directory (not a link) within files.read in permissions.toml.",
    "PROJECT_POLICY": "The file policy changed or is invalid. Run the command again.",
}
SKIP_LABELS = (("denied", "denied or out of scope"), ("links", "links"), ("special", "special files"),
               ("unsafe", "unsafe attributes or hard links"), ("vcs", ".git directories"),
               ("other", "other skipped"))
REASONS = {"files": "file count limit", "entries": "entry count limit", "depth": "depth limit",
           "unreadable": "unreadable directory"}
HELP = """Ask about the project in a plain sentence; the program picks the files and parts it needs. You can also ask for file changes
(you see a diff and approve the save first) or type "carry on". Large pastes go to Claude as attachments; Ctrl+C twice quits.
Optional (local commands, nothing is sent):
  /files [DIR]      show file names (up to 40)
  /search TEXT      search project file contents for this exact text (up to 20 matches)
  /test RECIPE      run pytest, python-unittest, go-test or npm-test on an isolated copy of indexed files
  /refresh          scan the directory again (also happens before every question)
  /log              what the app handed to Claude in this session
  /model            choose the model (/model ID sets one directly)
  /effort           choose the reasoning level
  /agent NAME       change the role (a saved conversation keeps its own)
  /settings         show and change these settings
  /exit             quit (also /quit, /q or Ctrl+D)"""


def _entries(count):
    return "1 entry" if count == 1 else "%d entries" % count


def _source(path, first, last):
    return "%s:%d–%d" % (_safe(path, 80), first, last) if first != last else "%s:%d" % (_safe(path, 80), first)


SCOPE_ENDED = {
    "SOURCE_CHANGED": "the project changed since your approval (also a change saved here)",
    "GRANT_EXPIRED": "its time ran out",
    "BUDGET_EXHAUSTED": "its stages were used up",
    "CANCELLED": "you stopped the previous question with Ctrl+C",
}


def test_files(project, limit=3):
    """Indexed files that look like the project's own tests (names only, from the local index; nothing is read)."""
    found = []
    for entry in project.files:
        name = entry.path.rpartition("/")[2]
        if ((name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")))
                or name.endswith("_test.go") or name.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts"))):
            found.append(entry.path)
            if len(found) >= limit:
                break
    return found


class EditRefused(ValueError):
    """The proposed change cannot be mapped onto the owner's write policy; the message is for the user."""


def build_edit_plan(project, snapshot, decision):
    """Map a project-relative edit proposal onto configured scopes, or refuse with a human reason.

    The model names only project files that the scan indexed; the CONTROLLER picks the write and read
    scopes, so the model never chooses its own authority."""
    policy = snapshot.policy
    if policy is None:
        raise EditRefused("No permissions.toml: changing files needs a write scope.")
    root = project.root
    writes = [s for s in policy.write_scopes() if s.mode == "tree" and (root == s.base or root.startswith(s.base + "/"))]
    if not writes:
        raise EditRefused("files.write in permissions.toml does not cover this project (it needs an entry like ~/Projects/**).")
    write = max(writes, key=lambda s: len(s.base))
    reads = [s for s in policy.scopes() if s.mode == "tree"]

    def relative(path, base):
        absolute = root + "/" + path
        return absolute[len(base) + 1:]
    selectors, existing, created = [], [], []
    try:
        for path in decision["files"]:
            entry = project._by_path.get(path)
            if entry is None:
                raise EditRefused(recipient("Claude named a file that is not in the project index."))
            if entry.size > task_contract.MAX_FILE:
                raise EditRefused("File %s is over the %d MiB limit for edit mode." % (_safe(path, 80), task_contract.MAX_FILE // 1048576))
            covering = [s for s in reads if (root + "/" + path).startswith(s.base + "/")]
            if not covering:
                raise EditRefused("File %s is not within files.read." % _safe(path, 80))
            scope = max(covering, key=lambda s: len(s.base))
            selectors.append({"id": "f%d" % scope.index, "path": relative(path, scope.base)})
            existing.append(task_contract.path(relative(path, write.base)))
        for path in decision["create"]:
            if path in project._by_path:
                raise EditRefused("File %s already exists and is not overwritten as a new file." % _safe(path, 80))
            parent = path.rpartition("/")[0]
            if parent and not any(e.path.startswith(parent + "/") for e in project.files):
                raise EditRefused("Directory %s does not exist; edit mode does not create directories." % _safe(parent, 80))
            created.append(task_contract.path(relative(path, write.base)))
    except ValueError as exc:
        if isinstance(exc, EditRefused):
            raise
        raise EditRefused("This path cannot be used in edit mode (up to 5 levels below the write scope directory, "
                          "names made of letters, digits, dots, _ and -).") from None
    plan = {"action": "edit", "write": "w%d" % write.index, "files": selectors, "create": created, "check": decision["check"]}
    try:
        return parse_plan(json.dumps(plan), snapshot.access), existing, created
    except ValueError:
        raise EditRefused("This change is outside the scopes in permissions.toml.") from None


def build_delete_plan(project, snapshot, decision):
    """Map project-relative deletions onto the owner's write scope; returns (scope, workspace, names)."""
    policy = snapshot.policy
    if policy is None:
        raise EditRefused("No permissions.toml: deleting files needs a write scope.")
    root = project.root
    writes = [s for s in policy.write_scopes() if s.mode == "tree" and (root == s.base or root.startswith(s.base + "/"))]
    if not writes:
        raise EditRefused("files.write in permissions.toml does not cover this project (it needs an entry like ~/Projects/**).")
    scope = max(writes, key=lambda s: len(s.base))
    names, absolute = [], []
    try:
        for path in decision["delete"]:
            entry = project._by_path.get(path)
            if entry is None:
                raise EditRefused(recipient("Claude named a file that is not in the project index."))
            if entry.size > workspace_trash.MAX_FILE_BYTES:
                raise EditRefused("File %s is too large to delete safely (limit 16 MiB)." % _safe(path, 80))
            names.append(task_contract.path((root + "/" + path)[len(scope.base) + 1:]))
            absolute.append(root + "/" + path)
        policy.require_write(tuple(absolute), index=scope.index)
        workspace = policy.write_workspace(scope.index)
    except EditRefused:
        raise
    except ValueError:
        raise EditRefused("This deletion is outside the scopes in permissions.toml or the path is too deep "
                          "(up to 5 levels below the write scope directory).") from None
    return scope, workspace, names


def build_parser():
    from .run_controls import add_arguments
    parser = argparse.ArgumentParser(prog="ai-desktop project",
                                     description="Work with one project directory: read-only analysis, with changes and deletions only after you approve them")
    parser.add_argument("path", help="Directory within files.read in permissions.toml")
    add_arguments(parser, session=True)
    parser.add_argument("-c", "--continue", dest="resume", action="store_true",
                        help="Continue the latest saved conversation about this directory")
    parser.add_argument("--no-save", dest="no_save", action="store_true",
                        help="Do not save this conversation (it is saved locally by default)")
    parser.add_argument("--format", choices=("auto", "plain", "markdown"), default="auto")
    parser.add_argument("--v", "--verbose", "-v", "--details", dest="details", action="store_true",
                        help="Show the exact payload and diagnostics")
    return parser


def conversation_binding(root):
    """A saved project conversation belongs to its directory, not to one version of its files: every question rescans
    the project anyway, and a binding to the contents would make a conversation unusable after its first saved change."""
    import hashlib
    return hashlib.sha256(("ai-desktop-project\0" + root).encode("utf-8")).hexdigest()


def latest_conversation(root, *, store=None):
    """The id of the most recently used saved conversation about this directory, or None."""
    from .material_cli import latest_saved
    return latest_saved("project", root, conversation_binding(root), store=store)


class ProjectSession(Session):
    accepts_pastes = True
    _task_consent = None                    # the approved project task, kept for follow-up questions (S9)
    def __init__(self, project, name, policy, args, reader, *, clock=time.monotonic, sleep=time.sleep,
                 window=None, sender=send_once):
        self.project, self.name, self.policy, self.args, self.reader = project, name, policy, args, reader
        self._clock, self._sender = clock, sender
        from .material_cli import Pacer
        self.window = window or SessionWindow(quiet=not args.details, clock=clock)
        self.pacer = Pacer(clock=clock, sleep=sleep)
        self.tasks, self.history = [], []
        self.edit_transfers = []                # whole files handed over for a proposed change, shown by /log
        self.prompt_session = PromptSession()
        self._skipped_shown = None              # last skipped-entry count already reported after an answer
        self.progress = ""                      # the model's own working summary (what is done, what next), session-only
        self._current = None
        self._task_consent = None
        from .run_controls import provider_allowed
        self.can_send = provider_allowed(policy)
        self.binding = Binding(project.project_id, project.manifest_sha256())

    def _gate_kwargs(self, packet):
        return dict(question=packet.question, context=None, project=packet.material)

    def _access_unchanged(self, session, packet):
        if not super()._access_unchanged(session, packet):
            return False
        try:
            if self._task_consent is None:
                raise GrantDenied("GRANT_INVALID")
            self._task_consent.check(self.project)
        except GrantDenied as exc:
            session.cancel(packet)
            print(GRANT_HINTS[exc.code])
            return False
        return True

    def ledger(self):
        if not self.edit_transfers or any(task["session"].ledger() for task in self.tasks):
            super().ledger()
        for number, item in enumerate(self.edit_transfers, 1):
            print("Edit %d: %d file(s), %d B, state: %s: %s" % (
                number, len(item["files"]), item["bytes"], item["state"],
                ", ".join(_safe(path, 60) for path in item["files"][:8]) or "new files only"))

    # --- optional local helpers --------------------------------------------------------
    def files(self, prefix):
        if not self._refresh():
            return
        try:
            items, following, total = self.project.listing(prefix.strip(), 0, 40)
        except ProjectError:
            print("Enter a directory relative to the project, e.g. /files src.")
            return
        for entry in items:
            print("%s  (%d B)" % (_safe(entry.path), entry.size))
        print("(showing %d of %d files)" % (len(items), total) if following else "(%d file(s))" % total)

    def search(self, needle):
        if not self._refresh():
            return
        try:
            result = self.project.search_text(needle)
        except ProjectError as exc:
            print(ROOT_HINTS["PROJECT_POLICY"] if exc.code == "PROJECT_POLICY"
                  else "Enter non-empty text of up to 256 bytes, without control characters.")
            return
        for hit in result.hits:
            print("%s:%d: %s" % (_safe(hit.path), hit.line, _safe(hit.text)))
        if not result.hits:
            print("No matches in the files searched." if result.complete else "No matches in this batch; the search did not cover everything.")
        elif not result.complete:
            print("There are more matches or unsearched files; narrow the phrase.")
        skipped = {k: v for k, v in result.skipped.items() if v}
        if skipped:
            print("Skipped in the search: %s." % ", ".join("%s: %d" % kv for kv in skipped.items()))

    # --- a question in plain words -----------------------------------------------------
    @task_skills
    def question(self, text, span=None):
        if not self.can_send:
            print(network_hint())
            return
        try:
            text = validate_question(text.strip())
        except ValueError:
            print("The question must be one line of plain text, up to %d bytes." % MAX_QUESTION_BYTES)
            return
        pastes = self.prompt_session.take_pastes(text)
        self._pastes = pastes
        if not self._refresh():
            return
        self._current = text
        loop = Loop(self.project, history=self.history, progress=self.progress, pasted=pastes)
        scope = self._task_consent
        if scope is not None and not scope.reusable(self.project):
            # A changed file, policy, model or recipient, the deadline or the budget ends the approved task.
            print("The approved task scope ended: %s. Approve the new preview." % SCOPE_ENDED.get(
                scope.ended, "the permissions, the model or the recipient changed"))
            scope.close()
            scope = self._task_consent = None
        if scope is not None:
            try:
                scope.follow_up(self.project, text)
            except GrantDenied:
                scope = self._task_consent = None
            else:
                if self.args.details:
                    self._scope(text, loop)                 # --v still shows exactly what goes out
                print(recipient("Within the task you approved: the question, file names and parts of %s go to Claude "
                                "(%d stage(s) and about %d min left; Ctrl+C stops).") % (
                    self.name, scope.stages_left, (scope.seconds_left + 59) // 60))
                pasted = [o for o in loop.observations if o["kind"] == "pasted"]
                if pasted:
                    print("With your pastes: " + ", ".join("#%d (%d lines, %d B)" % (
                        o["id"], o["lines"], len(o["text"].encode("utf-8"))) for o in pasted))
        if scope is None:
            scope = ProjectTaskConsent(self.project, text, clock=self._clock)
            self._scope(text, loop)
            try:
                approved = confirm("project-task", reader=self.reader)
            except EOFError:
                approved = False
            except KeyboardInterrupt:
                self._interrupted()
                approved = False
            if not approved:
                scope.close()
                print("Cancelled. No data was sent.")
                return
        keep = False
        try:
            if self._task_consent is not scope:
                # Approval re-scans: even the first stage must use the exact
                # project previewed before the owner answered.
                scope.approve(self.project)
                self._task_consent = scope
            for stage in range(1, MAX_STAGES + 1):
                if stage > 1:
                    print(self._coverage_line(loop, stage - 1))
                    scope.check(self.project)
                    loop = Loop(self.project, history=self.history, progress=self.progress, pasted=pastes,
                                visited=loop.visited, stage=stage)
                grant = scope.next_stage(self.project)
                session = TransferSession(self.binding, grant, clock=self._clock)
                self.tasks.append({"session": session, "label": "%s: %s (stage %d)" % (self.name, _safe(text, 50), stage)})
                self._open_section() if stage == 1 else self._open_section("Stage %d of %d" % (stage, MAX_STAGES))
                try:
                    partial = self._run(text, loop, session)
                finally:
                    self._close_section()
                if partial is None:
                    keep = True
                    return
            print("Reached the limit of %d stages; partial conclusions are above. Type \"continue\" to start another round." % MAX_STAGES)
            print()
            keep = True
        except GrantDenied as exc:
            print(GRANT_HINTS[exc.code])
        except KeyboardInterrupt:
            print("\nInterrupted.")
            self._interrupted()
        finally:
            # Only a question that ended normally keeps the task for a follow-up; a refusal or Ctrl+C asks again.
            if not keep or self._task_consent is not scope:
                scope.close()
                if self._task_consent is scope:
                    self._task_consent = None

    def _coverage_line(self, loop, stage):
        """Controller-counted coverage of the whole deep task (never the model's claim)."""
        total = sum(e.size for e in self.project.files)
        files = len(loop.visited)
        size = sum(self.project._by_path[p].size for p in loop.visited if p in self.project._by_path)
        return (recipient("Partial analysis, stage %d of %d: Claude read %d of %d files (%s of %s in the index).") % (
            stage, MAX_STAGES, files, len(self.project.files),
            "%.1f KiB" % (size / 1024), "%.1f MiB" % (total / 1048576)))

    def _refresh(self, announce=False):
        """Rescan before each question so the index matches the disk now; the grant binds the fresh manifest."""
        before = self.project.stats.get("indexed")
        try:
            self.project.scan()
        except (ProjectError, ValueError):
            print("Could not refresh the index (the policy or directory changed). Run the command again.")
            return False
        self.binding = Binding(self.project.project_id, self.project.manifest_sha256())
        now = self.project.stats["indexed"]
        if announce or now != before:
            print("Index refreshed: %d file(s)%s." % (now, "" if now == before else " (was %d)" % before))
        return True

    def _scope(self, text, loop):
        stats = self.project.stats
        pasted = [o for o in loop.observations if o["kind"] == "pasted"]
        skipped = ["%s: %d" % (label, stats[key]) for key, label in SKIP_LABELS if stats[key]]
        print()
        if not self.args.details:
            # Compact by default: what leaves the computer in one line, extras only when present.
            total = self._skipped_total()
            print(recipient("To Claude: the question, file names and parts from %s (%d file(s)%s%s)") % (
                self.name, stats["indexed"], ", %s skipped" % _entries(total) if total else "",
                "" if self.project.complete else "; INDEX INCOMPLETE: %s" % REASONS.get(self.project.reason, "limit")))
            extras = []
            if pasted:
                extras.append("pastes: " + ", ".join("#%d (%d lines, %d B)" % (o["id"], o["lines"], len(o["text"].encode("utf-8")))
                                                       for o in pasted))
            if loop.history:
                extras.append("context: %d earlier question(s) and answer(s)" % len(loop.history))
            if extras:
                print(" · ".join(extras))
            print("Task scope: up to %d stages (%d per question), %d requests and %d MiB of project data in %d minutes, "
                  "also for follow-up questions in this window; Ctrl+C stops." %
                  (TASK_STAGES, MAX_STAGES, TASK_REQUESTS, TASK_BYTES // 1048576, TASK_SECONDS // 60))
            print("A changed file, permission or model asks again. Tests, edit-data transfers and saving have their own approval.")
            return
        print(rule("To send"))
        print(field("Question", _safe(text, 200)))
        print(field("Project", "%s (%s), %d file(s)%s" % (
            self.name, self.project.project_id[:8], stats["indexed"],
            "" if self.project.complete else " — INDEX INCOMPLETE (%s)" % REASONS.get(self.project.reason, "limit"))))
        if skipped:
            print(field("Skipped", *skipped))
        if pasted:
            print(field("Pastes", *["#%d: %d lines, %d B" % (o["id"], o["lines"], len(o["text"].encode("utf-8"))) for o in pasted]))
        if loop.history:
            print(field("Context", "%d earlier question(s) and answer(s) from this session (shortened)" % len(loop.history)))
        print(field("Data", recipient("the question, plus the file names, matches and parts Claude asks for")))
        print(field("Without", "Internet, file writes or running code · task time limit %d s" % TASK_SECONDS))
        print(field("Content", recipient("Claude will ask for a list of file names, a search or lines of files from this project."),
                    "File names, matches and parts are sent as data; the rest of the project is not sent.",
                    "Up to %d stages (%d per question), %d requests and %d MiB of project data in total, also for follow-up "
                    "questions in this window until a file, permission or model changes; Ctrl+C stops." %
                    (TASK_STAGES, MAX_STAGES, TASK_REQUESTS, TASK_BYTES // 1048576),
                    "Each stage: up to %d requests, %d KiB of data and %d s." % (MAX_STEPS, TOTAL_BYTES // 1024, GRANT_SECONDS),
                    "Tests, additional edit-data transfers and saving have their own approval."))
        print(SEPARATOR)

    def _run(self, text, loop, session):
        prepare = lambda step_input: session.prepare_step(text, step_input, loop.data_bytes(), (), project=True)
        for number in range(1, loop.max_steps + 1):
            kind, value = self._step_request(number, loop, session, prepare, parse_decision, plain_answer)
            if kind == "end":
                return value
            if kind == "skill":
                continue
            packet, decision = value
            action = decision["action"]
            if action == "answer":
                return self._answer(text, loop, decision)
            # A late reply may be displayed, but cannot authorize a read,
            # test or edit after the original project scope changed/expired.
            self._task_consent.check(self.project)
            if action == "test":
                self._close_section()
                outcome = self.test_project(decision["recipe"])
                if outcome:
                    self.remember(text, outcome)  # only the controller status; raw local output stays in the terminal
                return
            if action == "edit":
                return self._edit(text, loop, decision, packet)
            if action == "ask_user":
                return self._ask_user(text, decision)
            if self._no_progress(number, loop, decision):
                return self._stopped(loop)
            try:
                note = loop.execute(decision)
            except ValueError:                  # ProjectError is a ValueError: a changed policy or directory
                print("The file policy changed during the task, so it was stopped.")
                return self._stopped(loop)
            self._line(recipient("  Step %d/%d: Claude asked for %s") % (number, loop.max_steps, _safe(note, 200)))
        return self._stopped(loop)

    def _delivered_lines(self, loop):
        """What Claude really received, one entry per file, as display lines."""
        received, size = loop.delivered_summary()
        if not received:
            return [], "no file lines", size
        lines = ["%s: %s" % (_safe(path, 80), lines_label(spans[:8]) + (" …" if len(spans) > 8 else ""))
                 for path, spans in list(received.items())[:10]]
        if len(received) > 10:
            lines.append("… (+%d more file(s))" % (len(received) - 10))
        return lines, "", size

    def _notes(self, loop, rejected=()):
        """Things a reader must not assume away: shown even without --v, and nothing else."""
        notes = []
        if rejected:
            notes.append(("Rejected", [_source(p, a, b) for p, a, b in rejected]
                          + [recipient("(Claude cited sources it did not receive)")]))
        if not self.project.complete:
            notes.append(("Index", [recipient("incomplete (%s) — there may be files Claude could not see") %
                                     REASONS.get(self.project.reason, "limit")]))
        partial = [needle for needle, complete in loop.searches if not complete]
        if partial:
            notes.append(("Incomplete", ["search (budget or further pages): " + ", ".join('"%s"' % _safe(n, 40) for n in partial)]))
        if loop.format_note:
            notes.append(self._plain_text_note())
        if loop.changed:
            notes.append(("Changed", [", ".join(_safe(p, 80) for p in loop.changed[:8]) + " (during the task or after the scan)"]))
        return notes

    def _received_field(self, loop):
        lines, empty, size = self._delivered_lines(loop)
        if not lines:
            return field(recipient("Claude received"), "%s; the rest of the project was not sent" % empty)
        return field(recipient("Claude received"), *lines, "(%s; the rest of the project was not sent)" % (
            "%d B of parts read" % size if size else "search results only"))

    def _skipped_field(self):
        stats = self.project.stats
        skipped = ["%s: %d" % (label, stats[key]) for key, label in SKIP_LABELS if stats[key]]
        return field("Out of reach", *skipped) if skipped else None

    def _edit(self, text, loop, decision, packet):
        from .run_controls import require_changes
        try:
            require_changes()
        except AnalysisDenied:
            print("This agent is read-only. The proposed change was rejected.")
            return self._remember_edit(text, "the agent profile allows reading only")
        """Claude proposes a change: map it to the owner's write policy, ask once, then hand over to the proven
        ask/task pipeline (copy test, full diff, separate write consent, rollback). Nothing is written here."""
        self._close_section()
        if decision.get("delete"):
            return self._delete(text, decision)
        try:
            request_text = self._expand_pastes(text)
            snapshot = assistant_flow.capture_access()
            # A newer policy must never authorize resending earlier project observations.
            try:
                self.policy.recheck()
            except (OSError, ValueError):
                raise EditRefused("The project policy changed. Start a new project session.") from None
            plan, existing, created = build_edit_plan(self.project, snapshot, decision)
            versions = {}
            for path, name in zip(decision["files"], existing):
                try:
                    _, raw = permissions.read_bounded(snapshot.policy, self.project.root + "/" + path,
                                                      task_contract.MAX_FILE)
                except ValueError:
                    raise EditRefused("File %s is unavailable or changed since the analysis. Ask again." % _safe(path, 80)) from None
                version = task_workspace.sha(raw)
                if loop.fully_read(path) and version != loop.delivered_versions.get(path):
                    raise EditRefused("File %s changed since the analysis. Ask again to work on the current version." % _safe(path, 80))
                versions[name] = version
            blocker = self._edit_blocker(snapshot, plan)
            if blocker:
                raise EditRefused(blocker)
        except (EditRefused, ProjectError, assistant_flow.AccessDenied, AnalysisDenied) as exc:
            print(rule("File change"))
            print(field("Refused", str(exc) if isinstance(exc, EditRefused) else "The file policy does not allow this change."))
            print(SEPARATOR)
            print()
            return self._remember_edit(text, "proposed change rejected by the app")
        # Content Claude has not seen yet would leave the computer in the edit request: that needs its own yes.
        # A new file, or files Claude already read whole in this task, add nothing new: no second question.
        unsent = [path for path in decision["files"] if not loop.fully_read(path)]
        summary = " · ".join(part for part in (
            "new: " + ", ".join(_safe(p, 80) for p in decision["create"]) if created else "",
            "to change: " + ", ".join(_safe(p, 80) for p in decision["files"]) if existing else "",
            "check: " + decision["check"]) if part)
        suite = decision["check"] in task_contract.TEST_SUITES
        code = any(path.endswith((".py", ".go", ".js", ".mjs", ".cjs", ".ts")) for path in decision["files"] + decision["create"])
        tests = test_files(self.project) if code and not suite else []
        untested = ("the project has tests (%s), but this change is checked with %s only: they are NOT run. "
                    "Ask for the change \"and check it with the tests\" to run them." % (
                        ", ".join(_safe(p, 60) for p in tests), decision["check"])) if tests else None
        test_line = ("%s once, offline, on a copy of the complete permitted project with the proposed changes "
                     "(network, user HOME and host writes blocked; output stays local)" % decision["check"])
        test_consent = None
        if unsent or suite:
            print(rule("File change"))
            print(field("To change", *[_safe(p, 80) for p in decision["files"]]))
            if created:
                print(field("New", *[_safe(p, 80) for p in decision["create"]]))
            print(field("Check", test_line if suite else decision["check"]))
            if untested:
                print(field("Warning", untested))
            if unsent:
                print(field("Next", recipient("the content of files Claude has not seen yet (%s) goes to Claude in a separate task;") %
                            ", ".join(_safe(p, 60) for p in unsent), "saving happens only after you see the diff and give separate consent."))
            else:
                print(field("Next", "saving happens only after you see the test result and the diff and give separate consent."))
            print(SEPARATOR)
            if suite:
                # S9: the declared test is part of this one decision; the copy is still re-checked before it runs.
                test_consent = self._edit_test_consent(decision["check"])
            try:
                approved = confirm(("project-edit-test" if unsent else "project-test") if suite else "project-edit",
                                   reader=self.reader)
            except EOFError:
                approved = False
            except KeyboardInterrupt:
                self._interrupted()
                approved = False
            if approved and test_consent is not None:
                try:
                    test_consent.approve()
                except AnalysisDenied:
                    approved = False
            if not approved:
                if test_consent is not None:
                    test_consent.close()
                print("Cancelled. File contents were not sent. Nothing was saved." if unsent
                      else "Cancelled. Nothing was tested or saved.")
                print()
                return self._remember_edit(text, "the user rejected the proposed change")
        else:
            print("File change — %s. Preparing the diff…" % summary)
            if untested:
                print("Warning: " + untested)
        try:
            code, fact = self._run_edit(request_text, snapshot, plan, versions, packet, test_consent=test_consent)
        finally:
            if test_consent is not None:
                test_consent.close()
        print()
        if fact:
            # The controller's own outcome with the next step, kept in the saved conversation (S9).
            if untested and code == 0:
                fact += "; the project's tests were NOT run (next: run them with /test or ask for a check with the tests)"
            return self._remember_edit(text, fact)
        return self._remember_edit(text, "change saved after the user's consent" if code == 0
                                   else "change NOT saved (rejected, failed test or error)")

    def _edit_test_consent(self, check):
        """One declared test on the full project copy, valid only while the approved project task still holds."""
        from .analysis_contract import CONSENT_CEILING, consent_seconds
        from .execution import current
        from .task_test_consent import CopyTestConsent
        selected = current()
        seconds = min(CONSENT_CEILING, 2 * consent_seconds(selected.get("effort") if selected is not None else None))
        task = self._task_consent

        def guard():
            if task is None or self._task_consent is not task:
                return False
            task.check(self.project)
            return True
        return CopyTestConsent(self._test_scope(check), check, guard=guard, seconds=seconds)

    def _test_scope(self, check):
        return {"root": self.project.root, "project": self.project.project_id, "copy": "permitted-project",
                "manifest": self.project.manifest_sha256(), "check": check, "test_runs": 1}

    def test_project(self, recipe):
        from . import project_checks
        return project_checks.run(self.project, recipe, self.reader).summary or None

    def _expand_pastes(self, text):
        """The edit request carries the pasted text itself (the marker alone would tell the model nothing)."""
        expanded = text
        for number, body in getattr(self, "_pastes", ()):
            expanded = expanded.replace(paste_token(number, body), body)
        if len(expanded.encode("utf-8")) > task_contract.MAX_INSTRUCTION:
            raise EditRefused("The file change request with its paste is over %d B. Shorten the paste or split the change." % task_contract.MAX_INSTRUCTION)
        return expanded

    def _edit_blocker(self, snapshot, plan):
        """Why a change cannot start right now (an unfinished earlier task, a full journal ...), in words."""
        try:
            workspace = snapshot.policy.write_workspace(int(plan["write"][1:]))
        except ValueError:
            return ("The write scope in permissions.toml does not point to a safe working directory "
                    "(e.g. a whole HOME needs policy version = 2; an entry like ~/Projects/** is recommended).")
        try:
            task_workspace._can_apply(workspace)
        except task_workspace.JournalLimit:
            return "Reached the limit of saved tasks in the write scope directory. Remove finished entries with: ai-desktop task prune --workspace DIRECTORY."
        except ValueError as exc:
            if str(exc) == "recovery required":
                return ("An earlier file change task is unfinished or in conflict. "
                        "Check: ai-desktop task status --workspace DIRECTORY (then task rollback).")
            return "The task journal in the write scope directory is unreadable or has unexpected entries (ai-desktop task status)."
        except (ExecutionDenied, OSError):
            return "The write scope directory is unavailable or has unsafe permissions."
        return None

    def _delete(self, text, decision):
        from .run_controls import require_changes
        try:
            require_changes()
        except AnalysisDenied:
            print("This agent is read-only. The proposed deletion was rejected.")
            return self._remember_edit(text, "the agent profile allows reading only")
        """Deleting is moving to a recoverable trash: it needs the owner's own yes (nothing is sent to Claude)."""
        print(rule("File deletion"))
        try:
            snapshot = assistant_flow.capture_access()
            scope, workspace, names = build_delete_plan(self.project, snapshot, decision)
            prepared = workspace_trash.prepare(workspace, names)
        except (EditRefused, assistant_flow.AccessDenied, AnalysisDenied, ExecutionDenied, ValueError, OSError) as exc:
            print(field("Refused", str(exc) if isinstance(exc, EditRefused)
                        else workspace_trash.FULL_MESSAGE if isinstance(exc, workspace_trash.TrashFull)
                        else "The file policy does not allow this deletion."))
            print(SEPARATOR)
            print()
            return self._remember_edit(text, "proposed deletion rejected by the app")
        print(field("To delete", *["%s (%d B)" % (_safe(p, 80), e["size"])
                                     for p, e in zip(decision["delete"], prepared.data()["entries"])]))
        print(field("Trash", recipient("files are moved to a private task directory and can be restored; nothing goes to Claude")))
        print(SEPARATOR)
        try:
            approved = confirm("project-delete", reader=self.reader)
        except EOFError:
            approved = False
        except KeyboardInterrupt:
            self._interrupted()
            approved = False
        if not approved:
            print("Cancelled. Nothing was deleted.")
            print()
            return self._remember_edit(text, "the user rejected the file deletion")
        try:
            snapshot.policy.recheck()
            plan = workspace_trash.execute(prepared, prepared.authorize())
        except workspace_trash.TrashRecoveryRequired as exc:
            print("Deletion interrupted. The result needs checking; the data and journal were kept.")
            self._trash_recovery(workspace, exc.plan)
            return self._remember_edit(text, "deletion interrupted; the task trash needs checking")
        except KeyboardInterrupt:
            print("\nDeletion interrupted. Check the task state before the next operation.")
            self._trash_recovery(workspace, prepared.data()["plan"])
            return self._remember_edit(text, "deletion interrupted; the state needs checking")
        except (ExecutionDenied, ValueError, OSError):
            print("Not deleted: a file changed, a name is unusual or the policy changed. Nothing was deleted.")
            print()
            return self._remember_edit(text, "file deletion failed, nothing was deleted")
        print("Deleted %d file(s) (in the task trash)." % len(names))
        print("Restore: " + shlex.join(["ai-desktop", "task", "restore", "--workspace", workspace, "--plan", plan]))
        print()
        return self._remember_edit(text, "deleted %d file(s) after the user's consent (can be restored)" % len(names))

    @staticmethod
    def _trash_recovery(workspace, plan):
        print("State: " + shlex.join(["ai-desktop", "task", "status", "--workspace", workspace]))
        print("Restore: " + shlex.join(["ai-desktop", "task", "restore", "--workspace", workspace, "--plan", plan]))
        print()

    def _run_edit(self, text, snapshot, plan, versions, packet, *, test_consent=None):
        args = ask_cli.build_parser().parse_args(["--v"] if self.args.details else [])
        args.format = self.args.format
        permit = prepared = None
        try:
            self.window.close()                       # a short-lived window ends here; the conversation's kept session stays
            self.pacer.wait_turn(_countdown, _countdown_end)
            self._task_consent.check(self.project)
            # Only data from the exact packet that elicited this edit may cross the hand-off.
            # The context is subsequently sealed together with the selected files in WriteGrant.
            from .execution import current
            envelope = json.loads(packet.payload)
            if (not packet.verify() or envelope.get("execution") != current() or
                    envelope["project"]["project"]["id"] != self.project.project_id):
                raise ask_edit.EditDenied("consent")
            permit = QuestionConsent(text, snapshot, plan_ready=True)
            permit.approve()
            prepared = ask_edit.prepare(plan, snapshot, args, self.reader, question_consent=permit,
                                        expected_versions=versions, project_context=envelope["project"], test_root=self.project.root)
            self.pacer.record()
            sent = prepared.source["files"]
            transfer = {"files": [item["path"] for item in sent if item["content"] is not None],
                        "bytes": sum(len(item["content"].encode("utf-8")) for item in sent if item["content"] is not None),
                        "state": "not known whether it arrived"}      # recorded before the hand-off, like the read steps
            self.edit_transfers.append(transfer)
            def check_edit_scope():
                self._task_consent.check(self.project)
                permit.check()
            response = ask_cli.request(text, args, self.reader, task=prepared.source, write_grant=prepared.write_grant,
                                       check=check_edit_scope, question_consent=permit)
            transfer["state"] = "answer received"
            check_edit_scope()
            permit.close()
            approved_test = None
            if test_consent is not None:
                scope = self._test_scope(prepared.check)
                approved_test = lambda: test_consent.consume(scope, prepared.check)
            done = ask_edit.finish(prepared, response, args, self.reader, approved_test=approved_test)
            return done.code, done.fact
        except GrantDenied as exc:
            print(GRANT_HINTS[exc.code])
        except InputBudgetExceeded:
            print("The agreed context and selected files exceed the edit request limit. Narrow the task; nothing was saved.")
        except ask_edit.CreateCollision:
            print("The file to create already exists. Nothing was saved.")
        except ask_edit.EditInterrupted as stopped:
            # Not "nothing was saved": the save began, so some files may already be written.
            command = shlex.join(["ai-desktop", "task", "status", "--workspace", prepared.workspace])
            print("Saving was interrupted: %d of %d file(s) confirmed saved, the rest may differ." % (stopped.saved, stopped.total))
            print("Check: " + command + ", then ai-desktop task rollback for this directory.")
            return 2, ("change INTERRUPTED while saving (%d of %d file(s) saved); next: ai-desktop task status, "
                       "then task rollback" % (stopped.saved, stopped.total))
        except ask_edit.EditConflict:
            print("The file changed after it was read (another editor or terminal). Nothing was overwritten.")
            return 2, "change NOT saved: a file changed after it was read; next: ask again to work on the current version"
        except ask_edit.EditBusy:
            print("Another terminal is saving in this directory right now. Nothing was written.")
            return 2, "change NOT saved: another terminal was saving in this directory; next: ask again when it is done"
        except (ask_edit.EditDenied, ExecutionDenied) as exc:
            print("The change was not prepared (the consent, policy or file state changed). Nothing was saved.")
        except AnalysisDenied as exc:
            print(failure_hint(getattr(exc, "reason", None), HINTS.get(getattr(exc, "reason", None), "Could not get an answer. Check ai-desktop status."),
                               **({"diagnostic": exc.diagnostic} if exc.diagnostic is not None else {})))
            print("Data already handed over cannot be taken back; the change will not be retried automatically.")
        except assistant_flow.Declined:
            print("Cancelled.")
        except KeyboardInterrupt:
            print("\nInterrupted.")
            self._interrupted()
        finally:
            if permit is not None:
                permit.close()
        return 2, ""

    def _interrupted(self):
        # Ctrl+C is a cancellation: the next question asks for the task scope again.
        if self._task_consent is not None:
            self._task_consent.close()
            self._task_consent.ended = "CANCELLED"
        return super()._interrupted()

    def _remember_edit(self, text, outcome):
        self.remember(text, "[%s]" % outcome)

    def _skipped_total(self):
        stats = self.project.stats
        return sum(stats[key] for key, _ in SKIP_LABELS)

    def _skipped_line(self):
        """One short line shown by default: excluded entries affect completeness, but their names are never shown."""
        total = self._skipped_total()
        return recipient("Out of reach for Claude: %s skipped during the scan (details: --v).") % _entries(total) if total else None

    def _answer(self, text, loop, decision):
        confirmed, rejected = loop.confirm_sources(decision["sources"])
        self._show_answer(decision)
        notes = self._notes(loop, rejected)
        if self.args.details:                       # --v: the full account of sources and scope, every time
            self._sources_block([_source(p, a, b) for p, a, b in confirmed])
            print()
            print(rule("Scope"))
            print(self._received_field(loop))
            skipped = self._skipped_field()
            if skipped:
                print(skipped)
            self._print_notes(notes)
            print(SEPARATOR)
            print("ASK: PASS")
        elif notes:                                  # default: silence unless there is something to be careful about
            print("\n" + rule("Note"))
            self._print_notes(notes)
            if self._skipped_line():
                print(field("Out of reach", "%s skipped during the scan (details: --v)" % _entries(self._skipped_total())))
            print(SEPARATOR)
            self._skipped_shown = self._skipped_total()
        elif self._skipped_line() and self._skipped_total() != self._skipped_shown:
            print("\n" + self._skipped_line())          # reported once per session, again only if the count changes
            self._skipped_shown = self._skipped_total()
        print()
        self.remember(text, decision["text"])
        if decision.get("progress"):
            self.progress = decision["progress"]
        return decision if decision.get("continue") else None

    def _stopped(self, loop):
        self._stop_header()
        print(self._received_field(loop))
        if self.args.details:
            skipped = self._skipped_field()
            if skipped:
                print(skipped)
        self._print_notes(self._notes(loop))
        if not self.args.details and self._skipped_line():
            print(field("Out of reach", "%s skipped during the scan (details: --v)" % _entries(self._skipped_total())))
        print(SEPARATOR)
        print()
        received, size = loop.delivered_summary()
        self._remember_stop(received, "parts of %d file(s) (%d B)" % (len(received), size))

    # --- loop ----------------------------------------------------------------------------
    def run(self):
        stats = self.project.stats
        print()
        print("Project %s (%s): %d file(s) in the index%s. Scanned locally." % (
            self.name, self.project.project_id[:8], stats["indexed"],
            "" if self.project.complete else " (index incomplete: %s)" % REASONS.get(self.project.reason, "limit")))
        print(recipient("Only what you approve goes to Claude."))
        print()
        print("Options:")
        print("/exit      quits")
        print("/help      shows optional commands")
        print()
        print("What do you want to do with it?")
        if not self.can_send:
            print(network_hint(short=True))
        return self._loop()

    def _loop(self):
        try:
            while True:
                try:
                    line = self._read_command()
                except EOFError:
                    return 0
                except KeyboardInterrupt:
                    print()
                    return 0
                if not self._dispatch(line.strip()) or self._quit:
                    return 0
        finally:
            try:
                self.window.close()
            except AnalysisDenied:
                print("Could not confirm that the session stopped. Run ai-desktop stop.")

    def _dispatch(self, line):
        if not line:
            return True
        if line.casefold() in ("koniec", "exit"):         # the bare word ends the session too, it is not a question
            return False
        if not line.startswith("/"):
            self.question(line)
            return True
        command, _, rest = line.partition(" ")
        rest = rest.strip()
        if command in SETTING_COMMANDS:
            # The same menus and checks as in `ask`; a saved conversation keeps its agent, its model and level may change.
            self.controls().handle(line, getattr(self, "conversation", None), reader=self.reader)
            return True
        if command in EXIT_COMMANDS:
            return False
        if command in ("/pomoc", "/help", "/?"):
            print(recipient(HELP))
        elif command == "/test":
            self.test_project(rest)
        elif command in ("/files", "/pliki"):
            self.files(rest)
        elif command in ("/search", "/szukaj"):
            self.search(rest)
        elif command in ("/log", "/rejestr"):
            self.ledger()
        elif command in ("/refresh", "/odswiez"):
            self._refresh(announce=True)
        else:
            print("Unknown command. /help shows the available ones.")
        return True


from .run_controls import configured


@configured
def main(argv=None, *, reader=input, clock=time.monotonic, sleep=time.sleep, window=None, sender=send_once):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if os.geteuid() == 0 or os.getuid() != os.geteuid():
            raise AnalysisDenied("identity")
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise AnalysisDenied("consent")
        try:
            policy = permissions.load_policy(optional=True)
        except ValueError as exc:
            print(_read_hint(exc))
            return 2
        if policy is None:
            print("No permissions.toml: this directory needs a files.read scope. See config/permissions.example.toml.")
            return 2
        try:
            project = Project(policy, os.path.abspath(os.path.expanduser(args.path))).scan()
        except ProjectError as exc:
            print(ROOT_HINTS.get(exc.code, ROOT_HINTS["PROJECT_ROOT"]))
            return 2
        except ValueError as exc:
            print(_read_hint(exc))
            return 2
    except AnalysisDenied:
        print("This command needs a regular user and a terminal.")
        return 2
    session = ProjectSession(project, os.path.basename(project.root), policy, args, reader, clock=clock, sleep=sleep,
                             window=window, sender=sender)
    try:
        if args.resume:
            if args.session is not None:
                print("Use either -c or --session ID, not both.")
                return 2
            args.session = latest_conversation(project.root)
            if args.session is None:
                print("No saved conversation about this directory yet; starting a new one.")
        # Like `ai-desktop`: only a real interactive conversation saves itself (a test double or a pipe never does).
        from .local_settings import saving
        session._auto_save = args.session is None and not args.no_save and reader is input and saving()
        session._save_target = ("project", project.root, conversation_binding(project.root))
        session.attach_conversation("project", project.root, conversation_binding(project.root))
        if args.session not in (None, "new"):
            session.show_earlier()
        from .material_cli import conversation
        with conversation(session, linked=window is None):
            return session.run()
    except (OSError, ValueError):
        print("Cannot use the saved conversation (it may belong to another agent, or was saved by an older version for "
              "one version of the files); check ai-desktop sessions list.")
        return 2
    finally:
        session.close_conversation()


if __name__ == "__main__":
    raise SystemExit(main())
