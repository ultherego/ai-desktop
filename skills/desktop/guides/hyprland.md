---
name: hyprland
description: Diagnoses Hyprland configuration, monitors, binds, window rules, autostart and portals from its version and versioned wiki. Use for config errors, a wrong monitor mode or position, a bind or rule that does not fire, an autostart program missing, or screen sharing failing.
programs: Hyprland, hyprctl, start-hyprland
---

# Hyprland diagnostics

## Scope and access

Establish one symptom, the time it started and the latest change (package update, config edit). AI Desktop reads without the user's graphical session, so `hyprctl` usually cannot reach the running instance. A failed IPC connection is missing visibility, not a fault; propose the `hyprctl` read to the user and ask for its output. Read configuration files only within the allowed file scope.

## Identify the version and session

1. Ask the user to run `hyprctl version`; offline, `Hyprland --version` prints the installed binary's version, which may differ from the running one until a restart.
2. The wiki is versioned: the default is the latest git commit. Use `https://wiki.hypr.land/VERSION/` for a tagged release (e.g. `0.56.0`).
3. The configuration language depends on the version. Since 0.55 Hyprland loads `hyprland.lua` when it exists and otherwise the legacy hyprlang `hyprland.conf`; the choice is made once at startup. The 0.57.0 release notes list "config: remove legacy config support". Do not mix Lua and hyprlang syntax; use the wiki for the installed version.
4. Hyprland is started with `start-hyprland`; its arguments go after `--`, e.g. `start-hyprland -- --config FILE`. Under systemd, Hyprland starts `hyprland-session.target` and `graphical-session.target` itself; old manual `systemctl --user` start lines in the config should be removed. A session started with uwsm is managed differently; establish which one the user uses.

## Configuration and logs

- Config: `$XDG_CONFIG_HOME/hypr/hyprland.lua`, usually `~/.config/hypr/hyprland.lua` (legacy versions: `hyprland.conf` in the same directory). If none exists, Hyprland creates an example config.
- Includes: Lua `require`, relative to `hyprland.lua` (`.` preferred as separator) or absolute; `*` loads several files. Each `require` is a separate scope, so an error in one file does not stop the others; requiring a module that does not exist stops the calling file.
- Lua stubs for editors: `/usr/share/hypr/stubs/`.
- Log of a session: `$XDG_RUNTIME_DIR/hypr/INSTANCE/hyprland.log`, one directory per instance; the newest directory is the latest session. Logging needs `debug.disable_logs` set to `false`.
- Crash report: `$XDG_CACHE_HOME/hyprland/hyprlandCrashReportPID.txt`, or `~/.cache/hyprland/` when `XDG_CACHE_HOME` is unset. For a crash at launch, `coredumpctl info PID` shows the latest coredump.
- Portal: xdg-desktop-portal-hyprland (XDPH) has no file picker; the GTK one is the default fallback per `/usr/share/xdg-desktop-portal/hyprland-portals.conf`. A user override is `~/.config/xdg-desktop-portal/hyprland-portals.conf`.

## Investigation

1. Config errors: ask the user for `hyprctl configerrors`. Offline, `Hyprland --verify-config` (from `Hyprland --help`, not the wiki) prints whether the config has errors without running Hyprland; `-c FILE` selects the file. It still initializes part of the compositor, so a failure in a sandbox without a seat is not proof of a config error.
2. Monitors: ask for `hyprctl monitors all` (active and inactive outputs). Compare the output name or `desc:` prefix with the `hl.monitor` rules; an empty `output` is the fallback rule for unmatched monitors.
3. Binds: ask for `hyprctl binds` (registered binds) and `hyprctl submap`. A bind callback must not block; a slow function freezes input. If an error occurs before binds are registered, Hyprland registers emergency binds and says so in the error popup.
4. Window rules: ask for `hyprctl clients` or `hyprctl activewindow` to get the real `class`, `initial_class` and title, then compare with the `match` table; matches are regular expressions.
5. Autostart: in Lua, programs start from `hl.on("hyprland.start", ...)` with `hl.exec_cmd`. Check that the event handler is reached, i.e. no earlier error stopped that file.
6. Logs: ask for `hyprctl rollinglog` or read the latest `hyprland.log` if the path is allowed. Trace logging (`HYPRLAND_TRACE=1 AQ_TRACE=1`) is very verbose; propose it only for a reproducible issue.

## Interpretation

- A fundamental Lua syntax error makes Hyprland refuse to reload the config; a runtime error aborts the current file; a type error continues with an error popup.
- The config reloads automatically when files change, so a broken edit can take effect immediately.
- `hyprctl` output without `-j` is for reading; use `-j` when a value must be parsed exactly.
- An installed package does not prove the running version; compare `hyprctl version` with the package.

## Change and verification

Back up the file first. Propose the smallest diff in the syntax of the installed version and get consent for the write. Check it with `Hyprland --verify-config` when allowed; otherwise ask the user to check `hyprctl configerrors` after saving. Automatic reload applies saved changes; `hyprctl reload` is a separate state change needing consent, and `reload full-reset` should not be used unless really necessary. Never exit the session or kill Hyprland as part of diagnostics. Verify by reproducing the original symptom (monitor, bind, rule) and reading the same `hyprctl` output again. Undo restores only your change.

## Result

Give the Hyprland version (running and installed), config language, the file and wiki page used, the evidence, and whether the change was proposed or applied and verified. Name the missing read when IPC was unavailable.

## Sources

- [Hyprland wiki: Core (config file, Lua, require, errors)](https://wiki.hypr.land/configuring/core/)
- [Hyprland wiki: Using hyprctl](https://wiki.hypr.land/configuring/core/advanced-configuration/using-hyprctl/)
- [Hyprland wiki: Monitors](https://wiki.hypr.land/configuring/core/monitors/)
- [Hyprland wiki: Binds](https://wiki.hypr.land/configuring/core/binds/)
- [Hyprland wiki: Window rules](https://wiki.hypr.land/configuring/core/rules/)
- [Hyprland wiki: Autostart](https://wiki.hypr.land/configuring/core/autostart/)
- [Hyprland wiki: Crashes and Bugs](https://wiki.hypr.land/Crashes-and-Bugs/)
- [Hyprland wiki source (systemd, XDPH, FAQ)](https://github.com/hyprwm/hyprland-wiki)
- [Hyprland news: Lua configs](https://hypr.land/news/26_lua/)
- [Hyprland v0.57.0 release notes](https://github.com/hyprwm/Hyprland/releases/tag/v0.57.0)
- [Hyprland(1)](https://man.archlinux.org/man/Hyprland.1.en)
- [Hyprland v0.57.2 src/main.cpp (help text)](https://github.com/hyprwm/Hyprland/blob/v0.57.2/src/main.cpp)
- [Arch package hyprland: files](https://archlinux.org/packages/extra/x86_64/hyprland/files/)
