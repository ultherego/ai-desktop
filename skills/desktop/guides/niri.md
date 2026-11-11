---
name: niri
description: Diagnoses the niri Wayland compositor from its version, KDL configuration and journal. Use for config parse errors, outputs, key bindings, window rules, startup programs, includes, or portals and screencasting.
programs: niri, niri-session
---

# niri diagnostics

## Scope and access

Establish one symptom, its start and the latest change. AI Desktop reads without the user's graphical session, so `niri msg` usually cannot reach the running instance. A failed IPC connection is missing visibility, not a fault; propose the `niri msg` read to the user. Read configuration files only within the allowed file scope.

## Identify the version and session

1. Installed binary: `niri --version`. Running instance: ask the user for `niri msg version`. After an upgrade, `niri msg` can fail to parse replies from an older running compositor until niri is restarted.
2. Wiki pages mark options with "Since: VERSION"; check that an option exists in the installed version before proposing it.
3. Session type: a display manager or the `niri-session` script runs niri as the systemd user service `niri.service`, which starts the graphical session target needed by portals. `niri --session` is for systems without systemd/dinit. Programs wanted by the session can be linked into `~/.config/systemd/user/niri.service.wants/`.

## Configuration and logs

- Config: `$XDG_CONFIG_HOME/niri/config.kdl` or `~/.config/niri/config.kdl`, falling back to `/etc/niri/config.kdl`. If both are missing, niri creates the user file from the built-in default config. `--config`/`-c` or `$NIRI_CONFIG` select another file; `--config` wins, and a path that is not a real file means no config is loaded. An empty `$NIRI_CONFIG` is ignored.
- Since 26.04, `niri msg action load-config-file --path FILE` changes the config path at runtime; establish whether the session uses a non-default file.
- Syntax is KDL. `//` comments a line, `/-` comments out a whole section. Most sections cannot be repeated; `output "NAME"` and similar per-device sections are exceptions.
- Includes (since 25.11): `include "file.kdl"` only at the top level, relative to the including file or absolute; `~/` paths since 26.04. Includes are positional: they override options set before them, and window rules from an included file are inserted at the `include` line. All included files are watched for changes.
- Logs: when niri runs as a systemd service, its messages go to the user journal: `journalctl --user-unit=niri -b`, or `journalctl -ef /usr/bin/niri` to follow.
- Portals: niri requires running as a session for portals. Arch's niri package installs `/usr/share/xdg-desktop-portal/niri-portals.conf`; the wiki lists `xdg-desktop-portal-gtk` (fallback) and `xdg-desktop-portal-gnome` (screencasting). With that config, the file chooser needs `nautilus`, or set `org.freedesktop.impl.portal.FileChooser=gtk;` in a copy of the file.

## Investigation

1. Syntax: `niri validate` parses the config and prints errors without a running session; `niri validate -c FILE` checks a specific file. Run it only when the exact command is allowed. AI Desktop runs it only with `-c` and the file's path (e.g. `~/.config/niri/config.kdl`): the sandbox has no home directory, so it shows that file's directory read-only.
2. Outputs: ask for `niri msg outputs` for names and modes, and compare with `output "NAME"` sections. If the mode is omitted or does not work, niri picks one automatically. `niri msg output` changes are temporary and are not saved.
3. Binds: each bind is a hotkey with one action. `Mod` is `Super` on a TTY and `Alt` in a nested window. Symbols on a shifted key need the modifier spelled out (e.g. `Mod+Shift+Comma`). With several layouts, the bind matching the active layout's keysym fires.
4. Window rules: ask for `niri msg windows`, `niri msg focused-window` or `niri msg pick-window` to get the real `app-id` and title; `match` values are regular expressions and `exclude` removes matches.
5. Startup: `spawn-at-startup` takes the program path then arguments; `spawn-sh-at-startup` runs a shell command. A systemd session also supports XDG autostart. Variables from the `environment` section do not reach programs started by systemd; `niri-session` imports the login shell's environment before starting niri.
6. Logs: ask for the journal lines from the boot and time of the symptom.

## Interpretation

- The config is live-reloaded on save. An invalid config does not crash niri; the last working state stays and the user is notified. An error notification means the edit was rejected, not that the session is broken.
- Formatted `niri msg` output is not stable; use `--json` for exact values.
- A setting can be overridden by an include placed later in the file.

## Change and verification

Back up the file first. Propose the smallest KDL diff with options documented for the installed version and get consent for the write. Check it with `niri validate` (or `niri validate -c FILE` for a copy) before the user saves it in place, because saving triggers live reload. A runtime change such as `niri msg output` or `load-config-file` is a separate state change needing consent. Never quit niri or stop `niri.service` as part of diagnostics. Verify with the same `niri msg` read and the original symptom. Undo restores only your change.

## Result

Give the niri version (installed and running), the config file and includes involved, the wiki page used, the evidence, the validator result, and whether the change was proposed or applied and verified. Name any read that was unavailable.

## Sources

- [niri wiki: Configuration: Introduction](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration:-Introduction.md)
- [niri wiki: Configuration: Include](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration:-Include.md)
- [niri wiki: Configuration: Outputs](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration:-Outputs.md)
- [niri wiki: Configuration: Key Bindings](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration:-Key-Bindings.md)
- [niri wiki: Configuration: Window Rules](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration:-Window-Rules.md)
- [niri wiki: Configuration: Miscellaneous](https://github.com/niri-wm/niri/blob/main/docs/wiki/Configuration:-Miscellaneous.md)
- [niri wiki: Xwayland (journal)](https://github.com/niri-wm/niri/blob/main/docs/wiki/Xwayland.md)
- [niri wiki: IPC](https://github.com/niri-wm/niri/blob/main/docs/wiki/IPC.md)
- [niri wiki: Getting Started](https://github.com/niri-wm/niri/blob/main/docs/wiki/Getting-Started.md)
- [niri wiki: Example systemd Setup](https://github.com/niri-wm/niri/blob/main/docs/wiki/Example-systemd-Setup.md)
- [niri wiki: Important Software](https://github.com/niri-wm/niri/blob/main/docs/wiki/Important-Software.md)
- [niri wiki: Integrating niri](https://github.com/niri-wm/niri/blob/main/docs/wiki/Integrating-niri.md)
- [niri src/cli.rs](https://github.com/niri-wm/niri/blob/main/src/cli.rs)
- [ArchWiki: Niri](https://wiki.archlinux.org/title/Niri)
- [Arch package niri: files](https://archlinux.org/packages/extra/x86_64/niri/files/)
