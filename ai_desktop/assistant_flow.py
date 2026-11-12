"""One bounded read proposal, checked against the user's captured permissions.

The model chooses data it needs, not its authority. Relative selections must
remain inside the configured scope. No commands, URLs or recursive tool loop
are accepted. One approval covers this
question and its captured permissions; each payload has its own managed session.
"""
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
import functools
import json
import os

from .run_controls import recipient, recipient_config, wait_message
from .execution import current as current_execution
from . import ask_context, ask_discovery, ask_edit, conversation_state, file_context, permissions, ask_followup
from .assistant_contract import PlanError, parse_plan, validate_access, _constant as _no_constant, _unique as _unique_keys
from .analysis_contract import (AnalysisDenied, ContextBudgetExceeded, InputBudgetExceeded, application_input,
                                terminal_text)
from .question_consent import QuestionConsent
from .discovery_contract import MAX_SKILL_ROUNDS
from .terminal_consent import confirm
from .terminal_style import card, paint, size_label, symbol


# Set only by the interactive conversation: a planner reply that is plain prose instead of a JSON plan is shown as an
# answer. It carries no authority (an answer never reads, runs or writes anything); one-shot `ask` stays strict.
lenient_prose = ContextVar("ai_desktop_lenient_prose", default=False)


# File-selector refusals the model may correct within the owner's plan_corrections budget (nothing is read first).
FILE_SELECTION_CHECKS = frozenset({"files-scope", "files-path", "files-directory", "files-selector"})


def _plan(response, access):
    try:
        return parse_plan(response, access)
    except PlanError as error:
        text = response.strip() if type(response) is str else ""
        if error.check == "json" and lenient_prose.get() and text:
            index = text.find('{"action"')
            if index >= 0:
                # A sentence followed by a plan: the plan is what counts. A broken plan, a plan inside a code fence
                # or one that breaks the strict JSON rules (duplicate keys, NaN) is an error, never an answer.
                if "```" in text:
                    raise error
                try:
                    decoder = json.JSONDecoder(object_pairs_hook=_unique_keys, parse_constant=_no_constant)
                    plan_object, _ = decoder.raw_decode(text[index:])
                except ValueError:
                    raise error from None
                return parse_plan(json.dumps(plan_object, ensure_ascii=False), access)
            if not text.startswith(("{", "[", "```json")) and '"action"' not in text:
                return parse_plan(json.dumps({"action": "answer", "text": text}, ensure_ascii=False), access)
        raise


class AccessDenied(ValueError):
    def __init__(self, reason=None, problem=()):
        self.reason = reason if reason in ("policy-invalid", "network-claude-disabled", "network-codex-disabled", "network-local-disabled") else None
        self.problem = tuple(problem) if type(problem) in (tuple, list) and all(type(line) is str for line in problem) else ()
        super().__init__("access denied")


class Declined(Exception):
    pass


class ContextUnavailable(ValueError):
    """A source did not answer; its hint is already printed, so no second, generic line follows."""


@dataclass(frozen=True)
class AccessSnapshot:
    policy: object
    sources: dict
    access: dict
    provider: str = "claude"

    def recheck(self):
        if self.policy is None:
            # Creating a policy also revokes an earlier unconfigured decision.
            if permissions.load_policy(optional=True) is not None:
                raise AccessDenied()
        else:
            self.policy.recheck()

    def require_claude(self):
        self.recheck()
        if self.sources["claude"] is not True:
            reason = "network-" + self.provider + "-disabled" if self.provider in ("claude", "codex", "local") else None
            raise AccessDenied(reason)


def _usable_writes(policy, access):
    """A write scope whose directory does not exist cannot take a file (write_workspace never creates one), so the
    model must not offer it. Ids keep their policy index; the policy itself is unchanged."""
    if "write" not in access:
        return access
    roots = {"w" + str(scope.index): scope.base.rsplit("/", 1)[0] if scope.mode == "exact" else scope.base
             for scope in policy.write_scopes()}
    usable = [item for item in access["write"] if os.path.isdir(roots[item["id"]])]
    trimmed = {key: value for key, value in access.items() if key != "write"}
    return {**trimmed, "write": usable} if usable else trimmed


def shared_directory(policy):
    """The directory the user started the program in, for relative paths in a question, written like the scope
    patterns (~ for the home directory). Shared only when a recursive read scope already lets the model list it and no
    deny rule or default protection covers it; otherwise None and it stays local."""
    if type(policy) is not permissions.Policy:
        return None
    try:
        root = os.getcwd()
        if (not root.startswith("/") or os.path.realpath(root) != root or not policy.allows(root + "/_")
                or policy.excludes(root) or policy._protected(root)
                or not any(s.mode == "tree" and (root == s.base or root.startswith(s.base + "/")) for s in policy.scopes())):
            return None
        if root == policy.home or root.startswith(policy.home + "/"):
            return "~" + root[len(policy.home):]
        return root
    except (OSError, ValueError):
        return None


def capture_access():
    try:
        policy = permissions.load_policy(optional=True)
    except (OSError, ValueError) as exc:
        reason = exc.args[0] if type(exc) is ValueError and len(exc.args) == 1 else None
        raise AccessDenied(reason, getattr(exc, "problem", ())) from None
    if policy is None:
        # Without an explicit policy the single question approval sends only
        # the question. No implicit system, file, weather or web access is granted.
        sources = {"claude": True, "weather": False, "systemd": []}
        access = {"files": [], "systemd": [], "weather": False}
    else:
        sources, access = policy.sources(), _usable_writes(policy, policy.access())
        directory = shared_directory(policy)
        if directory is not None:
            access = {**access, "cwd": directory}
    from .execution import current
    selected = current()
    if selected is not None and selected["provider"] in ("codex", "local"):
        if policy is not None or selected["provider"] == "local":
            sources = {**sources, "claude": policy is not None and policy.provider_allowed(selected["provider"])}
        # This adapter has no external WebSearch tool. Local file/systemd and
        # explicitly selected weather remain controller-owned collectors.
        access = {key: value for key, value in access.items() if key != "web"}
        sources = {key: value for key, value in sources.items() if key != "web"}
    if selected is not None and selected["access"] == "read-only":
        # A read-only profile gets neither files to edit nor commands that change the live system.
        access = {key: value for key, value in access.items() if key not in ("write", "change_commands")}
    else:
        from .project_checks import current_directory
        if current_directory(policy) is not None:
            access = {**access, "project_test": True}  # capability only; the test runs locally
    return AccessSnapshot(policy, sources, validate_access(access),
                          "claude" if selected is None else selected["provider"])


def describe_access(snapshot):
    access = snapshot.access
    print(recipient("Access: Claude ") + ("allowed" if snapshot.sources["claude"] else "blocked")
          + "; file scopes: " + str(len(access["files"])) + "; systemd: "
          + (", ".join(access["systemd"]) or "none") + "; weather: "
          + ("allowed" if access["weather"] else "none")
          + "; Internet: " + ("allowed" if access.get("web") is True else "none")
          + "; write scopes: " + str(len(access.get("write", []))) + ".")


def _legacy_selection(args, snapshot):
    """Existing explicit flags narrow access; none can bypass the TOML policy."""
    files, systemd, weather = [], [], None
    if args.no_context:
        return {"action": "read", "files": files, "systemd": systemd, "weather": weather}
    if args.systemd_state and not args.systemd:
        raise ValueError("state requires explicit scope")
    if args.systemd:
        scopes = ("system", "user") if args.systemd == "both" else (args.systemd,)
        if any(scope not in snapshot.access["systemd"] for scope in scopes):
            raise AccessDenied()
        systemd = [{"scope": scope, "view": args.systemd_state or "installed"} for scope in scopes]
    if args.weather:
        if snapshot.access["weather"] is not True:
            raise AccessDenied()
        weather = args.weather
    return {"action": "read", "files": files, "systemd": systemd, "weather": weather}


def collect(plan, snapshot, args, reader, *, question_consent=None, defer=()):
    """Only a validated plan and the original, unchanged policy reach readers."""
    plan = parse_plan(json.dumps(plan), snapshot.access)
    if plan["action"] != "read":
        raise AccessDenied()
    if type(question_consent) is not QuestionConsent:
        raise AccessDenied()
    question_consent.check()
    context = []
    if plan["files"]:
        if snapshot.policy is None:
            raise AccessDenied()
        context.append(file_context.collect(snapshot.policy, plan["files"], args, reader,
                                            question_consent=question_consent, defer=defer))
    for selection in plan["systemd"]:
        question_consent.check()
        context.extend(ask_context._systemd(selection["scope"], selection["view"], args, reader,
                                            check=question_consent.check, question_consent=question_consent))
    if plan["weather"] is not None:
        question_consent.check()
        try:
            item = ask_context._weather(plan["weather"], args, reader, check=question_consent.check,
                                        question_consent=question_consent)
        except ask_context.weather_context.WeatherError as exc:
            print(ask_context.WEATHER_HINTS[exc.reason])
            raise ContextUnavailable("context unavailable") from None
        if item is not None:
            context.append(item)
    question_consent.check()
    if args.details:
        ask_context.preview_context(context, details=True)
    return context


def _deny(policy, code, prefix, sent):
    """Final, explained local refusal: nothing from the failed step is sent, and no partial result is passed off."""
    selected = recipient_config()
    print(prefix + ": " + file_context._read_hint(ValueError(code) if code else ValueError(), policy))
    if code in ask_discovery.RECOVERABLE or code == "discovery-required":
        print("The limit of plan corrections ran out (plan_corrections=" + str(policy.limits().plan_corrections)
              + " in [files] of permissions.toml). Narrow the question or raise the limit.")
    print(recipient(sent, config=selected))
    raise file_context.FileReadDenied("input")


COMMITTED_OUTPUT = 256
_LABEL = {"read": "Command", "change": "Change", "test": "Test after change", "undo": "Undo"}


def _record_full(needed=1):
    """A write or deletion is not started when this turn could not remember it; says so and returns True."""
    if conversation_state.change_room(needed):
        return False
    print("Not done: this turn's record of changes is full, so this change would not be remembered. "
          "Ask for it in a new message.")
    return True


# A change may record up to four facts: the change, its test, its undo (or a declined undo) and a stop after it ran.
CHANGE_RECORD = 4


def change_problem(policy, test, undo):
    """Why a change from the `change` list cannot run with this test and undo, or "" when both fit. Test and undo run
    on the live system, so only exact templates count for them, never an open "..." read template."""
    if test is None or undo is None:
        return "A change needs a test (an exact `allow` command) and an undo (a `change` or exact `allow` command)"
    words = lambda argv: " ".join(argv)
    if policy.allows_change(test):
        return "The test %s itself changes the system; it must be a read command from `allow`" % words(test)
    if not policy.allows_exact_command(test):
        return ("The test %s is not an exact `allow` command (a template ending in ... does not count)" % words(test))
    if not (policy.allows_change(undo) or policy.allows_exact_command(undo)):
        return ("The undo %s is neither on the `change` list nor an exact `allow` command" % words(undo))
    return ""
_OUTCOME = {"declined": "declined by the user, not run", "not-allowed": "not on the list, not run",
            "unavailable": "sandbox unavailable, not run", "timeout": "timed out", "ok": "ok",
            "missing": "this program is not on the system, not run"}


def _finished(label, step):
    healthy = step["status"] == "ok" and step["exit"] in (0, None)
    state = "%s%s" % (step["status"], "" if step["exit"] is None else ", code " + str(step["exit"]))
    dot = " " + symbol("dot") + " "
    return "%s %s%s%s%s%d B %s conversation" % (
        paint(symbol("ok" if healthy else "fail"), "green" if healthy else "red", "bold"),
        paint(label, "bold"), dot, paint(state, "green" if healthy else "red"), dot,
        len(step["output"].encode("utf-8")), symbol("arrow"))


def _group(ledger, count):
    """The head of a group of read commands; their rows hang under it like a tree."""
    print(paint(symbol("bullet"), "violet", "bold") + " " + paint("Running %d command%s" % (count, "" if count == 1 else "s"), "bold"))
    ledger.tree_rows = 0


def _branch(ledger):
    """The tree mark of the next row: a corner for the first one under the head, an indent after it."""
    rows = getattr(ledger, "tree_rows", None)
    if rows is None:
        return ""
    ledger.tree_rows = rows + 1
    return (symbol("branch") if rows == 0 else " ") + " "


def _read_line(shown, step, branch=""):
    """One row per read-only command: a whole batch stays readable on one screen."""
    healthy = step["status"] == "ok" and step["exit"] in (0, None)
    # A non-zero code with no output at all is often an answer, not a fault (`pacman -Qu`: nothing to update).
    empty = step["status"] == "failed" and step["exit"] is not None and not step["output"].strip()
    state = size_label(len(step["output"].encode("utf-8"))) if healthy else "%s%s" % (
        "empty" if empty else step["status"], "" if step["exit"] is None else ", code " + str(step["exit"]))
    tone = "accent" if healthy else "yellow" if empty else "red"
    return "  %s%s %s %s %s" % (paint(branch, "muted"), paint(symbol("ok" if healthy else "skip" if empty else "fail"), tone, "bold"), shown,
                              paint(symbol("dot"), "muted"), paint(state, "muted" if healthy else tone))


def _open_page(ledger, url, reader, selected, snapshot):
    """S10 stage A: one public page through the program's web broker. An address from the user's question, the search
    sources or an opened page's links opens at once; one the model composed is shown in full and needs a yes, because
    after local data was read an address can carry it out. The result (also a refusal) is data for the next round."""
    from . import web_broker
    from .terminal_consent import confirm
    empty = {"title": "", "text": "", "links": [], "truncated": False}
    try:
        clean = web_broker.clean_url(url)
    except web_broker.WebError:
        print(paint("  %s Page refused (not a public https address): %s" % (symbol("skip"), terminal_text(url[:200])),
                    "muted"))
        clean = None
    if clean is None:
        _add_page(ledger, {"op": "page", "url": "", "status": "refused", **empty}, snapshot)
        return
    if clean not in ledger.known_urls:
        print("The model wants to open an address that did not come from you, the search or an opened page:")
        print("  " + terminal_text(clean))
        if not confirm("web-open", reader=reader, config=selected):
            _add_page(ledger, {"op": "page", "url": clean, "status": "declined", **empty}, snapshot)
            return
    try:
        page = web_broker.open_page(clean)
    except web_broker.WebError as error:
        status = "refused" if error.code in ("url", "address") else "failed"
        print(paint("  %s Page %s · %s (%s)" % (symbol("skip"), terminal_text(clean[:160]), status, error.code), "muted"))
        _add_page(ledger, {"op": "page", "url": clean, "status": status, **empty}, snapshot)
        return
    step = {"op": "page", "url": page["final_url"], "status": "ok", "title": page["title"], "text": page["text"],
            "links": page["links"], "truncated": page["truncated"]}
    print(paint("  %s Page %s · %s" % (symbol("ok"), terminal_text(page["final_url"][:160]),
                                        size_label(len(page["text"].encode("utf-8")))), "muted"))
    conversation_state.note_action("Opened the public page %s (%s)" % (page["final_url"], page["title"] or "no title"),
                                   read=True)
    _add_page(ledger, step, snapshot)


def _download(ledger, urls, reader, selected, snapshot, permit=None):
    """S10 stage B: public files into a fresh quarantine folder, after one consent for the whole set. An address that did
    not come from the user, the search or an opened page is marked. Nothing is run, unpacked or installed; every file
    (also a refusal) is a discovery step for the next round."""
    from . import web_download
    from .web_broker import WebError, clean_url

    def step(url, status, reason="", found=None):
        found = found or {}
        _add_download(ledger, {"op": "download", "url": url, "status": status, "reason": reason,
                               "name": found.get("name", ""), "size": found.get("size", 0),
                               "sha256": found.get("sha256", ""), "content_type": found.get("content_type", ""),
                               "folder": found.get("folder", "")},
                      snapshot)
    clean = []
    for url in urls:
        try:
            clean.append(clean_url(url))
        except WebError:
            print(paint("  %s Download refused (not a public https address): %s"
                        % (symbol("skip"), terminal_text(url[:200])), "muted"))
            step("", "refused", "url")
    clean = list(dict.fromkeys(clean))
    if not clean:
        return
    print("Download into a quarantine folder (files are not run, unpacked or installed):")
    for url in clean:
        print("  " + terminal_text(url) + ("" if url in ledger.known_urls else
                                           paint("  (address composed by the model)", "yellow")))
    if not confirm("download", reader=reader, config=selected):
        for url in clean:
            step(url, "declined")
        return
    try:
        folder = web_download.quarantine()
    except (OSError, WebError):
        for url in clean:
            step(url, "failed", "space")
        print(paint("  %s Could not create the quarantine folder." % symbol("fail"), "red"))
        return
    with permit.waiting() if permit is not None else nullcontext():
        _fetch_all(ledger, clean, folder, step)
    print("Quarantine folder: " + terminal_text(folder))


def _fetch_all(ledger, clean, folder, step):
    """The approved files one after another; time spent here is the user's chosen transfer, not the question's."""
    from urllib.parse import urlsplit
    from . import web_download
    from .web_broker import WebError
    stopped = False
    for url in clean:
        if stopped:
            step(url, "cancelled", "cancelled")
            continue
        shown = os.path.basename(urlsplit(url).path) or url
        def progress(size, shown=shown):
            print(paint("  %s %s · %s so far" % (symbol("dot"), terminal_text(shown[:80]), size_label(size)), "muted"),
                  flush=True)
        try:
            found = web_download.download(url, folder, progress=progress)
        except KeyboardInterrupt:
            stopped = True
            print(paint("  %s Download cancelled; the partial file was removed." % symbol("skip"), "muted"))
            step(url, "cancelled", "cancelled")
            continue
        except WebError as error:
            status = "refused" if error.code in ("url", "address") else "cancelled" if error.code == "cancelled" else "failed"
            print(paint("  %s %s · %s (%s)" % (symbol("skip"), terminal_text(url[:160]), status, error.code), "muted"))
            step(url, status, error.code if error.code in web_download.CODES else "response")
            continue
        print(paint("  %s %s · %s · sha256 %s" % (symbol("ok"), terminal_text(found["name"]), size_label(found["size"]),
                                                 found["sha256"][:16]), "muted"))
        label = "~/" + os.path.relpath(os.path.join(folder, found["name"]), web_download._home())
        conversation_state.note_action("Downloaded %s (%d bytes, sha256 %s) as %s"
                                       % (found["final_url"], found["size"], found["sha256"], label), read=True)
        step(url, "ok", "", dict(found, folder="~/" + os.path.relpath(folder, web_download._home())))


def _inspect(ledger, label, snapshot):
    """S10 stage C: what a downloaded archive holds, read locally without unpacking; a discovery step for the model."""
    from . import archive_install
    empty = {"format": "", "files": 0, "folders": 0, "bytes": 0, "top": [], "top_count": 0, "executables": 0,
             "examples": []}
    try:
        home = snapshot.policy.home if snapshot.policy is not None else archive_install_home()
        facts = archive_install.inspect(archive_install.resolve(label, home))
    except archive_install.ArchiveDenied as refused:
        print(paint("  %s Archive %s · refused (%s)" % (symbol("skip"), terminal_text(label.rsplit("/", 1)[-1]),
                                                       refused.reason), "muted"))
        step = {"op": "archive", "archive": label, "status": "refused", "reason": refused.reason, **empty}
    else:
        print(paint("  %s Archive %s · %s, %d files, %s" % (symbol("ok"), terminal_text(label.rsplit("/", 1)[-1]),
                                                           facts["format"], facts["files"], size_label(facts["bytes"])),
                    "muted"))
        step = {"op": "archive", "archive": label, "status": "ok", "reason": "",
                **{key: facts[key] for key in empty}}
        step["top"] = [name[:200] for name in step["top"][:32]]
        step["examples"] = [name[:200] for name in step["examples"][:32]]
    try:
        ledger.add_archive(step)
    except ask_discovery.BudgetExhausted as exhausted:
        if not ledger.soft_stop(exhausted.name):
            _budget_stop(snapshot.policy, exhausted.name)


def archive_install_home():
    from .web_download import _home
    return _home()


def _add_download(ledger, step, snapshot):
    try:
        ledger.add_download(step)
    except ask_discovery.BudgetExhausted as exhausted:
        if not ledger.soft_stop(exhausted.name):
            _budget_stop(snapshot.policy, exhausted.name)


def _add_page(ledger, step, snapshot):
    try:
        ledger.add_page(step)
    except ask_discovery.BudgetExhausted as exhausted:
        if not ledger.soft_stop(exhausted.name if exhausted.name != "pages" else "discovery_rounds"):
            _budget_stop(snapshot.policy, exhausted.name if exhausted.name != "pages" else "discovery_rounds")


def _run_command(snapshot, ledger, plan, permit, args, reader):
    """Read-only commands (sandbox, no prompt: the owner listed them in `allow`) or a CHANGE with its test and undo (live
    system, strong yes, undo only on a second yes). The controller writes what really happened to the task memory and to
    the model's next request."""
    from . import command_ops
    policy, argv = snapshot.policy, plan["argv"]
    if policy is not None:
        argv = policy.expand_home(argv)            # `~/x` as a shell would expand it; files.read still decides
    selected = current_execution()
    shown = lambda words: terminal_text(" ".join(words))

    def add(words, role, status, code=None, output="", cut=False):
        observed = ledger.add_run({"op": "run", "argv": list(words), "status": status, "exit": code,
                                   "output": output, "truncated": cut, "role": role})
        outcome = _OUTCOME.get(status, "failed")
        conversation_state.note_action("%s %s: %s%s" % (_LABEL[role], " ".join(words), outcome,
                                                       "" if code is None else ", code %d" % code),
                                       read=role == "read")
        return observed

    def spend():
        try:
            ledger.take_command()
            return ledger.output_room()
        except ask_discovery.BudgetExhausted as exhausted:
            if exhausted.name == "discovery_bytes" and ledger.soft_stop(exhausted.name):
                print(paint("  %s output budget used up (discovery_bytes): further commands are not run, the answer "
                            "uses what was collected" % symbol("skip"), "muted"))
                return None
            if exhausted.name in ("discovery_bytes", "command_rounds") and ledger.budget_closed():
                # a late command after the model was told to stop: recorded as refused, so the model answers next
                add(argv, "change" if policy.allows_change(argv) else "read", "not-allowed",
                    output="The output budget is used up; answer from what was collected.\n")
                return None
            _budget_stop(policy, exhausted.name)

    def committed_room():
        """The live change already ran, so its test and undo are not optional: the byte budget may shrink their output
        but never skip them. can_afford(3) reserved their command rounds before the change."""
        ledger.commands_left = max(ledger.commands_left - 1, 0)
        try:
            return ledger.output_room()
        except ask_discovery.BudgetExhausted:
            return COMMITTED_OUTPUT

    change_ran = []

    def run_step(words, role, host):
        room = spend() if role == "change" else committed_room()
        if room is None:
            return None
        path = command_ops.resolve(words[0])
        if path is None:
            add(words, role, "not-allowed")
            return None
        permit.check()
        step = command_ops.execute(path, words, output_limit=room, timeout=policy.limits().command_timeout,
                                   host=host, role=role)
        if role == "change":
            change_ran.append(True)
        # The command has already run on the live system: its result is recorded and shown before consent can stop the
        # question. A lapsed consent stops what comes next, it does not erase what happened.
        step = add(words, role, step["status"], step["exit"], step["output"], step["truncated"])
        print(_finished(_LABEL[role], step))
        if args.details:
            print(terminal_text(step["output"]))
        permit.check()
        return step

    changing = policy.allows_change(argv)
    if not changing:
        if "test" in plan:
            print("A test and an undo apply only to changes from the `change` list: " + shown(argv))
            spend() is None or add(argv, "read", "not-allowed")
            return
        room = spend()
        if room is None:
            return
        path = command_ops.resolve(argv[0]) if policy.allows_command(argv) else None
        if path is None:
            if policy.allows_command(argv):
                print(paint("  %s%s %s %s program not found" % (_branch(ledger), symbol("skip"), shown(argv), symbol("dot")), "muted"))
                add(argv, "read", "missing")
                return
            print("This command is not on the allowed list in permissions.toml and will not run: " + shown(argv))
            add(argv, "read", "not-allowed")
            return
        permit.check()
        if args.details:
            print("%s %s sandbox: read-only, no network" % (path, symbol("dot")))
        extra = {}
        share, sockets = policy.share_processes(), policy.host_sockets()
        binds = policy.sandbox_binds(argv)
        if share or sockets or binds:         # owner opt-ins and named paths; otherwise the call is what it always was
            extra["command"] = functools.partial(command_ops.sandbox_command, share_processes=share,
                                                 host_sockets=sockets, binds=binds)
        step = command_ops.execute(path, argv, output_limit=room, timeout=policy.limits().command_timeout, **extra)
        permit.check()
        step = add(argv, "read", step["status"], step["exit"], step["output"], step["truncated"])
        print(_read_line(shown(argv), step, _branch(ledger)))
        if args.details:
            print(terminal_text(step["output"]))
        return
    # A change: it needs its test (a reading command) and its undo (a change or reading command), all named up front.
    test, undo = plan.get("test"), plan.get("undo")
    # Test and undo run on the live system, so only exact templates count for them, never an open "..." read template.
    problem = change_problem(policy, test, undo)
    if not problem and not conversation_state.change_room(CHANGE_RECORD):
        problem = ("this turn's record of changes is full, so a further change would not be remembered; "
                   "ask for it in a new message")
    if problem or not ledger.can_afford(3):
        # without room for change + test + undo (or without a valid test/undo) nothing is changed
        if not problem:
            _budget_stop(policy, "command_rounds" if ledger.commands_left < 3 else "discovery_bytes")
        print("Change not run: %s. %s" % (shown(argv), terminal_text(problem)))
        spend() is None or add(argv, "change", "not-allowed", output=problem + "\n")
        return
    permit.check()
    print()
    for text in card("SYSTEM CHANGE · live, no sandbox", [
            "change            " + shown(argv), "test after change " + shown(test), "undo              " + shown(undo)],
            tone="red"):
        print(text)
    with permit.waiting():
        agreed = confirm("run-change", reader=reader, config=selected)
    if not agreed:
        print("The change was not made.")
        spend() is None or add(argv, "change", "declined")
        return
    try:
        changed = run_step(argv, "change", True)
        if changed is None:
            return                  # nothing ran (refused or budget closed): there is nothing to test or undo
        tested = None
        if changed["status"] == "ok":
            tested = run_step(test, "test", True)
            if tested is None:
                conversation_state.note_action("Test after change %s: skipped, no budget left" % " ".join(test))
        healthy = (changed is not None and changed["status"] == "ok" and changed["exit"] == 0
                   and tested is not None and tested["status"] == "ok" and tested["exit"] == 0)
        if healthy:
            print("Test after change passed.")
            return
        print("The change or the test after it FAILED. Undo: " + shown(undo))
        with permit.waiting():
            undo_agreed = confirm("run-undo", reader=reader, config=selected)
        if undo_agreed:
            run_step(undo, "undo", True)
        else:
            conversation_state.note_action("Undo %s: declined by the user, not run" % " ".join(undo))
            print("The undo was not run. The system may still be changed.")
    except AnalysisDenied:
        if change_ran:
            # No further command runs under a lapsed consent or changed policy, and nothing more is sent. The owner decides.
            conversation_state.note_action("Change %s: stopped after it ran, further steps not run" % " ".join(argv))
            print("The change WAS MADE, but consent expired or the permissions changed. The next steps will not "
                  "run and nothing more will be sent.")
            print("Check the system. Test: " + shown(test))
            print("You can run the undo yourself: " + shown(undo))
        raise


def _budget_stop(policy, name):
    if name == "skills_bytes":
        from .skill_runtime import MAX_KNOWLEDGE_BYTES
        print("Refused: selected Skills and references exceed their separate %d B budget." % MAX_KNOWLEDGE_BYTES)
        raise file_context.FileReadDenied("input")
    limits = policy.limits() if policy is not None else permissions.Limits()
    if name not in permissions.LIMIT_SPECS:
        # An internal bound (steps of one question), not a setting: the model kept looking without reaching an answer.
        print("Refused: this question used all %d steps the program allows for looking around (%s). "
              "Ask a narrower question; a partial result is not sent as if it were complete."
              % (getattr(limits, name, 0), name))
        print(recipient("The local result was not sent to Claude."))
        raise file_context.FileReadDenied("input")
    print("Refused: the file discovery budget ran out: " + name + "=" + str(getattr(limits, name))
          + " ([files] of permissions.toml; ceiling in the code: " + str(permissions.LIMIT_SPECS[name][2])
          + "). A partial result is not sent as if it were complete.")
    print(recipient("The local result was not sent to Claude."))
    raise file_context.FileReadDenied("input")


def _observed(carried, **payload):
    """What this task collected earlier, for its last request. It fits in nearly every case; if it does not, the request
    still goes, and the owner is told what the model will not see instead of the whole answer being refused."""
    document = carried.get("document")
    if document is None:
        return {}
    try:
        application_input(None, discovery=document, **payload)
    except (ContextBudgetExceeded, InputBudgetExceeded):
        print(paint("The results of earlier steps do not fit in the request: the model will not see them at this stage.", "muted"))
        return {}
    return {"discovery": document}


def _run_once(question, args, reader, request, *, snapshot, phase=None, conversation_consent=None, skills=None):
    """One plan and answer, optionally preceded by bounded discovery. Without a conversation grant it asks every time."""
    def progress(value):
        if phase is not None:
            phase(value)
    explicit = args.no_context or args.systemd or args.systemd_state or args.weather
    if args.preview:
        describe_access(snapshot)
        for scope in snapshot.access["files"] + snapshot.access.get("write", []):
            print("  " + scope["id"] + ": " + terminal_text(json.dumps(scope["pattern"], ensure_ascii=False)))
        if explicit:
            _legacy_selection(args, snapshot)
        if args.details:
            print(json.dumps({"question": question, "access": snapshot.access}, ensure_ascii=False, indent=2))
        print("Permissions preview: no model, no directory listing and no reading of the selected data.")
        print("Preview done. Nothing was sent.")
        return None
    snapshot.require_claude()
    explicit_plan = _legacy_selection(args, snapshot) if explicit else None
    selected = current_execution()
    permit = QuestionConsent(question, snapshot)
    try:
        if args.details:
            describe_access(snapshot)
            print(json.dumps({"question": question, "access": snapshot.access, "sources": snapshot.sources},
                             ensure_ascii=False, indent=2))
        if conversation_consent is None or not conversation_consent.covers(snapshot):
            if not confirm("ask-question", reader=reader, config=selected):
                print("Cancelled. The question was not sent.")
                raise Declined()
            if conversation_consent is not None:
                conversation_consent.grant(snapshot)
        permit.approve()
        print(wait_message(config=selected), flush=True)
        if explicit:
            progress("collect")
            context = [] if args.no_context else collect(explicit_plan, snapshot, args, reader,
                                                         question_consent=permit)
            progress("answer-request")
            answer = request(question, args, reader, context=context, check=permit.check, question_consent=permit)
        else:
            ledger = ask_discovery.DiscoveryLedger(snapshot.policy, question, (selected or {}).get("instructions", ""))
            carried = {}
            loaded = set()
            if skills is not None and conversation_consent is not None:
                for name, body in conversation_consent.reusable_skills(skills):
                    try:
                        ledger.add_skill(name, body)
                    except ask_discovery.BudgetExhausted:
                        continue
                    loaded.add(name)
                if loaded:
                    pending = ledger.snapshot()
                    try:
                        application_input(None, question=question, access=snapshot.access, discovery=pending.document)
                    except (ContextBudgetExceeded, InputBudgetExceeded):
                        ledger = ask_discovery.DiscoveryLedger(snapshot.policy, question, (selected or {}).get("instructions", ""))
                        loaded.clear()
                    else:
                        carried["document"] = permit.bind_discovery(snapshot.policy, pending, reuse_loader=skills)
            progress("plan-request")
            response = request(question, args, reader, access=snapshot.access, check=permit.check,
                               question_consent=permit, **({"discovery": carried["document"]} if carried else {}))
            permit.check()
            progress("plan")

            def corrected(error):
                """A command that breaks the word rules (spaces, a shell script, too many words) is refused with
                command-invalid; the model may correct it within the owner's correction budget. Anything else ends."""
                if error.check in FILE_SELECTION_CHECKS and snapshot.policy is not None and ledger.correction(
                        ask_discovery.Recoverable(error.check, [], None)):
                    # The file it means is often clear from the conversation, only written the wrong way (a write
                    # scope's id, ~/name, a directory): the code lets it write the same selection correctly.
                    print(paint("  %s The proposed file selection was not valid (%s); the model may correct it."
                                % (symbol("skip"), error.check), "muted"))
                    return
                if error.check == "archive" and snapshot.policy is not None and ledger.correction(
                        ask_discovery.Recoverable("archive-label", [], None)):
                    print(paint("  %s The archive was not named as a downloaded file; the model may correct it."
                                % symbol("skip"), "muted"))
                    return
                if error.check != "command" or snapshot.policy is None or not ledger.correction(
                        ask_discovery.Recoverable("command-invalid", [], None)):
                    raise error
                print(paint("  %s The proposed command was not valid (one word per argument, no spaces or shell); "
                            "the model may correct it." % symbol("skip"), "muted"))

            def next_plan():
                """One more planner round that carries the cumulative local observations (same single approval)."""
                while True:
                    try:
                        pending = ledger.snapshot()
                    except ValueError:
                        _budget_stop(snapshot.policy, "discovery_bytes")
                    document = permit.bind_discovery(snapshot.policy, pending, resource_loader=skills)
                    carried["document"] = document
                    progress("plan-request")
                    reply = request(question, args, reader, access=snapshot.access, discovery=document,
                                    check=permit.check, question_consent=permit)
                    permit.check()
                    progress("plan")
                    try:
                        following = _plan(reply, snapshot.access)
                    except PlanError as error:
                        corrected(error)
                        continue
                    if following["action"] == "web":
                        # A search is allowed only first, before any local data; later it is refused as a correction
                        # (web-late) so the model goes on with local data or answers, instead of ending the question.
                        if not ledger.correction(ask_discovery.Recoverable("web-late", [], None)):
                            raise PlanError("action")
                        print(paint("  %s A web search is only possible before local data is collected; "
                                    "the model continues without it." % symbol("skip"), "muted"))
                        continue
                    break
                if following["action"] not in ("list", "find", "run", "skill", "read", "read-range", "search-content", "manual", "web-open", "download", "inspect", "install", "answer", "edit", "delete", "move", "copy", "project-test"):
                    raise PlanError("action")
                return following

            try:
                plan = _plan(response, snapshot.access)
            except PlanError as error:
                corrected(error)
                plan = next_plan()

            context = None
            skill_rounds = 0
            while True:
                if plan["action"] == "web" and plan.get("continue") and snapshot.policy is not None:
                    # Search first, then the local part of the same question (an edit, commands, reads). The search
                    # carries nothing local; its checked answer goes to the next round as data. One search only:
                    # next_plan never accepts another web step.
                    permit.continue_after_web()
                    progress("answer-request")
                    from .analysis_contract import web_followup
                    with web_followup():
                        found = request(question, args, reader, web=True, check=permit.check,
                                        question_consent=permit)
                    permit.check()
                    _show_web(found, args)
                    # Recorded first, so a later turn can refer to what was shown even if this question stops below.
                    conversation_state.note_action(conversation_state.web_note(found))
                    try:
                        ledger.add_web(found)
                    except ask_discovery.BudgetExhausted as exhausted:
                        _budget_stop(snapshot.policy, exhausted.name)
                    plan = next_plan()
                    continue
                if snapshot.policy is None and (plan["action"] in ("list", "find", "run", "read-range", "search-content")
                                                or plan["action"] == "read" and plan["files"]):
                    raise AccessDenied()
                if plan["action"] == "skill":
                    # On demand: until the model asks, only the name and description of a Skill leave this computer.
                    if skills is None:
                        raise PlanError("action")
                    previous = {step["name"]: step["text"] for step in ledger.steps if step["op"] == "skill"}
                    wanted = [name for name in plan["names"] if name not in loaded or
                              "/" in name and skills(name) != previous.get(name)]
                    if not wanted or skill_rounds >= MAX_SKILL_ROUNDS:
                        # Nothing new, or the Skill rounds are used up: a correction,
                        # so the model goes on with what it has instead of the question ending.
                        code = "skill-repeat" if not wanted else "skill-limit"
                        if not ledger.correction(ask_discovery.Recoverable(code, [], None)):
                            raise PlanError("action")
                        print(paint("  %s %s; the model continues with what it has." % (symbol("skip"), (
                            "The requested Skill is already loaded" if not wanted
                            else "No more Skills can be loaded for this question")), "muted"))
                        plan = next_plan()
                        continue
                    skill_rounds += 1
                    progress("collect")
                    for name in wanted:
                        loaded.add(name)
                        text = skills(name)
                        try:
                            ledger.add_skill(name, text)
                        except ask_discovery.BudgetExhausted as exhausted:
                            _budget_stop(snapshot.policy, exhausted.name)
                        print(paint("  %s Skill %s %s %s" % (
                            symbol("skip" if text is None else "ok"), name, symbol("dot"),
                            "no such Skill" if text is None else size_label(len(text.encode("utf-8")))), "muted"))
                    plan = next_plan()
                    if conversation_consent is not None:
                        conversation_consent.remember_skills(ledger.steps[-len(wanted):])
                    continue
                if plan["action"] == "run":
                    # An operation the owner listed in permissions.toml; its bounded result goes back to the model as
                    # data in the next planner round.
                    progress("collect")
                    if "commands" in plan:
                        # One planner step, many reads: the model gets all the results in its next single round.
                        _group(ledger, len(plan["commands"]))
                        for index, argv in enumerate(plan["commands"]):
                            # The first one always goes through the budget check: with no rounds left it is recorded
                            # as refused (or ends the question with the budget message), never silently skipped.
                            if ledger.commands_left <= 0 and index:
                                break
                            permit.renew()    # commands may take their full timeout each; the question ceiling still holds
                            _run_command(snapshot, ledger, {"action": "run", "argv": argv}, permit, args, reader)
                    else:
                        if "test" not in plan and not (snapshot.policy and snapshot.policy.allows_change(plan["argv"])):
                            _group(ledger, 1)
                        else:
                            ledger.tree_rows = None          # a change shows its own card, not a tree
                        _run_command(snapshot, ledger, plan, permit, args, reader)
                    plan = next_plan()
                    continue
                if plan["action"] == "web-open":
                    progress("collect")
                    _open_page(ledger, plan["url"], reader, selected, snapshot)
                    plan = next_plan()
                    continue
                if plan["action"] == "inspect":
                    progress("collect")
                    _inspect(ledger, plan["archive"], snapshot)
                    plan = next_plan()
                    continue
                if plan["action"] == "download":
                    progress("collect")
                    _download(ledger, plan["urls"], reader, selected, snapshot, permit)
                    plan = next_plan()
                    continue
                if plan["action"] == "manual":
                    # Installed documentation, read by the program itself: public data, no network, no files policy.
                    progress("collect")
                    try:
                        found = ledger.manual(plan)
                        print(paint("  %s Manual %s(%s)%s · %s" % (
                            symbol("ok" if found["status"] == "ok" else "skip"), plan["page"], plan["section"],
                            (" \u201c" + plan["needle"] + "\u201d") if plan.get("needle") else "",
                            "%d excerpt(s)" % len(found["fragments"]) if found["status"] == "ok" else "not installed"),
                            "muted"))
                    except ask_discovery.BudgetExhausted as exhausted:
                        if not ledger.soft_stop(exhausted.name):
                            _budget_stop(snapshot.policy, exhausted.name)
                    plan = next_plan()
                    continue
                if plan["action"] in ("read-range", "search-content"):
                    progress("collect")
                    try:
                        ledger.content(plan, permit)
                        permit.check()
                    except ask_discovery.Recoverable as problem:
                        if not ledger.correction(problem):
                            _deny(snapshot.policy, problem.code, "Content read refused", "The excerpt was not sent to Claude.")
                    except ask_discovery.BudgetExhausted as exhausted:
                        if not ledger.soft_stop(exhausted.name):
                            _budget_stop(snapshot.policy, exhausted.name)
                    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as error:
                        code = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 else None
                        if (type(code) is not str or code not in ask_discovery.RECOVERABLE
                                or not ledger.correction(ask_discovery.Recoverable(
                                    code, ledger.selectors([plan["file"]]),
                                    snapshot.policy.file_size() if code == "files-size" else ledger.limit_for(code)))):
                            _deny(snapshot.policy, code if type(code) is str else None, "Content read refused",
                                  "The excerpt was not sent to Claude.")
                    plan = next_plan()
                    continue
                if plan["action"] in ("list", "find"):
                    # Discovery is an explicit, budgeted controller operation, never an open-ended tool loop.
                    progress("collect")
                    try:
                        ledger.run(plan, permit)
                        permit.check()
                    except ask_discovery.BudgetExhausted as exhausted:
                        if not ledger.soft_stop(exhausted.name):
                            _budget_stop(snapshot.policy, exhausted.name)
                    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError) as error:
                        code = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 else None
                        if (type(code) is not str or code not in ask_discovery.RECOVERABLE
                                or not ledger.correction(ask_discovery.Recoverable(
                                    code, ledger.selectors(plan["files"]), ledger.limit_for(code)))):
                            _deny(snapshot.policy, code if type(code) is str else None, "Directory listing refused",
                                  "The directory listing was not sent to Claude.")
                    plan = next_plan()
                    continue
                if plan["action"] == "edit" and plan.get("files") and snapshot.policy is not None:
                    # S3: a file too large to send whole can be edited only through excerpts read in this question.
                    # Without them the model gets one correction (files-size) to search or read the part first.
                    oversized = _without_excerpts(snapshot.policy, plan["files"], ledger.excerpts())
                    if oversized and ledger.correction(ask_discovery.Recoverable(
                            "files-size", ledger.selectors(oversized), snapshot.policy.limits().read_file_size)):
                        print(paint("  %s The file is too large to send whole; Claude finds the part to change first."
                                    % symbol("skip"), "muted"))
                        plan = next_plan()
                        continue
                if plan["action"] != "read" or not plan["files"]:
                    break
                try:
                    ledger.ground(plan["files"])
                    progress("collect")
                    context = collect(plan, snapshot, args, reader, question_consent=permit,
                                      defer=ask_discovery.RECOVERABLE)
                    break
                except (ask_discovery.Recoverable, file_context.FileReadDenied) as refused:
                    if type(refused) is file_context.FileReadDenied and refused.code is None:
                        raise
                    problem = refused if type(refused) is ask_discovery.Recoverable else ask_discovery.Recoverable(
                        refused.code, ledger.selectors(plan["files"]), ledger.limit_for(refused.code))
                    if not ledger.correction(problem):
                        _deny(snapshot.policy, problem.code, "File read refused",
                              "The file contents were not sent to Claude.")
                    plan = next_plan()
            if plan["action"] == "project-test":
                from . import project_checks
                result = project_checks.current(plan["recipe"], reader, policy=snapshot.policy, permit=permit)
                if result.summary:
                    conversation_state.note_sources(result.sources)
                    conversation_state.note_action(result.summary)
                return result
            if plan["action"] == "answer":
                return plan["text"]
            if plan["action"] == "web":
                # Web is a separate final request: the exact original question and the conversation (added by the
                # request builder), never files, task memory, Skills or a write proposal.
                permit.require_web()
                progress("answer-request")
                answer = request(question, args, reader, web=True, check=permit.check,
                                 question_consent=permit)
                permit.check()
                return answer
            if plan["action"] in ("delete", "edit", "move", "copy", "install") and _record_full():
                return None
            if plan["action"] == "install":
                # Local only: nothing goes to the model, the owner's yes comes after the plan (S10 stage C).
                progress("edit-review")
                permit.check()
                with permit.waiting():
                    return ask_edit.install_archive(plan, snapshot, reader)
            if plan["action"] in ("move", "copy"):
                # Local only, like a deletion: nothing goes to the model, the owner's yes comes after the plan.
                progress("edit-review")
                permit.check()
                with permit.waiting():
                    return ask_edit.transfer_files(plan, snapshot, reader)
            if plan["action"] == "delete":
                # Local only: nothing goes to the model, and the owner's own yes comes after the list of files.
                progress("edit-review")
                permit.check()
                with permit.waiting():
                    return ask_edit.delete_files(plan, snapshot, reader)
            if plan["action"] == "edit":
                progress("edit-prepare")
                prepared = ask_edit.prepare(plan, snapshot, args, reader, question_consent=permit,
                                            excerpts=ledger.excerpts() if ledger is not None else None)
                progress("edit-request")
                response = request(question, args, reader, task=prepared.source, write_grant=prepared.write_grant,
                                   check=permit.check, question_consent=permit, **_observed(carried, task=prepared.source))
                permit.check()
                # Transfer permission is finished. The subsequent test/review
                # uses the captured write guard and a separate TaskPlan TTL.
                permit.close()
                progress("edit-review")
                return ask_edit.finish(prepared, response, args, reader)
            if context is None:
                progress("collect")
                context = collect(plan, snapshot, args, reader, question_consent=permit)
            progress("answer-request")
            answer = request(question, args, reader, context=context, check=permit.check, question_consent=permit,
                             **_observed(carried, question=question, context=context))
        permit.check()
        return answer
    finally:
        permit.close()


def _without_excerpts(policy, selections, excerpts):
    """The edit selectors naming a file above read_file_size with no excerpt read in this question (metadata only)."""
    limit = policy.limits().read_file_size
    found = []
    for selection in selections:
        try:
            paths = file_context.resolve_paths(policy, [selection])
        except (OSError, ValueError):
            continue                      # the edit preparation reports an unusable selection itself
        for path in paths:
            try:
                size = os.lstat(path).st_size
            except OSError:
                continue
            if size > limit and not (excerpts.get(path) or {}).get("fragments"):
                found.append(selection)
                break
    return found


def _show_web(text, args):
    """The checked web answer, shown before the local part of the question continues."""
    from .analysis_terminal import render_answer
    from .terminal_style import rule
    print("\n" + rule() + "\n")
    print(render_answer(text, output_format=getattr(args, "format", "auto")))
    print("\n" + rule() + "\n")
    print(paint("  The question continues with the local part.", "muted"), flush=True)


from .skill_runtime import version_scope


@version_scope
def run(question, args, reader, request, *, phase=None, conversation_consent=None, skills=None, turn_context=None):
    """One ordinary turn, or at most two collision follow-ups.

    Each turn closes the previous QuestionConsent and opens a new one; the prompt
    is skipped only under the conversation's grant for the same permissions.
    A reply is an instruction to prepare a new plan, never a write approval.
    Provider failures, policy refusals and cleanup errors are not retried.
    """
    for turn in range(ask_followup.MAX_FOLLOWUPS + 1):
        try:
            if phase is not None:
                phase("access")
            snapshot = capture_access()
            with turn_context(snapshot) if turn_context is not None else nullcontext():
                return _run_once(question, args, reader, request, snapshot=snapshot, phase=phase,
                                 conversation_consent=conversation_consent, skills=skills)
        except ask_edit.CreateCollision as conflict:
            if turn == ask_followup.MAX_FOLLOWUPS:
                print("The conflict was not resolved after two follow-ups. Nothing was saved.")
                raise Declined() from None
            question = ask_followup.clarify(question, conflict.path, workspace=conflict.workspace, reader=reader)
            if question is None:
                print("Cancelled. The existing file stays unchanged.")
                raise Declined() from None
    raise AssertionError("unreachable collision turn")
