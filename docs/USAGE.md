# Usage

## How a conversation works

1. **You type a question and press Enter**, for example "check everything you know about my PC".
2. **AI Desktop does not interpret your question.** It does not look for words such as "everything" and does not decide for you what matters. Your text goes to the model as written. The program only adds what you allowed in `permissions.toml`:
   - which commands may run (`allow`) and which changes may be proposed (`change`);
   - which directories may be read;
   - how many commands and how much data may be used;
   - the names and short descriptions of your Skills, without their content;
   - the latest exchanges of this conversation and notes about earlier actions.

   Your files and command output are not sent yet.
3. **You are asked for consent:** `Send this request to Claude? [y/n]`. Until then nothing has left your computer.
4. **The model only answers with a plan.** Claude runs in a closed environment without any tools and cannot run anything itself. It sends back a plan as text, for example "run `sensors`, `free -h`, `lscpu`". The model, not AI Desktop, decides that "everything" means many commands.
5. **AI Desktop checks every command in the plan.**
   - Commands that are not on your list are refused.
   - Commands from `allow` run in a sandbox: read-only, without network, with a mostly empty `/run` and D-Bus only through a read filter.
   - Commands from `change` are shown on a red card and wait for you to type `yes`.
6. **The results go back to the model**, cut to the `command_output` and `discovery_bytes` limits.
7. **The model may ask for more**, for example another batch of commands, a file or a Skill. Steps 5 and 6 repeat while the limits allow. Within this one question you are not asked again, except for changes (`yes`) and file writes (a diff to approve).
8. **The model writes the answer**, and AI Desktop shows it.
9. **The conversation is saved locally.** With your next question the model sees the latest exchanges of this conversation, and `ai-desktop -c` returns to it later.

In short: the model decides what it needs; AI Desktop decides what is actually allowed, according to your file.

## Choose where to work

| In a terminal | Result |
|---|---|
| `ai-desktop` | Start or resume a conversation. Outside a terminal the default remains offline status. |
| `ai-desktop agent list` / `ai-desktop agent use local` | Inspect services or choose the default for future conversations. |
| `ai-desktop agents use reviewer` | Select a role and its Skills. A role cannot expand the access policy. |
| `/provider codex` inside `ask` | Review the destination and the context the new recipient would receive. Confirm to continue the same conversation there. |
| `ai-desktop account list` / `ai-desktop usage` | See accounts and cached quota windows. After connecting native Codex limits in DankBar, click refresh or run `ai-desktop usage --refresh --provider codex` for a fresh reading. |
| `ai-desktop panel` / `ai-desktop status --json` | Open the interactive panel or read local metadata for a desktop widget. |
| `ai-desktop clients list` | Inspect installed sources and registrations without starting a client. |
| `ai-desktop local models` | Explicitly query the model list of your configured local inference server. |

For local inference, start your own trusted OpenAI-compatible server on `127.0.0.1`, enable `local = true` under `[network]` in your existing policy, then choose `ai-desktop agent use local`. The default port is 8080. Cloud accounts and the local service have independent permissions. There is no automatic recipient switch.

See [agent settings, accounts and sessions](AGENT_MANAGEMENT.md), [verified client installation](AGENT_INSTALL.md) and [Niri shortcuts and the DMS widget](DESKTOP.md).

## Daily work

Everywhere, `--v` shows details and the data being sent, and does not change consents. `--format plain` turns off answer formatting, `--format markdown` prints the source Markdown (`ask`, `material`, `project`, `task run`).

### `ai-desktop ask`

```sh
ai-desktop ask
ai-desktop ask --question "Why does the unit not start?"
ai-desktop ask --question "What is an inode?" --no-context
```

Without `--question` this is one conversation: after each answer `>` comes back. You can run any number of conversations at the same time in different terminals; each has its own session. A Claude or Codex answer appears paragraph by paragraph while it is written (Codex's live answer is not yet checked with a real request); a web search shows its progress on the waiting line ("Searching the web (1)", "Reading search results (1)") and its answer appears once the sources are checked. Typing `/` lists the commands above the input; their menus (`/model`, `/effort`, `/skills`, `/agent`, `/provider`) open there too, also while an answer is being written, and a choice made during an answer applies after it. An empty line or two Ctrl-D end the conversation. It is saved locally after each completed turn; `--no-save` keeps it in memory only. `ai-desktop -c` (or `ai-desktop ask -c`) reopens the conversation used last, of any kind (`ask`, `project`, `material`), and first prints the whole thread; if the newest saved conversation is damaged or open in another terminal it says so instead of opening an older one. `ai-desktop settings save off` turns automatic saving off for good (`on` turns it back on); then only `--session new` saves. Weather (`[network] weather = true`): name the city in the question; for questions without one, `ai-desktop settings weather-city CITY` remembers a city in `~/.config/ai-desktop` (`off` forgets it, no argument shows it). Without a remembered city the program asks for one and offers to remember it; your location is never guessed. Only the newest turns that fit the request limits go to the model. Ctrl-C while waiting stops only the current turn. `ai-desktop stop` in another terminal also ends only the question being answered; the conversation continues and the next question opens a new session. A message can be up to 128 KiB including pastes (a large paste appears as a placeholder).

The conversation opens in a full-screen view: the input stays at the bottom, PgUp/PgDn or the mouse wheel move through earlier text, and End or any key brings back the live conversation. Drag with the mouse to select text: releasing copies it to the clipboard (a double click takes a word or a path); holding Shift uses the terminal's own selection instead. The input has the usual editing keys: Ctrl/Alt+arrows by words, Ctrl+Backspace or Ctrl+W, Ctrl+Delete, Ctrl+U, Ctrl+K, Home/End, Ctrl+Z to undo. When the conversation ends, its text is left in the terminal's own history. `ai-desktop ask --inline`, or the word `inline` in `~/.config/ai-desktop/view`, keeps the earlier view in the terminal's own screen.

How consents work:

1. **One `Send this request to Claude? [y/n]:` per conversation.** It covers further questions, reads and commands from `allow`. The program asks again when `permissions.toml` or the chosen service or agent changes.
2. **Reads and discovery without asking.** The model lists and searches names, then can search content and read bounded line ranges in authorized files. Paths must come from discovery or your text. Source hashes and coverage distinguish excerpts from complete files. The budgets are in the [limits table](CONFIGURATION.md#optional-limits).
3. **Commands from `allow` without asking**, up to 32 in one model step and 24 per question by default (`command_rounds`, at most 128). All their output shares `discovery_bytes`; when it fills up, no more commands run and the model answers from what it collected. A command that ends with a non-zero code and prints nothing (for example `pacman -Qu` with nothing to update) is shown in yellow as `empty`, not as a failure.
4. **Each write separately.** An edit or a new file may follow from earlier discovery. The program checks the proposal on an isolated copy, shows the full diff and asks `[y/n]`. A project suite uses the complete permitted project copy and asks before running it (in `ai-desktop project` this is part of the edit decision). A failed or refused test blocks the write.
5. **Deletion separately.** The program lists the files and asks `[y/n]`. Files are not erased, they go to the task trash: at most 8 files at a time, up to 16 MiB each.
6. **Changes from the `change` list** require typing `yes`.

After an applied change the model takes the next step by itself or finishes the answer, at most 5 automatic steps. Each further write again shows the diff and asks. A refusal or error hands the turn back to you. Type `continue` to go on after the limit.

In a normal conversation, ask to run the current project's tests or type `/test pytest`, `/test python-unittest`, `/test go-test` or `/test npm-test`. Start AI Desktop in a project already covered by a recursive `files.read` rule and use a profile that permits execution. Before each run the controller shows the current directory, a snapshot identifier and the number of indexed and excluded files. It asks before running the fixed recipe on a temporary copy. Raw test output stays in the terminal. The conversation remembers the result for that snapshot. Go and npm require a systemd user manager with delegated CPU, memory and PID controllers and run without downloading dependencies.

The check on the copy comes from a list: `python-unittest`, `pytest`, `go-test`, `npm-test`, `python-syntax`, `json`, `toml`, `bash-syntax`, `fish-syntax`, `text`. The `text` check only confirms valid UTF-8 text: **syntax and behaviour are not checked**. No check proves the project is correct.

You turn on web search with `web = true`. It is a separate request with your question and the latest exchanges of the conversation, so a question about "the error above" is understood, also after `ai-desktop -c`. Files, task memory and Skills are not sent; anything the conversation already contains (for example an earlier answer quoting a log) is, and Claude is told to keep personal details out of search queries. A question may search first and then continue locally ("find how to do X and change a.py"): the checked search answer is shown and handed to the next step, which may read files, run your allowed commands or propose an edit with the usual diff and yes. A question searches at most once, and only before anything local was read. Addresses the model writes into a web answer's text are removed; the program's list of checked sources is the only place addresses appear. Source numbers in the text are optional: the list at the end is enough, and a number that names no listed source is removed. Weather (`weather = true`) reveals the place name and your IP address to the Open-Meteo service, and the result goes to the model.

During a conversation you can type a command starting with `/`. After you type `/` a list appears under the line, arrows select, Tab completes. An installed Skill is a command too: `/arch why does the boot hang?` loads that Skill for the question; Enter or Tab on `/ar` only completes `/arch ` so you can type the question after it. Commands do not erase the conversation: `/help`, `/model`, `/effort`, `/skills`, `/agent`, `/provider`, `/settings`, `/permissions`, `/context`, `/test`, `/exit`. They work locally and send nothing, except `/provider`, which moves the conversation to another recipient after you confirm, and `/test`, which asks before it runs a suite on a copy. Claude can also read the installed manual page of a tool during a question ("Manual pacman(8) “--overwrite” · 1 excerpt(s)"): the program reads the page from `/usr/share/man` itself, so it describes the installed version, and nothing goes to the network. `/context` shows what of the conversation the next question sends: how many recent turns word for word, how many task-memory entries (active, done, rejected), what was left out to fit the limits; every turn stays in the local thread. After a change of model or Skills the program asks once more for consent to send. `/skills` changes the Skills of the agent for good (saved in its profile). The Skills that come with a built-in agent (`default`, `diagnostic` and the others) are always on, also new ones added by an update; your own Skills come after them. `/NAME question` (for example `/arch what does my firewall block?`) sends the question with that Skill already loaded, like in Claude Code; a Skill the agent does not have is turned on for this conversation only. The `/` list shows the installed Skills after the commands. A question starting with a path, e.g. `/etc/fstab what is it?`, goes to the model normally. To send a single word starting with `/`, start with a space.

#### Conversation memory

With each question the model receives up to 64 recent exchanges word for word within 512 KiB (a question up to 128 KiB, an answer up to 32 KiB with visible truncation) and the task memory: up to 96 entries of 1 KiB, 64 KiB in total. When the task memory is full, outdated versions of corrected entries and working notes go first; refused proposals and completed actions are kept longer, and active decisions and the goal last. Older turns are not summarised and cannot be fetched back into a later request. A long history uses up the service limit, because it goes with every question. `/context` shows what the next question sends and what was left out. If you revoke access to a file, the answers that depend on it disappear from memory before the next question.

A saved record (at most 16 MiB) keeps the 64 exchanges that can go to the model, the task memory and a read-back thread of up to 2000 turns (256 KiB per question or answer); beyond that the oldest turns of the thread are no longer shown. `ai-desktop -c` prints the whole thread, but the model still gets only the newest turns that fit the limits above. Interactive terminal `ask` creates the record after the first completed turn or completed action and atomically checkpoints later completed turns. A declined or cancelled first turn creates nothing. One-shot `--question` and non-terminal embedded invocations are not saved implicitly. If local storage cannot accept a checkpoint, the answer remains visible and the conversation continues in memory with a warning; only the last successfully stored checkpoint survives process or terminal failure.

### `ai-desktop material FILE`

One UTF-8 text file up to `file_size`, covered by `files.read`. You ask in a plain sentence; nothing is sent before the question.

- A file up to 200 KiB goes to the model whole.
- With a larger one the model asks for searches and line reads, at most 8 requests per question, up to 256 KiB per request and 2 MiB in total. You see each step, and the rest of the file is not sent.
- "Analyze the whole file": the program splits the file into up to 512 parts, shows the request and byte budgets, and asks once for the whole task. The consent expires after 30 minutes. `part_size` is configurable up to 512 KiB. Coverage is counted by the program, and an interrupted analysis is marked as partial. Required Skills must be loaded before the fixed map/reduce plan starts.
- The answer gives the lines the model really received. Session memory keeps up to 16 questions and bounded answers.
- No writing, no Internet and no running code. Refusals cover among others an empty file, a link, control characters and invalid UTF-8.

A material conversation is saved like the others; `ai-desktop material -c FILE` continues it while the file is unchanged (line numbers refer to one version of the file), otherwise it starts anew. Local commands that send nothing: `/find TEXT`, `/show A-B`, `/log`, `/exit`. `/ask A-B QUESTION` narrows the question to those lines. Like `ai-desktop`, the conversation keeps one session and service connection for all its requests, so `sudo` asks at most once per conversation (again only after 15 idle minutes or 4 hours) and there is no wait between steps.

### `ai-desktop project DIRECTORY`

The project editor receives the authorized conversation and observations behind the proposed edit. Type `/test pytest`, `/test python-unittest`, `/test go-test` or `/test npm-test` to run a fixed suite on an isolated copy of the authorized index. The controller requests approval for the copy. On a size refusal it reports the measured file count or byte total, the relevant policy setting and the fixed copy ceiling (20,000 files, 32 MiB per file and 128 MiB total); defaults are 2,000 files, 8 MiB per file and 64 MiB total, configured separately with `test_copy_*`. A refused copy runs no tests. The test output stays in your terminal. Go and npm need a delegated systemd user scope with verified cgroup v2 limits and the corresponding local toolchain. Recipes never install dependencies.


One directory covered by `files.read`. The scan is local and repeated before each question. Limits: 20,000 files, 100,000 entries, depth
16. Entries from `deny`, links, special files and `.git` are skipped and counted.

- **Reading.** One consent covers one task: at most ten stages (five per question), 80 requests, 20 MiB of project data and 50 minutes in total. Each stage retains its own 8-request, 2 MiB and 10-minute ceiling. File names, matches and fragments are sent. Further stages, and follow-up questions in the same window, continue within the same approved project snapshot, policy and recipient without asking again ("Within the task you approved…"). A changed file (also one you saved through AI Desktop), permission, model or recipient, the deadline, the budget, a refusal or Ctrl+C ends the task: the next question shows the scope and asks again.
- **Changes.** This mode is not read-only: after a request for a change the model proposes files, and from there the same flow as in `ask` applies (consent to send, test on a copy, diff, separate consent to write, undo). When the proposal declares a project suite (`pytest`, `python-unittest`, `go-test`, `npm-test`), sending the files and that one test run are a single decision; the copy is still re-checked before the test runs, and if the approval expired or the task ended in the meantime, you are asked again before the test. This needs a `files.write` entry with the directory and subdirectories covering the project, e.g. `~/Projects/**`. Edits support up to 64 changed files, 5 path levels and 8 MiB per resulting file, within the owner’s lower read limits. The local test copy supports paths up to 16 levels deep independently of editable targets.
- **Saving and continuing.** A project conversation is saved locally after its first question (`--no-save` turns this off, `--session new|ID` picks one). `ai-desktop project -c DIRECTORY` continues the latest conversation about that directory, also after its files changed: it shows the last questions with the recorded outcomes, and every question rescans the files.
- **Checks.** When the project has test files and a change to code is checked only for syntax, the change preview warns that the project's tests are not run; ask for the change "and check it with the tests" to run them. The same session as in `ai-desktop` is kept for the whole conversation: one `sudo` prompt and no waits between steps.
- **Deletion.** A list of files, `[y/n]`, the task trash and a printed restore command.
- Project suites can run on the approved copy. The project mode has no general-purpose command runner or web search.

Local commands: `/files [DIR]`, `/search TEXT`, `/refresh`, `/log`, `/test RECIPE`, `/exit`. Large pastes (up to 32 KiB) appear as a placeholder and go to the model as data. Enter sends, and Alt+Enter breaks the line. Two Ctrl+C within 3 seconds end the session.

### `ai-desktop task`

A change to exactly the files you name, without `permissions.toml`: the choice is yours.

```sh
ai-desktop task run "Fix the addition in calc.py" --workspace /path/to/project \
    --file calc.py --context test_calc.py --test python-unittest
```

`--file` may be changed, `--context` is read-only. The directory must be given as an absolute path and must not be the whole home directory. Limits: 512 selected input files, 64 changed files, 8 MiB per file and 16 MiB in total, instruction up to 128 KiB. Small unique anchored replacements are applied against the original bytes; missing, repeated or overlapping anchors are refused. Tests run on a copy, without network or home directory. Python tests cannot create processes, threads or sockets, so not every test suite will work here. `--preview` shows the data without the model or a test. The initial scope approval covers preparing the proposal and running the declared test once on the selected-file copy, including Go/npm. The summary shows the workspace, files, recipe and finite preparation budget. Changed files, recipient, scope or recipe invalidate the approval. The full diff and final write decision remain separate; no failed test authorizes a write.

### Task approval scope

- `ai-desktop project`: one approval covers the task, including follow-up questions, while the project, permissions, model and recipient stay the same. Sending edit data and its declared project suite are one decision. Each change outcome (saved, test failed, save declined, file changed, save interrupted) is recorded with its next step in the conversation, which `ai-desktop project -c DIRECTORY` continues.
- `ai-desktop task run`: the initial approval covers preparation and one run of the declared test on the selected-file copy.
- `ai-desktop` (ask): conversation consent is not permission to run code, so each test on a copy asks separately.

All final writes, deletions and undo operations keep their separate decisions. Approvals live in the process only; a resumed conversation never restores them.

### Undo and restore

After a write the program prints the ready command.

```sh
ai-desktop task status   --workspace DIRECTORY              # states, including the trash, moves and copies
ai-desktop task rollback --workspace DIRECTORY              # undo a write, separate consent
ai-desktop task restore  --workspace DIRECTORY --plan ID    # bring deleted files back, or undo a move or copy
ai-desktop task prune    --workspace DIRECTORY              # remove finished entries (a copy kept after undo stays), separate consent
```

- Undo compares **bytes and file mode**, so an editor saving the same content does not block it. When the file was changed later, undo refuses and leaves your version.
- Such a conflict shows in `task status` and can be retried, but it does not block other writes. New writes are blocked only by unresolved temporary files and unfinished tasks.
- Restore works only on free names and never overwrites a newer file.
- **Moving and copying.** In a conversation you can ask to move or copy a file or a whole folder inside one `files.write` scope ("move the folder `old-version` from Downloads into my project folder"). The program shows source, destination, number of files and size, and asks; it never overwrites, so the destination must not exist. A move is one rename on one filesystem (use a copy across disks). A copy is built under a hidden name next to the destination and appears only when complete. Undo with the printed `task restore` command: a move goes back to its old name, a copy is moved into `.ai-desktop-tasks` rather than deleted. Links, devices and files you do not own are refused.
- The journal lives in `.ai-desktop-tasks` in the task directory, contains the old and new content of files and has private permissions. The limit is 128 journals per directory, with no automatic cleanup. Do not delete it to get around a refusal, because you lose the ability to undo.
- The journal is tied to the directory: a moved or replaced directory may give a safe refusal, and history from another place is marked as unlinked and is not used for undo.


## Your own Skills

The packaged Skills lead from a symptom through the needed reads and interpretation of results to an established cause or a clearly stated lack of data. They take the sandbox restrictions into account: lack of access to the session or network is not proof of a host failure. A proposed fix requires a separate plan, consent and a check of the result; loading a procedure runs nothing.

A Skill is a procedure, not a permission. The shared loader starts with assigned names and descriptions in all four modes: `ask`, `material`, `project` and `task`. The model requests needed procedures and individual bundled references; unrelated bodies stay out of its request. Bundles are frozen and rechecked for changes before handoff. References include official source URLs, a checked date, version applicability and a content hash; a rolling wiki snapshot must not be described as documentation verified for an installed version. Skill knowledge has a separate bounded budget from file-discovery output. Native client tools and automatic native Skill discovery remain disabled. Files and commands are decided by `permissions.toml`. The package contains only general Skills; what concerns your computer you keep on your side:

```sh
ai-desktop skills new my-system      # creates ~/.config/ai-desktop/skills/my-system/SKILL.md
ai-desktop skills                    # list of available Skills
ai-desktop skills show my-system     # preview
ai-desktop agents                    # assign a Skill to an agent
```

A packaged Skill always wins: each update replaces the program's Skills, so give your own a different name. An own Skill with a packaged name is ignored and reported by `/skills`, `ai-desktop skills` and `ai-desktop doctor`; `skills new` refuses packaged names. **Do not put secrets or data about yourself in it: the description and content of a Skill may go to the model.** A path written in a Skill counts as one given by you, so the model can read it without discovery; `deny` still decides.

## Agents and models

An agent is a saved set: service, model, reasoning level, access (`changes` or `read-only`), instructions and Skills. An agent adds no permissions. A `read-only` agent does not write, does not delete and does not get `change` commands.

```sh
ai-desktop settings                                 # settings menu
ai-desktop settings models                          # available models and levels
ai-desktop settings set --model sonnet --effort high
ai-desktop agents setup                             # ready-made roles and Skills, does not change yours
ai-desktop ask --agent reviewer --model opus --effort high
ai-desktop sessions                                 # saved conversations
ai-desktop -c                                       # continue the last conversation, showing the whole thread
```

Details: [AGENT_MANAGEMENT.md](AGENT_MANAGEMENT.md).

## Codex through a system package

Choose Codex independently of Claude. The adapter remains experimental. Install `openai-codex` through your package manager, or register your existing native/npm installation with `ai-desktop clients register codex`, then use the existing separate account flow:

```sh
ai-desktop agent use codex
ai-desktop doctor --clients
ai-desktop codex status
ai-desktop login --codex
```

In `permissions.toml` set `codex = true` in `[network]`: the Claude and Codex permissions are separate, and Codex is blocked by default. Codex has no web search. Disconnecting the account: `ai-desktop codex logout --allow-chatgpt-logout`. Removing the native client does not prevent disconnecting an existing SIWC account or a native quota grant.

## Data and privacy

The provider receives: your question, the instructions of the program and the agent, assigned Skill metadata and the procedures/references actually loaded, the `read` and `write` patterns and command templates, names from discovery, the content and **absolute paths** of files read, command output, systemd and weather data and the conversation memory. `deny` rules are not sent. The official client adds its own technical context, so `--v` is not a record of all traffic. The full list: [SECURITY.md](../SECURITY.md).

- Data that was sent cannot be withdrawn: neither stopping, nor undo, nor uninstalling removes it from the provider.
- Interactive `ask`, `project` and `material` save a private bounded checkpoint after the first completed turn and later completed turns unless you use `--no-save`. `--session new` still creates an explicit record. An answer may quote files it read; saved records contain no OAuth credentials or reusable approvals.
- The terminal may keep the scrollback, and `--question` goes to the shell history. Do not attach `--v` output to public reports.
