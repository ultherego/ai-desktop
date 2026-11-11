---
name: pacdiff
description: Using pacdiff from pacman-contrib to list and review .pacnew, .pacsave and .pacorig files.
tools: pacman-contrib
source: https://man.archlinux.org/man/pacdiff.8
section: Description; Options; Environment
checked: 2026-11-11
version: 1.13.1
---

# pacdiff

Applies when `.pacnew`, `.pacsave` or `.pacorig` files need review and pacman-contrib is installed. Confirm the installed version with `pacdiff -V` and the options with `pacdiff -h` before relying on this note.

## Read first

- `pacdiff -o` prints the found files instead of merging them; this is the read-only listing.
- The default search is `-p`/`--pacmandb`: backup entries of installed packages in the local pacman database. `-f`/`--find` scans with find (default path `/etc`, changed with `DIFFSEARCHPATH`); `-l`/`--locate` uses locate. Combine either with `-o` for a broader listing.
- Inspect each pair with a separate, scoped diff before any merge.

## Interpretation

Without `-o`, pacdiff is interactive: for each file it offers view, merge, skip, diff, remove or overwrite. The diff program defaults to `vim -d` (or `nvim -d` if `EDITOR=nvim`) and is overridden with `DIFFPROG`. `-3`/`--threeway` shows a 3-way diff only if the base package is still in the cache (`-c` names another cache directory); otherwise it falls back to 2-way. The 3-way merge program defaults to `diff3 -m` (`MERGEPROG`). Deleting old cache versions can therefore remove the merge base.

## Changes needing consent

Merge, remove and overwrite modify files under `/etc` and are system changes. `-s`/`--sudo` makes pacdiff use sudo and sudoedit; never run it that way silently. Propose one file at a time with the resulting content, get the user's consent, and prefer `-b`/`--backup`, which saves the old file with `.bak` when overwriting, so it can be restored. An interactive session cannot be driven by the model; the user runs it or approves a planned edit instead.

## Stop when

Stop at the `-o` listing and diffs if the user has not approved the specific file and result, if the base for a 3-way merge is missing and the change is not understood, or if the file belongs to an unidentified package.
