---
name: wayfire
description: Diagnoses the Wayfire Wayland compositor from its version, wayfire.ini and plugin list, and its log. Use for config errors, plugins that are enabled but do nothing, outputs, key bindings, window rules, autostart, a black screen without panel, or portals and screen sharing.
programs: wayfire
---

# Wayfire diagnostics

## Scope and access

Wayfire is a small wlroots-based core with most features in plugins. The panel and background come from `wf-shell` and the settings GUI from `wcm`; these are separate packages. The CachyOS installer group "Wayfire" installs `wayfire-desktop-git` (wayfire, wf-shell, wcm, waybar, mako and other tools) and `cachyos-wayfire-settings`, whose dotfiles live in `/etc/skel/.config/` (including `wayfire.ini`, `wf-shell.ini`, `waybar/`); skel is copied only when an account is created.

AI Desktop reads without the user's graphical session. Wayfire's IPC socket (`WAYFIRE_SOCKET`, provided by the `ipc` plugin, which is not in the default plugin list) and tools such as `wlr-randr` need that session; a failed connection is missing visibility, not a fault. Propose such reads to the user. Read configuration files only within the allowed file scope.

## Identify the version and session

1. Installed: `wayfire -v` (`--version`) prints the version.
2. Use the documentation of the installed version: `wayfire(1)` and the Wayfire wiki (Configuration, FAQ, Tips & Tricks).
3. Session start: a display manager using `/usr/share/wayland-sessions/wayfire.desktop` (the CachyOS group uses ly), or `wayfire` from a TTY. `-c FILE` selects a config file.

## Configuration and logs

- Lookup in 0.11 (source and `wayfire(1)`): `-c FILE`, else `WAYFIRE_CONFIG_FILE`, else `$XDG_CONFIG_HOME/wayfire/wayfire.ini` if it exists, else `$XDG_CONFIG_HOME/wayfire.ini` (`$XDG_CONFIG_HOME` defaults to `~/.config`). Wayfire logs `Using config file: PATH` and writes the path to `WAYFIRE_CONFIG_FILE`. A stray `~/.config/wayfire/wayfire.ini` shadows `~/.config/wayfire.ini`.
- Format: INI sections and `name = value`; `#` starts a comment and must be escaped as `\#` in a value. Options are named `section.option`, e.g. `core.plugins`.
- Plugins: `core.plugins` is a space-separated list; plugin options live in sections named after the plugin. The upstream example is `/usr/share/doc/wayfire/wayfire.ini`. Plugins are looked up in `/usr/lib/wayfire` and `${XDG_DATA_HOME}/wayfire/plugins` (`WAYFIRE_PLUGIN_PATH`); option metadata in `/usr/share/wayfire/metadata` (`WAYFIRE_PLUGIN_XML_PATH`).
- Reload: `workarounds.auto_reload_config` (default `true`) reloads the file shortly after it is written (`workarounds.config_reload_delay`, default 20 ms). Saving the file applies it.
- Autostart: `[autostart]` with `id = command` runs each command with `sh` at startup; `autostart_wf_shell` (default true) starts wf-panel and wf-background when they are not listed.
- Logs: Wayfire logs to stdout; the FAQ says logs usually end up in the system logs in a graphical session, or in the terminal when started from the command line. `-d` enables debug logging; `-d CATEGORY` adds categories such as `xwayland`, `wlroots`, `layer-shell`, `kbd`. A crash prints a backtrace to the log.
- Portals: `/usr/share/xdg-desktop-portal/wayfire-portals.conf` prefers `wlr`, then any backend.

## Investigation

1. Read the effective file: check which lookup path exists and whether `WAYFIRE_CONFIG_FILE` or `-c` is set in the session start.
2. There is no documented offline config validator. Check the INI structure by reading: every option must follow a section header.
3. Plugins: compare `core.plugins` with the plugins installed in `/usr/lib/wayfire`. The FAQ: a plugin that throws while loading is discarded but still shown as enabled in WCM; look for `Failed to load plugin` in the log. A newly installed or updated plugin usually needs a Wayfire restart.
4. Black screen with only a cursor: Wayfire draws no background or panel itself; check that `wf-shell` (or another panel) is installed and started from `[autostart]`.
5. Outputs: ask for `wlr-randr`, or read `new output: NAME` in the log, and compare with `[output:NAME]` sections (`mode = WIDTHxHEIGHT@RATE`, `position = x,y`, `scale`, `transform`; `mode = off` disables).
6. Input: `[input]` holds `xkb_layout`, `xkb_variant`, `xkb_options` and libinput options.
7. Window rules: `[window-rules]` lines `name = on <event> if <criteria> then <command>`. A rule without `if` also applies to panel and background windows.
8. Portals and slow GTK start: the wiki adds `dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY XAUTHORITY` to `[autostart]`; for screen sharing it also passes `WAYFIRE_SOCKET` and `XDG_CURRENT_DESKTOP=wayfire` and restarts the portals after `sleep 1`.

## Interpretation

- "Enabled" in WCM only reflects the file, not a loaded plugin; the log decides.
- With auto-reload, a half-edited file saved earlier can be the cause.
- Invisible cursor (e.g. nouveau) or flicker are known issues; the FAQ workarounds are `WLR_NO_HARDWARE_CURSORS=1` and `WLR_DRM_NO_MODIFIERS=1` set before Wayfire starts.
- Wayfire has a hard-coded Ctrl+Alt+Backspace that ends the session; warn the user before testing key combinations.

## Change and verification

Back up the file first and change one option. Get consent for the write. With `auto_reload_config` on, writing the file is already the apply step, so say so before consent. Plugin installs, environment variables and portal changes need a new Wayfire start, which ends the session: propose it as a separate consented step and never end the session as part of diagnostics. Verify with the log, the same user-run read and the original symptom. Undo restores only your change.

## Result

Give the Wayfire version, the config file actually loaded, the plugin list and any plugin load errors, the evidence, and whether the change was proposed or applied and verified. Name the missing read when the session, IPC or the log was unavailable.

## Sources

- [wayfire(1)](https://man.archlinux.org/man/wayfire.1.en)
- [Wayfire wiki: Configuration](https://github.com/WayfireWM/wayfire/wiki/Configuration), [FAQ](https://github.com/WayfireWM/wayfire/wiki/FAQ), [Tips & Tricks](https://github.com/WayfireWM/wayfire/wiki/Tips-&-Tricks), [Debugging problems](https://github.com/WayfireWM/wayfire/wiki/Debugging-problems)
- [Wayfire 0.11.0 source](https://github.com/WayfireWM/wayfire/tree/v0.11.0): `src/default-config-backend.cpp`, `metadata/workarounds.xml`, `wayfire.ini`, `wayfire-portals.conf`
- [ArchWiki: Wayfire](https://wiki.archlinux.org/title/Wayfire)
- [Arch package wayfire: files](https://archlinux.org/packages/extra/x86_64/wayfire/files/)
- [CachyOS netinstall.yaml](https://github.com/CachyOS/cachyos-calamares/blob/cachyos/src/modules/netinstall/netinstall.yaml), [cachyos-wayfire-settings PKGBUILD](https://github.com/CachyOS/CachyOS-PKGBUILDS/blob/master/cachyos-wayfire-settings/PKGBUILD)
