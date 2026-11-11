---
name: cosmic
description: Diagnoses the COSMIC desktop by System76 on Arch Linux from its version, cosmic-config files and journal. Use for a crashing compositor, panel or applet, settings that do not apply or reset, or a safe change to a COSMIC configuration key.
programs: cosmic-session, start-cosmic
---

# COSMIC diagnostics

## Scope and access

Establish one symptom: the compositor `cosmic-comp` (windows, tiling, input, monitors), the panel or dock (`cosmic-panel`, applets), COSMIC Settings, or one application. Ask what changed last: an upgrade, a setting, a monitor. Commands run by AI Desktop have no graphical session, session D-Bus bus or Wayland socket, and only commands the owner allowed run. COSMIC tools started from the sandbox cannot reach the session; a failed connection is missing visibility, not a fault. `journalctl --user` may not be visible either.

## Identify the version and session

1. If allowed, `pacman -Qi cosmic-session cosmic-comp cosmic-panel cosmic-settings` gives the installed versions; COSMIC components are separate packages with their own versions (the `cosmic` group or `cosmic-session` with dependencies). An installed package does not prove COSMIC is the running session.
2. COSMIC is a Wayland session. `cosmic-session` sets `XDG_SESSION_TYPE=wayland` and `COSMIC_VERSION` in the systemd user environment; `start-cosmic` sets `XDG_CURRENT_DESKTOP=COSMIC` and `DCONF_PROFILE=cosmic`. To confirm, propose `loginctl show-session SESSION_ID -p Type -p Desktop` (from `loginctl list-sessions`).
3. The session starts from a display manager (`cosmic.desktop` in `/usr/share/wayland-sessions/`), from the greetd-based `cosmic-greeter.service`, or by `start-cosmic` from a console. The user target is `cosmic-session.target`.

## Configuration and logs

COSMIC components use cosmic-config. Each component has an ID such as `com.system76.CosmicComp`, a version directory `vN` and one file per key, holding a RON value:

- user values: `~/.config/cosmic/ID/vN/KEY` (under `XDG_CONFIG_HOME`);
- shipped defaults: `/usr/share/cosmic/ID/vN/KEY`, e.g. `/usr/share/cosmic/com.system76.CosmicComp/v1/` (from `cosmic-settings`), `com.system76.CosmicPanel.Panel/v1/` and `com.system76.CosmicPanel.Dock/v1/` (from `cosmic-panel`), `com.system76.CosmicSettings.Shortcuts/v1/` (from `cosmic-comp` and `cosmic-settings-daemon`), `com.system76.CosmicTheme.Dark/v1/` and `v2/`;
- state: `~/.local/state/cosmic/ID/vN/`.

A component reads the user key in `vN`; if it is missing, the user key of `vN-1`, then the shipped default of `vN`. List the directories and read single key files; do not guess key names, use those present in `/usr/share/cosmic`. GTK settings in COSMIC use the dconf profile `/usr/share/dconf/profile/cosmic`.

Logs: `cosmic-session` and `cosmic-comp` log to the journal when it is available; `cosmic-comp` writes its version at start. If visible and allowed, read `journalctl -b _COMM=cosmic-comp` (or `_COMM=cosmic-session`, `_COMM=cosmic-panel`) around the symptom time. `coredumpctl list cosmic-comp` lists captured crashes, if allowed.

## Investigation

1. Compare the user key file with the shipped default of the same ID and version. A user key in an older `vN` directory still applies when the newer one has none, so check both.
2. A crashing panel, applet or compositor: read the journal and core dumps for that process. `cosmic-session` supervises its components and restarts them with exponential backoff, so repeated restarts appear as a series of start messages.
3. Settings that reset or do not apply: check whether COSMIC Settings wrote the key (file time) and whether the value parses; a malformed RON value makes the read fail instead of falling back to the default.
4. Night light tools such as wlsunset and gammastep do not work in COSMIC because `cosmic-comp` does not implement `wlr-gamma-control-unstable-v1`; this is a missing feature, not a fault.
5. `$SSH_AUTH_SOCK not set` at login with the COSMIC greeter means the variable is not configured; COSMIC does not provide a secrets store, so a component such as `gnome-keyring` is needed for remembered passwords.

## Interpretation

- An absent `~/.config/cosmic/ID` directory means defaults are used, not that the component is broken.
- Values are RON, not TOML or JSON; do not carry syntax from other compositors.
- An empty journal result may only mean the session journal is not visible from the sandbox.

## Change and verification

Change only after the user's consent with the exact diff. Prefer COSMIC Settings for options it exposes. For a hand edit, copy the key file first (e.g. `cp KEY KEY.bak` in its directory) and change one key file, keeping the RON form of the default. To return to the default, remove only the user key file, after a backup. The AI Desktop `text` check confirms UTF-8 only, not valid RON.

cosmic-config can watch files for changes, but live application by each component is not guaranteed; ask the user to confirm in the session whether the change took effect. Restarting a component or the session is a separate consented step the user runs; never end the session as part of diagnostics. Verify by reproducing the original symptom and re-reading the journal. Undo restores only the backed-up key file.

## Result

Give the COSMIC component versions, the session type and how it was established, the ID, version directory and key with evidence, the cause or hypothesis and one next step. State whether the change was only proposed or made and verified, and name any reading blocked by missing session visibility.

## Sources

- [ArchWiki: COSMIC](https://wiki.archlinux.org/title/COSMIC)
- [libcosmic: cosmic-config source](https://github.com/pop-os/libcosmic/blob/master/cosmic-config/src/lib.rs)
- [cosmic-session: start-cosmic](https://github.com/pop-os/cosmic-session/blob/master/data/start-cosmic) and [main.rs](https://github.com/pop-os/cosmic-session/blob/master/src/main.rs)
- [cosmic-comp: logger](https://github.com/pop-os/cosmic-comp/blob/master/src/logger/mod.rs)
- Arch package files: [cosmic-session](https://archlinux.org/packages/extra/x86_64/cosmic-session/files/), [cosmic-comp](https://archlinux.org/packages/extra/x86_64/cosmic-comp/files/), [cosmic-settings](https://archlinux.org/packages/extra/x86_64/cosmic-settings/files/), [cosmic-panel](https://archlinux.org/packages/extra/x86_64/cosmic-panel/files/), [cosmic-settings-daemon](https://archlinux.org/packages/extra/x86_64/cosmic-settings-daemon/files/)
- [loginctl(1)](https://man.archlinux.org/man/loginctl.1), [journalctl(1)](https://man.archlinux.org/man/journalctl.1) and [coredumpctl(1)](https://man.archlinux.org/man/coredumpctl.1)
