---
name: paccache
description: Measuring and safely cleaning the pacman package cache with paccache.
tools: pacman-contrib
source: https://man.archlinux.org/man/paccache.8
section: Description; Operations; Options; Systemd timer
checked: 2026-11-11
version: 1.13.1
---

# paccache

Applies when the package cache is large, or before a cleanup the user asked for. Confirm the installed version with `paccache -V` and the options with `paccache -h`.

## Read first

- The cache directory configured in `pacman.conf` (paccache's default; `-c DIR` selects another and can be repeated).
- A dry run: `paccache -d` lists candidates without changing anything. Add `-v` for names, `-k NUM` to preview another retention count, `-u` to target only uninstalled packages.
- Whether `paccache.timer` is already enabled; it cleans weekly with default options.

## Interpretation

By default paccache keeps the last three versions of each package. `--min-atime` or `--min-mtime` (e.g. `30 days ago`) keeps recent files even beyond `--keep`. `-i` excludes listed packages. `-a` limits the scan to one architecture. Older cached versions are the local source for a downgrade and the base for pacdiff's 3-way merge, so establish which versions are needed for recovery before removing them.

## Changes needing consent

Removing (`-r`) or moving (`-m DIR`) candidates is a system change. Present the dry-run list, the retention rule and the space reclaimed, and get consent. Removal cannot be undone locally. Moving to a separate directory is the reversible alternative: move files back to undo. Do not add `-f` without a stated reason. Enabling `paccache.timer` is a separate persistent change; undo it by disabling the timer.

## Stop when

Stop at the dry run if a recent upgrade is unverified, if a downgrade may be needed, or if the user has not approved the exact candidates. Do not delete the whole cache.
