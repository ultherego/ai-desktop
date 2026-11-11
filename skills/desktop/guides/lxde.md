---
name: lxde
description: Diagnoses the LXDE X11 desktop (startlxde, LXSession, LXPanel, Openbox through openbox-lxde, PCManFM desktop) on Arch Linux from its version, profile files and run log. Use for a missing or crashing panel, autostart entries, keyboard shortcuts, the window manager choice, screen tearing or locking, or a safe configuration change.
programs: startlxde, lxsession, lxpanel
---

# LXDE diagnostics

## Scope and access

Establish one symptom: the panel or a plugin, autostart, keyboard shortcuts, the window manager, desktop icons or wallpaper, monitors, or screen locking. AI Desktop reads run without the user's graphical session, session D-Bus bus and X display, and only commands the owner allowed run. `lxpanelctl`, `openbox --reconfigure`, `lxsession -r` and the settings dialogs cannot reach the session from the sandbox; propose them for the user to run. A failed connection is missing visibility, not a fault. Reading allowed files is possible.

## Identify the version and session

1. If allowed, `pacman -Qi lxde-common lxsession lxpanel openbox pcmanfm` gives the installed versions; `lxpanel --version` prints the panel version.
2. On Arch the packages with plain names (`lxpanel`, `lxsession`, `pcmanfm`) are GTK 3 builds; they replace the former `-gtk3` variants. The `lxde` group contains the full desktop; the minimum is `lxde-common`, `lxsession` and Openbox or another window manager.
3. LXDE is X11 only. The session file is `/usr/share/xsessions/LXDE.desktop`; from startx use `exec startlxde` in `~/.xinitrc`. LXDM is the default display manager of the group. To confirm what runs, propose `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`).
4. `startlxde` sets `XDG_CURRENT_DESKTOP=LXDE` and `XDG_MENU_PREFIX=lxde-`, copies `/etc/xdg/openbox/LXDE/rc.xml` to `~/.config/openbox/lxde-rc.xml` if that file is missing, starts D-Bus if needed and runs `lxsession -s LXDE -e LXDE`.

## Configuration and logs

- Session: `~/.config/lxsession/LXDE/desktop.conf`, or `/etc/xdg/lxsession/LXDE/desktop.conf` when the user file does not exist; `[Session]` `window_manager=openbox-lxde` selects the window manager.
- Autostart: `~/.config/lxsession/LXDE/autostart`, one command per line without a trailing `&`; a leading `@` restarts the command if it crashes. When the user file exists, the global `/etc/xdg/lxsession/LXDE/autostart` (lxpanel, pcmanfm desktop, xscreensaver) is not executed. XDG Autostart entries are also started.
- Panel: profile `LXDE` in `~/.config/lxpanel/LXDE/` (`config`, `panels/`), defaults in `/etc/xdg/lxpanel/LXDE/`; an optional `gtkrc` there styles the panel.
- Window manager: `openbox-lxde` runs `openbox --config-file ~/.config/openbox/lxde-rc.xml`, so plain Openbox and LXDE use different `rc.xml` files and shortcuts. System defaults, including `menu.xml`, are in `/etc/xdg/openbox/LXDE/`.
- Desktop and wallpaper: `pcmanfm --desktop --profile LXDE`; defaults in `/etc/xdg/pcmanfm/LXDE/pcmanfm.conf`.
- Appearance: LXAppearance writes `~/.gtkrc-2.0`, `~/.config/gtk-3.0/settings.ini` and `~/.icons/default/index.theme`.
- Log: LXSession redirects its own output and that of the programs it starts to `~/.cache/lxsession/LXDE/run.log`; the file is truncated at each login, so read it before the user logs in again.

## Investigation

1. Read `run.log` for errors from lxpanel, openbox or autostart commands.
2. Panel missing: check whether the user autostart file exists without an `lxpanel --profile LXDE` line, then compare `~/.config/lxpanel/LXDE/panels/` with the defaults. To restart the panel, propose `lxpanelctl restart` to the user.
3. Shortcuts: read the `<keyboard>` section of `~/.config/openbox/lxde-rc.xml`, not `rc.xml`; check it is well-formed with `xmllint --noout` (libxml2).
4. Wrong window manager: read `window_manager=` in the user or global `desktop.conf` and look for a `WM --replace` line in autostart.
5. Desktop icons, wallpaper or screensaver missing: the same autostart rule applies to `pcmanfm --desktop` and `xscreensaver`.
6. Menu entries: `.desktop` files in `/usr/share/applications/` and `~/.local/share/applications/`; the layout is `/etc/xdg/menus/lxde-applications.menu`.

## Interpretation

- Creating a user autostart file silently drops the global entries; this often explains a missing panel or desktop after an autostart change.
- `lxpanel: cairo-scaled-font.c ... Assertion` with some GTK themes is fixed on ArchWiki by installing `ttf-dejavu`; crashes on some Unicode pages by `ttf-droid`.
- LXDE enables no compositing; tearing is remedied with a composite manager such as picom, started from autostart.
- LXDE has no screen locker of its own; `lxlock` uses the first known locker it finds.
- `startlxde` sets `SAL_USE_VCLPLUGIN=gtk`, which overrides LibreOffice theming.

## Change and verification

Change only after the user's consent with the exact diff. Copy every file first (e.g. `cp ~/.config/openbox/lxde-rc.xml ~/.config/openbox/lxde-rc.xml.bak`) and change one line or element. When creating a user autostart file, copy the global file as its starting point so its entries are kept. Validate edited XML with `xmllint --noout`.

Applying the change is a separate consented step the user runs in the session: `openbox --reconfigure` for `lxde-rc.xml`, `lxpanelctl restart` for the panel, `lxsession -r` to reload Xsettings; autostart and `desktop.conf` changes apply at the next login. Never log the user out or end the session. Verify by re-reading the file, reproducing the symptom and reading `run.log` after the next login.

## Result

Give the LXDE component versions, the session type, the profile file and line with its evidence, the cause or hypothesis and one next step. State whether the change was proposed or made and verified, and name any value visible only inside the session.

## Sources

- [ArchWiki: LXDE](https://wiki.archlinux.org/title/LXDE) and [Openbox](https://wiki.archlinux.org/title/Openbox)
- [lxde-common source](https://github.com/lxde/lxde-common) (`startlxde.in`, `openbox-lxde.in`, `autostart`, `desktop.conf`); [lxsession 0.5.7 `lxsession/main.vala`](https://github.com/lxde/lxsession/blob/0.5.7/lxsession/main.vala)
- [startlxde(1)](https://man.archlinux.org/man/startlxde.1), [lxsession(1)](https://man.archlinux.org/man/lxsession.1), [lxpanel(1)](https://man.archlinux.org/man/lxpanel.1), [lxpanelctl(1)](https://man.archlinux.org/man/lxpanelctl.1), [openbox(1)](https://man.archlinux.org/man/openbox.1), [xmllint(1)](https://man.archlinux.org/man/xmllint.1)
- Package file lists: [lxde-common](https://archlinux.org/packages/extra/any/lxde-common/files/), [lxsession](https://archlinux.org/packages/extra/x86_64/lxsession/files/), [lxpanel](https://archlinux.org/packages/extra/x86_64/lxpanel/files/), [openbox](https://archlinux.org/packages/extra/x86_64/openbox/files/)
