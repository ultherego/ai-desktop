---
name: arch
description: General Arch Linux diagnostics and safe changes. Use for pacman and packages, systemd units and boot time, networking and DNS, time sync, sound, Bluetooth, AMD graphics, Btrfs, zram and swap, disks and firmware, power and CPU frequency, firewall (ufw, nftables), SSH, Flatpak, Docker and AppArmor.
---

# Arch Linux

Load the one guide that matches the task (`arch/TOPIC`) before planning; load another only when the evidence points there. References (`arch/REFERENCE`) hold source-backed details for one tool and say which version they describe.

## Guides

| Guide | Use for |
|---|---|
| `arch/pacman` | pacman errors, conflicts, interrupted updates |
| `arch/systemd` | failed units, slow boot or shutdown |
| `arch/network` | no network, NetworkManager, VPN |
| `arch/dns-time` | names that do not resolve, wrong DNS, clock and NTP |
| `arch/audio` | PipeWire/WirePlumber, missing devices, dropouts |
| `arch/bluetooth` | controller, pairing, sleep regressions |
| `arch/amdgpu` | AMD graphics hangs, artifacts, display |
| `arch/btrfs` | space, ENOSPC, checksum errors |
| `arch/zram` | zram, zswap, swap and memory pressure |
| `arch/storage-health` | S.M.A.R.T., TRIM, firmware updates |
| `arch/power` | power profiles, CPU frequency, governors |
| `arch/firewall` | which firewall is active, blocked ports |
| `arch/ssh` | key login, sshd configuration, hardening |
| `arch/flatpak` | Flatpak apps, permissions, runtimes |
| `arch/docker` | daemon, containers, networks, disk use |
| `arch/apparmor` | profiles, modes, denials |

## Rules for every guide

1. Establish the symptom and the goal first. Do not tune or "clean up" without one.
2. Read before changing. Run reads only as the policy allows them ("if allowed"); a refused or failing read is a gap in evidence, never proof that something is absent or healthy.
3. Do not invent commands or flags. When syntax may depend on the version, get the installed package version (`pacman -Q PACKAGE`) and the tool's own `--help` or manual; a reference marked `rolling` is general guidance, not a version match.
4. Any change of system state (packages, services, configuration, kernel parameters, firmware) is a separate step that needs the user's consent: state what changes, how to undo it and what to keep a copy of first. Prefer the smallest change and the tool that already manages the setting.
5. Verify with the same measurement as the original symptom. A service that is active or a file that parses does not prove the problem is solved.
6. Report what was checked, what was not, the conclusion and its uncertainty, and the one next read when evidence is missing. Separate a plan from a change already made.
