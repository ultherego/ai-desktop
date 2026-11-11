---
name: arch-storage-health
description: Diagnoses disk health with S.M.A.R.T., SSD TRIM and device firmware updates through fwupd on Arch Linux. Use for "is my disk dying", "should I enable TRIM" or "is there a firmware update".
---

# Disk health, TRIM and firmware

## Inputs and access

Establish one device (`/dev/DEVICE`), its type (ATA/SATA, NVMe, USB bridge) and one question: health, TRIM or firmware. Reads may need root; run them only when the policy allows the exact arguments. A denial or a bridge hiding the device is no evidence of health or failure.

## Investigation

1. If allowed, `smartctl --scan` lists devices; `smartctl -i /dev/DEVICE` shows whether SMART is enabled and the firmware version. A USB bridge may need `-d TYPE`; the `uas` driver can block SAT pass-through.
2. Read `smartctl -H`, `-A`, `-l selftest` and `-l error` (or `-x` for everything). For NVMe, health comes from the Critical Warning byte and attributes from the SMART/Health Information log.
3. The `smartctl` exit status is a bitmask: bit 3 "DISK FAILING", bit 4 pre-fail attributes at or below threshold, bit 5 the same in the past, bits 6 and 7 error and self-test log records.
4. For TRIM, `lsblk --discard`: non-zero `DISC-GRAN` and `DISC-MAX` indicate support. Check `fstrim.timer`, its journal and the mount options. `fstrim -n` is a dry run.
5. For firmware, `fwupdmgr get-devices`, `get-updates`, `get-history` and `check-reboot-needed`.

## Interpretation

- A failing `-H` means the device has failed or predicts failure within 24 hours: copy the data off first.
- An attribute fails when `VALUE` is at or below `THRESH`; `WHEN_FAILED` shows `FAILING_NOW`, `In_the_past` or `-`. Type `Pre-fail` alone means nothing; a failed `Old_age` attribute means wear-out. Raw values and SSD attribute names are vendor-specific unless the drive is in the drive database.
- The NVMe error log is lost on power cycle; the Error Information Log Entries count is kept.
- `fstrim.timer` trims weekly. Continuous TRIM (`discard`) can freeze devices with non-queued TRIM (before SATA 3.1); frequent trimming can shorten the life of low-quality SSDs. Btrfs uses async discard by default since kernel 6.2; dm-crypt discard is off by default for security.
- A device in `get-devices` may not be updatable. BIOS/UEFI updates need UEFI boot mode and a prepared EFI system partition.

## Change and verification

Each item needs separate user consent.

- Self-tests: `smartctl -t short|long|conveyance /dev/DEVICE` are data-safe but take minutes to hours (`smartctl -c`) and slow the drive. Never use captive `-C` on a drive with mounted partitions; `-X` aborts.
- TRIM only after support is confirmed: unsupported TRIM can lose data. Enable `fstrim.timer` or run `fstrim -v MOUNTPOINT`; `--all` returns 0, 32 (all failed) or 64 (some failed). Undo by disabling the timer or removing `discard`, keeping a copy of `/etc/fstab`.
- Firmware: `fwupdmgr refresh` contacts the LVFS. `update` applies some updates live and stages others for reboot; a UEFI update can remove the boot loader NVRAM entry, so have a recovery path and never interrupt it. `downgrade` works only if an older release exists. Do not run `report-devices` or `report-history`; they upload data.

Verify by repeating the read: the self-test log, timer runs in the journal, the new version in `get-devices` and `get-results`.

## Result

Give the device type, the health verdict with evidence, the TRIM state and the firmware status. Separate the plan from changes made; name the one missing read.

## Sources

- [ArchWiki: S.M.A.R.T.](https://wiki.archlinux.org/title/S.M.A.R.T.).
- [ArchWiki: Solid state drive](https://wiki.archlinux.org/title/Solid_state_drive).
- [ArchWiki: fwupd](https://wiki.archlinux.org/title/Fwupd).
- [smartctl(8)](https://man.archlinux.org/man/smartctl.8).
- [fstrim(8)](https://man.archlinux.org/man/fstrim.8).
- [fwupdmgr(1)](https://man.archlinux.org/man/fwupdmgr.1).
