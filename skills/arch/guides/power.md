---
name: power
description: Diagnoses power profiles and CPU frequency scaling on Arch Linux: power-profiles-daemon, scaling drivers, governors and cpupower. Use for a CPU stuck at a low clock, battery drain or a profile that does not stick.
---

# Power profiles and CPU frequency

## Inputs and access

Establish the symptom: low clock under load, battery drain, a profile that changes back, or values that are hard to understand; note AC or battery. Run reads only within the policy; a denied read does not mean a file or service is absent, and never assume a command ran.

## Investigation

1. If allowed, read `cpupower frequency-info` for driver, governor and limits. `*-info` commands report the first core by default; `-c all` widens it.
2. Read `/sys/devices/system/cpu/cpuX/cpufreq/`: `scaling_governor`, `scaling_min_freq`, `scaling_max_freq`, `scaling_cur_freq`, `energy_performance_preference` and `bios_limit` when present.
3. Boost: `/sys/devices/system/cpu/intel_pstate/no_turbo` for `intel_pstate`, otherwise `/sys/devices/system/cpu/cpufreq/boost`.
4. With power-profiles-daemon, read `powerprofilesctl get`, `list`, `list-holds` and `query-battery-aware`. Launching `powerprofilesctl` also attempts to start the service.
5. Find every tool that may control the setting: power-profiles-daemon, `tuned`/`tuned-ppd`, TLP, `cpupower.service` with `/etc/default/cpupower-service.conf`, `thermald`, and `/etc/acpi/handler.sh`.

## Interpretation

- The kernel selects the driver at boot. `amd_pstate` has active (reported as `amd_pstate_epp`), passive and guided modes; `intel_pstate` in passive mode reports `intel_cpufreq`.
- In active mode of `intel_pstate` or `amd_pstate`, `powersave` and `performance` are pseudo-governors that become an energy preference hint; both scale dynamically. Only with other drivers does `powersave` run at `scaling_min_freq`.
- A low `scaling_max_freq` or `bios_limit` points to a limit, not the governor; the wiki names BIOS settings and, on some devices, a failing battery with an undersized adapter.
- A profile that changes back usually has a second controller: a hold, battery-aware switching or another service. power-profiles-daemon conflicts with TLP, tuned and system76-power; only one such tool should manage the settings.
- Battery drain needs a measured link to load and profile; boost or `performance` alone is not proof.

## Change and verification

Setting a profile, governor, limit, EPP or boost changes system state. Record current values and the controlling tool first so the state can be restored. Change it through the tool that already controls it; to switch tools, the wiki masks `power-profiles-daemon` instead of running both.

After separate consent, apply one change, e.g. `powerprofilesctl set PROFILE` or `cpupower frequency-set -g GOVERNOR`, and compare the same load, clock and power draw. Do not propose `processor.ignore_ppc=1`: the wiki warns the BIOS limit is a safety feature. `amd_pstate=` or `cpufreq.default_governor` are kernel parameters and need their own plan and way back.

## Result

Name the driver and mode, governor or profile, limits, controlling tool and the link to the symptom. Without evidence of a problem, explain the values; if a measurement is missing, name the one read you need.

## Sources

- [ArchWiki: CPU frequency scaling](https://wiki.archlinux.org/title/CPU_frequency_scaling)
- [ArchWiki: Power management](https://wiki.archlinux.org/title/Power_management)
- [powerprofilesctl(1)](https://man.archlinux.org/man/powerprofilesctl.1)
- [cpupower(1)](https://man.archlinux.org/man/cpupower.1)
