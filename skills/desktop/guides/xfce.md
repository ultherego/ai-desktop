---
name: xfce
description: Diagnoses the Xfce 4 desktop (xfce4-session, Xfconf, xfwm4, xfce4-panel) on Arch Linux. Use for settings that do not apply, a broken or failsafe session, a missing panel or window manager, autostart entries, or the experimental Wayland session.
programs: xfce4-session, xfwm4, xfce4-panel
---

# Xfce diagnostics

## Scope and access

Establish one symptom: a setting that does not apply, a panel or plugin problem, missing window decorations, a session that fails to load, or an autostart entry. AI Desktop reads run without the user's graphical session and D-Bus session bus, so `xfconf-query`, `xfce4-settings-editor` and panel commands cannot reach the running `xfconfd`. Propose them for the user to run; a failed connection means missing visibility, not a fault. Reading files within the allowed scopes is possible.

## Identify the version and session

- `xfce4-session --version` prints the version of the session manager; versions of other components can differ, so name the one involved.
- The X11 session file is `/usr/share/xsessions/xfce.desktop`; the Wayland session is `/usr/share/wayland-sessions/xfce-wayland.desktop` (Xfce Session (Wayland), experimental, labwc as compositor). Ask which one the user logs into; an installed session file does not prove it is running.
- `startxfce4` starts the session from xinitrc; `xfce4-session` should not be called directly.

## Configuration and logs

- Xfconf channels are XML files in `~/.config/xfce4/xfconf/xfce-perchannel-xml/`; system defaults are under `/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/` (e.g. `xfce4-session.xml`). The panel default layout is `/etc/xdg/xfce4/panel/default.xml`.
- Xfconf files should be edited only when Xfconf is not running; manual edits do not take effect immediately. A running session may also overwrite them.
- Saved sessions live in `~/.cache/sessions/` (`$XDG_CACHE_HOME/sessions/`).
- Autostart follows the XDG Autostart specification (Session and Startup, Application Autostart tab).
- In the Wayland session labwc reads `~/.config/xfce4/labwc/`, not `~/.config/labwc/`.
- Session debugging: with `XFSM_VERBOSE` set, xfce4-session writes `~/.xfce4-session.verbose-log`; the previous run is kept as `~/.xfce4-session.verbose-log.last`.
- Panel debugging: `PANEL_DEBUG=1 xfce4-panel` prints messages to stderr; `PANEL_DEBUG=all` shows every domain.

## Investigation

1. Read the relevant channel XML (e.g. `xfwm4.xml`, `xfce4-panel.xml`, `xfce4-session.xml`, `xfce4-desktop.xml`, `xfce4-power-manager.xml`) and compare it with the system default.
2. For a live value, propose to the user: `xfconf-query -l` (channels), `xfconf-query -c CHANNEL -l -v` (properties with values), `xfconf-query -c CHANNEL -m` (watch changes while reproducing).
3. For a session that loads wrong programs or the wrong window manager, check whether a saved session in `~/.cache/sessions/` overrides the failsafe session in `xfce4-session.xml` (`/sessions/Failsafe/Client0_Command`, `Client1_Command`) and whether `/general/SaveOnExit` is on.
4. For ownership problems, check that the relevant folders in the home directory are owned by the user starting Xfce.
5. For the panel or a plugin, ask the user to collect `PANEL_DEBUG` output.

## Interpretation

- Settings that do not take effect: a separate Xsettings daemon can override them.
- Session failure symptoms: mouse cursor shown as an X, no window decorations, a grey desktop, `xfwm4-settings` reporting `These settings cannot work with your current window manager (unknown)`, `No window manager registered on screen 0`, or `Unable to load a failsafe session`. The last names possible causes: xfconfd not running (D-Bus setup problem), `$XDG_CONFIG_DIRS` not including `/etc`, or a broken xfce4-session installation. A corrupt saved session is another cause.
- A different window manager set in the failsafe session does not apply while a saved session exists; saved sessions must be cleared and saving disabled for the first logout.
- Missing trash in Thunar or xfdesktop: the optional dependency gvfs.
- Wayland: support is experimental; labwc works out of the box, Wayfire needs session file changes. Wallpaper settings do not apply under Wayland.

## Change and verification

Change only after the user's consent, with a copy of every file to be changed. Prefer the smallest change: one property, proposed as an `xfconf-query -c CHANNEL -p PROPERTY -s VALUE` command (`-n -t TYPE` to create, `-r` to reset) for the user to run in the session, rather than editing XML while Xfconf runs. Moving a whole directory away (`~/.config/xfce4/` to a backup, or deleting `~/.cache/sessions/`) resets more than the symptom and takes effect at the next login; propose it only as a last resort with the backup kept.

Applying a change to the running panel (`xfce4-panel -q`, then starting `xfce4-panel`) or window manager (`xfwm4 --replace`) is a separate consented step. Do not log the user out or end the session. Verify by re-reading the property or file and reproducing the original symptom.

## Result

Name the Xfce component and version, the session type, the channel, property or file, the evidence and the write state. If a decisive value is only visible inside the session, name the command the user should run.

## Sources

- [ArchWiki: Xfce](https://wiki.archlinux.org/title/Xfce)
- [Xfce docs: xfconf-query](https://docs.xfce.org/xfce/xfconf/xfconf-query)
- [Xfce docs: Xfconf](https://docs.xfce.org/xfce/xfconf/start)
- [Xfce docs: debugging the panel](https://docs.xfce.org/xfce/xfce4-panel/debugging)
- [xfce4-session(1)](https://man.archlinux.org/man/xfce4-session.1)
- Package file lists: [xfce4-session](https://archlinux.org/packages/extra/x86_64/xfce4-session/files/), [xfce4-panel](https://archlinux.org/packages/extra/x86_64/xfce4-panel/files/), [xfwm4](https://archlinux.org/packages/extra/x86_64/xfwm4/files/), [xfconf](https://archlinux.org/packages/extra/x86_64/xfconf/files/)
