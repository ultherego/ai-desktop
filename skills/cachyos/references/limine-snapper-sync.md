---
name: limine-snapper-sync
description: How limine-snapper-sync turns Snapper snapshots into Limine boot entries and how to check them.
tools: limine-snapper-sync, limine
source: https://gitlab.com/Zesko/limine-snapper-sync
section: Description; Boot Configuration; Tool Configuration; Troubleshooting
checked: 2026-11-11
version: 1.32.1
---

# limine-snapper-sync

Applies when Limine and Snapper are both in use on Btrfs and snapshot entries are missing, unexpected, or filling the boot partition. The tool adds an entry with matching kernel files when a Snapper snapshot is created and removes it on deletion. It does not create entries for newly installed kernels, and snapshots made before it was installed are not added.

Read first: `limine-snapper-list` (entries in the boot menu) and `limine-snapper-info` (versions, number of bootable snapshots, kernel verification). Compare with the Snapper list. Configuration is `/etc/default/limine`, which takes precedence over `/etc/limine-snapper-sync.conf`. The boot partition is found automatically at `/efi`, `/boot`, `/boot/efi` or `/limine` unless `ESP_PATH` is set.

Interpret: the tool only writes `$ESP_PATH/limine.conf`. It needs kernel entries `//<Kernel name>` inside an OS entry `/<OS name>` and a `//Snapshots` or `/Snapshots` keyword; only `boot():` paths on the same partition are supported. Fewer entries than snapshots usually follow from `MAX_SNAPSHOT_ENTRIES` (default `auto`, which drops older entries without warning when `LIMIT_USAGE_PERCENT`, default 85, is reached) or `EXCLUDE_SNAPSHOT_TYPES`. An empty `limine.conf` on FAT32 can follow power loss or an unsafe editor write.

Changes needing separate consent: running `limine-snapper-sync` (it rewrites `limine.conf`), editing `/etc/default/limine`, `limine-snapper-remove`, `limine-snapper-restore`, and enabling `limine-snapper-sync.service`. Way back: the tool keeps `limine.conf.old` (and a manifest backup); after a restore it adds a "backup" entry for reverting. Restore methods (`replace`, `rsync`, `snapper`) differ in subvolume ID and history; name the configured one before any restore.

Stop when restore is blocked by its own safeguards (free space, wrong `ESP_PATH` or `FS_UUID`, incompatible layout), when hash mismatches or a "Broken manifest" point to faulty hardware, or when the boot partition layout is unconfirmed. Protect data first; do not bypass safeguards.
