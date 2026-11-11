---
name: gnome
description: Diagnoses the GNOME desktop on Arch Linux from its version, dconf settings, extensions and user units. Use for a crashing or frozen GNOME Shell, extensions that broke after an upgrade, settings that do not apply, or a safe dconf or extension change.
programs: gnome-control-center
---

# GNOME diagnostics

## Scope and access

Establish one symptom: GNOME Shell (top bar, overview, extensions), a setting that does not apply, monitors, or one application. Ask what changed last: a GNOME upgrade, a new extension or theme. Commands run by AI Desktop have no graphical session, session D-Bus bus or Wayland socket, and only commands the owner allowed run. `gsettings get`, `gnome-extensions list` and similar session tools are to be proposed to the user; a failed session connection is missing visibility, not a fault. `journalctl --user` may not be visible either.

## Identify the version and session

1. If allowed, `pacman -Qi gnome-shell mutter gnome-session` gives the installed versions. An installed package does not prove GNOME is the running session.
2. The current Arch `gnome-session` ships only `/usr/share/wayland-sessions/gnome.desktop`, so the GNOME and GNOME Classic sessions run on Wayland; X applications run through Xwayland. To confirm, propose `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`).
3. The session type comes from systemd at session start; setting `XDG_SESSION_TYPE` after login changes nothing.

## Configuration and logs

- Settings: GNOME Settings and GNOME applications store settings in dconf. The user database is `~/.config/dconf/user` (binary, in `$XDG_CONFIG_HOME/dconf/`). System databases are in `/etc/dconf/db/`, built from keyfiles in `/etc/dconf/db/DATABASE.d/`, with locks in its `locks` subdirectory; profiles are in `/etc/dconf/profile/` or named by `DCONF_PROFILE`.
- `gsettings` uses schemas and validates values; `dconf` works on the database directly without schema checks. Keys are equivalent: `gsettings set a.b key value` is `dconf write /a/b/key value`. dconf needs the session bus to write; if allowed and the file is visible, `dconf dump /org/gnome/shell/` may still read values.
- Extensions: per user in `~/.local/share/gnome-shell/extensions/`, system-wide in `/usr/share/gnome-shell/extensions/` or `/usr/local/share/gnome-shell/extensions/`, one directory per UUID with a `metadata.json` whose `shell-version` lists supported versions. Enabled ones are in the key `org.gnome.shell enabled-extensions`.
- Logs: GNOME Shell runs as a user unit whose name starts with `org.gnome.Shell` (package `gnome-shell`). If visible and allowed, read `journalctl --user -u 'org.gnome.Shell*' -b`. `coredumpctl list gnome-shell` lists captured crashes, if allowed.

## Investigation

1. Extensions after an upgrade: list the extension directories and compare each `metadata.json` `shell-version` with the installed `gnome-shell` version. Propose `gnome-extensions list --enabled --details` to the user; `gsettings get org.gnome.shell enabled-extensions` may also list removed extensions.
2. Shell crash or freeze: read the shell unit journal and core dumps around the symptom time. Malfunctioning extensions, appearance tweaks and low memory are common causes.
3. Extensions suddenly all off: the user unit `org.gnome.Shell-disable-extensions.service` runs `gsettings set org.gnome.shell disable-user-extensions true` when gnome-shell flagged extensions as the likely cause of a failure (the flag file `gnome-shell-disable-extensions` in the user runtime directory).
4. Setting does not apply: check whether a system dconf database or a lock in `/etc/dconf/db/` sets the key. If the user cannot change settings at all, the user database may be corrupt.
5. "Unable to apply stored configuration for monitors": ArchWiki documents disabling the settings daemon xrandr plugin with `dconf write /org/gnome/settings-daemon/plugins/xrandr/active false`; treat it as a proposed change.

## Interpretation

- An extension whose `shell-version` does not include the running version is rejected after an upgrade; first check for an update at extensions.gnome.org/local.
- `gsettings set org.gnome.shell disable-extension-version-validation true` only allows trying an incompatible extension; the likely outcome is a shell crash. It is not a fix.
- If disabling user extensions (`disable-user-extensions true`) removes the symptom, isolate the extension one at a time instead of keeping all disabled.
- System-wide extensions installed by a package (e.g. `gnome-shell-extensions`) cannot be removed per user; removing the package affects all users.

## Change and verification

Change only after the user's consent with the exact command or diff. Before a dconf change, propose saving the subtree with `dconf dump /PATH/ > FILE`; `dconf load /PATH/ < FILE` restores it. Change one key with `gsettings set` (validated) rather than `dconf write`. Disable one extension with `gnome-extensions disable UUID`; `gnome-extensions reset UUID` disables it in GNOME only. Editing `metadata.json` changes a third-party file and is lost on update. Deleting `~/.config/dconf/user*` resets all user settings; it is a last resort with a backup.

These commands need the user's session, so they are steps the user runs. On Wayland, restarting GNOME Shell (e.g. `pkill -HUP gnome-shell`) kills the whole session; never propose it as diagnostics. Verify by re-reading the key or extension state and reproducing the original symptom, ideally after the user logs in again. Undo restores only the saved subtree or the one disabled extension.

## Result

Give the GNOME Shell version, the session type and how it was established, the key, extension or unit with its evidence, the cause or hypothesis and one next step. State whether the change was only proposed or made and verified, and name any reading blocked by missing session visibility.

## Sources

- [ArchWiki: GNOME](https://wiki.archlinux.org/title/GNOME)
- [ArchWiki: GNOME/Troubleshooting](https://wiki.archlinux.org/title/GNOME/Troubleshooting)
- [gnome-extensions(1)](https://man.archlinux.org/man/gnome-extensions.1)
- [dconf(1)](https://man.archlinux.org/man/dconf.1) and [dconf(7)](https://man.archlinux.org/man/dconf.7)
- [GNOME Shell: org.gnome.Shell-disable-extensions.service](https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/main/data/org.gnome.Shell-disable-extensions.service)
- [Arch package files: gnome-shell](https://archlinux.org/packages/extra/x86_64/gnome-shell/files/) and [gnome-session](https://archlinux.org/packages/extra/x86_64/gnome-session/files/)
- [loginctl(1)](https://man.archlinux.org/man/loginctl.1), [journalctl(1)](https://man.archlinux.org/man/journalctl.1) and [coredumpctl(1)](https://man.archlinux.org/man/coredumpctl.1)
