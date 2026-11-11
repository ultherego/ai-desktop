---
name: pacnew-pacsave
description: Locating and reviewing .pacnew and .pacsave configuration files after a pacman upgrade or removal.
tools: pacman
source: https://wiki.archlinux.org/title/Pacman/Pacnew_and_Pacsave
section: Why these files are created; Types explained; Locating .pac* files; Managing .pac* files
checked: 2026-11-11
version: rolling
---

# .pacnew and .pacsave files

Applies after pacman warns that a file was "installed as" `.pacnew` (upgrade) or "saved as" `.pacsave` (removal, or an upgrade that first removes the package). pacman never merges these itself.

## Read first

- The files a package backs up: `pacman -Qii NAME` (backup list).
- Which `.pac*` files exist: `find /etc -name '*.pacnew' -o -name '*.pacsave'`.
- Which ever existed: `grep '\.pacnew\|\.pacsave' /var/log/pacman.log`; pipe to `tail` for recent ones. The log also lists files that no longer exist, so confirm each path is still present.
- A plain `diff` of the live file and its `.pacnew`, scoped to that one file.

## Interpretation

A `.pacnew` appears only when the original, the current and the new version all differ: the live file was modified, and the package changed it too. It can appear for a file the user never edited, because installing another package may change files owned by other packages. A `.pacsave` is a modified backup file left after removal; `pacman -R` with `-n` creates none. A missing `.pacnew` for an unmodified file is normal: pacman overwrote it silently.

## Changes needing consent

Merging, replacing or deleting a file under `/etc` is a separate system change. Show the exact diff of the result, the owning package and the affected service, and get the user's consent. Never overwrite a live file with its `.pacnew` blindly, even if it looks unedited. Before writing, keep a copy of the current file so the change can be reverted by restoring it; delete the `.pacnew` only after the merged file is verified. Restoring from a `.pacsave` likewise needs consent and a check that the reinstalled package still uses that format.

## Stop when

Stop at the diff if the file is security-sensitive (for example under `/etc/pam.d`) and the meaning of a change is unclear, if the owning package cannot be identified, or if the user has not approved the exact merged content. Leave unhandled files listed in the result rather than guessing.
