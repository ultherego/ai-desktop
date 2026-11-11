---
name: mate
description: Diagnoses the MATE desktop (mate-session, mate-panel, Marco, mate-settings-daemon) on Arch Linux from its version, dconf paths and session files. Use for a missing or broken panel, Marco or compositing problems, keyboard shortcuts, startup applications, monitor layout, saved sessions, or a safe configuration change.
programs: mate-session, mate-panel
---

# MATE diagnostics

## Scope and access

Establish one symptom: the panel or an applet, window management (Marco, compositing, decorations), keyboard shortcuts, startup applications, monitors, or a session that loads the wrong programs. AI Desktop reads run without the user's graphical session, session D-Bus bus and X display, and only commands the owner allowed run. `gsettings`, `dconf read`, `dconf dump`, `mate-panel --replace` and `marco --replace` cannot reach the session from the sandbox; propose them for the user to run. A failed connection is missing visibility, not a fault. Reading allowed files is possible.

## Identify the version and session

1. If allowed, `pacman -Qi mate-session-manager mate-panel marco mate-settings-daemon` gives the installed versions; `mate-session --version` and `marco --version` print them too.
2. The Arch package ships only the X11 session `/usr/share/xsessions/mate.desktop`. From startx the session is started by `exec mate-session` in `~/.xinitrc`. To confirm what runs, propose `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`).
3. The base desktop is `marco`, `mate-panel` and `mate-session-manager`; the `mate` and `mate-extra` groups are installed separately.

## Configuration and logs

- Settings are GSettings in the binary dconf database `~/.config/dconf/user`; values can only be read through session tools. Schemas are in `/usr/share/glib-2.0/schemas/` (`org.mate.*.gschema.xml`).
- Session: schema `org.mate.session` at `/org/mate/desktop/session/` (`default-session`, `auto-save-session`); required components (window manager, panel, file manager) at `/org/mate/desktop/session/required-components/`.
- Panel: `/org/mate/panel/` (`general/`, `toplevels/`, `objects/`); default layouts are in `/usr/share/mate-panel/layouts/` (`default.layout`).
- Marco: `/org/mate/marco/` (`general`, `global-keybindings`, `window-keybindings`, `keybinding-commands`). Custom shortcuts handled by mate-settings-daemon are under `/org/mate/desktop/keybindings/`.
- Startup applications: `~/.config/autostart/`, `/usr/share/autostart`, `/usr/share/mate/autostart` and `/etc/xdg/autostart/` (`mate-settings-daemon.desktop`); edited with `mate-session-properties`.
- Saved session: `~/.config/mate-session/saved-session/`.
- Monitors: the saved layout is `~/.config/monitors.xml` (previous version `monitors.xml.backup`), applied by the mate-settings-daemon xrandr plugin.
- Logs: MATE has no dedicated log file. `mate-session --debug` enables debugging code; if the journal is visible and allowed, read `journalctl -b` around the symptom.

## Investigation

1. Panel missing or wrong: propose `dconf dump /org/mate/panel/` in the session and compare `toplevel-id-list` and `object-id-list` with the `toplevels/` and `objects/` entries. A crash recorded by systemd-coredump appears in `coredumpctl list mate-panel`, if allowed.
2. Wrong window manager or none: propose `gsettings get org.mate.session.required-components windowmanager`; `mate-wm` uses that value, or the `WINDOW_MANAGER` environment variable when set.
3. Programs that should not start: check `~/.config/mate-session/saved-session/` and `gsettings get org.mate.session auto-save-session`, then the autostart directories, including `Hidden=true` overrides.
4. Shortcuts: propose `dconf dump /org/mate/marco/global-keybindings/` and `dconf dump /org/mate/desktop/keybindings/`; two actions bound to the same keys conflict.
5. Compositing or tearing: propose `gsettings get org.mate.Marco.general compositing-manager`. Marco's tear-free compositing needs DRI3; without it (e.g. the NVIDIA proprietary driver) vertical sync is not available.
6. Monitors reset at login: read `~/.config/monitors.xml` and compare with the connected outputs.

## Interpretation

- A saved session or a startup entry with `wm-name --replace` can replace Marco regardless of the required-components value.
- Missing panel shadow after login is a known race; the ArchWiki workaround delays Marco through a copy of `marco.desktop` in `~/.local/share/applications/`.
- An extra decoration on client-side decorated windows when unmaximized is caused by `mate-netbook`.
- GTK 2 applications ignoring MATE themes need `gtk-engines` and `gtk-engine-murrine`.

## Change and verification

Change only after the user's consent with the exact command. Back up the affected path first (`dconf dump /org/mate/panel/ > ~/mate-panel.dconf` run by the user, restorable with `dconf load /org/mate/panel/ < ~/mate-panel.dconf`) or record the old value with `gsettings get`. Change one key with `gsettings set SCHEMA KEY VALUE` or `mate-control-center`, not by editing the binary database. For `monitors.xml`, copy the file before changing it.

Applying a change to the running panel (`mate-panel --replace`) or window manager (`marco --replace`) is a separate consented step the user runs in the session. `mate-panel --reset` restores the default layout and loses customizations; propose it only as a last resort after a backup. Never end the session. Verify by re-reading the key and reproducing the symptom.

## Result

Give the MATE component and version, the session type, the dconf path, key or file with its evidence, the cause or hypothesis and one next step. State whether the change was proposed or made and verified, and name any value visible only inside the session.

## Sources

- [ArchWiki: MATE](https://wiki.archlinux.org/title/MATE) and [XDG Autostart](https://wiki.archlinux.org/title/XDG_Autostart)
- [mate-session(1)](https://man.archlinux.org/man/mate-session.1), [mate-panel(1)](https://man.archlinux.org/man/mate-panel.1), [mate-wm(1)](https://man.archlinux.org/man/mate-wm.1), [marco(1)](https://man.archlinux.org/man/marco.1), [dconf(1)](https://man.archlinux.org/man/dconf.1), [dconf(7)](https://man.archlinux.org/man/dconf.7)
- Upstream schemas and sources: [mate-session-manager](https://github.com/mate-desktop/mate-session-manager), [mate-panel](https://github.com/mate-desktop/mate-panel), [marco](https://github.com/mate-desktop/marco), [mate-settings-daemon](https://github.com/mate-desktop/mate-settings-daemon), [mate-desktop](https://github.com/mate-desktop/mate-desktop) (`mate-rr-config.c`)
- Package file lists: [mate-session-manager](https://archlinux.org/packages/extra/x86_64/mate-session-manager/files/), [mate-panel](https://archlinux.org/packages/extra/x86_64/mate-panel/files/), [marco](https://archlinux.org/packages/extra/x86_64/marco/files/), [mate-settings-daemon](https://archlinux.org/packages/extra/x86_64/mate-settings-daemon/files/)
