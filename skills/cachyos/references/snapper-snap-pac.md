---
name: snapper-snap-pac
description: Snapper configurations, snap-pac pre/post snapshots around pacman, listing and comparing snapshots, and their limits.
tools: snapper, snap-pac
source: https://wiki.archlinux.org/title/Snapper
section: Creating a new configuration; Pre/post snapshots; Managing snapshots; Wrapping pacman transactions in snapshots; Suggested filesystem layout; Troubleshooting
checked: 2026-11-11
version: rolling
---

# Snapper and snap-pac

Applies when a change must be traced to a pacman transaction or a snapshot is considered for recovery. A configuration lives in `/etc/snapper/configs/<config>` and is listed in `SNAPPER_CONFIGS` in `/etc/conf.d/snapper`; snapshots are stored under `.snapshots/<number>/snapshot`.

Read first: `snapper list-configs`, then `snapper -c <config> list`. `snap-pac` uses pacman hooks to create a pre snapshot before and a post snapshot after each transaction; a matching pair brackets one change. Activity is logged in `/var/log/snapper.log`. Dates in `info.xml` are UTC.

Interpret: Snapper can compare snapshots; take the subcommand from the installed `snapper` help. Patterns in `/etc/snapper/filters/*.txt` and `/usr/share/snapper/filters/*.txt` are ignored in comparisons but are still captured. Orphans show up as subvolumes in `btrfs subvolume list -o <parent>/.snapshots` that `snapper -c <config> list` does not show.

Snapshots are not backups. A snapshot of `@` excludes every other subvolume, even nested ones; a non-Btrfs `/boot` or ESP cannot be snapshotted. The pacman database in `/var/lib/pacman` must stay on the root subvolume (`@`). For copies on another drive see the Btrfs incremental backup method.

Changes needing separate consent: `create-config`, `set-config`, `create`, `delete` (delete a pre snapshot together with its post), `delete-config` (removes all snapshots of that config), timeline limits and timers (cron plus systemd timers can duplicate snapshots), and making a snapshot writable with `btrfs property set ... ro false`. Deletion has no undo. A root restore is done from live media: move the current `@` aside (for example `@.broken`) instead of deleting it, so the way back stays; then check fstab subvolid, boot loader entries and initramfs.

Stop when the layout or config scope is unclear, the subvolume to restore is in use, or the only copy of data would be replaced.
