---
name: noctalia
description: Diagnoses Noctalia v5, the native Wayland desktop shell that the CachyOS installer sets up with its niri and Hyprland groups, from its version, TOML config layers, validator and log file. Use for a shell that does not start or reports it is already running, settings that do not apply, config warnings after updates, plugins, notifications or lock screen problems.
programs: noctalia
---

# Noctalia diagnostics

## Scope and access

Noctalia is a shell (bars, dock, launcher, control center, notifications, wallpaper, lock screen, OSD, desktop widgets) running on a Wayland compositor. Keep it separate from the compositor: window management, outputs and keybinds belong to the compositor guide. On Arch the package is `noctalia` (v5, `/usr/bin/noctalia`), a native C++/OpenGL ES program without Qt; it is not Quickshell-based. The v4 line used Quickshell (`qs`, `noctalia-qs`, JSON config); its commands and config do not apply to v5. CachyOS settings packages `cachyos-niri-noctalia`, `cachyos-hypr-noctalia` and `cachyos-mango-noctalia` depend on `noctalia` and ship `/etc/skel/.config/noctalia/config.toml`; skel is copied only when an account is created.

AI Desktop reads without the user's graphical session and Wayland socket. `noctalia msg ...` talks to the running shell; a failed connection is missing visibility, not a fault. Propose IPC commands to the user. Reading files within the allowed scopes is possible.

## Identify the version and session

1. Installed: `noctalia --version` prints `noctalia VERSION`.
2. Running: ask the user for `noctalia msg status` (basic state as JSON).
3. Start method: Noctalia has no own autostart; the compositor starts it. CachyOS dotfiles use `spawn-at-startup "noctalia"` (niri, `~/.config/niri/cfg/autostart.kdl`), `hl.exec_cmd("noctalia")` in the `hyprland.start` hook (Hyprland Lua config), and `exec_once = noctalia &` (Mango, `~/.config/mango/cfg/autostart.conf`). `noctalia --daemon` returns after the shell has initialized.

## Configuration and logs

- Your config: every `*.toml` directly in `$NOCTALIA_CONFIG_HOME/noctalia/`, `$XDG_CONFIG_HOME/noctalia/` or `~/.config/noctalia/`, merged in alphabetical order. `[include] files = [...]` adds files or directories (relative to the including file, `~` and `$VAR` expanded); the including file wins; `autoload = false` loads only the entry file and its includes.
- GUI overrides: `~/.local/state/noctalia/settings.toml` (or under `NOCTALIA_STATE_HOME` / `XDG_STATE_HOME`). It loads last and wins over hand-written files. `state.toml` beside it can hold private runtime data; do not quote it.
- Order: built-in defaults, then your files, then `settings.toml`. Both layers are watched and hot-reloaded about 150 ms after a write.
- Files under `~/.config/noctalia/` are never rewritten; old keys are migrated in memory and Settings keeps a warning until the source file is updated.
- Plugins: local `~/.local/share/noctalia/plugins/`; git sources under `~/.local/state/noctalia/plugins/`. Installed is not enabled: `[plugins].enabled` lists active ones, and a Settings toggle writes an override to `settings.toml`.
- Log: `$XDG_CACHE_HOME/noctalia/noctalia.log` (fallback `~/.cache/noctalia/noctalia.log`), previous file `noctalia.log.1` after 1 MiB. `NOCTALIA_LOG_LEVEL` (`debug`, `info`, `warn`, `error`) sets the initial console level; the file log is unfiltered.

## Investigation

1. Read the log; config diagnostics carry file, line and column.
2. Validate offline: `noctalia config validate` checks the merged config as the shell loads it; with a directory or file argument it checks only that path. Errors exit with status 1; warnings (unknown, obsolete or clamped values) exit 0. Run it only when the exact command is allowed; otherwise propose it.
3. A hand-written value has no effect: look for the same key in `settings.toml`, which wins. Ask the user for `noctalia msg config-get PATH` for the effective value.
4. Not starting: "error: noctalia is already running" means a single-instance lock is held; check the compositor config and other autostart entries for a second start.
5. Plugins: ask for `noctalia msg plugins list` (sources and enabled state).
6. Notifications: `[notification] enable_daemon = true` (default) makes Noctalia claim `org.freedesktop.Notifications`; `false` leaves the name to another daemon, internal toasts still work.
7. Lock screen: authenticates through PAM service `login`; check that `/etc/pam.d/login` exists and contains the methods expected.

## Interpretation

- A passing validator with warnings means the config loads; warnings are cleanup hints, not faults.
- Changes in Settings land in `settings.toml`, not in `~/.config/noctalia/`.
- v4 instructions (`qs -c ...`, JSON settings) do not apply to v5.

## Change and verification

Back up the file and change one key in a hand-written file, or prefer Settings or `noctalia msg config-set PATH VALUE`, which validate and write to `settings.toml`. Get consent for the write. Run `noctalia config validate` when allowed. Hot reload applies saved files; `noctalia msg config-reload` is a separate consented step. Clearing GUI overrides means moving `settings.toml` aside with a backup. Plugin enable/update and package updates change installed code and need consent. Do not end the compositor session; changing the autostart line takes effect only at the next session start. Verify with the log, the validator and the original symptom.

## Result

Name the Noctalia version, compositor, start method, the config file and layer that set the value, the validator result, the evidence and the write state. If the decisive check needs the session (`noctalia msg`), name the command the user should run.

## Sources

- [Noctalia README](https://github.com/noctalia-dev/noctalia) and docs at v5.4.1 ([docs/user](https://github.com/noctalia-dev/noctalia/tree/v5.4.1/docs/user)): [How configuration works](https://docs.noctalia.dev/noctalia/configuration/), [Running Noctalia](https://docs.noctalia.dev/noctalia/getting-started/running-the-shell/), [FAQ](https://docs.noctalia.dev/noctalia/getting-started/faq/), [IPC](https://docs.noctalia.dev/noctalia/ipc/), [Plugins](https://docs.noctalia.dev/noctalia/plugins/), [Notifications](https://docs.noctalia.dev/noctalia/services/notifications/)
- [Noctalia source `src/main.cpp`](https://github.com/noctalia-dev/noctalia/blob/v5.4.1/src/main.cpp)
- [Arch package noctalia: files](https://archlinux.org/packages/extra/x86_64/noctalia/files/)
- [CachyOS netinstall.yaml](https://github.com/CachyOS/cachyos-calamares/blob/cachyos/src/modules/netinstall/netinstall.yaml), PKGBUILDs [cachyos-niri-noctalia](https://github.com/CachyOS/CachyOS-PKGBUILDS/blob/master/cachyos-niri-noctalia/PKGBUILD), [cachyos-hypr-noctalia](https://github.com/CachyOS/CachyOS-PKGBUILDS/blob/master/cachyos-hypr-noctalia/PKGBUILD), [cachyos-mango-noctalia](https://github.com/CachyOS/CachyOS-PKGBUILDS/blob/master/cachyos-mango-noctalia/PKGBUILD)
