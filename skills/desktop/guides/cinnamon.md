---
name: cinnamon
description: Diagnoses the Cinnamon desktop (cinnamon, cinnamon-session, Muffin) on Arch Linux. Use for crashes or fallback mode, broken applets, desklets or extensions (spices), settings in gsettings/dconf that do not apply, or settings daemon plugins overriding display and input configuration.
programs: cinnamon, cinnamon-session, cinnamon-settings
---

# Cinnamon diagnostics

## Scope and access

Establish one symptom: a crash or fallback mode, a misbehaving applet, desklet or extension, a setting that does not apply, or display/keyboard/mouse settings being overridden. AI Desktop reads run without the user's graphical session and D-Bus session bus, so `gsettings`, `dconf`, `cinnamon-dbus-command` and Looking Glass cannot reach the running Cinnamon. Propose them for the user to run; a failed connection means missing visibility, not a fault. Reading files within the allowed scopes is possible.

## Identify the version and session

- `cinnamon --version` prints the version; upstream asks for it in bug reports.
- The X11 session is `/usr/share/xsessions/cinnamon.desktop`; the package also ships `/usr/share/wayland-sessions/cinnamon-wayland.desktop`. Ask which one the user logs into; Wayland-specific bugs are tracked separately upstream.
- `cinnamon-session` starts the session; it comes from the separate `cinnamon-session` package, which also ships `cinnamon-session.target` as a systemd user unit.

## Configuration and logs

- Most settings are GSettings keys stored in dconf. The main schema `org.cinnamon` holds `enabled-applets`, `enabled-desklets`, `enabled-extensions` and `panels-enabled`; themes are under `org.cinnamon.theme`, window manager preferences under `org.cinnamon.desktop.wm.preferences`.
- Applets, desklets and extensions (spices) are installed per user in `~/.local/share/cinnamon/applets/`, `desklets/` and `extensions/`, system-wide in `/usr/share/cinnamon/`. Per-spice settings are JSON files in `~/.config/cinnamon/spices/UUID/`; upstream asks that they be changed through the settings interface, not edited by hand.
- Wallpapers added from a custom path are copied to `~/.cinnamon/backgrounds`.
- Sessions are defined in `cinnamon.session` files under `$XDG_CONFIG_HOME/cinnamon-session/sessions` and the XDG config and data dirs; saved applications go to `$XDG_CONFIG_HOME/cinnamon-session/saved-session`.
- Settings daemon plugins start from `.desktop` entries in `/etc/xdg/autostart/` (e.g. `cinnamon-settings-daemon-xrandr.desktop`); user overrides go to `~/.config/autostart`.
- Logs: Looking Glass (`cinnamon-looking-glass`) shows open windows, loaded extensions and a log, useful for crashes caused by incompatible spices. The upstream bug template points Linux Mint users to `~/.xsession-errors`; on Arch, check whether the file exists before relying on it.

## Investigation

1. Read the spice directories and `~/.config/cinnamon/spices/` to list user-installed spices and their settings; compare with the defaults in `/usr/share/cinnamon/`.
2. Propose to the user in the session: `gsettings get org.cinnamon enabled-applets` (and `enabled-extensions`, `enabled-desklets`), or `dconf dump /org/cinnamon/` to capture the whole tree.
3. For crashes and fallback mode, ask the user to open Looking Glass and read its log, or to share `~/.xsession-errors` if present. Starting a test session with `cinnamon-session --debug` shows which settings daemon plugins started.
4. For overridden xrandr or xinput settings, check `~/.config/autostart` for copies of the `cinnamon-settings-daemon-*.desktop` entries and their `Hidden=true` lines.
5. For `cinnamon-settings` failing with a missing Python module, check for stale `*.pyc` files under `/usr/lib/cinnamon-settings`.

## Interpretation

- Upstream's first suspect for odd behavior is a third-party spice. Disabling it and restarting Cinnamon is the standard test.
- Fallback mode means Cinnamon crashed; window control there needs metacity and gnome-panel.
- Cinnamon does not support a different window manager.
- On Intel GPUs, `xf86-video-intel` can cause random freezes; the modesetting driver is recommended instead.
- Video tearing fixes for GNOME also apply, because Muffin is based on Mutter.
- No password prompt for privileged applications: polkit requires the user to be in the `wheel` group.

## Change and verification

Change only after the user's consent. Before a dconf change, have the user save the subtree, e.g. `dconf dump /org/cinnamon/ > cinnamon-backup.dconf`; `dconf load /org/cinnamon/ < cinnamon-backup.dconf` restores it. Prefer one key changed with `gsettings set SCHEMA KEY VALUE`, or one spice disabled through `cinnamon-settings applets`, `extensions` or `desklets`. To stop a settings daemon plugin, copy its entry to `~/.config/autostart` and add `Hidden=true`; removing the copy undoes it.

Restarting Cinnamon to apply a change is a separate consented step: the user runs `cinnamon-dbus-command RestartCinnamon 1`, or types `r` in the Alt+F2 dialog. `cinnamon --replace` is a recovery step for an unresponsive shell, not routine diagnostics. Do not log the user out or end the session. Verify by re-reading the key or file and reproducing the original symptom.

## Result

Name the Cinnamon version, session type, the spice, key or file involved, the evidence and the write state. If the decisive log is only visible in Looking Glass or the session bus, name what the user should run.

## Sources

- [ArchWiki: Cinnamon](https://wiki.archlinux.org/title/Cinnamon)
- [cinnamon(1)](https://man.archlinux.org/man/cinnamon.1), [cinnamon-session(1)](https://man.archlinux.org/man/cinnamon-session.1), [cinnamon-settings(1)](https://man.archlinux.org/man/cinnamon-settings.1), [cinnamon-looking-glass(1)](https://man.archlinux.org/man/cinnamon-looking-glass.1)
- [Cinnamon bug report template](https://github.com/linuxmint/cinnamon/blob/master/.github/ISSUE_TEMPLATE/bug_report.yaml)
- [Cinnamon schema org.cinnamon](https://github.com/linuxmint/cinnamon/blob/master/data/org.cinnamon.gschema.xml), [cs_applets.py](https://github.com/linuxmint/cinnamon/blob/master/files/usr/share/cinnamon/cinnamon-settings/modules/cs_applets.py), [KeybindingTable.py](https://github.com/linuxmint/cinnamon/blob/master/files/usr/share/cinnamon/cinnamon-settings/bin/KeybindingTable.py)
- Package file lists: [cinnamon](https://archlinux.org/packages/extra/x86_64/cinnamon/files/), [cinnamon-session](https://archlinux.org/packages/extra/x86_64/cinnamon-session/files/)
