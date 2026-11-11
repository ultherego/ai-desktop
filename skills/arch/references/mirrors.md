---
name: mirrors
description: Diagnosing out-of-sync or failing pacman mirrors and planning a mirror list change.
tools: pacman-mirrorlist, reflector
source: https://wiki.archlinux.org/title/Mirrors
section: Enabling a specific mirror; Force pacman to refresh the package lists; Sorting mirrors; Troubleshooting
checked: 2026-11-11
version: rolling
---

# Mirrors

Applies to 404s, failed downloads, TLS errors or package lists that disagree after a mirror change.

## Read first

- Active `Server` lines in `/etc/pacman.d/mirrorlist` and any `Server` or `Include` lines in `/etc/pacman.conf`. pacman uses them in listed order and falls back to the next entry on failure.
- Whether a `mirrorlist.pacnew` is waiting from a `pacman-mirrorlist` upgrade.
- System time, if the error is TLS-related.
- The Arch Mirror Status page, which the user can consult, for whether a mirror is out of sync.

## Interpretation

The fastest mirror may be out of sync; rank by speed, then drop mirrors that are out of sync. Package lists from an old and a new mirror may not correspond even if dates suggest they do. Mirrors stated directly in `pacman.conf` must be the same for all repositories, or a partial upgrade can result. A `mirrorlist could not be read` error means the file is missing.

## Tools are distribution-specific

The Arch list comes from `pacman-mirrorlist`. Reflector retrieves the Mirror Status list, filters and sorts it and overwrites `/etc/pacman.d/mirrorlist`; rankmirrors from pacman-contrib ranks an existing list. A derivative distribution may ship its own mirror lists, repositories and ranking tool. Identify that tool by its package and its own documentation; do not apply Arch's or Reflector's options to it.

## Changes needing consent

Editing or regenerating the mirror list or `pacman.conf` is a system change: back up the file first (e.g. `cp /etc/pacman.d/mirrorlist /etc/pacman.d/mirrorlist.backup`), show the diff, and restore from the backup to undo. Running a ranking tool contacts many servers; do it sparingly (no more than every 1–2 weeks). After switching from a broken mirror, `pacman -Syyu` (or `-Syyuu` to downgrade too-new packages) is a full transaction needing its own consent; it is unnecessary in most cases.

## Stop when

Stop if the cause is not mirror-related (signature, disabled repository), if the distribution's own tool is not identified, or if the user has not approved the new list.
