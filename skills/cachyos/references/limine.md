---
name: limine
description: Where Limine reads its configuration, how entries are written, and which changes are safe.
tools: limine
source: https://wiki.archlinux.org/title/Limine
section: Supported file systems; Configuration; pacman hook; Tips and tricks
checked: 2026-11-11
version: rolling
---

# Limine

Applies when Limine is the confirmed boot loader and an entry, path or kernel parameter is in question. Limine reads only FAT12/16/32 and ISO9660, so kernel, initramfs and other boot files must sit on a FAT partition such as the ESP.

Read first: locate `limine.conf`. On UEFI it is searched first next to the Limine EFI executable; otherwise in the root, `/boot`, `/limine` or `/boot/limine` of a supported partition on the boot drive. When an entry tool is installed, `limine-list` shows entries as a tree. Limine does not add its own firmware boot entry; that is separate NVRAM state.

Interpret: an entry starts with `/Name` and carries `protocol`, `path`, `cmdline` and `module_path`. `boot():/` means the partition holding `limine.conf`; a different partition needs `uuid(PARTUUID):/`. A path may end in `#` plus a BLAKE2B checksum. With entry automation, `/etc/default/limine`, `KERNEL_CMDLINE[...]` and `/etc/kernel/cmdline` are the sources and `limine-update` regenerates `limine.conf`; a hand edit of generated entries is lost. Do not copy parameters from `/proc/cmdline` of a live ISO, temporary environment or snapshot.

Changes needing separate consent: editing `limine.conf` or `/etc/default/limine`, `limine-update`, `limine-install`, `limine bios-install`, `efibootmgr` entries and pacman hooks. Keep a copy of the current `limine.conf` and name the exact diff; undo restores that copy and regenerates. A BIOS hook can install to the wrong disk if device names change.

`ENABLE_ENROLL_LIMINE_CONFIG=yes` embeds the config checksum in the EFI binary: editing `limine.conf` without re-enrolling prevents boot, and disabling Secure Boot does not help. Require a working fallback loader first.

Stop when the disk, ESP or boot mode is unconfirmed, when no fallback path exists, or when firmware fast boot may hide a disk ("Failed to open image"). Only a controlled boot confirms a change.
