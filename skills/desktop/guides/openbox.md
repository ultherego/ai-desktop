---
name: openbox
description: Diagnoses the Openbox X11 window manager, standalone or inside another desktop, on Arch Linux from its version and the rc.xml, menu.xml, autostart and environment files. Use for keybindings, autostart that does not run, menu errors, per-application rules, themes, or a safe configuration change.
programs: openbox, openbox-session, obxprop
---

# Openbox diagnostics

## Scope and access

Establish one symptom: a keybinding, autostart, the root menu, a per-application rule, the theme or decorations, or behavior after a configuration change. Ask whether Openbox runs standalone (`openbox-session`) or as the window manager of another desktop such as LXDE or LXQt. AI Desktop reads run without the user's X display and session D-Bus bus, and only commands the owner allowed run. `openbox --reconfigure`, `obxprop` and `obconf-qt` cannot reach the session from the sandbox; propose them for the user. A failed connection is missing visibility, not a fault. Reading allowed files is possible.

## Identify the version and session

1. If allowed, `pacman -Qi openbox` gives the installed version; `openbox --version` prints it.
2. Openbox is an X11 window manager. The session file is `/usr/share/xsessions/openbox.desktop`; read its `Exec=` line. With startx, `~/.xinitrc` runs `openbox` or `openbox-session`; only `openbox-session` provides autostart. To confirm what runs, propose `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`).
3. Which configuration file is used: LXDE starts Openbox through `openbox-lxde` with `--config-file ~/.config/openbox/lxde-rc.xml`; standalone Openbox and LXQt use `~/.config/openbox/rc.xml`. `openbox --config-file FILE` selects another file.

## Configuration and logs

- System defaults are in `/etc/xdg/openbox/` (`rc.xml`, `menu.xml`, `autostart`, `environment`); user files are in `~/.config/openbox/` and always override the global ones. A user profile is usually created by copying the global directory.
- `rc.xml`: sections such as `<focus>`, `<placement>`, `<theme>`, `<desktops>`, `<applications>`, `<keyboard>`, `<mouse>`, `<menu>`. Keybindings belong to `<keyboard>`; modifiers are `S`, `C`, `A`, `W`, `M`, `H`.
- `menu.xml`: the root (right-click) menu; it is static unless pipe menus or generators are used.
- Autostart: `openbox-session` sources `/etc/xdg/openbox/environment` and `~/.config/openbox/environment`, then runs `/etc/xdg/openbox/autostart` and `~/.config/openbox/autostart`. XDG Autostart entries are started by `/usr/lib/openbox/openbox-xdg-autostart`, which needs `python-pyxdg`. The man pages still name `autostart.sh`; the Arch package ships `autostart`.
- Themes are in `/usr/share/themes/*/openbox-3/` or a copy in the home directory; GUI tools are `obconf-qt` and `lxappearance-obconf`.
- Logs: Openbox writes no log file. `--debug`, `--debug-focus` and `--debug-session` display debugging output wherever the session sends program output (under LXDE `~/.cache/lxsession/LXDE/run.log`).

## Investigation

1. Read the active `rc.xml` and check that it is well-formed with `xmllint --noout ~/.config/openbox/rc.xml` (libxml2); do the same for `menu.xml`.
2. Keybinding does nothing: find the `<keybind key="...">` in `<keyboard>`, check the modifier codes and look for the same key bound twice; bindings outside `<keyboard>` are ignored.
3. Autostart does not run: confirm the session uses `openbox-session`; check that each long-running command in `autostart` ends with `&`; for XDG entries check that `python-pyxdg` is installed.
4. Commands start out of order or are skipped: ArchWiki suggests small delays, e.g. `(sleep 3s && program) &`.
5. Per-application rule does not match: propose `obxprop | grep "^_OB_APP"` in the session and click the window; compare `_OB_APP_NAME`, `_OB_APP_CLASS`, `_OB_APP_ROLE` and `_OB_APP_TYPE` with the `<application>` entry. Fixed per-monitor placement works only with x and y defined.
6. Programs needing authentication fail in a standalone session: a Polkit authentication agent must be started from autostart.

## Interpretation

- Openbox does not always apply edited files automatically; an unchanged behavior after an edit may only mean the configuration was not reloaded.
- Editing `rc.xml` has no effect under LXDE, which reads `lxde-rc.xml`.
- A blank grey screen after `openbox-session` is normal; a right click on the background opens the menu.
- Openbox provides no compositing effects such as transparency; they need a separate composite manager.
- A theme that cannot be selected needs an `openbox-3` directory with a `themerc` file, and readable permissions.

## Change and verification

Change only after the user's consent with the exact diff. Copy the file first (e.g. `cp ~/.config/openbox/rc.xml ~/.config/openbox/rc.xml.bak`) and change one element. If no user profile exists, copy the global files with `cp -a /etc/xdg/openbox ~/.config/` rather than editing `/etc/xdg/openbox/`. Validate with `xmllint --noout` before any reload.

Reloading is a separate consented step the user runs in the session: `openbox --reconfigure` rereads the configuration; `openbox --restart` and `openbox --replace` restart or replace the window manager and are larger steps. Never run `openbox --exit` or end the session. Autostart and environment changes apply at the next login. Verify by reproducing the symptom; undo restores the backup and reconfigures again.

## Result

Give the Openbox version, how it is started and which configuration file is active, the element or line with its evidence, the cause or hypothesis and one next step. State whether the change was proposed or made and verified, and name any value visible only inside the session.

## Sources

- [ArchWiki: Openbox](https://wiki.archlinux.org/title/Openbox) and [LXDE](https://wiki.archlinux.org/title/LXDE)
- Openbox docs: [Configuration](https://openbox.org/help/Configuration), [Autostart](https://openbox.org/help/Autostart), [Bindings](https://openbox.org/help/Bindings)
- [openbox(1)](https://man.archlinux.org/man/openbox.1), [openbox-session(1)](https://man.archlinux.org/man/openbox-session.1), [obxprop(1)](https://man.archlinux.org/man/obxprop.1), [openbox-lxde(1)](https://man.archlinux.org/man/openbox-lxde.1), [xmllint(1)](https://man.archlinux.org/man/xmllint.1)
- [LXQt wiki: Window managers (X11)](https://github.com/lxqt/wiki/blob/main/docs/lxqt.wiki/Window-managers-(X11).md); [lxde-common `openbox-lxde.in`](https://github.com/lxde/lxde-common)
- Package file lists: [openbox](https://archlinux.org/packages/extra/x86_64/openbox/files/), [obconf-qt](https://archlinux.org/packages/extra/x86_64/obconf-qt/files/), [libxml2](https://archlinux.org/packages/core/x86_64/libxml2/files/)
