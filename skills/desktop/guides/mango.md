---
name: mango
description: Diagnoses MangoWM (mango, formerly mangowc), the wlroots-based Wayland compositor offered by the CachyOS installer, from its version, config files and offline config check. Use for config errors, source includes, key bindings and layouts, monitor rules, window rules, autostart, portals and screen sharing.
programs: mango, mmsg, mangonag
---

# MangoWM diagnostics

## Scope and access

The Arch package is `mangowm` (upstream `mangowm/mango`, formerly `mangowc`); it installs `mango`, the IPC client `mmsg` and the error bar `mangonag`. The CachyOS installer group "MangoWM" installs `cachyos-mangowc-dms` (mangowm plus DMS); `cachyos-mango-noctalia` pairs it with Noctalia. The shell (bar, launcher, notifications) is a separate program: use the DMS or Noctalia guide for it.

AI Desktop reads without the user's graphical session. `mmsg` connects to the socket in `MANGO_INSTANCE_SIGNATURE`, which is set only inside the session; a failed connection is missing visibility, not a fault. Propose `mmsg` reads to the user. Read configuration files only within the allowed file scope.

## Identify the version and session

1. Installed: `mango -v` prints `mango VERSION`. Running: ask the user for `mmsg get version`.
2. Use the docs of the installed version and `mmsg(1)`, `mangonag(1)`.
3. Session start: a display manager using `/usr/share/wayland-sessions/mango.desktop` (`Exec=mango`, the CachyOS group uses SDDM), or `mango` from a TTY. With a D-Bus session bus, mango imports its environment into systemd and D-Bus and starts the user unit `mango-session.target`, which binds to `graphical-session.target`. It sets `XDG_CURRENT_DESKTOP=mango`.

## Configuration and logs

- Lookup: `-c FILE`, else `~/.config/mango/config.conf`, then `~/.config/mango/config.toml`, then `/etc/mango/config.conf` and `/etc/mango/config.toml` (the shipped fallback and reference). The path is built from `HOME`, not `XDG_CONFIG_HOME`.
- Format: `key=value` lines; `.toml` files use the same option names in TOML. The reader is chosen by file extension, so conf and TOML can be mixed.
- Includes: `source=FILE` (absolute, `~` or relative such as `./cfg/keybinds.conf`); `source_optional=FILE` is ignored when missing. CachyOS dotfiles put `config.conf` and `cfg/*.conf` in `/etc/skel/.config/mango/`; skel is copied only when an account is created, so existing users keep their own files.
- `env=NAME,value` sets environment variables and is reset on every reload.
- Autostart: `exec_once` runs once at start; `exec` runs again on every reload.
- Reload: `auto_reload_config` (default `1`) watches the main file and every sourced file and reloads after any change. Saving a file applies it.
- Logs: mango writes to stderr (tags `[ERROR]`, `[WARN]`, `[DEBUG]`); `-d` enables debug logging. Where an existing log ends up depends on how the session was started.
- Portals: `/usr/share/xdg-desktop-portal/mango-portals.conf` (gtk by default, wlr for ScreenCast and Screenshot); a user override is `~/.config/xdg-desktop-portal/mango-portals.conf`.

## Investigation

1. Syntax: `mango -c FILE -p` checks the config file and exits with success or failure without starting the compositor. Run it only when the exact command is allowed; otherwise propose it.
2. Effective file: check which of the lookup paths exists, then follow every `source` line; a missing non-optional source is an error, a missing `source_optional` is silent.
3. Outputs: ask for `mmsg get all-monitors` or `wlr-randr`, then compare names, make, model and serial with `monitor_rule=name:...` lines. When several match fields are set, all must match.
4. Bindings: `bind` converts a key name to a keycode using the layouts in `xkb_rules_layout`. On a key that does not trigger, the upstream FAQ suggests `code:N` (keycode from `wev`) or `binds` to match the keysym. Ask for `mmsg get keymode` when a key mode seems stuck.
5. Window rules: ask for `mmsg get all-clients` or `mmsg get focusing-client` to read the real `app_id` and `title`, and compare with `window_rule=...,app_id:...`; with both `app_id` and `title`, both must match. Values support regex.
6. Autostart: a program started twice after reload suggests `exec` instead of `exec_once`, or a second start method (for example the shell started both by mango and by systemd).
7. Screen sharing: needs `xdg-desktop-portal-wlr`, which has no chooser of its own and tries slurp, wmenu, wofi, rofi, bemenu, mew and fuzzel; with none installed the picker never appears. Upstream says mango now updates the activation environment itself; an old `dbus-update-activation-environment ... XDG_CURRENT_DESKTOP=wlroots` line should be removed.

## Interpretation

- A passing `mango -p` confirms that the file parses, not that a bind, output or rule behaves as intended.
- Because of auto-reload, the symptom may come from a half-edited file saved earlier, not from the current one.
- Config errors appear in a `mangonag` bar at the top of the screen; ask the user for its message.

## Change and verification

Back up the file first and change only one setting. Get consent for the write. Check with `mango -c FILE -p` when allowed. With `auto_reload_config=1`, writing the file is already the apply step, so say that before consent; otherwise `reload_config` (a binding, or `mmsg dispatch reload_config`) is a separate consented step. `exec` lines run again on reload. Never `quit` mango as part of diagnostics. Portal changes need a portal restart or a new login, proposed separately. Verify with the same `mmsg` read and the original symptom. Undo restores only your change.

## Result

Give the mango version (installed and running), the config file actually loaded and its sources, the documentation used, the evidence, the `-p` result, and whether the change was proposed or applied and verified. Name the missing read when IPC or the log was unavailable.

## Sources

- [mangowm docs: Basics](https://mangowm.github.io/docs/configuration/basics), [Monitors](https://mangowm.github.io/docs/configuration/monitors), [XDG portals](https://mangowm.github.io/docs/configuration/xdg-portals), [Keys](https://mangowm.github.io/docs/bindings/keys), [Window rules](https://mangowm.github.io/docs/window-management/rules), [IPC](https://mangowm.github.io/docs/ipc), [FAQ](https://mangowm.github.io/docs/faq)
- [mango source 0.18.5](https://github.com/mangowm/mango/tree/0.18.5) (`src/main.c` options, `src/config/load.c` lookup, `assets/`)
- [mmsg(1)](https://man.archlinux.org/man/mmsg.1.en)
- [Arch package mangowm: files](https://archlinux.org/packages/extra/x86_64/mangowm/files/), [PKGBUILD](https://gitlab.archlinux.org/archlinux/packaging/packages/mangowm/-/blob/main/PKGBUILD)
- [CachyOS netinstall.yaml](https://github.com/CachyOS/cachyos-calamares/blob/cachyos/src/modules/netinstall/netinstall.yaml), [cachyos-mangowc-dms PKGBUILD](https://github.com/CachyOS/CachyOS-PKGBUILDS/blob/master/cachyos-mangowc-dms/PKGBUILD)
