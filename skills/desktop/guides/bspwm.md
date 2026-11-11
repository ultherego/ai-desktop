---
name: bspwm
description: Diagnoses the bspwm X11 tiling window manager and its sxhkd key bindings from the version, bspwmrc and sxhkdrc. Use for a blank screen, key bindings that do not work, monitors and desktops on the wrong screen, window rules, panels started twice, or Java applications without content.
programs: bspwm, bspc
---

# bspwm diagnostics

## Scope and access

Establish one symptom, when it started and the latest change. bspwm runs on X11 and only reacts to X events and messages on its socket; it handles no keyboard or pointer input. Key bindings belong to a separate hotkey daemon, normally `sxhkd` (package `sxhkd`), which runs `bspc` commands. The CachyOS installer group "bspwm" installs bspwm, sxhkd, polybar, picom config and the ly display manager.

AI Desktop reads without the user's graphical session. `bspc` needs the bspwm socket (`BSPWM_SOCKET`, otherwise `/tmp/bspwm<host_name>_<display_number>_<screen_number>-socket`) and `xrandr` and `xprop` need the X server; a failed connection is missing visibility, not a fault. Propose those reads to the user. Read configuration files only within the allowed file scope.

## Identify the version and session

1. Installed: `bspwm -v` prints the version. Use `bspwm(1)`, `bspc` (same page) and `sxhkd(1)` for that version.
2. Session start: a display manager using `/usr/share/xsessions/bspwm.desktop` (`Exec=bspwm`), or `bspwm` from `~/.xinitrc` with xinit.
3. Running instance: ask the user for `bspc wm -g` (status) or `bspc wm -d` (dumps the current state).

## Configuration and logs

- bspwm: `$XDG_CONFIG_HOME/bspwm/bspwmrc` (`~/.config/bspwm/bspwmrc`), or `bspwm -c FILE`. It is a shell script that calls `bspc` and must be executable. It receives an argument: `0` on first start, greater than `0` after a restart.
- sxhkd: `$XDG_CONFIG_HOME/sxhkd/sxhkdrc`, or `sxhkd -c FILE`. Commands run via `$SHELL -c` unless `SXHKD_SHELL` is set.
- Examples: `/usr/share/doc/bspwm/examples/bspwmrc` and `sxhkdrc`; the example bspwmrc starts sxhkd with `pgrep -x sxhkd > /dev/null || sxhkd &`.
- Settings: `bspc config [-m MONITOR|-d DESKTOP|-n NODE] SETTING [VALUE]`; rules: `bspc rule -a CLASS[:INSTANCE[:NAME]] key=value ...`; `external_rules_command` names an external rule script.
- Logs: bspwm writes no log file; its output goes to whatever started it (the terminal or xinit, or the display manager's session handling). X server log: `~/.local/share/xorg/Xorg.N.log` for rootless Xorg, otherwise `/var/log/`.

## Investigation

1. Blank screen or no key bindings (ArchWiki): sxhkd installed and started in the background; `~/.config/bspwm/bspwmrc` executable; the terminal named in `sxhkdrc` installed (the example uses urxvt).
2. "Could not grab key ... with modfield ...": the same key is bound twice, or sxhkd is started twice; check bspwmrc and shell login files such as `~/.profile` for extra sxhkd starts.
3. fish as login shell: desktop switching bindings using `^` fail and commands can be slow; ArchWiki sets `SXHKD_SHELL` to bash or sh, or escapes `^`.
4. Monitors and desktops: ask for `bspc query -M --names` and `xrandr -q`, then compare with `bspc monitor NAME -d ...` lines. Desktop indexes follow monitor order; `bspc wm -O NAME...` fixes the order. Names with special characters need a `%` prefix.
5. Window rules: ask the user to run `xprop | grep WM_CLASS` and click the window; the second string is the class used by `bspc rule -a`. Ask for `bspc rule -l` to list active rules.
6. Duplicates after restart: bspwmrc runs again on every restart, so panels and daemons started without a guard (like the `pgrep` check) start twice; use the restart argument or a guard.
7. Java applications gray or empty: ArchWiki sets `wmname LG3D` (package `wmname`) or `export _JAVA_AWT_WM_NONREPARENTING=1` in bspwmrc.

## Interpretation

- No bindings at all is usually an sxhkd problem, not a bspwm one.
- `bspc` returns a non-zero exit code when bspwm cannot handle the message; a socket error only means no access to the session.
- Desktops on the wrong monitor after hotplug follow from index-based bindings; ArchWiki recommends referring to desktops by name.

## Change and verification

Back up `bspwmrc` or `sxhkdrc` first and change the smallest part. Get consent for the write. There is no documented offline validator. Applying is a separate consented step: running a single `bspc config` or `bspc rule` command, `bspc wm -r` (restarts bspwm and re-runs bspwmrc), or sending `SIGUSR1` to sxhkd to reload `sxhkdrc`. Never `bspc quit` as part of diagnostics; it ends the session. Verify with the same `bspc` read and the original symptom. Undo restores only your change.

## Result

Give the bspwm version, the bspwmrc and sxhkdrc actually used, how sxhkd is started, the evidence, and whether the change was proposed or applied and verified. Name the missing read when X11 or the socket was unavailable.

## Sources

- [bspwm(1)](https://man.archlinux.org/man/bspwm.1.en)
- [sxhkd(1)](https://man.archlinux.org/man/sxhkd.1.en)
- [bspwm README](https://github.com/baskerville/bspwm/blob/master/README.md), [examples/bspwmrc](https://github.com/baskerville/bspwm/blob/master/examples/bspwmrc)
- [ArchWiki: bspwm](https://wiki.archlinux.org/title/Bspwm), [ArchWiki: Xorg](https://wiki.archlinux.org/title/Xorg)
- [Arch package bspwm: files](https://archlinux.org/packages/extra/x86_64/bspwm/files/)
- [CachyOS netinstall.yaml](https://github.com/CachyOS/cachyos-calamares/blob/cachyos/src/modules/netinstall/netinstall.yaml)
