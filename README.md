<p align="center">
  <img src="docs/assets/logo.svg" width="96" height="96" alt="AI Desktop logo">
</p>

<h1 align="center">AI Desktop</h1>

<p align="center">A terminal assistant for diagnostics, configuration and file work on CachyOS and Arch Linux x86_64.</p>

In a terminal, start `ai-desktop`, write a plain sentence, and the program itself collects the data your `~/.config/ai-desktop/permissions.toml` file allows and sends it to the model. Every write shows a full diff and waits for your consent.

The interface and settings are in English, and the selected model answers in the language of your question.

## What it does

- **Conversation in the terminal.** Ask in a plain sentence; the conversation is saved locally and `ai-desktop -c` returns to it.
- **Reads only what you allow.** Files, commands, systemd and network access are decided by `permissions.toml`. `deny` always wins.
- **Commands and edits with your approval.** Read commands run in a sandbox. Changes need `yes`, and every write shows the full diff first, with undo.
- **Web search with checked sources.** With `web = true`, a Claude answer can use a search; the program lists the sources it checked.
- **Claude or Codex in a separate sandboxed account.** An independently installed client runs under the `ai-desktop` account in Bubblewrap, with network access through a filtering proxy. Local inference on `127.0.0.1` needs no cloud client.
- **Any number of terminals.** Each conversation has its own session and runs in parallel.

**What this is not:**

- It is not a background service or an autonomous agent. It runs when you ask it something.
- The model gets no shell and no tools. It proposes steps, and the program checks them and runs only what the permissions and your consent allow.
- It does not use an API key and does not switch to another service when something fails.
- It is not certified isolation or a secret scanner. See [SECURITY.md](SECURITY.md).

## Status

This is a development version, not a 1.0 release.

The current candidate package is **0.1.0-165** (changes: [CHANGELOG.md](CHANGELOG.md)).

What was checked on CachyOS, and what was not, is in the progress log of [docs/ROADMAP.md](docs/ROADMAP.md), the only roadmap and progress record. Read [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) before using this on important data.

## Requirements

- CachyOS or Arch Linux, x86_64, with systemd.
- Python 3.12 or newer, Bubblewrap and working unprivileged namespaces.
- A regular terminal: without one the program does not ask for consent, it refuses.
- `sudo`: the client session is started by a fixed helper with administrator rights.
- One model service: an independently installed Claude Code or Codex client, or your own local OpenAI-compatible server.

## Install

AI Desktop comes from its own signed pacman repository. Run these once:

```sh
# 1. Trust the AI Desktop signing key
curl -fsSLo /tmp/ai-desktop.asc https://raw.githubusercontent.com/ultherego/ai-desktop/main/packaging/signing-key.asc
sudo pacman-key --add /tmp/ai-desktop.asc
sudo pacman-key --lsign-key CFF85C09853A969B65A9D80DAD1162EE3172A20C

# 2. Add the repository to pacman
grep -q '^\[ai-desktop\]' /etc/pacman.conf || printf '\n[ai-desktop]\nSigLevel = Required DatabaseRequired TrustedOnly\nServer = https://github.com/ultherego/ai-desktop/releases/download/repo\n' | sudo tee -a /etc/pacman.conf

# 3. Install
sudo pacman -Syu ai-desktop
```

Updates then come with `sudo pacman -Syu`, like any other package.

## First start

AI Desktop talks to Claude or Codex through **their own client**, which you install yourself (it never downloads one), or to a model server on your computer.

```sh
# Codex: the Arch package
sudo pacman -S openai-codex
ai-desktop agent use codex

# or Claude: your Claude Code installation, approved once for AI Desktop
ai-desktop clients register claude                 # Codex from npm or its installer: ai-desktop clients register codex
ai-desktop agent use claude

# What AI Desktop may read and run: start from the example and edit it
mkdir -p ~/.config/ai-desktop
install -m600 /usr/share/doc/ai-desktop/permissions.example.toml ~/.config/ai-desktop/permissions.toml
$EDITOR ~/.config/ai-desktop/permissions.toml     # for Codex set codex = true under [network]

# Log in (a separate AI Desktop account) and start
ai-desktop login
ai-desktop
```

In the conversation, `/help` lists the commands. `ai-desktop -c` continues the last conversation. Every setting of `permissions.toml` in one line each, with examples: [docs/PERMISSIONS.md](docs/PERMISSIONS.md) (also installed in `/usr/share/doc/ai-desktop/`).

Building from source, other client installations, updating and uninstalling: [docs/INSTALLATION.md](docs/INSTALLATION.md).

## Documentation

| Page | Contents |
|---|---|
| [Installation](docs/INSTALLATION.md) | Install, clients, sudo, updating, uninstalling and what stays on disk |
| [Usage](docs/USAGE.md) | How a conversation works, consents, `ask`, `material`, `project`, `task`, undo, Skills, agents, Codex, data and privacy |
| [Configuration](docs/CONFIGURATION.md) | `permissions.toml`: paths, limits, `[commands]`, the template |
| [Permissions reference](docs/PERMISSIONS.md) | Every `permissions.toml` setting in one line |
| [Agents and accounts](docs/AGENT_MANAGEMENT.md) | Agents, models, conversations, Codex, local server, accounts and quotas |
| [Client installation](docs/AGENT_INSTALL.md) | Supported Claude/Codex installations, registration, compatibility |
| [Desktop](docs/DESKTOP.md) | Launchers, Niri shortcuts and the DMS widget |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | `doctor` and common errors |
| [Tests](docs/TESTING.md) | `scripts/verify.py` modes |
| [SECURITY.md](SECURITY.md) | Reporting problems, trust boundaries, what is not guaranteed |
| [Threat model](docs/THREAT_MODEL.md) | Architecture and threats |
| [Known limitations](docs/KNOWN_LIMITATIONS.md) | What is missing or unchecked |
| [Roadmap](docs/ROADMAP.md) | The only roadmap and progress record |
| [Release checklist](docs/RELEASE_CHECKLIST.md) | List for release maintainers |
| [CHANGELOG.md](CHANGELOG.md) | What each candidate changed |

## Tests

```sh
python3 -B scripts/verify.py --mode ci --fail-fast     # syntax, unit tests, no AI provider
```

Other modes are in [docs/TESTING.md](docs/TESTING.md). Green tests do not prove isolation on your computer.

## License

[Apache License 2.0](LICENSE).
