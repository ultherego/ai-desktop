---
name: lxqt
description: Diagnoses the LXQt desktop (lxqt-session, lxqt-panel, lxqt-globalkeys, the X11 window manager or Wayland compositor) on Arch Linux from its version and files in ~/.config/lxqt. Use for a missing panel or plugin, autostart entries, global shortcuts, the window manager or compositor choice, the Wayland session, or a safe configuration change.
programs: lxqt-session, lxqt-panel, startlxqt
---

# LXQt diagnostics

## Scope and access

Establish one symptom: the panel or a plugin, autostart or a module, shortcuts, the window manager or compositor, monitors, or desktop icons. AI Desktop reads run without the user's graphical session, session D-Bus bus, X display or Wayland socket, and only commands the owner allowed run. `lxqt-config`, `lxqt-config-session` and other dialogs cannot run from the sandbox; propose them for the user. A failed connection is missing visibility, not a fault. Reading allowed files is possible.

## Identify the version and session

1. If allowed, `pacman -Qi lxqt-session lxqt-panel lxqt-globalkeys` gives the installed versions; `lxqt-panel --version` prints the panel version.
2. X11: `/usr/share/xsessions/lxqt.desktop` runs `startlxqt`, which launches `lxqt-session`; from startx use `exec startlxqt` in `~/.xinitrc`.
3. Wayland: the optional `lxqt-wayland-session` package adds `/usr/share/wayland-sessions/lxqt-wayland.desktop` (LXQt (Wayland)) and `startlxqtwayland`. Supported compositors are labwc, Wayfire, kwin_wayland, Hyprland, niri, River and Sway.
4. To confirm what runs, propose `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`).

## Configuration and logs

- User configuration is in `~/.config/lxqt/`, initialized at first login; defaults for new users are in `/usr/share/lxqt/` (`session.conf`, `lxqt.conf`, `globalkeyshortcuts.conf`, `windowmanagers.conf`, `waylandwindowmanagers.conf`) and `/etc/xdg/lxqt/panel.conf`.
- `~/.config/lxqt/session.conf`: `[General]` holds `window_manager=` (X11, chosen in a dialog at first login) and, for Wayland, the compositor; `[Environment]` holds session variables.
- `~/.config/lxqt/panel.conf`: panels and plugins; under Wayland `[General]` may set `preferred_backend=` per desktop.
- `~/.config/lxqt/globalkeyshortcuts.conf`: shortcuts of `lxqt-globalkeysd` (X11 only).
- Autostart follows XDG Autostart in `~/.config/autostart/` and `/etc/xdg/autostart/`. Whether an entry is LXQt Autostart or Global Autostart depends on its `OnlyShowIn` key, not on its directory; entries with `X-LXQt-Module=true` are modules managed under Session Settings.
- Wayland compositor configurations created by LXQt are in `~/.config/lxqt/wayland/` (templates in `/usr/share/lxqt/wayland/`); labwc and kwin_wayland use their own default locations.
- Openbox, a common X11 choice, uses `~/.config/openbox/rc.xml`; desktop icons of PCManFM-Qt are in `~/.config/pcmanfm-qt/lxqt/desktop-items-0.conf`.
- Logs: LXQt writes no dedicated log file. If the journal is visible and allowed, read `journalctl -b` around the symptom; crashes recorded by systemd-coredump appear in `coredumpctl list lxqt-panel`.

## Investigation

1. Panel missing or a plugin absent: read `~/.config/lxqt/panel.conf` and compare it with `/etc/xdg/lxqt/panel.conf`. Plugin descriptions are in `/usr/share/lxqt/lxqt-panel/`; CPU and System Statistics plugins need `libstatgrab` and `libsysstat`.
2. Window manager missing or wrong (X11): read `window_manager=` in `session.conf` and check that the executable exists; known ones are listed in `/usr/share/lxqt/windowmanagers.conf`.
3. Shortcuts: on X11 compare `globalkeyshortcuts.conf` with the window manager's bindings; identical keys conflict, and some conflicts are reported at session start. On Wayland shortcuts are handled only by the compositor.
4. Autostart: list `~/.config/autostart/` and `/etc/xdg/autostart/`, including `Hidden=true` overrides and `OnlyShowIn` values.
5. Wayland limits: Shortcut Keys, power key settings and Monitor Settings (except under kwin_wayland) are disabled; the keyboard indicator and qeyes plugins are not supported; the workspace switcher depends on the compositor.
6. Desktop icons stuck together: the ArchWiki fix stops Desktop in Session Settings, removes `desktop-items-0.conf` and starts it again.

## Interpretation

- An empty `window_manager=` or a missing executable explains a session without window decorations.
- Mouse, keyboard and monitor settings under Wayland belong to the compositor, not to LXQt files.
- Cursor size differing between windows on X11 points to missing `xorg-xrdb`.
- Screen locking by default uses `xdg-screensaver`, which works only with XScreenSaver or xautolock in LXQt; another locker is set in Session Settings.

## Change and verification

Change only after the user's consent with the exact diff. Prefer the LXQt Settings dialogs (`lxqt-config-session`, the panel's right-click settings), run by the user in the session. For a manual edit, copy the file first (e.g. `cp ~/.config/lxqt/session.conf ~/.config/lxqt/session.conf.bak`) and change one key.

Applying a change to the running panel, desktop or window manager is a separate consented step (Session Settings can stop and start modules). Never log the user out or end the session. Verify by re-reading the file and reproducing the symptom.

## Result

Give the LXQt version, the session type and the window manager or compositor, the file and key with its evidence, the cause or hypothesis and one next step. State whether the change was proposed or made and verified, and name any value visible only inside the session.

## Sources

- [ArchWiki: LXQt](https://wiki.archlinux.org/title/LXQt) and [XDG Autostart](https://wiki.archlinux.org/title/XDG_Autostart)
- LXQt wiki: [Wayland Session](https://github.com/lxqt/wiki/blob/main/docs/lxqt.wiki/Wayland-Session.md), [Window managers (X11)](https://github.com/lxqt/wiki/blob/main/docs/lxqt.wiki/Window-managers-(X11).md), [Launching sessions](https://github.com/lxqt/wiki/blob/main/docs/lxqt.wiki/Launching-LXQt-sessions.md), [Miscellaneous](https://github.com/lxqt/wiki/blob/main/docs/lxqt.wiki/ConfigMiscellaneous.md)
- [lxqt-session(1)](https://man.archlinux.org/man/lxqt-session.1), [startlxqt(1)](https://man.archlinux.org/man/startlxqt.1), [lxqt-panel(1)](https://man.archlinux.org/man/lxqt-panel.1), [lxqt-wayland-session(1)](https://man.archlinux.org/man/lxqt-wayland-session.1)
- Package file lists: [lxqt-session](https://archlinux.org/packages/extra/x86_64/lxqt-session/files/), [lxqt-panel](https://archlinux.org/packages/extra/x86_64/lxqt-panel/files/), [lxqt-globalkeys](https://archlinux.org/packages/extra/x86_64/lxqt-globalkeys/files/), [lxqt-wayland-session](https://archlinux.org/packages/extra/any/lxqt-wayland-session/files/)
