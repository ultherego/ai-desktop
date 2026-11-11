---
name: qtile
description: Diagnoses the Qtile tiling window manager (X11, optionally a Wayland compositor) from its version, config.py and log file. Use for config errors, Qtile falling back to its default config, key bindings, groups and screens, widgets with missing dependencies, autostart hooks, or breakage after an upgrade.
programs: qtile
---

# Qtile diagnostics

## Scope and access

Establish one symptom, when it started and the latest change. Qtile is configured in Python and runs on X11 or, with `-b wayland`, as a Wayland compositor. The CachyOS installer group "Qtile" is X11-based (xorg-server, picom, rofi and the ly display manager) and installs `cachyos-qtile-settings`, whose dotfiles live in `/etc/skel/.config/qtile/` (`config.py`, `scripts/autostart.sh`); skel is copied only when an account is created.

AI Desktop reads without the user's graphical session. `qtile shell`, `qtile cmd-obj` and `qtile run-cmd` talk to the running Qtile over its socket; a failed connection is missing visibility, not a fault. Propose them to the user. Read the config and log within the allowed file scope.

## Identify the version and session

1. Installed: `qtile -v` (`--version`) prints the version. Use the documentation of that version (docs.qtile.org and `/usr/share/doc/qtile/CHANGELOG`).
2. Session start: the Arch package ships `/usr/share/xsessions/qtile.desktop` (`qtile start`), `/usr/share/wayland-sessions/qtile-wayland.desktop` (`qtile start -b wayland`) and `qtile-generic.desktop` for both. Without a display manager, `~/.xinitrc` ends with `exec qtile start`.
3. `qtile start` fails if it detects that Qtile is already running.

## Configuration and logs

- Lookup order: `-c FILE`; `$XDG_CONFIG_HOME/qtile/config.py`; `~/.config/qtile/config.py`; the first `qtile/config.py` in `$XDG_CONFIG_DIRS` (default `/etc/xdg`); otherwise the built-in `libqtile.resources.default_config`. If no file exists, Qtile tries to create one as a copy of the default.
- Fallback: on start, restart or reload, a config file with an error makes Qtile load the default config. Unexpected default bindings or bar usually mean `config.py` failed.
- The config is Python: it may import other modules from `~/.config/qtile/` and run code at import time.
- Autostart: the `startup` hooks in `config.py`; the ArchWiki pattern runs `~/.config/qtile/autostart.sh` from a `startup_once` hook.
- Backend-specific code: `qtile.core.name` is `"x11"` or `"wayland"`. Wayland input settings use `wl_input_rules`.
- Log: `~/.local/share/qtile/qtile.log`. Level `-l DEBUG|INFO|WARNING|ERROR|CRITICAL` (default WARNING), path `-p`.
- X11 server log: `~/.local/share/xorg/Xorg.N.log` for rootless Xorg, otherwise `/var/log/`.

## Investigation

1. Read the log first: tracebacks from loading `config.py`, widget errors and warnings appear there.
2. Offline check: `qtile check` (or `qtile check -c FILE`) loads and validates the config, lists widgets with import errors, then type-checks with `mypy` and `stubtest` if both are installed (otherwise it skips type checking). It imports the config, so code in it runs: propose it, or run it only when that exact command is allowed. `python -m py_compile ~/.config/qtile/config.py` checks syntax only.
3. Upgrades: read the changelog for the new version. `qtile migrate --lint` shows lines affected by breaking changes without changing the file; `qtile migrate` without `--yes` asks before applying.
4. State at runtime: ask the user for `qtile cmd-obj -o group` style reads (`-f commands` lists functions of an object, `-i` shows help) or `qtile shell`.
5. Screens: on X11 ask for `xrandr --current`; compare with the `screens` list in `config.py`.
6. Widgets: `qtile check` and the log name a widget whose Python dependency is missing.

## Interpretation

- A passing `qtile check` means the config loads and type-checks, not that every binding or widget behaves as intended.
- Default bindings after an edit point to a config error, even when Qtile keeps running.
- Pointer events reaching the wrong XWayland window on Wayland is a known upstream bug.

## Change and verification

Back up `config.py` (and any imported module) first and change the smallest part. Get consent for the write. Run `qtile check` when allowed. Reloading is a separate consented step: the default binding is Mod+Ctrl+R (`lazy.reload_config()`). For larger changes the docs recommend testing in a nested session (`scripts/xephyr` or `scripts/wephyr` from the source). Never shut Qtile down (`lazy.shutdown()`, default Mod+Ctrl+Q) as part of diagnostics. Verify with the log and the original symptom. Undo restores only your change.

## Result

Give the Qtile version, the backend (X11 or Wayland), the config file actually loaded, whether the default config was used, the log evidence, the `qtile check` result, and whether the change was proposed or applied and verified. Name the missing read when the session or the socket was unavailable.

## Sources

- [Qtile docs: The config file](https://docs.qtile.org/en/stable/manual/config/index.html), [Troubleshooting](https://docs.qtile.org/en/stable/manual/troubleshooting.html), [FAQ](https://docs.qtile.org/en/stable/manual/faq.html), [qtile start](https://docs.qtile.org/en/stable/manual/commands/shell/qtile-start.html), [qtile cmd-obj](https://docs.qtile.org/en/stable/manual/commands/shell/qtile-cmd.html), [qtile migrate](https://docs.qtile.org/en/stable/manual/commands/shell/qtile-migrate.html), [Wayland](https://docs.qtile.org/en/stable/manual/wayland.html)
- [Qtile 0.37.1 source](https://github.com/qtile/qtile/tree/v0.37.1): `libqtile/scripts/check.py`, `libqtile/scripts/main.py`, `resources/*.desktop`
- [ArchWiki: Qtile](https://wiki.archlinux.org/title/Qtile), [ArchWiki: Xorg](https://wiki.archlinux.org/title/Xorg)
- [Arch package qtile: files](https://archlinux.org/packages/extra/x86_64/qtile/files/)
- [CachyOS netinstall.yaml](https://github.com/CachyOS/cachyos-calamares/blob/cachyos/src/modules/netinstall/netinstall.yaml), [cachyos-qtile-settings PKGBUILD](https://github.com/CachyOS/CachyOS-PKGBUILDS/blob/master/cachyos-qtile-settings/PKGBUILD)
