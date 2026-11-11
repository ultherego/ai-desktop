---
name: cachyos
description: CachyOS-specific diagnostics and safe changes. Use for CachyOS repositories, mirrors and Cachy-Update, the CachyOS kernels and Kernel Manager, sched-ext schedulers (scx_loader, scxctl), hardware detection and chwd, NVIDIA and hybrid graphics, gaming (Proton-CachyOS, game-performance), boot, Limine and snapshots (Snapper, snap-pac, cachy-chroot) and CachyOS settings and bug reports.
---

# CachyOS

CachyOS builds on Arch Linux: general mechanisms (pacman, systemd, networking, Btrfs) are in the `arch` guides. Use these guides for what CachyOS adds or changes. Load the one guide that matches the task (`cachyos/TOPIC`) before planning; references (`cachyos/REFERENCE`) hold source-backed details for one tool.

## Guides

| Guide | Use for |
|---|---|
| `cachyos/packages` | repositories, mirrors, signatures, Hello, Package Installer, Cachy-Update |
| `cachyos/kernel` | kernel choice, Kernel Manager, initramfs, DKMS |
| `cachyos/scheduler` | sched-ext, scx_loader, scxctl, SCX Manager |
| `cachyos/hardware` | device and driver binding, chwd, NVIDIA, two GPUs |
| `cachyos/gaming` | Steam, Proton-CachyOS, Wine, game-performance |
| `cachyos/boot-snapshots` | boot failures, Limine, Snapper, snap-pac, recovery |
| `cachyos/diagnostics` | cachyos-settings, kerver, topmem, bug reports |

## Rules for every guide

1. A CachyOS kernel or repository alone does not identify the installation: read `os-release`, the installed packages and the versions that matter for the task.
2. Read before changing, only as the policy allows; a refused or failing read is a gap in evidence, not proof.
3. Take commands and flags from the installed tool's `--help`, its manual or a matching reference; never invent them. A `rolling` reference is general guidance, not a version match.
4. Every change (packages, kernel, scheduler, bootloader, snapshots, drivers) needs the user's consent, a stated way back and, for boot and storage, a recovery path that works if the system no longer starts.
5. Snapshots are not backups. Uploading a report (`paste-cachyos`, bug report helpers) sends data off the computer: say what it contains and ask first.
6. Verify with the original symptom and report what was checked, what was not and the next read when evidence is missing.
