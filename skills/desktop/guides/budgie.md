---
name: budgie
description: Diagnoses the Budgie 10.10 desktop (budgie-panel, Raven, budgie-session, labwc) on Arch Linux from its version, GSettings schemas and labwc files. Use for a missing or crashing panel, Raven or applet problems, keyboard shortcuts that do not work, autostart entries, display settings that are not restored, or a safe configuration change.
programs: budgie-panel, budgie-desktop, startbudgielabwc
---

# Budgie diagnostics

## Scope and access

Establish one symptom: the panel or an applet, Raven (the sidebar), shortcuts, autostart, display settings, or the lock screen. AI Desktop reads run without the user's graphical session, session D-Bus bus and Wayland socket, and only commands the owner allowed run. `gsettings`, `dconf read`, `dconf dump`, `budgie-desktop-settings` and `labwc -r` cannot reach the session from the sandbox; propose them for the user to run. A failed connection is missing visibility, not a fault. Reading allowed files is possible.

## Identify the version and session

1. If allowed, `pacman -Qi budgie-desktop budgie-session budgie-control-center` gives the installed versions. `budgie-desktop --version` prints the version.
2. Budgie 10.10 runs only on Wayland; Xorg support was dropped. The session file is `/usr/share/wayland-sessions/budgie-desktop.desktop` (Budgie Desktop); from a text console the session starts with `startbudgielabwc`. To confirm the running session, propose `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`).
3. `startbudgielabwc` runs labwc as the compositor with its own configuration directory and `budgie-desktop` as the session command. Budgie does not support another window manager.

## Configuration and logs

- Settings are GSettings in the binary dconf database `~/.config/dconf/user`; read values through session tools. Panel layout lives under `/com/solus-project/budgie-panel/` (schema `com.solus-project.budgie-panel`); window manager options use `com.solus-project.budgie-wm`. Schemas and the Arch defaults override (`30_org.archlinux.budgie-desktop.gschema.override`) are in `/usr/share/glib-2.0/schemas/`.
- labwc reads `~/.config/budgie-desktop/labwc/` (`rc.xml`, `menu.xml`), not `~/.config/labwc/`. Missing files are copied there from `/usr/share/budgie-desktop/labwc/` at session start.
- The labwc bridge (`/usr/lib/budgie-desktop/labwc_bridge.py`, autostarted by `org.buddiesofbudgie.labwc-bridge.desktop`) writes keyboard, input, theme and shortcut values from GSettings into that `rc.xml` and runs `labwc -r`. When it migrates an old `rc.xml` it saves `rc.xml.backup` first. It logs to the systemd journal.
- Autostart follows XDG Autostart: `~/.config/autostart/` and `/etc/xdg/autostart/` (Budgie ships its `org.buddiesofbudgie.*` entries there). The session definition is `/usr/share/gnome-session/sessions/org.buddiesofbudgie.BudgieDesktop.session`.
- Display configuration across sessions is kept by budgie-desktop-services (`org.buddiesofbudgie.Services`); the screen locker is a separate program such as gtklock or swaylock.

## Investigation

1. Panel missing, empty or crashing: propose `dconf dump /com/solus-project/budgie-panel/` in the session to see panels and applets. If the journal is visible and allowed, read `journalctl -b` around the symptom; a crash recorded by systemd-coredump appears in `coredumpctl list budgie-panel`.
2. Applets: bundled plugins are in `/usr/lib/budgie-desktop/plugins/` and Raven widgets in `/usr/lib/budgie-desktop/raven-plugins/`; check that the applet named in the panel settings has a directory there.
3. Shortcuts: compare Budgie Control Center (Keyboard, View and Customize Shortcuts) with the `<keyboard>` section of `~/.config/budgie-desktop/labwc/rc.xml`; the bridge regenerates that section from GSettings.
4. Autostart: list entries in `~/.config/autostart/` and `/etc/xdg/autostart/`, including `Hidden=true` copies that disable a system entry.
5. Display not restored after suspend or login: upstream directs this to budgie-desktop-services; note the outputs and the moment it happens.
6. Crash reports: upstream runs `budgie-panel --replace` under gdb and collects `bt full`; this replaces the running panel, so it is a consented step for the user.

## Interpretation

- A manual edit of shortcuts or input options in `rc.xml` can be overwritten by the bridge; change the GSettings value instead.
- `~/.config/labwc/` is ignored by Budgie; a fix placed there has no effect.
- `budgie-extras` changes existing functionality and can cause problems; ask whether it is installed.

## Change and verification

Change only after the user's consent with the exact command or diff. Prefer the settings applications or one `gsettings set SCHEMA KEY VALUE` command for the user to run in the session; keep the old value (`gsettings get`) or a `dconf dump` of the path as the backup. For a labwc option the bridge does not manage, copy `rc.xml` first and change one element; `xmllint --noout ~/.config/budgie-desktop/labwc/rc.xml` (libxml2) checks that the file is well-formed.

Reloading labwc (`labwc -r`) or replacing the panel (`budgie-panel --replace`) is a separate consented step the user runs in the session. `budgie-panel --reset` and `--reset-raven` discard the layout; propose them only as a last resort after a `dconf dump` backup. Never end the session. Verify by re-reading the value or file and reproducing the symptom.

## Result

Give the Budgie version, the session type, the schema, key or file with its evidence, the cause or hypothesis and one next step. State whether the change was proposed or made and verified, and name any value visible only inside the session.

## Sources

- [ArchWiki: Budgie](https://wiki.archlinux.org/title/Budgie) and [XDG Autostart](https://wiki.archlinux.org/title/XDG_Autostart)
- Budgie docs: [shortcuts](https://docs.buddiesofbudgie.org/10.10/user/keyboard-shortcuts), [issues](https://docs.buddiesofbudgie.org/10.10/user/reporting-issues), [backtraces](https://docs.buddiesofbudgie.org/10.10/developer/workflow/providing-backtraces)
- [budgie-desktop source v10.10.2](https://github.com/BuddiesOfBudgie/budgie-desktop/tree/v10.10.2): `startbudgielabwc.in`, `labwc_bridge.py`, panel schema
- [budgie-panel(1)](https://man.archlinux.org/man/budgie-panel.1), [budgie-session(1)](https://man.archlinux.org/man/budgie-session.1), [dconf(1)](https://man.archlinux.org/man/dconf.1), [dconf(7)](https://man.archlinux.org/man/dconf.7), [labwc(1)](https://man.archlinux.org/man/labwc.1)
- Package file lists: [budgie-desktop](https://archlinux.org/packages/extra/x86_64/budgie-desktop/files/), [budgie-session](https://archlinux.org/packages/extra/x86_64/budgie-session/files/), [budgie-control-center](https://archlinux.org/packages/extra/x86_64/budgie-control-center/files/), [budgie-desktop-services](https://archlinux.org/packages/extra/x86_64/budgie-desktop-services/files/), [libxml2](https://archlinux.org/packages/core/x86_64/libxml2/files/)
