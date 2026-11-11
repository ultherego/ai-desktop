---
name: sway
description: Diagnoses the sway Wayland compositor from its version, config files and man pages. Use for config errors, outputs, key bindings and layouts, for_window rules, exec autostart, missing drop-in includes, or portals and screen sharing.
programs: sway, swaymsg, swaynag
---

# sway diagnostics

## Scope and access

Establish one symptom, when it started and the latest change. AI Desktop reads without the user's graphical session, so `swaymsg` usually cannot find the IPC socket (`SWAYSOCK`, then `I3SOCK`). Messages such as "Unable to retrieve socket path" mean missing visibility, not a fault; propose the `swaymsg` read to the user to run in a terminal inside sway. Read configuration files only within the allowed file scope.

## Identify the version and session

1. Running instance: ask the user for `swaymsg -t get_version`. Offline: `sway -v` prints the installed version.
2. Use the man pages of the installed version: `sway(1)`, `sway(5)`, `sway-output(5)`, `sway-input(5)`, `sway-bar(5)`, `swaymsg(1)`.
3. Session start: `sway` on a TTY, a shell login snippet, or a display manager using `/usr/share/wayland-sessions/sway.desktop`. Sway needs seat access through systemd-logind with polkit, or seatd.

## Configuration and logs

- Search order: `~/.sway/config`, `$XDG_CONFIG_HOME/sway/config` (suggested; `$XDG_CONFIG_HOME` defaults to `~/.config`), `~/.i3/config`, `$XDG_CONFIG_HOME/i3/config`, `/etc/sway/config`, `/etc/i3/config`. The first found wins, so an old `~/.sway/config` or i3 file can shadow the expected one. `-c FILE` selects another file.
- The config is a list of sway commands run at startup; a line ending in `\` continues on the next line.
- `include PATHS`: a full path or one relative to the parent config, with shell expansion (wordexp(3)). The same file is included only once; later attempts are ignored.
- Drop-ins: the user config is expected to contain `include /etc/sway/config.d/*`. Arch's sway package ships `/etc/sway/config.d/50-systemd-user.conf`, which imports environment variables into the systemd user session and D-Bus; it is required by e.g. xdg-desktop-portal-wlr. A user config copied without this line is a common cause of portal failures.
- `swaymsg -t get_config` returns the loaded config but does not expand includes; read included files separately.
- Logs: the sway wiki captures the log from stderr (`sway -d 2> ~/sway.log`), so where an existing log ends up depends on how sway was started. If the session was started with `exec systemd-cat --identifier=sway sway`, read `journalctl --user --identifier sway`.
- Portals: Arch's package installs `/usr/share/xdg-desktop-portal/sway-portals.conf`. The sway wiki uses the `gtk` and `wlr` backends; a user override is `~/.config/xdg-desktop-portal/sway-portals.conf`.

## Investigation

1. Syntax: `sway -C` (`--validate`) checks the config and exits; add `-c FILE` to check a specific file. Run it only when the exact command is allowed.
2. Outputs: ask for `swaymsg -t get_outputs` for names, modes and scale, and compare with `output NAME mode|position|scale|enable|disable` lines; `*` matches any output.
3. Inputs and layouts: ask for `swaymsg -t get_inputs`. With several layouts, `bindsym` binds can break on the secondary layout; `bindsym --to-code` translates the keysyms into keycodes of the first configured layout.
4. Binds and modes: ask for `swaymsg -t get_binding_modes` and `swaymsg -t get_binding_state` to see whether a mode is stuck.
5. Window rules: ask for `swaymsg -t get_tree` to read the real `app_id` (Wayland) or `class` (Xwayland) and compare with `for_window [criteria]`; criteria values can be regular expressions.
6. Autostart: `exec` runs a shell command at startup; `exec_always` runs it again after `reload`. A program started twice after reload suggests `exec_always`.
7. Portals: check the drop-in include and that `WAYLAND_DISPLAY` and `XDG_CURRENT_DESKTOP` reach the D-Bus or systemd activation environment.

## Interpretation

- A passing `sway -C` confirms syntax, not that a bind, output or rule works.
- By default swaynag warns when a binding is overwritten (silenced per bind with `--no-warn`); `swaynag_command -` disables swaynag. A duplicate bind can therefore hide the expected one.
- Running `swaymsg` inside a terminal multiplexer started before sway can use an outdated `SWAYSOCK`; that is a visibility problem, not a sway fault.

## Change and verification

Back up the file first. Propose the smallest diff and get consent for the write. Check it with `sway -C -c FILE` when allowed. `reload` (e.g. `swaymsg reload`) applies changes and re-runs `exec_always`; it is a separate state change needing consent. Never exit sway as part of diagnostics. A debug log (`sway -d 2> ~/sway.log`) needs a new session start; propose it only as a separate, consented step. Verify with the same `swaymsg` read and the original symptom. Undo restores only your change.

## Result

Give the sway version (installed and running), the config file actually loaded and its includes, the man page used, the evidence, the validator result, and whether the change was proposed or applied and verified. Name the missing read when IPC or the log was unavailable.

## Sources

- [sway(1)](https://man.archlinux.org/man/sway.1.en)
- [sway(5)](https://man.archlinux.org/man/sway.5.en)
- [sway-output(5)](https://man.archlinux.org/man/sway-output.5.en)
- [swaymsg(1)](https://man.archlinux.org/man/swaymsg.1.en)
- [ArchWiki: Sway](https://wiki.archlinux.org/title/Sway)
- [sway wiki: Home (version, debug log)](https://github.com/swaywm/sway/wiki)
- [sway wiki: systemd integration](https://github.com/swaywm/sway/wiki/systemd-integration)
- [sway wiki: XDG Desktop Portal configuration](https://github.com/swaywm/sway/wiki/XDG-Desktop-Portal-configuration)
- [Arch package sway: files](https://archlinux.org/packages/extra/x86_64/sway/files/)
