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

The current candidate package is **0.1.0-158** (changes: [CHANGELOG.md](CHANGELOG.md)).

What was checked on CachyOS, and what was not, is in the progress log of [docs/ROADMAP.md](docs/ROADMAP.md), the only roadmap and progress record. Read [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) before using this on important data.

## Requirements

- CachyOS or Arch Linux, x86_64, with systemd.
- Python 3.12 or newer, Bubblewrap and working unprivileged namespaces.
- A regular terminal: without one the program does not ask for consent, it refuses.
- `sudo`: the client session is started by a fixed helper with administrator rights.
- One model service: an independently installed Claude Code or Codex client, or your own local OpenAI-compatible server.

## Install

AI Desktop uses a compatible **independently installed Claude Code or Codex client** and never downloads one. Supported system packages are used directly. A client installed in your home directory (official installer or npm) is registered once as an administrator-approved copy with `ai-desktop clients register claude` or `ai-desktop clients register codex`.

```fish
sudo pacman -S --needed base-devel git fish python bubblewrap xdg-dbus-proxy libseccomp sudo python-rich python-cryptography
git clone https://github.com/ultherego/ai-desktopv2.git ~/ai-desktop
cd ~/ai-desktop
python3 -B scripts/install_agents.py codex --install    # claude, codex, both or local
sudo systemctl daemon-reload
```

The full guide, with client selection, login, the access policy, updating and uninstalling, is in [docs/INSTALLATION.md](docs/INSTALLATION.md). Client variants and migration are in [docs/AGENT_INSTALL.md](docs/AGENT_INSTALL.md).

## Quick start

```fish
ai-desktop agent use codex     # choose the service (claude, codex or local)
ai-desktop doctor --clients   # check the selected client offline
ai-desktop login --codex      # connect a separate AI Desktop account
ai-desktop ask --preview      # see what the policy allows, without the model or sending data
ai-desktop ask                # start a conversation
ai-desktop -c                 # continue the last conversation
```

Create your policy first from `config/permissions.example.toml`; see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

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

```fish
python3 -B scripts/verify.py --mode ci --fail-fast     # syntax, unit tests, no AI provider
```

Other modes are in [docs/TESTING.md](docs/TESTING.md). Green tests do not prove isolation on your computer.

## License

[Apache License 2.0](LICENSE).
