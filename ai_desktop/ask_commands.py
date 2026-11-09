"""Local controls typed at the `ask` prompt: nothing here is sent to the model and the conversation stays as it is.

Called only for what the owner typed at the prompt, never for model output. A control can change which model or
which instructions the next question uses; it can never widen permissions, which permissions.toml alone decides.
"""
from dataclasses import replace
import re

from . import agent_profiles as profiles, execution, run_controls
from .conversation_sessions import SessionError
from .overlay_menu import choose
from .terminal_menu import MenuInputError

COMMANDS = (
    ("/help", "list commands"),
    ("/model", "change the model"),
    ("/effort", "change the reasoning level"),
    ("/skills", "turn Skills on or off in this conversation"),
    ("/agent", "change the role (or select a provider by name)"),
    ("/provider", "switch the recipient of this conversation"),
    ("/settings", "show and change the settings"),
    ("/permissions", "show what the program can access"),
    ("/test", "run an offline suite in the current authorized project"),
    ("/exit", "end the conversation"),
)
# The older Polish command names keep working; they are simply no longer listed.
_ALIASES = {"/pomoc": "/help", "/?": "/help", "/sila": "/effort", "/ustawienia": "/settings", "/uprawnienia": "/permissions",
            "/koniec": "/exit", "/quit": "/exit", "/q": "/exit"}
_SHAPE = re.compile(r"/[^/\s]*\Z")           # `/etc/fstab what is it?` is a question, `/model` is a command
EXIT = "exit"
PAGE = 8
_NEXT, _PREVIOUS, _APPLY = "Next page", "Previous page", "Apply"
_AGAIN = "The next question will ask for consent again because the settings changed. The conversation continues."
CUSTOM = "custom"                            # "Other ID" in the model menu: the owner types the ID
# What /settings offers, in its order; `material` and `project` offer the ones that work there.
SETTINGS = ("/model", "/effort", "/agent", "/provider", "/skills")


def _pick(title, options, default=0):
    """An index into `options`, or None when cancelled or when there is no terminal for a menu. Long lists are paged,
    because one menu frame holds a limited number of rows."""
    options = tuple(options)
    page = default // PAGE
    while True:
        start = page * PAGE
        shown = list(options[start:start + PAGE])
        count = len(shown)
        following = len(shown) if start + count < len(options) else None
        if following is not None:
            shown.append(_NEXT)
        previous = len(shown) if page else None
        if previous is not None:
            shown.append(_PREVIOUS)
        try:
            answer = choose(title, shown, default=default - start if start <= default < start + count else 0)
        except MenuInputError as failed:
            # Never silently: say why the menu did not open (a fixed code), then the caller shows the typed form.
            print("The menu could not open (%s)." % (failed.args[0] if failed.args and type(failed.args[0]) is str
                                                      and failed.args[0].startswith("menu-") else "menu"))
            return None
        if answer is None:
            return None
        if answer == following:
            page += 1
        elif answer == previous:
            page -= 1
        else:
            return start + answer


class Deferred:
    """A choice made in a menu while the model worked; it is applied after the answer, with the conversation then."""

    def __init__(self, label, run):
        self.label, self.run = label, run


_READ_ONLY = ("/help", "/settings", "/permissions")


class Controls:
    def __init__(self, *, session=None, settings=SETTINGS):
        self.skills = None                   # this conversation's choice; None means what the agent has assigned
        self.session = session               # the PromptSession whose next prompt can start with a typed command
        self.settings = tuple(settings)

    def _assigned(self):
        name, profile = self._profile()
        return name, profile, (profile.skills if self.skills is None else self.skills)

    def activate(self):
        """Replace the full mode-independent prompt with the assigned catalog before asking anything."""
        selected = execution.current()
        if selected is None:
            return
        try:
            _, profile, chosen = self._assigned()
            execution.select(dict(selected, instructions=profiles.catalog_for(replace(profile, skills=tuple(chosen)))))
        except (profiles.ProfileError, OSError, ValueError, KeyError):
            raise profiles.ProfileError("skill-catalog") from None

    def load_skill(self, name):
        """The body of one Skill the model asked for. None for anything outside this conversation's selection."""
        try:
            _, _, chosen = self._assigned()
            from . import skills
            if type(name) is not str or not skills.RESOURCE.fullmatch(name):
                return None
            owner = name.partition("/")[0]
            if owner not in chosen:
                return None
            snapshot = skills.bundle(owner)
            selected = execution.current()
            if selected is not None and profiles.CATALOG_HEADER in selected["instructions"]:
                if "bundle-sha256: " + snapshot["sha256"] not in selected["instructions"]:
                    return None
            from .skill_runtime import current_versions
            return skills.resource_text(snapshot, name, versions=current_versions())
        except (profiles.ProfileError, OSError, ValueError, KeyError):
            return None

    def check_skill(self, name, text):
        """Recheck the bundle/assignment, independent of newer version observations."""
        try:
            from . import skills
            owner = name.partition("/")[0]
            _, _, chosen = self._assigned()
            snapshot = skills.bundle(owner)
            selected = execution.current()
            return (owner in chosen and (selected is None or profiles.CATALOG_HEADER not in selected["instructions"]
                    or "bundle-sha256: " + snapshot["sha256"] in selected["instructions"]))
        except (profiles.ProfileError, OSError, ValueError, KeyError):
            return False

    def handle(self, line, conversation=None, *, talk=None, consent=None, reader=input, run_test=None):
        """False: `line` is an ordinary question. True: handled here. EXIT: the owner ends the conversation."""
        if line[:1].isspace():
            return False                     # a leading space is the documented way to send such a word as a question
        text = line.strip()
        word, _, value = text.partition(" ")
        if not _SHAPE.fullmatch(word) or "\n" in word:
            return False
        value = value.strip()
        name = _ALIASES.get(word, word)
        if name == "/":
            chosen = _pick("Commands", tuple("%s  %s" % item for item in COMMANDS))
            if chosen is None:
                self._help()
                return True
            name = COMMANDS[chosen][0]
        if name == "/help":
            self._help()
        elif name == "/exit":
            return EXIT
        elif name == "/settings":
            chosen = self._settings_menu()
            if chosen is not None:
                return self.handle(chosen, conversation, talk=talk, consent=consent, reader=reader, run_test=run_test)
        elif name == "/test":
            if run_test is not None:
                run_test(value)
            else:
                print("Use /test RECIPE in an interactive conversation.")
        elif name == "/permissions":
            self._permissions()
        elif name == "/model":
            self._choice("/model", "model", value, conversation)
        elif name == "/effort":
            self._choice("/effort", "effort", value, conversation)
        elif name == "/provider" or (name == "/agent" and self._provider_alias(value)):
            self._switch_provider(value, conversation, talk, consent, reader)
        elif name == "/agent":
            self._agent(value, conversation)
        elif name == "/skills":
            self._skills(value, conversation)
        else:
            print("Unknown command %s. List: /help. To send this as a question, start with a space." % word)
        return True

    def during_turn(self, line, conversation=None):
        """A `/` command typed while the model works. Read-only ones are shown at once; a menu opens at once and only
        its choice waits; anything else waits whole. Returns what to queue: message lines and Deferred choices, or a
        `terminal_input.Prefill` that goes back into the input for the owner to finish (a model ID to type).
        `conversation` is the saved conversation of this turn, if any: what it cannot accept is refused at once."""
        if line[:1].isspace():
            return [line]
        text = line.strip()
        word, _, value = text.partition(" ")
        if not _SHAPE.fullmatch(word) or "\n" in word:
            return [line]
        value = value.strip()
        name = _ALIASES.get(word, word)
        if name == "/":
            chosen = _pick("Commands", tuple("%s  %s" % item for item in COMMANDS))
            if chosen is None:
                return []
            name = COMMANDS[chosen][0]
        if name == "/settings":
            run_controls.conversation_command("/settings")
            chosen = self._settings_menu(show=False)
            if chosen is None:
                return []
            name = chosen
        if name in _READ_ONLY:
            self.handle(name)
            return []
        if name == "/agent" and conversation is not None and not self._provider_alias(value):
            print(run_controls.AGENT_TIED)        # refused now, not after the answer
            return []
        if value:
            return [name + " " + value]
        if name in ("/model", "/effort"):
            picked = self._menu_value(name, name[1:], " \u00b7 applies after the answer")
            if picked == CUSTOM:
                from .terminal_input import Prefill
                print(self._custom_hint())
                return [Prefill("/model ")]
            return [] if picked is None else [name + " " + picked]
        if name == "/agent":
            picked = self._pick_agent()
            return [] if picked is None else ["/agent " + picked]
        if name == "/skills":
            try:
                profile_name, profile = self._profile()
                from . import skills
                available = tuple(skills.names())
            except (OSError, ValueError, KeyError):
                return ["/skills"]
            result = self._skill_menu(available, list(profile.skills if self.skills is None else self.skills))
            if result is None:
                return []
            return [Deferred("/skills " + (", ".join(result) or "none"),
                             lambda conversation: self._set_skills(profile_name, profile, result, conversation))]
        return [name]

    def _menu_value(self, command, field, suffix=""):
        """The value chosen in the /model or /effort menu: the same choices and labels as `ai-desktop settings`
        (the model list comes from the client itself). None when cancelled; CUSTOM for "Other ID"."""
        provider = self._provider()
        if field == "model":
            from .management_cli import model_choices
            values, labels, note = model_choices(provider)
            if note:
                print(note)
        else:
            values = execution.efforts_for(provider)
            labels = tuple(execution.effort_label(key) for key in values)
        values, labels = tuple(values), tuple(labels)
        if not values:
            return None
        try:
            present = self._active()[field]
        except (OSError, ValueError, KeyError):
            present = None
        shown = execution.model_label(present) if field == "model" else execution.effort_label(present)
        chosen = _pick("%s \u00b7 now: %s%s" % (command, shown, suffix), labels,
                       default=values.index(present) if present in values else 0)
        return None if chosen is None else values[chosen]

    def _custom_hint(self):
        return "Type the %s model ID after /model and press Enter." % execution.provider_name(self._provider())

    def _settings_menu(self, show=True):
        """/settings: the current settings, then a menu of the ones that can change here. Returns the command of the
        chosen entry (its own menu opens next), or None when closed or when there is no terminal for a menu."""
        if show:
            self._settings()
        try:
            selected = self._active()
        except (OSError, ValueError, KeyError):
            selected = None
        rows = []
        for command in self.settings:
            if command == "/model":
                rows.append("Model: " + execution.model_label(selected["model"] if selected else "default"))
            elif command == "/effort":
                rows.append("Reasoning: " + execution.effort_label(selected["effort"] if selected else "default"))
            elif command == "/agent":
                rows.append("Agent: " + (selected["profile"] if selected else "default"))
            elif command == "/provider":
                rows.append("Service: " + execution.provider_name(selected["provider"] if selected else "claude"))
            elif command == "/skills":
                try:
                    _, profile = self._profile()
                    chosen = profile.skills if self.skills is None else self.skills
                    rows.append("Skills: " + (", ".join(chosen) or "none"))
                except (OSError, ValueError, KeyError):
                    rows.append("Skills")
        if not rows:
            return None
        chosen = _pick("Settings \u00b7 choose one to change", tuple(rows))
        return None if chosen is None else self.settings[chosen]

    def _pick_agent(self):
        try:
            names = tuple(sorted(self._config().profiles))
            present = self._profile()[0]
        except (OSError, ValueError, KeyError):
            return None
        if not names:
            return None
        chosen = _pick("/agent \u00b7 now: %s \u00b7 applies after the answer" % present, names,
                       default=names.index(present) if present in names else 0)
        return None if chosen is None else names[chosen]

    @staticmethod
    def _help():
        for name, description in COMMANDS:
            print("%-14s %s" % (name, description))
        print("Commands work locally, send nothing and do not change the conversation so far. To save permanently: ai-desktop agents.")

    @staticmethod
    def _provider():
        selected = execution.current()
        return selected["provider"] if selected is not None else "claude"

    @staticmethod
    def _config():
        config = profiles.Store().load().config
        return config.with_provider(Controls._provider())

    def _provider_alias(self, value):
        if value not in execution.PROVIDERS:
            return False
        # Preserve an existing role named after a provider. /provider is always unambiguous.
        try:
            return value not in self._config().profiles
        except (OSError, ValueError):
            return False

    def _destination(self, provider):
        from .conversation_sessions import role_key
        profile, _ = role_key(execution.current())
        selected = profiles.resolve(profile=profile, provider=provider)
        with execution.using(selected):
            self.activate()
            return execution.current()

    def _switch_provider(self, value, conversation, talk, consent, reader):
        from . import assistant_flow, conversation_transfer
        if not value:
            index = _pick("Provider for this conversation", execution.PROVIDERS,
                          default=execution.PROVIDERS.index(self._provider()))
            if index is None:
                return
            value = execution.PROVIDERS[index]
        if value not in execution.PROVIDERS:
            print("Choose a provider: " + ", ".join(execution.PROVIDERS))
            return
        if talk is None or consent is None:
            print("Choose a provider at the interactive conversation prompt.")
            return
        try:
            conversation_transfer.switch(value, prepare=self._destination, talk=talk, saved=conversation,
                                         consent=consent, reader=reader)
        except assistant_flow.AccessDenied:
            print("Provider switch refused by the current permissions. Check permissions.toml.")
        except (OSError, ValueError, KeyError):
            print("Could not switch. Check the selected account, agent settings and saved conversation.")

    def _profile(self):
        selected = execution.current()
        config = self._config()
        name = selected["profile"] if selected is not None else config.active
        return name, config.profiles[name]

    def _settings(self):
        run_controls.conversation_command("/settings")
        if "/skills" not in self.settings:
            return
        try:
            _, profile = self._profile()
        except (OSError, ValueError, KeyError):
            return
        chosen = profile.skills if self.skills is None else self.skills
        print("Skills: " + (", ".join(chosen) or "none"))

    @staticmethod
    def _permissions():
        from . import assistant_flow
        try:
            snapshot = assistant_flow.capture_access()
        except assistant_flow.AccessDenied:
            print("Could not read permissions.toml. Check this file.")
            return
        assistant_flow.describe_access(snapshot)
        for scope in snapshot.access["files"] + snapshot.access.get("write", []):
            print("  %s: %s" % (scope["id"], scope["pattern"]))

    @staticmethod
    def _rebind(conversation):
        print(_AGAIN)
        if conversation is None:
            return
        try:
            conversation.rebind(execution.current())
        except (SessionError, OSError, ValueError):
            print("The change works in this conversation, but could not be stored with the saved conversation.")

    @staticmethod
    def _active():
        """What the next question would use: a role is resolved even while the plain default leaves no snapshot."""
        return execution.current() or profiles.resolve()

    def _choice(self, command, field, value, conversation):
        try:
            present = self._active()[field]
        except (OSError, ValueError, KeyError):
            present = None
        if not value:
            value = self._menu_value(command, field)
            if value is None:
                run_controls.conversation_command(command)
                return
            if value == CUSTOM:
                # As in the settings: the owner types the ID. It is typed at the prompt, which starts with
                # "/model ", so it is checked by the same rules as any /model ID typed there.
                if self.session is not None:
                    self.session.prefill = "/model "
                print(self._custom_hint())
                return
        if value == present:
            print("No change: %s is already %s." % (command, value))
            return
        before = execution.current()
        run_controls.conversation_command((command + " " + value).strip())
        if value and execution.current() != before:
            self._rebind(conversation)

    def _agent(self, value, conversation):
        if conversation is not None:
            print(run_controls.AGENT_TIED)
            return
        if not value:
            try:
                names = tuple(sorted(self._config().profiles))
            except (OSError, ValueError):
                names = ()
            try:
                present = self._profile()[0]
            except (OSError, ValueError, KeyError):
                present = None
            chosen = _pick("/agent · now: %s" % present, names,
                           default=names.index(present) if present in names else 0) if names else None
            if chosen is None:
                run_controls.conversation_command("/agent")
                return
            value = names[chosen]
        try:
            if value == self._profile()[0]:
                print("No change: /agent is already %s." % value)
                return
        except (OSError, ValueError, KeyError):
            pass
        before = execution.current()
        run_controls.conversation_command("/agent " + value)
        if execution.current() != before:
            self.skills = None
            self.activate()
            self._rebind(None)

    def _skills(self, value, conversation):
        from . import skills
        try:
            name, profile = self._profile()
            available = tuple(skills.names())
        except (OSError, ValueError, KeyError):
            print("Could not read the agent settings. Check ai-desktop agents list.")
            return
        chosen = list(profile.skills if self.skills is None else self.skills)
        if value:
            if value not in available:
                print("There is no Skill %s. Available: %s" % (value, ", ".join(available)))
                return
            chosen = [item for item in chosen if item != value] if value in chosen else chosen + [value]
        else:
            result = self._skill_menu(available, chosen)
            if result is None:
                print("Skills: " + (", ".join(chosen) or "none") + " · available: " + ", ".join(available))
                print("Switch one: /skills NAME")
                return
            chosen = result
        self._set_skills(name, profile, chosen, conversation)

    def _set_skills(self, name, profile, chosen, conversation):
        selected = dict(execution.current() or profiles.resolve(profile=name))
        try:
            selected["instructions"] = profiles.catalog_for(replace(profile, skills=tuple(chosen)))
            execution.select(selected)
        except (profiles.ProfileError, OSError, ValueError):
            print("Skills not changed: they could not be loaded, or together they are over %d KiB."
                  % (profiles.MAX_EXECUTION_INSTRUCTIONS // 1024))
            return
        self.skills = tuple(chosen)
        print("Skills: " + (", ".join(chosen) or "none") + " (this conversation; permanently: ai-desktop agents).")
        self._rebind(conversation)

    @staticmethod
    def _skill_menu(available, chosen):
        """Enter ticks the highlighted Skill, as does Space; ticks survive changing the page. None: cancelled or no terminal."""
        marks = {index for index, name in enumerate(available) if name in chosen}
        page, position = 0, None
        while True:
            start = page * PAGE
            names = list(available[start:start + PAGE])
            rows = list(names)
            following = len(rows) if start + len(names) < len(available) else None
            if following is not None:
                rows.append(_NEXT)
            previous = len(rows) if page else None
            if previous is not None:
                rows.append(_PREVIOUS)
            apply = len(rows)
            rows.append(_APPLY)
            local = {index - start for index in marks if start <= index < start + len(names)}
            try:
                answer = choose("Skills · Space ticks · selected: %d" % len(marks), rows,
                                default=apply if position is None else position, marks=local, markable=len(names))
            except MenuInputError:
                return None
            if answer is None:
                return None
            marks = {index for index in marks if not start <= index < start + len(names)} | {index + start for index in local}
            if answer == apply:
                return [name for index, name in enumerate(available) if index in marks]
            if answer == following:
                page, position = page + 1, None
            elif answer == previous:
                page, position = page - 1, None
            else:
                marks.symmetric_difference_update({answer + start})
                position = answer
