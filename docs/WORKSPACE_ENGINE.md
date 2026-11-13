# Workspace analysis engine

Design of the one mechanism that lets an ordinary `ai-desktop` conversation, started in a project directory, analyse a
large repository over many steps, without a separate mode and without the user managing batches, limits or stages.
It answers conditions 7 and 8 of the 1.0 release in [ROADMAP.md](ROADMAP.md); progress is recorded only there.

## The problem

A conversation question used to be one growing document of steps (listings, searches, excerpts, command output) sent
whole with every request. Its byte budget (`discovery_bytes`, at most 256 KiB) was therefore the size of the whole
question: once the steps filled it, discovery ended, even with rounds left. A file larger than one excerpt (32 KiB) was
read only by the final read, which ended the question. A long task such as "read every file of this folder and
describe each" stopped after a few dozen files, without a limit having been reached in any meaningful sense.

`ai-desktop project` had the opposite defect: its stages kept going, but each stage started from an empty controller
and lost the unfinished searches, pinned versions and evidence of earlier stages (fixed in candidate 170).

## How the reference agents do it

- Claude Code explores a large repository by agentic search (listing, `grep`-like search, reading files and following
  references) without a mandatory embedding index ([Anthropic: how Claude Code works in large codebases](https://claude.com/resources/articles/how-claude-code-works-in-large-codebases-best-practices-and-where-to-start)).
- Anthropic's context engineering guidance and API describe three tools for long tasks: clearing old tool results while
  keeping a placeholder of what they were, structured notes kept outside the context window, and compaction
  ([cookbook: context engineering tools](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)).
  The notes must be excluded from clearing, or the agent loses track of what it saved.
- Codex compacts its context automatically near the window limit; reports of lost task continuity after compaction
  show why the program, not the model's own summary, has to keep the durable state
  ([Codex CLI context compaction](https://codex.danielvaughan.com/2026/03/31/codex-cli-context-compaction-architecture/)).

AI Desktop cannot give the model its own tools (the model never reads anything itself), so the program plays the role
of the agent loop: it runs each step locally, under the policy, and decides what one request carries.

## Three separate limits

| Level | What bounds it | Setting |
|---|---|---|
| One step | one excerpt or page: 32 KiB of whole lines; one search: 20 snippets; one command's output | `command_output`, fixed excerpt size |
| One request (the window) | the steps one request carries; file text in it; files in it | `discovery_bytes`, `read_total_size`, `read_files` |
| One question | rounds of steps, commands, pages, downloads; each portion extended on the owner's yes, at most twice | `discovery_rounds`, `command_rounds` |
| The task across questions | conversation history and task memory; in `project` the approved task scope (stages, requests, bytes, time) | conversation limits, project task scope |

A request is always bounded. A question is bounded by rounds that the owner can extend. A task continues in the next
message from the conversation's history and task memory.

## The window (candidate 171)

`ask_discovery.DiscoveryLedger` keeps the question's steps and builds each request from them:

1. **Clearing.** When a new step does not fit the window, the oldest steps leave it (`make_room`). One `cleared` step at
   the head of the document counts them and names the newest 128 (`f0:src/a.c lines 1-240 of 900`, `list f0:src`,
   the command line). Grounding keeps every name ever observed, so the model can read a cleared file again: the return
   to the sources.
2. **Only sent steps are cleared.** A step that has not reached the model never leaves the window. When the files of
   one read do not fit together, what was read goes first (`WindowFull`) and the model asks for the rest.
3. **Notes.** Any plan may carry `"notes"`: the model's working memory (findings, what is left), at most 32 KiB and at
   most a quarter of the window, replaced whole. The program keeps it in every request of the question as `op=notes`
   and never clears it. Notes are data, never authority; they pass the same text rules as other model text.
4. **Pages.** A read of one to four exact files returns each as an `op=content` step. A file larger than one excerpt
   comes as its first page of whole lines (`complete=false`); the model reads the rest with `read-range`. Only a file
   that is not plain text, or whose first line alone exceeds an excerpt, goes to the ordinary final read.
5. **Owner limits per request.** `read_total_size` and `read_files` bound the file text and files of one request; older
   excerpts leave the window to make room. Excerpts that left it still count as delivered for an edit of a large file.

### Invariants kept

- The question's consent accepts a new window only when the steps that stayed are unchanged and in order, every step
  that left is counted by the trace, the count never goes down, and at least one new step follows
  (`question_consent._added_steps`). The number of rounds still bounds the sends of one consent.
- Before every transfer the identities of exactly the excerpts in the window are checked again (`recheck_discovery`),
  as before; a cleared excerpt is no longer sent, so it is no longer rechecked.
- Clearing never removes an owner's Skill, the web answer or the `discovery-budget` refusal.
- Bubblewrap, `permissions.toml`, the file version checks, validation before every hand-off and change approval are
  unchanged.

## Next stages

1. **Task checkpoint across messages.** Record, per conversation, which files and ranges were read (with versions) and
   the last notes, so "continue" in the next message or after an interruption resumes without reading again what was
   done. Reuse the conversation's task memory and its taint rules for revoked sources.
2. **Search in the window.** Literal content search over a whole scope with a resumable cursor (as `project` has),
   run by `ripgrep` in the isolated executor of audit item 1, with the same deny filtering; the result pages are window
   steps.
3. **One index.** Bring the `project` index (paths, sizes, versions) into the conversation as an incremental local
   catalog, so a 10,000-file tree is listed and searched without a full rescan before every request; keep the full
   identity check before a read or a change.
4. **Verification after synthesis.** `material` map/reduce gets a step that reopens the cited source ranges to check a
   conclusion before the answer, as the window already allows in a conversation.
5. **`project` on the same window.** Its stages become portions of the same engine, so one mechanism serves both.

## Acceptance

Measured separately for transport (the model received the data), continuity (state kept, nothing repeated needlessly)
and quality (the expected findings):

| Scenario | Required result |
|---|---|
| A folder of about 200 small and large source files, "describe each file" | every file described in one question with at most the owner's continue answers; large files read in pages; no question ends early without the limit note |
| About 100 files | the requested areas analysed completely |
| About 1000 files | a defect that spans several distant modules found |
| About 10,000 files | the analysis continues across portions and messages without losing findings |
| 100,000 files or a million entries | no failure; selective search works; the coverage is stated |
| A file changed during the analysis | the change is detected and the stale excerpt not used |
| A permission revoked | its data refused from then on |
| An interrupted process | the task resumes from its checkpoint |
| Analysis, then change, then test | one task keeps its context; the change goes through the diff approval |

Real Claude and real Codex both run the matrix on CachyOS before 1.0. Unit tests cover the window rules
(`tests/test_workspace_window.py`).
