"""Adapt one configured edit proposal to the existing tested task executor.

The model selects a configured write scope, never an arbitrary workspace.
Existing data must also be readable; creation grants never read old contents.
"""
from dataclasses import dataclass
import json
import shlex
from types import SimpleNamespace

from . import conversation_state, file_context, permissions, task_contract, task_workspace, workspace_trash
from .analysis_contract import AnalysisDenied, canonical, terminal_text
from .assistant_contract import parse_plan
from .question_consent import QuestionConsent
from .terminal_style import size_label
from .workspace_executor import ExecutionDenied


class EditDenied(AnalysisDenied):
    """A local edit selection or its bound state was refused. `hint` (optional) says why, for the user."""

    def __init__(self, reason, hint=None):
        super().__init__(reason)
        self.hint = hint


class EditConflict(EditDenied):
    """The file changed after it was read (another terminal or an editor): nothing was overwritten."""


class EditInterrupted(EditDenied):
    """The save stopped after it began: `saved` of `total` files are written, the journal awaits recovery."""

    def __init__(self, saved, total):
        self.saved, self.total = saved, total
        super().__init__("input")


class EditBusy(EditDenied):
    """Another terminal is saving in this directory right now: nothing was written."""


# Expected version conflicts of a write; each ends only this proposal, never the conversation.
CONFLICTS = frozenset({"workspace changed", "source changed", "changed target", "changed file"})


class CreateCollision(EditDenied):
    """A validated creation path exists; never includes its contents."""

    def __init__(self, path, *, workspace):
        self.path = task_contract.path(path)
        # Local UI metadata only: never add this root to the model question.
        self.workspace = permissions._absolute(workspace)
        super().__init__("input")


@dataclass(frozen=True)
class CompletedTask:
    code: int
    fact: str = ""          # what the controller really did and the next step, for the conversation record (S9)


@dataclass(frozen=True)
class PreparedEdit:
    before: dict
    source: dict
    check: str
    workspace: str
    write_grant: object
    project_test: object = None
    local: dict = None              # the whole local contents when some files went to the model only as excerpts


class WriteGrant:
    def __init__(self, permit, snapshot, index, before, source, read_paths, targets):
        if type(permit) is not QuestionConsent or snapshot.policy is None:
            raise EditDenied("consent")
        self._permit, self._snapshot, self._policy = permit, snapshot, snapshot.policy
        self._index = index
        self._before, self._source = before, source
        self._policy_state = canonical({"access": snapshot.access, "sources": snapshot.sources})
        self._read_paths, self._targets = tuple(read_paths), tuple(targets)
        self._state = self._binding()

    def _binding(self):
        return canonical({"before": self._before, "source": self._source,
                          "index": self._index, "read_paths": self._read_paths, "targets": self._targets})

    def check(self):
        """Write approval has its own TTL; policy and targets remain bound."""
        try:
            if (self._snapshot.policy is not self._policy
                    or canonical({"access": self._snapshot.access, "sources": self._snapshot.sources}) != self._policy_state
                    or self._binding() != self._state):
                raise ValueError()
            self._snapshot.require_claude()
            if self._read_paths and self._policy.select(self._read_paths, limit=self._policy.limits().read_files) != self._read_paths:
                raise ValueError()
            if self._policy.require_write(self._targets, index=self._index) != self._targets:
                raise ValueError()
            try:
                task_workspace.unchanged(self._before)
            except ValueError as exc:
                if exc.args and exc.args[0] in CONFLICTS:
                    raise EditConflict("input") from None      # another terminal or an editor saved the file
                raise
            self._snapshot.require_claude()
            if (self._snapshot.policy is not self._policy or self._binding() != self._state
                    or canonical({"access": self._snapshot.access, "sources": self._snapshot.sources}) != self._policy_state):
                raise ValueError()
        except EditConflict:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError, ExecutionDenied):
            raise EditDenied("input") from None

    def require_transfer(self, permit, source):
        if permit is not self._permit or canonical(source) != canonical(self._source):
            raise EditDenied("consent")
        permit.require_write_scope(self._policy, "w" + str(self._index))
        if source["instruction"] != permit.question:
            raise EditDenied("consent")
        self.check()
        permit.check()


def _size_hint(policy, path, code):
    """Why a file of an edit could not be read whole, in the owner's own settings."""
    limits = policy.limits()
    name = terminal_text(path.rsplit("/", 1)[1])
    if code == "files-size":
        return ("%s is larger than files.read_file_size (%s), so it cannot be sent whole. Ask Claude to find the place to "
                "change first (a search or a range of lines), then it edits only that part; or raise read_file_size in "
                "permissions.toml (up to 8 MiB)." % (name, size_label(limits.read_file_size)))
    if code == "files-total":
        return ("The files of this change are together larger than files.read_total_size (%s). Change fewer files at "
                "once or raise read_total_size in permissions.toml." % size_label(limits.read_total_size))
    return None


def prepare(plan, snapshot, args, reader, *, question_consent, expected_versions=None, project_context=None, test_root=None,
            excerpts=None):
    from .run_controls import require_changes
    require_changes()
    """Snapshot only approved readable data and absent writable new targets."""
    try:
        plan = parse_plan(canonical(plan), snapshot.access)
        if plan["action"] != "edit" or type(question_consent) is not QuestionConsent:
            raise ValueError()
        policy = snapshot.policy
        question_consent.require_write_scope(policy, plan["write"])
        index = int(plan["write"][1:])
        scopes = {"w" + str(scope.index): scope for scope in policy.write_scopes()}
        scope = scopes[plan["write"]]
        advertised = {item["id"]: item["pattern"] for item in snapshot.access.get("write", [])}
        if scope.pattern != advertised[plan["write"]]:
            raise ValueError()
        workspace = policy.write_workspace(index)
        detached = task_workspace._can_apply(workspace)
        if args.details and type(detached) is int and detached:
            print(task_workspace.DETACHED_HISTORY_HINT)
        read_paths, contents, large = (), {}, {}
        if plan["files"]:
            question_consent.require_files(policy, plan["files"])
            read_paths = file_context.resolve_paths(policy, plan["files"])
            if any(not path.startswith(workspace + "/") for path in read_paths):
                raise ValueError()
            question_consent.check()
            # S3: a file larger than read_file_size goes to the model only as the excerpts it already read in this
            # question; the whole file is read locally (below) and must be the version those excerpts came from.
            limit = policy.limits().read_file_size
            large = {path: entry for path, entry in (excerpts or {}).items()
                     if path in read_paths and entry["size"] > limit and entry["fragments"]}
            ordinary = tuple(path for path in read_paths if path not in large)
            for path in large:
                if policy.select(permissions.recheck_selection(policy, (path,))) != (path,):
                    raise ValueError()
            if ordinary:
                try:
                    bundle = permissions.read_selected(policy, ordinary)
                except ValueError as exc:
                    hint = _size_hint(policy, ordinary[0], exc.args[0] if exc.args else "")
                    if hint is not None:
                        raise EditDenied("input", hint) from None
                    raise
                conversation_state.note_sources([file["path"] for file in bundle["files"]])
                contents = {item["path"]: item["content"] for item in bundle["files"]}
                if set(contents) != set(ordinary):
                    raise ValueError()
            if large:
                conversation_state.note_sources(list(large))
        question_consent.check()
        existing = [task_contract.path(path[len(workspace) + 1:]) for path in read_paths]
        if expected_versions is not None and (type(expected_versions) is not dict or set(expected_versions) != set(existing)):
            raise ValueError("expected versions")
        created = list(plan["create"])
        names = existing + created
        if not names or len(set(names)) != len(names) or len(names) > task_contract.MAX_FILES:
            raise ValueError()
        if created:
            policy.require_write(tuple(workspace + "/" + name for name in created), index=index)
        try:
            before = task_workspace.snapshot(workspace, names, must_be_absent=created)
        except task_workspace.NewTargetExists as exc:
            # Only a typed, pre-read collision may lead to a user clarification.
            # All other refusals still fail closed without a model retry.
            raise CreateCollision(exc.path, workspace=workspace) from None
        except ValueError as exc:
            if str(exc) == "text":
                print("This file cannot be edited without changing its bytes: it has mixed line endings or control characters.")
            raise
        files, local, targets = [], [], []
        for name in names:
            path = workspace + "/" + name
            old = before["files"][name]["file"]
            if path in large:
                if old is None or not policy.can_write(path, index=index):
                    raise ValueError()
                if old["sha256"] != large[path]["version"]:
                    raise EditConflict("input")         # changed since Claude read the excerpts: nothing is sent
                parts = [{"first": item["first"], "last": item["last"], "text": task_contract.plain_text(item["text"])}
                         for item in sorted(large[path]["fragments"], key=lambda item: item["start"])]
                kept, used = [], 0
                for part in parts[-task_contract.MAX_EXCERPTS:]:
                    used += len(part["text"].encode("utf-8"))
                    if used > task_contract.MAX_EXCERPT_TOTAL:
                        break
                    kept.append(part)
                if not kept:
                    raise EditDenied("input", _size_hint(policy, path, "files-size"))
                targets.append(path)
                files.append({"path": name, "excerpts": kept, "size": large[path]["size"], "writable": True})
                local.append({"path": name, "content": task_contract.plain_text(old["content"]), "writable": True})
                continue
            if name in created:
                if old is not None:
                    raise ValueError()
                content, writable = None, True
            else:
                if old is None or task_contract.plain_text(old["content"]) != contents[path]:
                    raise ValueError()
                if expected_versions is not None and old["sha256"] != expected_versions[name]:
                    raise ValueError("changed since project approval")
                content = contents[path]
                writable = policy.can_write(path, index=index)
            if writable:
                targets.append(path)
            files.append({"path": name, "content": content, "writable": writable})
            local.append({"path": name, "content": content, "writable": writable})
        source = {"instruction": question_consent.question, "files": files}
        if project_context is not None:
            source["project_context"] = project_context
        source = task_contract.request(source)
        whole = task_contract.request({"instruction": question_consent.question, "files": local}) if large else None
        grant = WriteGrant(question_consent, snapshot, index, before, source, tuple(p for p in read_paths if p not in large),
                           targets)
        grant.require_transfer(question_consent, source)
        project_test = None
        if plan["check"] in task_contract.TEST_SUITES:
            from .project_checks import current_directory
            from .project_edit_test import PreparedProjectTest
            root = test_root if test_root is not None else current_directory(policy)
            if root is None:
                print("Project suites need the project directory covered by a recursive files.read rule. "
                      "Open that directory before asking for the edit and test.")
                raise ValueError("project test root")
            if any(not target.startswith(root + "/") for target in targets):
                print("The files to edit are outside the project selected for the test. Open their project and retry.")
                raise ValueError("edit outside tested project")
            try:
                project_test = PreparedProjectTest(policy, root, workspace, before)
            except ValueError as exc:
                from .project_scope import TestScopeRefused
                if isinstance(exc, TestScopeRefused):
                    print("The local project test copy cannot be prepared. Nothing was sent or saved.")
                    for detail in exc.details:
                        print(detail)
                raise
        return PreparedEdit(before, source, plan["check"], workspace, grant, project_test, whole)
    except (EditDenied, AnalysisDenied):
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ExecutionDenied):
        raise EditDenied("input") from None


def delete_files(plan, snapshot, reader):
    """Move the listed files to the task's recoverable trash after the owner's yes. Nothing is sent to the model."""
    from .run_controls import require_changes
    from .terminal_consent import confirm
    require_changes()
    policy = snapshot.policy
    try:
        if policy is None or plan["action"] != "delete" or plan["write"] not in {
                item["id"] for item in snapshot.access.get("write", [])}:
            raise ValueError()
        index = int(plan["write"][1:])
        workspace = policy.write_workspace(index)
        names = [task_contract.path(name) for name in plan["paths"]]
        policy.require_write(tuple(workspace + "/" + name for name in names), index=index)
        prepared = workspace_trash.prepare(workspace, names)
        sizes = [entry["size"] for entry in prepared.data()["entries"]]
    except workspace_trash.TrashFull:
        print(workspace_trash.FULL_MESSAGE)
        raise EditDenied("input") from None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ExecutionDenied):
        raise EditDenied("input") from None
    print()
    print("To delete in " + terminal_text(workspace) + ":")
    for name, size in zip(names, sizes):
        print("  " + terminal_text(name) + " (" + size_label(size) + ")")
    summary = ", ".join(names[:4])
    if not confirm("project-delete", reader=reader):
        print("Cancelled. Nothing was deleted.")
        conversation_state.note_action("Deletion NOT done (declined by the user): " + summary)
        return CompletedTask(2)
    restore = shlex.join(["ai-desktop", "task", "restore", "--workspace", workspace, "--plan", prepared.data()["plan"]])
    try:
        policy.recheck()
        plan_id = workspace_trash.execute(prepared, prepared.authorize())
    except workspace_trash.TrashRecoveryRequired:
        print("Deletion interrupted; the data and journal were kept. State: "
              + shlex.join(["ai-desktop", "task", "status", "--workspace", workspace]))
        print("Restore: " + restore)
        conversation_state.note_action("Deletion interrupted, needs checking: " + summary)
        return CompletedTask(2)
    except KeyboardInterrupt:
        print("\nDeletion interrupted. Restore: " + restore)
        conversation_state.note_action("Deletion interrupted, needs checking: " + summary)
        return CompletedTask(2)
    except (OSError, ValueError, ExecutionDenied):
        raise EditDenied("input") from None
    print("Moved to the task trash: %d." % len(names))
    print("Restore: " + shlex.join(["ai-desktop", "task", "restore", "--workspace", workspace, "--plan", plan_id]))
    conversation_state.note_action("Moved to the task trash, can be restored: " + summary)
    return CompletedTask(0)


def transfer_files(plan, snapshot, reader):
    """Move or copy a file or a whole folder inside one write scope after the owner's yes; nothing is overwritten and
    the step can be undone with `ai-desktop task restore`. Nothing is sent to the model (S10)."""
    from . import workspace_transfer
    from .run_controls import require_changes
    from .terminal_consent import confirm
    require_changes()
    policy = snapshot.policy
    operation = plan.get("action")
    try:
        if policy is None or operation not in ("move", "copy") or plan["write"] not in {
                item["id"] for item in snapshot.access.get("write", [])}:
            raise ValueError()
        index = int(plan["write"][1:])
        workspace = policy.write_workspace(index)
        source, target = task_contract.path(plan["from"]), task_contract.path(plan["to"])
        absolute = (workspace + "/" + source, workspace + "/" + target)
        # Both ends inside files.write; the source must also be readable (deny and the default protection win).
        policy.require_write(absolute, index=index)
        if not policy.allows(absolute[0]):
            raise ValueError()
        prepared = workspace_transfer.prepare(workspace, operation, source, target)
        facts = prepared.data()
    except workspace_transfer.TransferDenied as refused:
        reasons = {"exists": "the destination already exists (nothing is overwritten)", "missing": "it does not exist",
                   "device": "they are on different filesystems: copy instead of move",
                   "type": "it holds a link, a device or a file you do not own",
                   "space": "there is not enough free space", "inside": "a folder cannot go inside itself",
                   "size": "it has more than %d entries" % workspace_transfer.MAX_FILES}
        print("Refused: cannot %s %s to %s: %s." % (operation, terminal_text(plan.get("from", "")),
                                                    terminal_text(plan.get("to", "")),
                                                    reasons.get(refused.reason, "the workspace does not allow it")))
        conversation_state.note_action("%s NOT done (%s): %s -> %s" % (operation.capitalize(), refused.reason,
                                                                        plan.get("from"), plan.get("to")))
        return CompletedTask(2)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ExecutionDenied):
        raise EditDenied("input") from None
    print()
    what = "folder (%d files, %d folders)" % (facts["files"], facts["folders"]) if facts["kind"] == "folder" else "file"
    print("%s in %s:" % (operation.capitalize(), terminal_text(workspace)))
    print("  %s  ->  %s" % (terminal_text(source), terminal_text(target)))
    print("  %s, %s; the destination does not exist yet, nothing is overwritten" % (what, size_label(facts["bytes"])))
    if not confirm(operation, reader=reader):
        print("Cancelled. Nothing was changed.")
        conversation_state.note_action("%s NOT done (declined by the user): %s -> %s" % (operation.capitalize(),
                                                                                         source, target))
        return CompletedTask(2)
    try:
        policy.recheck()
        plan_id = workspace_transfer.execute(prepared, prepared.authorize())
    except workspace_transfer.TransferDenied as refused:
        print("Refused: the files changed or the destination appeared after the plan was shown (%s). Nothing "
              "was overwritten." % refused.reason)
        conversation_state.note_action("%s NOT done (changed): %s -> %s" % (operation.capitalize(), source, target))
        return CompletedTask(2)
    except (OSError, ValueError, ExecutionDenied):
        raise EditDenied("input") from None
    print("Moved." if operation == "move" else "Copied.")
    print("Undo: " + shlex.join(["ai-desktop", "task", "restore", "--workspace", workspace, "--plan", plan_id]))
    conversation_state.note_action("%s %s -> %s (can be undone with ai-desktop task restore)" % (
        "Moved" if operation == "move" else "Copied", source, target))
    return CompletedTask(0)


def finish(prepared, response, args, reader, *, approved_test=None):
    """approved_test: the owner's earlier yes for exactly this declared project test (S9), spent on the attempt."""
    from .task_cli import finish_task
    values = dict(vars(args), workspace=prepared.workspace, test=prepared.check)
    if approved_test is not None and prepared.project_test is None:
        raise EditDenied("consent")
    outcome = {}
    local = prepared.local if prepared.local is not None else prepared.source
    try:
        code = finish_task(prepared.before, local, response, SimpleNamespace(**values), reader,
                           write_guard=prepared.write_grant.check, project_test=prepared.project_test,
                           approved_test=approved_test, outcome=outcome,
                           excerpts=task_contract.excerpt_texts(prepared.source) or None)
    except EditConflict:
        conversation_state.note_action("Change NOT saved: the file changed after it was read; nothing was overwritten")
        raise
    except task_workspace.SaveInterrupted as stopped:
        # Not a refusal before writing: some files may be written. The truth goes to the conversation, with recovery.
        conversation_state.note_action(
            "Change INTERRUPTED while saving: %d of %d file(s) confirmed saved, the rest may differ; "
            "check ai-desktop task status --workspace %s, then task rollback" % (stopped.saved, stopped.total,
                                                                                  prepared.workspace))
        raise EditInterrupted(stopped.saved, stopped.total) from None
    except (ExecutionDenied, RecursionError):
        raise EditDenied("input") from None
    except BlockingIOError:
        conversation_state.note_action("Change NOT saved: another terminal was saving in this directory")
        raise EditBusy("input") from None
    except ValueError as exc:
        if not (exc.args and exc.args[0] in CONFLICTS):
            raise
        conversation_state.note_action("Change NOT saved: the file changed after it was read; nothing was overwritten")
        raise EditConflict("input") from None
    fact = _change_fact(response, code, prepared.check, outcome.get("reason"))
    conversation_state.note_action(fact)
    return CompletedTask(code, fact)


# The controller's own reason (task_cli.finish_task) -> what happened and the next step; never the model's words.
_NOT_APPLIED = {
    "unchanged": "No change needed: the file already had the proposed content; nothing was written",
    "test-cancelled": "Change NOT applied: the user cancelled the test; nothing was written. Next: ask again to retry",
    "test-failed": "Change NOT applied: test %s failed on the copy; nothing was written. "
                   "Next: fix the change or the test, then ask again",
    "write-declined": "Change NOT applied: the user declined the save after the diff; nothing was written",
}


def _change_fact(response, code, check, reason=None):
    """What really happened to a proposed change, from the controller's own result (never from the model's words)."""
    try:
        proposal = json.loads(response)
        files = ", ".join(str(item["path"]) for item in proposal["files"][:4])
        summary = " ".join(str(proposal["summary"]).split())[:120]
    except (ValueError, TypeError, KeyError, AttributeError):
        files, summary = "?", ""
    if code == 0:
        return ("Change applied (test %s passed; undo: ai-desktop task rollback): %s [%s]" % (check, summary, files)).strip()
    if reason in _NOT_APPLIED:
        text = _NOT_APPLIED[reason]
        return ("%s: %s [%s]" % (text % check if "%s" in text else text, summary, files)).strip()
    return "Change NOT applied (code %s; cancelled, refused or test failed): %s [%s]" % (code, summary, files)
