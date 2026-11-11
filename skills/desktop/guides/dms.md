---
name: dms
description: Diagnoses DMS (Dux Modular Shell, formerly DankMaterialShell), the Quickshell-based desktop shell run on top of niri, Hyprland, Sway, MangoWC and other Wayland compositors. Use for a shell that does not start or runs twice, settings that do not apply, broken plugins, autostart or tray problems, and shell updates.
programs: dms
---

# DMS diagnostics

## Scope and access

DMS is a shell (bar, launcher, notifications, lock screen, control center, settings) running on a Wayland compositor. Keep it separate from the compositor: a missing bar or launcher is a DMS question, window rules and outputs belong to the compositor guide. The project was renamed from DankMaterialShell to Dux Modular Shell; on Arch the package is `dux-shell`, which replaces and provides `dms-shell`. The CLI is `dms`.

AI Desktop reads run without the user's graphical session, the user's systemd manager and the DMS IPC socket. `dms ipc ...`, `dms doctor`, `dms systemd status` and `systemctl --user` / `journalctl --user` are commands to propose to the user; a failed connection means missing visibility, not a fault. Reading files within the allowed scopes is possible.

## Identify the version and session

- `dms version` shows the version. The CLI version should match the shell UI version; `dms doctor` checks this.
- Runtime: in 2.1 the UI runs as a Quickshell child process of the `dms` backend, and an older Quickshell can reduce features; newer DMS docs use dux-qt-base, a Quickshell fork loaded by `dms` itself. `dms doctor -v` reports which runtime and file are in use.
- Establish the compositor and how DMS is started: the systemd user unit `dms.service` (`/usr/lib/systemd/user/dms.service`) or a `dms run` line in the compositor configuration. Both at once start two instances.

## Configuration and logs

- Main settings: `~/.config/dms/settings.json`; optional drop-ins `~/.config/dms/config.d/*.json` take precedence and load in lexicographic order. Unchanged defaults are not written to the file.
- Other files: `~/.config/dms/clsettings.json` (clipboard), `~/.config/dms/plugin_settings.json`, `~/.local/state/dms/session.json`, `~/.cache/dms/dms-colors.json`.
- Since 2.0, `~/.config/DankMaterialShell` is migrated to `~/.config/dms` and left as a compatibility symlink. A config at `~/.config/quickshell/dms` is no longer found automatically; it is used only with `-c DIR` or `DMS_SHELL_DIR`.
- Plugins: `~/.config/dms/plugins/` (each with `plugin.json`); `~/.config/dms/plugins.lock.json` records registry plugins and exact Git commits.
- Environment: the Dux installer writes `~/.config/environment.d/90-dms.conf`; a `systemctl --user edit dms` override affects only DMS and apps it launches.
- Autostart entries managed by DMS are ordinary files in `~/.config/autostart/`; DMS only writes them, systemd's XDG autostart generator starts them.
- On niri, DMS-generated includes live in `~/.config/niri/dms/`; DMS never rewrites `config.kdl` or `dms/binds.kdl`.
- Logs: with systemd, `journalctl --user -u dms -n 50`; otherwise `dms run` in a terminal prints them.

## Investigation

1. Read `settings.json` and `config.d/` to find the effective value; a value missing from the file is the default.
2. Ask the user to run `dms doctor` (or `dms doctor -v`): it checks versions, runtime, compositor integration, configuration files and whether something starts `dms.service`.
3. Startup: ask for `dms systemd status`, `systemctl --user status dms` and the journal; check the compositor config for a `dms run` line as well.
4. Plugin not detected: check that it is under `~/.config/dms/plugins/` and that `plugin.json` is valid JSON. Plugin not loading: its errors appear in the output of `dms run` started from a terminal.
5. IPC targets: `dms ipc list` shows targets and functions only while DMS is running.
6. Autostart entries not running: ask for `systemctl --user is-active xdg-desktop-autostart.target`.

## Interpretation

- `Nothing starts dms.service with UNIT` from `dms doctor`: no compositor session unit wants the service. `dms systemd enable` links it to the session unit (`niri.service`, `hyprland-session.target`, `mango-session.target`); plain `systemctl --user enable dms` ties it to `graphical-session.target`, so it also starts under GNOME or Plasma.
- `xdg-desktop-autostart.target` inactive: the session does not pull it in; it cannot be started by hand.
- Tray icons missing for apps started at login: they started before DMS.
- An `undefined symbol` error mentioning `Qt_6_PRIVATE_API`: the Quickshell runtime was built against other Qt libraries and must be rebuilt or updated after a Qt update.
- DMS supports Wayland only.

## Change and verification

Change only after the user's consent. Copy `settings.json` and any `config.d/` files before editing; change one key, or prefer the Settings UI, which writes only the effective value. A broken file can be moved aside so DMS regenerates defaults, keeping the backup. Change only one start method; with systemd, remove the `dms run` line from the compositor config.

Applying changes is a separate consented step: `dms restart` (also after package updates), or `dms kill` then `dms restart` for duplicate instances. Updates are consented system changes: `sudo pacman -Syu dux-shell` for the package, `dms update` only for non-packaged installs; read the release notes for breaking changes. Plugin changes (`dms plugins update`, `dms plugins restore FILE`) change installed code and need consent. Do not end the compositor session. Verify with `dms doctor`, the journal and the original symptom.

## Result

Name the DMS version and runtime, the compositor, the start method, the file or key involved, the evidence and the write state. If the decisive check needs the session (`dms doctor`, IPC, user journal), name the command the user should run.

## Sources

- [dux-shell README](https://github.com/duxlabs-dev/dux-shell) (formerly AvengeMedia/DankMaterialShell)
- DMS docs ([dux-docs](https://github.com/duxlabs-dev/dux-docs), versions 2.1 and current): [Managing](https://duxlabs.dev/docs/dms/managing), [Systemd service](https://duxlabs.dev/docs/dms/cli-systemd), [doctor](https://duxlabs.dev/docs/dms/cli-doctor), [process management](https://duxlabs.dev/docs/dms/cli-process-management), [IPC](https://duxlabs.dev/docs/dms/keybinds-ipc), [plugins](https://duxlabs.dev/docs/dms/plugins-overview), [autostart](https://duxlabs.dev/docs/dms/autostart)
- [Arch package dux-shell: files](https://archlinux.org/packages/extra/x86_64/dux-shell/files/), [PKGBUILD](https://gitlab.archlinux.org/archlinux/packaging/packages/dux-shell/-/blob/main/PKGBUILD)
