# Changelog

## 0.1.0-123: web search progress

- While Claude searches the web, the waiting line shows what it is doing: "Searching the web (1)", "Reading search results (1)", "Writing the answer". The answer itself still appears only after its sources are checked, so an answer with sources the search did not return is never shown.

## 0.1.0-122: the answer while it is written

- In a conversation with Claude the answer appears paragraph by paragraph while it is being written, instead of all at once at the end. The checked final answer still decides; if it differs from what was shown, the program says so and prints it whole.
- Codex, web search and `material`/`project`/`task` still show the answer at the end.

## 0.1.0-121: smaller package

- A development-only test probe (`e2_boundary`, `e2_probe`) is no longer installed. No visible change.

## 0.1.0-120: cleanup

- No visible change. Shared code for `material` and `project`, an unused early network experiment removed from the package, and shorter documentation of the current state.

## 0.1.0-119: `task prune`

- `ai-desktop task prune --workspace DIRECTORY` removes finished history entries you choose from a list: saved and undone tasks, finished conflicts, unlinked history and fully restored deletions. It asks separately and never changes project files. Unfinished saves and deletions still in the trash always stay. Before, at the limit of 128 entries per directory the history had to be cleaned by hand.
- At that limit the message now names this command. `task status` showed an internal error there instead.
- `task restore` with an unknown ID refuses at once, before asking for consent.

## 0.1.0-118: `max` reasoning's long event stream

- The Claude client's event stream may now hold up to 4096 events (it was 128). With `max`, long thinking produces many events, and the finished answer was refused as `result`. The stream stays limited to 1 MiB.
- A refused Claude answer names the exact check, for example `Could not get an answer (result, claude-result-turns)`.

## 0.1.0-117: longer answers

- One answer may now be up to 96 KiB (about 45,000 characters with Polish letters; it was 32 KiB). With `max`, asking to "list everything" after dozens of commands produced a longer answer, and the whole answer was refused as `output-limit` after many minutes. The model is now told the limit and asked to put the most important first.

## 0.1.0-116: a current status line and a named failure reason

- The line under the input (service, profile, model, reasoning) follows the settings: it changes after a `/` command and when a queued setting is applied. Before, it kept the value from the start of the conversation.
- When a question fails for an unusual reason, the message names its fixed code, for example `Could not get an answer (result)`, instead of only `Could not get an answer`.

## 0.1.0-115: a longer wait for `high`, and cleanup

- With reasoning `high` the program waits up to 5 minutes for one answer (it was 110 s, as for the default); `xhigh` and `max` keep 15 minutes.
- Removed code that guessed a weather or systemd source from the question text; no path used it any more. Removed four acceptance scripts that only worked with packages 0.1.0-14 to -17.

## 0.1.0-114: a system-call filter for the client

- The Claude and Codex clients now run with a filter that refuses kernel interfaces a chat client never needs: mounting and namespaces, kernel modules, `ptrace` and reading other processes' memory, `bpf`, kernel keys, `userfaultfd`, clock, reboot and swap calls (`io_uring` reports as unavailable). Ordinary file, network and process calls are unaffected.

## 0.1.0-113: the widget heading follows the theme

- The **AI Desktop** heading is styled like the DMS system-update popout: the theme's primary colour at the large size and medium weight, with the service, role and model in the theme's muted colour right under it.

## 0.1.0-112: `/` list during a turn, widget title and DMS-only widget link

- While the model works, typing `/` shows the same list of commands above the input as when it waits; arrows choose, Tab completes and Enter opens the highlighted command (its menu at once, the choice after the answer). Closing a menu no longer turns off pasting in the live input.
- The widget title **AI Desktop** is smaller and uses the theme's second colour.
- `ai-desktop widget install` links the DankBar widget into DMS only when DMS is installed; without DMS it changes nothing, and it never replaces another plugin. `install_agents.py --install` runs it after installing the package.

## 0.1.0-111: fixes from a code review

- The keyboard is handed to `sudo` only after the live input has really stopped reading; an unfinished paste or character can no longer make keys typed for `sudo` land in your draft.
- `ai-desktop workspace recover --rollback` no longer deletes a newer editor save published while it checks a file.
- `host_sockets` accepts only sockets: `/run/user`, `/run/dbus` and directories are refused.
- A change made after many reads in one turn is always remembered, so it is not proposed again later.
- CI now runs the real sandbox and D-Bus filter tests instead of skipping them.
- The DankBar widget refreshes both services every minute; a Codex account with connected live limits is read live once a minute, like the refresh button. Claude usage still changes only after a Claude request: there is no live reading for Claude. The separate **Refresh limits** button in the Codex card is gone; the refresh icon and the minute timer do the same.
- The read-only D-Bus filter needs `xdg-dbus-proxy` 0.1.9 or newer (older versions cannot serve systemd's tools and have known filter bypasses); with an older proxy, `allow` commands get no D-Bus at all. CachyOS ships 0.1.9.

## 0.1.0-110: `/` menus open at once during a turn

- While the model works, `/help`, `/settings` and `/permissions` show at once; `/model`, `/effort`, `/agent` and `/skills` open their menu at once and your choice is applied after the answer, so a setting never changes halfway through a question. `/provider`, `/test` and `/exit` still wait for the answer.

## 0.1.0-109: a web search knows the conversation

- A web search now gets the latest exchanges of the conversation, like every other question, so "check online what the error above means" knows which error, also after returning with `ai-desktop -c`. Before, it got only the sentence you typed and often came back without sources. Files, task memory and Skills are still not sent, and Claude is told to keep personal details out of search queries.

## 0.1.0-108: a web search without sources says why

- When a web question ends without confirmed sources, the message now says which case it was: the model answered without searching, or the search ran but returned no link to cite. Before, both looked the same. Nothing more is sent or shown; only a fixed code (`web-no-search`, `web-no-links`) crosses the service boundary.

## 0.1.0-107: the input grows with longer text

- The input band grows upwards with what you type, like Codex and Claude Code: a long line wraps onto the next row and each new line (`\` + Enter) adds a row, up to about half the window; beyond that the text scrolls with the cursor. The conversation moves up to make room and comes back down after sending.

## 0.1.0-106: a wrongly written command no longer ends the turn

- Commands have no shell: each argument is one word without spaces. The model is now told this (and to use a waiting program such as `sleep infinity` to keep something running), and when it still writes a command with a script or spaces, it gets a `command-invalid` refusal and may correct it within `plan_corrections`, instead of the turn ending with `invalid plan (plan-command)`.

## 0.1.0-105: type while the model works

- The input stays live during a turn. Prepare the next message and press Enter to queue it: the queue is sent after the current answer, in order. `/` commands typed meanwhile are queued too, so a setting you choose takes effect only after the answer. Text you have not sent stays in the input for the next prompt. Ctrl+C clears the text, or stops the turn when the input is empty.
- A consent question (`y/n`, `yes`) now appears in the input itself and covers your draft until you answer; only keys typed after the question appeared count. Your draft comes back afterwards.
- The `sudo` password prompt takes the keyboard over completely, so no key typed there reaches the input.

## 0.1.0-104: room between the conversation and the input

- One empty row separates the conversation from the input band, so the last line of output no longer touches it. With the `/` list open, the empty row sits above the list.

## 0.1.0-103: `/` commands as a list above the input

- Typing `/` opens a list of matching commands with their descriptions above the input, like Codex and Claude Code; the highlighted one is marked in colour, arrows move it, Tab completes it and Enter runs it. The conversation moves up to make room, so nothing is covered, and the list closes when it no longer matches.

## 0.1.0-102: the conversation is always drawn from the top

- After the window changes size, the conversation is drawn again from the top (the head, then your questions and the answers at the new width) with the input at the bottom. Before, a terminal that grows pulled old lines back in and the whole conversation slid down. A resize while the model works is drawn when the input is ready again.

## 0.1.0-101: the conversation starts at the top and follows the window size

- Starting `ai-desktop` with a full screen no longer squeezes the conversation into the bottom rows: what was on the screen scrolls up into the terminal's history (it is not erased) and the head starts at the top of the window.
- Making the window larger or smaller redraws the input band at once, with what you have typed, across the new width.

## 0.1.0-100: the input stays at the bottom of the window

- In a colour terminal an interactive conversation keeps its input at the bottom of the window, like Codex: a taller band with `Ask ai-desktop to do anything` while it is empty, the service and profile under it, then the key hint. Everything else (answers, commands, consent questions) scrolls above it. A sent message goes into the thread on the same band.
- `/` commands are listed in the hint line while you type them. Resizing the window redraws the bottom rows.
- Without colour, in a pipe or in a window under 12 rows the prompt stays inline as before.

## 0.1.0-99: the template and documentation catch up

- `config/permissions.example.toml` uses the grouped form (`[commands.allow]` with `system`, `systemd`, `hardware`, `packages`) and shows `share_processes`, `host_sockets` and `deny` with their default values. The commands themselves are unchanged. An existing `permissions.toml` is never replaced.
- README and the threat model describe `...`, `commands.deny`, groups, the exact test and undo of a change, 32 commands per step, up to 128 per question, the full data budget and the yellow `empty` row. `docs/PERMISSIONS.md` explains `local`, `web` and `[local] port` more plainly.

## 0.1.0-98: a new look for conversations

- In a colour terminal the input is a band across the whole width with a different background; your sent messages keep it in the thread, and a muted hint with the keys is shown under it.
- An interactive conversation opens with a framed head: service, profile, model and reasoning.
- Commands appear as a tree under one head (`● Running 32 commands`, then `└ ✔ sensors · 1.4 KiB`).
- The waiting line reads `Claude: Analyzing the question (12s · Ctrl+C to stop)`.
- Without colour (`NO_COLOR`, `TERM=dumb`, a pipe) everything stays as before.

## 0.1.0-97: an empty result is not shown as a failure

- A read command that ends with a non-zero code and prints nothing (for example `pacman -Qu` when there is nothing to update) is shown in yellow as `empty, code 1`. A non-zero code with output (for example a permission message) stays red as `failed`.

## 0.1.0-96: up to 32 commands in one batch

- One plan may run up to 32 commands (before: 16), and the model is told it may send another batch while `command_rounds` allows. Before, a request for "everything" could send more than 16 and be refused as an invalid plan.

## 0.1.0-95: `command_rounds` above 32 works

- With `command_rounds` above 32 every question with commands was refused with a misleading `discovery_bytes` message: the data sent to the model still allowed at most 32 remaining commands. It now follows the 128 ceiling.

## 0.1.0-94: a full data budget no longer cancels the question

- When command output fills `discovery_bytes`, the remaining commands are skipped and the model answers from what it collected. Before, the next command of the same batch cancelled the whole question.
- Tip: with many commands, keep `command_output` small (e.g. `"16KiB"`), so one long log does not take most of `discovery_bytes`.

## 0.1.0-93: snapper works for read commands, more commands per question

- `snapper list-configs` and `snapper list` work again in `allow`: the D-Bus filter now passes the read-only Snapper calls the tool makes.
- `command_rounds` can be set up to `128` (before: `32`). The default stays `24`. All output still shares `discovery_bytes`.

## 0.1.0-92: refused changes say why

- The model is now told how to propose a change from `change`: with a `test` (an exact `allow` command) and an `undo` (a `change` or exact `allow` command). Before, it was not, so changes such as `docker pull ...` were refused.
- A refused change says exactly what is missing, on screen and to the model, so it can correct the plan or tell you which template to add.

## 0.1.0-91: command groups, open arguments and commands.deny

- `[commands.allow]` and `[commands.change]` can be split into named groups, e.g. `docker = [...]`, `general = [...]`.
- A template ending with `...` accepts further arguments, e.g. `"docker ps ..."`; paths among them follow `files.read` and `files.deny`. A change such as `"docker run --rm ..."` belongs in `change`, where every run asks for `yes`; `allow` refuses it, and an open read template never accepts a state-changing word such as `rm` or `pull` among its arguments.
- `[commands] deny` blocks whole commands, e.g. `"docker rm"`. See `docs/PERMISSIONS.md`.

## 0.1.0-90: read commands get D-Bus only for reading

- Commands from `allow` reach the system D-Bus only through `xdg-dbus-proxy` (now a package dependency), which passes a fixed list of read methods: properties, introspection and listing units, sessions, network links, devices and snapshots. State changes, DNS lookups, secrets and polkit are refused. Without the proxy a command gets no D-Bus.
- `resolvectl status` needs `"/run/systemd/resolve/io.systemd.Resolve"` in `host_sockets`; that socket also allows DNS lookups.

## 0.1.0-89: read commands no longer reach daemon sockets

- Commands from `allow` see an empty `/run` apart from the system D-Bus, the resolver's query socket and plain state files that read tools need. Docker, containerd, the systemd manager's varlink sockets, polkit agents, ssh and the display manager are hidden. Before, a read-only file system still let a command connect to any of them.
- New `[commands] host_sockets` brings back a specific socket, for example `host_sockets = ["/run/docker.sock"]` for `docker` templates. Docker's socket is root-equivalent.
- The system D-Bus is not filtered yet; see `docs/KNOWN_LIMITATIONS.md`.
- New `docs/PERMISSIONS.md`: every `permissions.toml` setting with one line on what it does, including common `host_sockets` paths.

## 0.1.0-88: plain task refusals and changes in the read-back thread

- `ai-desktop task` refusals say what happened and what to do (for example an interrupted earlier task, files changed after reading, a journal with wrong permissions), and otherwise name the program's reason code instead of a generic message. `--v` prints `TASK: REFUSED reason=...`. File contents and paths of the error are never shown.
- A saved change in a conversation is read back as what the program recorded: the files, the check and whether it was saved, instead of a bare result code.

## 0.1.0-87: continue the last conversation and read back the whole thread

- `ai-desktop -c` (or `ai-desktop ask -c`) reopens the most recently used saved conversation, with the same checks and consent as choosing it in `ai-desktop sessions`. With no saved conversation it starts a new one.
- A saved conversation keeps the whole thread, up to 2000 turns, apart from the bounded window sent to the model. Resuming prints it before the prompt, and `ai-desktop sessions show` lists it. Automatic next steps of a task are shown as such instead of the program's internal text.
- Revoking access also withholds old answers outside the request window. Turns still in the window are stored once.

## 0.1.0-86: Codex registration imports only files inside the release

- Registering a Codex installation refuses a top-level `codex-package.json`, `codex-path` or `codex-resources` that links outside the release directory. Before, such a link could point at any file the user can read, and the import would publish it to the client. Links nested inside the resource directories were already checked.

## 0.1.0-85: automatic local conversation checkpoints

- Interactive `ask` creates a private local record only after the first completed turn or completed action, then atomically checkpoints every later completed turn. A declined, cancelled or failed first turn that performed no action leaves no empty conversation behind.
- `--no-save` keeps the conversation only in memory and cannot be combined with `--session`. Existing explicit session creation and resume remain available. One-shot `--question` and non-terminal embedded readers are not persisted implicitly.
- The bounded record contains recent conversation state and provenance, never OAuth credentials, reusable grants, write approvals or a native-client session. Resuming still rechecks the current policy, sources, recipient and consent. A failed checkpoint leaves no partial JSON record, reports the local-storage problem and lets the in-memory conversation continue.
- Revoking access removes the dependent content from the saved copy at once. At the limit of 128 saved conversations the program says to delete old ones with `ai-desktop sessions`. Line and paragraph separators in a message no longer stop saving, and leftovers of a killed save are cleaned up.
- This changes local persistence and requires exact-candidate CachyOS CI/LOCAL, package and installed-file acceptance before merge. Full durable transcripts, semantic compaction, streaming and the `-c` shortcut remain unfinished.

## 0.1.0-84: bounded task approvals

- Approve up to five read-only project analysis stages once, with visible request, data and time limits. Further stages keep the same project snapshot, policy and recipient; changes or expiry stop the task. Ctrl+C interrupts it.
- Standalone `task run` declares the exact selected files and test before the first approval, then prepares the proposal and runs that test once without another Go/npm test prompt. The full diff and separate write approval remain mandatory.
- Grants stay in the current process, expire, cannot be reused for another scope or recipe, and are never restored from saved history. Ordinary `ask` and project-edit copy tests retain their existing approvals; the wider S9 flow remains unfinished.
- This candidate changes consent boundaries and requires new CachyOS acceptance before merge. Client registration, OAuth and the accepted Codex widget are unchanged.

## 0.1.0-83: Codex limits inside the widget

- Replace the Codex card's browser fallback with the next applicable account action: check sign-in, connect or reconnect limits, or refresh the existing connection. Show the provider's actual percentages and local reset times in the card; missing readings stay explicitly unknown.
- Refresh limits directly from the card, including an already connected account with no reading yet. Prevent concurrent refreshes and show progress. Opening the widget and its timer continue to read saved local state only.
- Preserve the existing separately approved native quota session and account binding. The candidate-82 client runtime passed CachyOS offline acceptance with Claude 2.1.319 and Codex 0.167.0; this widget follow-up does not change client execution, registration or OAuth protocols.

## 0.1.0-82: native compatibility and standalone package limits

- Separate Claude print-mode option checks from the signed-out account-status check. Exercise the complete production options with explicit unknown-option controls, including first-error ordering, and reject incidental error text or JSONL as parser proof. Preserve the positive offline status contract and strict runtime reply validation.
- Raise the shared native payload total from 512 MiB to 768 MiB: the official Codex 0.167.0 standalone bundle exceeds the old cap. Keep 384 MiB per file and all traversal/ownership checks. Analysis and quota services allow 3 GiB for the retained payload, its read-only namespace copy and native-client memory.
- Keep existing approved Claude registrations and separate account data. A previously refused Codex source can be registered after this application update. New exact-candidate CachyOS acceptance remains required before merge.

## 0.1.0-81: independent clients and explicit registration

- Use independently installed Claude Code or Codex through the existing isolated adapters. Supported system packages work directly; official native/HOME/npm installations use explicit administrator-approved registration and refresh of their native payload. Freeze the selected native executable and client-private resources for one operation, check compatibility offline before account/model access, and refuse changed installations or unsupported protocols.
- Inspect sources with `clients list`; register, refresh or remove an approved runtime copy explicitly. A source update never silently replaces its registered bytes. Remove vendor-client downloading and embedding from application installation. Claude, Codex, both or local-only setups are independent; upgrades retain the existing separate account data, and uninstalling AI Desktop leaves independent clients and separately retained registrations in place.
- Keep ordinary `doctor` and panel status metadata-only. Add explicit `doctor --clients` for the selected provider's offline compatibility check, with a distinction between the tested baseline and newer compatible but untested versions.
- The migrated runtime requires new CachyOS acceptance; acceptance of package 79 is not evidence for these changed boundaries.

## 0.1.0-80: system-client discovery and a single delivery roadmap

- Add read-only metadata inspection of system Claude Code and Codex to `doctor`, with separate missing, unsafe, unsupported-layout and changed-file outcomes. Inspection does not execute a client, access credentials or certify vendor origin, version or protocol compatibility.
- Record the chosen migration to independently installed system clients, with one shared detection layer, provider-specific compatibility and unchanged separate authentication. There is no planned second bundled/pinned mode. The current packaged-client runtime remains active until the complete migration and host acceptance pass.
- Reconcile README, installation, management, security, limitations and release documentation. Consolidate all work tracking into ROADMAP.md; preserve the accepted candidate-79 results and the outstanding core criteria.

## 0.1.0-79: large-project workflow and shared knowledge

- Separate larger file/context budgets from local test-copy budgets. Inspect large text by search and line ranges in ordinary conversation, and use checked anchored replacements for larger files. Preserve the complete diff, copy test, apply consent and rollback checks.
- Keep a larger bounded conversation window and task memory, with a separate larger saved-record budget. The continuation shortcut and full transcript/compaction work remain follow-up items.
- Load assigned Skills and their needed reference sections on demand in ask, material, project and task. Extend practical Arch/CachyOS procedures using official sources and explicit version applicability.
- Merged in PR #18 after five passing CI jobs and maintainer acceptance on CachyOS; unfinished follow-up criteria remain tracked in ROADMAP.md.

## 0.1.0-78: quota card layout and service selection

- Give the account list more vertical space when the screen allows it. Codex usage and connection buttons wrap inside the account card. The extra heading above the Codex card was removed.
- Click a usage card or focus it and press Enter to choose that service for new conversations. Its buttons keep their own actions. Claude and Codex both expose **Manage accounts…** in the accounts tab, including before sign-in.

## 0.1.0-77: live Codex limits in DankBar

- Connect a separate native Codex account from the widget. The upper-right refresh button and `ai-desktop usage --refresh --provider codex` fetch its current quota windows and reset times without sending a model request.
- The native session is isolated per desktop UID and slot. Connecting is opt-in, opening the widget reads local status, and a terminal action can disconnect the native grant.

## 0.1.0-76: account actions and status in DankBar

- Select Claude or Codex by clicking its card in **Accounts & login**. The refresh icon on both tabs reloads local status and saved quota readings.
- A successful login records the account for the widget. Connected accounts offer sign-out. Accounts from earlier installations can be checked locally. Terminal sign-in results identify the provider and account and use color when supported.
- Preserve the timestamp of each reported quota window across partial updates. When Codex has no readings, open ChatGPT usage settings from the widget.

## 0.1.0-75: open the widget on released DMS 2.x versions

- Use the button colors supported by DMS 2.0 and 2.1 to mark the selected tab. Opening the usage and account tabs works with the released button API.

## 0.1.0-74: load the quota card after a running DMS upgrade

- Move the quota card and its status script into a dedicated directory. When DMS reloads the widget after an upgrade, Qt can discover both files and show the current percentages and reset times.

## 0.1.0-73: graphical subscription usage and login

- The DMS bar can show the current five-hour percentage. The popout shows proportional usage bars with local resets for each reported window.
- Its **Accounts & login** tab can switch the service or account for new tasks and launch the native Claude/Codex sign-in picker. Authentication stays in the approved client flow.

## 0.1.0-72: subscription display and project test feedback

- The terminal panel and `usage` show separate five-hour and seven-day windows, utilization bars and local reset times. Current usage is colored by percentage. Expired readings are labelled with their last recorded value.
- The interactive panel keeps subscription details on a separate page. Returning to the menu restores the dashboard in place; leaving the panel restores the shell.
- An oversized project test reports which copy limits the authorized index exceeded before any tests run.

## 0.1.0-71: shared agent sessions and project checks

- Start a conversation with `ai-desktop`. Choose Claude, Codex or an explicitly allowed loopback model and review what will travel when changing provider in the same conversation.
- List named subscription accounts, inspect their last reported limits and open the offline agent panel or optional Niri/DMS launchers.
- Bind history filtering, Skill selection and delivery to current permissions. Saved answers with unverifiable sources are withheld on resume.
- Preserve agreed context for project edits, show which validation ran and offer fixed test recipes on an isolated copy.
- Require fixed nested command verbs, and verify downloaded clients before an optional private Arch package build.


- `permissions.toml`: the template `systemctl -H host <word>` is rejected, because `<word>` could become a command. After an option outside the list of options without a value, the next word is treated as its value. The `change` list can have 128 templates also in the request to the model (it was 24).
- Rebuilt procedures of 21 Skills: interpreting results, conditions for further steps, recognizing access restrictions and verifying changes. Removed overly broad recipes, among others a full balance as default cleanup and `systemctl revert` as the undo of a single edit.
- Files with a BOM and Windows line endings, e.g. CSV from Excel, can be read and edited. The model and the diff see plain text, and a write keeps the file format. Undo restores exactly the same bytes. Mixed line endings: reading works, editing is rejected.
- `ai-desktop ask`: the result of a performed change stays in memory when the model's next answer does not arrive, so the model will not propose it again. After access is revoked, new independent answers stay, and details of actions dependent on that file disappear. A conversation with many files always saves.
- `ai-desktop ask`: the final answer gets command output and loaded Skills, not only files read. "We agreed on the change, do it" works: the edit stage gets the conversation, task memory, command output and Skills.
- `task rollback` does not overwrite or delete a newer editor save: it leaves it and reports a conflict. Earlier it could report success after losing that version.
- A change request can be as long as a message in `ask` (128 KiB; it was 2 KiB), also in `task run` and `project`.
- A space before `/` sends the text as a question. A full trash refuses before deleting, and the recovery list still works.
- `ai-desktop ask`: consent for a conversation covers the whole `permissions.toml`. Removing a `deny` rule, changing a limit or `share_processes` requires new consent. Earlier the old consent stayed valid.
- Installation: `PKGBUILD` checks the SHA-256 of the Claude client and refuses to build with a wrong file. The README block does not run the file with a wrong checksum.
- New `arch-*` Skills based on the Arch Wiki: pacman, systemd, Btrfs, amdgpu, audio, network, Bluetooth, zram, Docker.
- `ai-desktop ask`: Skills loaded on demand. Only names and descriptions go with each question; the content goes when the model asks for it, and the program shows that. CachyOS Skills now start with the procedure.
- Reasoning `xhigh` and `max`: the model can answer for up to 15 minutes (it was 260 s). When the time runs out, the message talks about time, not about an expired consent.
- A paste that is too large is rejected whole. Earlier its remainder could end up in the next line.
- `ai-desktop ask`: a message up to 128 KiB including pastes (it was 2 KiB), memory of the last 32 exchanges (it was 8), an answer in history up to 8 KiB (it was 3 KiB). A paste that is too large no longer ends the conversation. `material` and `project` keep their limits.
- `ai-desktop ask`: `/` commands during a conversation, with a list under the line (`/model`, `/effort`, `/skills`, `/agent`, `/settings`, `/permissions`, `/help`, `/exit`). The menu starts on the current value, and long lists have pages. A change does not erase the conversation.
- A saved conversation is tied to the service, agent and access level. A change of model, reasoning or Skill content no longer cuts it off from resuming.
- New Skills for CachyOS, each about one thing: packages, kernels, sched-ext, boot and snapshots, hardware, games and extended diagnostics. Based on the CachyOS wiki. The `desktop-documentation` Skill points to the desktop documentation instead of guessing.
- Skills: no limit on the number per agent, up to 96 KiB per Skill and 104 KiB in total. The upper limit follows from the argument length restriction in Linux.
- **Security:** when consent expires or `permissions.toml` changes during a command from `[commands] change`, the result of the performed change is shown and recorded, and the program says plainly that the change was performed and gives the test and undo commands. Further steps are not run and nothing more is sent. Earlier the result was lost without a trace.
- `ai-desktop task`/`ask`/`project`: saving a change to an existing file no longer overwrites a save made at the same time by an editor. The file is exchanged atomically (`renameat2` with `RENAME_EXCHANGE`), and the exchanged version is compared with what the plan saw. On a difference the exchange is reverted and the write rejected, and the editor's version stays.
- **Security:** `material` and `project` check `permissions.toml` before each hand-over of data to Claude. A file change, turning off `network.claude` or revoking access to files being read stops further requests, also in whole-file analysis. Earlier it was enough that the consent and files were loaded at the start of the session.
- Polish diacritics in messages and help: the save and undo prompts, `task`, `analyze`, `status` and command descriptions.
- `ai-desktop task`: when `--workspace` points to a file, the program says so plainly instead of a general refusal.
- `ai-desktop task status` and `task rollback` list entries from the newest, with the date of the last change (journal file time). Number 1 is the newest task.

## 0.1.0-70: fixes

- `ai-desktop sessions`: a damaged saved conversation does not hide the others; `sessions list` points to it and `sessions remove` deletes it.
- `/log` in `project` also shows files handed to Claude during editing.

## 0.1.0-69: first public release (development version)

- `ai-desktop ask`: one consent per conversation, reads and commands from `permissions.toml` without extra questions, writing and deleting after a full diff or list of files and a separate consent, undo of changes.
- `ai-desktop material` for large files and `ai-desktop project` for project directories.
- The official Claude Code client in a separate system account and sandbox; optionally Codex.
- General Skills in the package, your own in `~/.config/ai-desktop/skills/`.
- The `permissions.toml` template contains only general commands that work in the sandbox and do not send logs or network names.
