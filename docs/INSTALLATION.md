# Installation, updating and uninstalling

## Claude Code and Codex clients

AI Desktop uses a compatible **independently installed Claude Code or Codex client**. Install only the provider you choose; local inference needs neither. The application package contains adapters and isolation machinery, without vendor executables. Cloud clients run under the separate `ai-desktop` account in Bubblewrap, with network access through a filtering proxy. Local inference uses an approved request to your configured loopback server.

Supported system packages are used directly. Clients installed in your home directory through the official installer or npm are registered explicitly as an administrator-approved copy; AI Desktop does not run a mutable HOME executable under its service account. Before credentials or model input are released, the selected native client and its private resources are bound to the operation and checked in an offline namespace. A compatible client update needs no AI Desktop rebuild; a registered client needs an explicit refresh to approve the new bytes. Your ordinary CLI login is not imported: connect an AI Desktop account separately. See [client installation and compatibility](AGENT_INSTALL.md).

## Installation from the signed repository (recommended)

The package is published in a signed pacman repository on GitHub Releases; the steps are in the [README](../README.md#install). pacman checks the signature of the package and of the repository database against the AI Desktop key you trusted with `pacman-key --lsign-key`; a package or database signed by any other key is refused. `sudo pacman -Syu` then updates AI Desktop with the rest of the system. The policy template and the settings reference are installed in `/usr/share/doc/ai-desktop/`.

## Building from source (CachyOS / Arch x86_64)

Keep the Claude/Codex installation you use: supported Arch/AUR packages, official native installers and current npm installations are covered. The example below uses the Arch Codex package. If you already installed through `curl` and a shell, omit `openai-codex` from the package command and register your existing client after installing AI Desktop. Local inference needs no cloud client. See [installation variants and migration](AGENT_INSTALL.md).

```sh
# 1. Application build tools and the selected system client (Codex example)
sudo pacman -S --needed base-devel git python bubblewrap xdg-dbus-proxy libseccomp sudo python-rich python-cryptography openai-codex

# 2. Code and application installation; this does not download a provider client
git clone https://github.com/ultherego/ai-desktopv2.git ~/ai-desktop
cd ~/ai-desktop
python3 -B scripts/install_agents.py codex --install
sudo systemctl daemon-reload

# 3. Select and check the client, then connect a separate AI Desktop account
ai-desktop agent use codex
ai-desktop doctor --clients
ai-desktop login --codex

# 4. Your access policy (fresh installation)
mkdir -p ~/.config/ai-desktop
cp ~/ai-desktop/config/permissions.example.toml ~/.config/ai-desktop/permissions.toml
chmod 600 ~/.config/ai-desktop/permissions.toml
$EDITOR ~/.config/ai-desktop/permissions.toml     # enable network.codex; fill in files.read; deny wins

# 5. Check and start
ai-desktop doctor
ai-desktop ask --preview      # permissions, without the model or sending data
ai-desktop ask                # conversation
```

Notes:

- **Client installation.** Choose `claude`, `codex`, `both` or `local` for `install_agents.py`; the same application can be installed before a provider. The helper never downloads, upgrades or removes a client. For a HOME/native/npm installation, use `ai-desktop clients register claude` or `ai-desktop clients register codex`, review the proposed payload and approve the administrator action. `--path /absolute/path` handles a recognized native installation outside automatic discovery. After updating that source, use `clients refresh PROVIDER`. See the [registration and update boundary](AGENT_INSTALL.md).
- **Sudo.** The client session is started by a fixed helper with administrator rights, so `ask`, `material`, `project`, `task run`, `login` and `stop` may ask for your `sudo` password. The package adds no sudoers or polkit rules.
- **Requirements.** Python 3.12 or newer, systemd, working unprivileged namespaces and a regular terminal: without one the program does not ask for consent, it refuses.
- **Checks.** Automated tests do not prove isolation on your computer; what has not been checked is listed in [known limitations](KNOWN_LIMITATIONS.md).

## Updating

Update the application and your Claude/Codex installations independently. A registered copy stays unchanged when its source updates; `ai-desktop clients list` exposes the source state, and `ai-desktop clients refresh PROVIDER` asks you to approve the replacement payload. Registration applies to the whole AI Desktop installation, so changing it affects every user of that provider. It takes priority over the system package until explicitly removed with `ai-desktop clients remove PROVIDER`. The adapter checks supported version ranges and required capabilities before account or model access. A newer client may pass those checks while remaining explicitly untested; an incompatible version or protocol is refused. One operation uses a fixed executable/resource snapshot, and a detected source change refuses further phases. Finish an operation before upgrading a client.

Upgrading from packages 0.1.0-79/80, which bundled their own clients, is described in [migration from packages 79/80](AGENT_INSTALL.md#migration-from-packages-7980).

```sh
ai-desktop stop
ai-desktop status
cd ~/ai-desktop
git pull
cd packaging/arch
makepkg --cleanbuild
sudo pacman -U ai-desktop-VERSION-x86_64.pkg.tar.zst    # VERSION = pkgver-pkgrel from PKGBUILD
sudo systemctl daemon-reload
ai-desktop status
```

Do not update during a task. An update is not atomic: files are replaced one after another and there is no automatic package rollback. After an interrupted update install the same file again and check `pacman -Qkk ai-desktop`. The package does not change `permissions.toml`, the login profile or the journals. An unknown or damaged file format means refusal, not migration; do not fix versions or hashes by hand.

## Uninstalling

1. End the session: `ai-desktop stop`, then `ai-desktop status`.
2. Check `ai-desktop task status --workspace DIRECTORY` for the directories you used. Uninstalling does not undo saved changes.
3. `sudo pacman -R ai-desktop`, then `sudo systemctl daemon-reload`.

**What stays on disk:**

| What | Where |
|---|---|
| Independently installed Claude/Codex clients | Their original system or user locations; not removed with AI Desktop |
| Administrator-approved client copies | `/var/lib/ai-desktop-clients/`; remove explicitly with `clients remove PROVIDER` before uninstalling if no longer needed |
| The `ai-desktop` system account | system |
| Claude and Codex login profiles | `/var/lib/ai-desktop/claude`, `/var/lib/ai-desktop/codex` |
| Policy, agents, your own Skills | `~/.config/ai-desktop/` |
| Saved conversations | `~/.local/state/ai-desktop/conversations/` |
| Task journals and trash | `.ai-desktop-tasks` in every directory where something was saved |
| Your project files and files changed by the program | in place |

The program has no "remove everything" command. You remove this data yourself and at your own risk; erasing `.ai-desktop-tasks` takes away undo and restore, and files from the trash disappear with it. Terminal history, system logs and data on the provider's side are not cleaned.
