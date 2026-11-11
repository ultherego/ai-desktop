---
name: plasma
description: Diagnoses KDE Plasma 6 on Arch Linux from its version, configuration files and user units. Use for a crashing or frozen plasmashell, missing panels or widgets, KWin problems, settings that do not apply, or a safe configuration change.
programs: plasmashell, startplasma-wayland
---

# KDE Plasma diagnostics

## Scope and access

Establish one symptom: the shell (panels, desktop, widgets), KWin (windows, effects, monitors), one application, or settings that do not apply. Ask what changed last: an upgrade, a new widget or theme, a monitor. Commands run by AI Desktop have no graphical session, session D-Bus bus or Wayland socket, and only commands the owner allowed run. A failed connection to `org.kde.plasmashell`, `org.kde.KWin` or the user manager is missing visibility, not a fault. `journalctl --user` may not be visible either.

## Identify the version and session

1. If allowed, `pacman -Qi plasma-workspace kwin` gives the installed versions. An installed package does not prove Plasma is the running session; ask or use session data.
2. The current Arch `plasma-workspace` ships only `startplasma-wayland` and conflicts with `plasma-x11-session`, so a current Plasma session is Wayland. To confirm, propose that the user runs `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`); `Type` is `wayland` or `x11`.
3. Plasma starts its services through the systemd user instance by default. If `~/.config/startkderc` sets `systemdBoot=false` in `[General]`, the boot scripts are used and the user units below do not apply.

## Configuration and logs

Most per-user settings are files in `~/.config/` (`XDG_CONFIG_HOME`); system-wide defaults can be in `/etc/xdg`. Read the files instead of calling session tools:

- `~/.config/plasma-org.kde.plasma.desktop-appletsrc`: panels, desktops and widgets, including each containment's `lastScreen`.
- `~/.config/plasmashellrc`: shell settings; its `[Updates]` group records which layout update scripts have run.
- `~/.config/kwinrc`: KWin options, e.g. `[ModifierOnlyShortcuts]`.
- `~/.config/kcminputrc` (cursor and input), `~/.config/plasma-localerc` (formats and language), `~/.config/ksmserverrc` (session restore).
- `~/.local/share/kscreen/`: saved monitor layouts, which can override other display settings.
- `~/.config/plasma-workspace/env/`: scripts run at login before Plasma starts, e.g. setting `KWIN_DRM_DEVICES`; `~/.config/plasma-workspace/shutdown/`: scripts run on exit.

Logs: the shell runs as the user unit `plasma-plasmashell.service` (package `plasma-workspace`) and the compositor as `plasma-kwin_wayland.service` (package `kwin`). If visible and allowed, read `journalctl --user -u plasma-plasmashell.service -b` and the same for KWin. Crashes captured by systemd-coredump are listed by `coredumpctl list plasmashell` or `coredumpctl list kwin_wayland`, if allowed.

## Investigation

1. Shell crash or freeze: read the plasmashell unit journal around the symptom time and the core dump list. Plasma problems are usually caused by unstable widgets (plasmoids) or Plasma themes; ask which was installed last and find it in `plasma-org.kde.plasma.desktop-appletsrc`.
2. Missing panels or widgets after a crash or monitor change: compare the `lastScreen` values in `plasma-org.kde.plasma.desktop-appletsrc` with the connected outputs.
3. KWin: for compositor state, propose that the user runs `qdbus6 org.kde.KWin /KWin org.kde.KWin.supportInformation` in the session; it prints options, the compositing backend and OpenGL capabilities. The debug console (`qdbus6 org.kde.KWin /KWin org.kde.KWin.showDebugConsole`) shows surfaces and input events. Neither works from the sandbox.
4. Theme, icons or colors do not change: ask the user to check `printenv QT_QPA_PLATFORMTHEME` in the session; a value such as qt5ct or qt6ct overrides Plasma settings.
5. Strange behavior after an upgrade: an old cache can cause hangs and broken menus. A `kf.service.sycoca` error about the menu spec points at files in `~/.config/menus/`.
6. Volume, media keys or notifications missing: hiding Audio Volume, Media Player or Notifications in the System Tray settings also disables those features.

## Interpretation

- A running `plasmashell` process does not prove the widgets work; an empty unit journal may only mean the user journal is not visible.
- `lastScreen` changes after a crash or hardware change explain lost panels without any damaged file.
- GTK theme files such as `~/.config/gtk-3.0/settings.ini` are overwritten by Plasma at every login while `kde-gtk-config` is installed; a manual edit there does not persist.
- Monitor problems can come from KWin, the kscreen layout or the driver; do not edit `kwinrc` for a layout stored in `~/.local/share/kscreen/`.

## Change and verification

Change only after the user's consent with the exact diff. Copy the file first (e.g. `cp ~/.config/kwinrc ~/.config/kwinrc.bak`) and change one key. `kwriteconfig6 --file kwinrc --group GROUP --key KEY VALUE` writes the same file; it is still a write. Prefer System Settings for options it exposes.

The ArchWiki's reset to defaults renames every `~/.config/plasma*` file to `.bak`; it loses all Plasma layout settings and is a last resort, not a first step. Cache rebuild (`kbuildsycoca6 --noincremental`, removing `~/.config/Trolltech.conf`) and emptying `~/.cache/` affect other applications; propose them separately.

Restarting the shell (`systemctl --user restart plasma-plasmashell.service`) is a separate consented state change the user runs in the session. Never restart KWin or end the session as part of diagnostics. Verify by reproducing the original symptom and re-reading the unit journal. Undo restores only the backed-up file.

## Result

Give the Plasma and KWin versions, the session type and how it was established, the file or unit with its evidence, the cause or hypothesis and one next step. State whether the change was only proposed or made and verified, and name any reading blocked by missing session visibility.

## Sources

- [ArchWiki: KDE](https://wiki.archlinux.org/title/KDE)
- [KDE Developer: Plasma scripting](https://develop.kde.org/docs/plasma/scripting/)
- [Arch package files: plasma-workspace](https://archlinux.org/packages/extra/x86_64/plasma-workspace/files/)
- [Arch package files: kwin](https://archlinux.org/packages/extra/x86_64/kwin/files/)
- [Arch package files: kconfig](https://archlinux.org/packages/extra/x86_64/kconfig/files/)
- [loginctl(1)](https://man.archlinux.org/man/loginctl.1) and [org.freedesktop.login1(5)](https://man.archlinux.org/man/org.freedesktop.login1.5)
- [journalctl(1)](https://man.archlinux.org/man/journalctl.1) and [coredumpctl(1)](https://man.archlinux.org/man/coredumpctl.1)
