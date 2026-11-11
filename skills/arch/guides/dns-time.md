---
name: dns-time
description: Diagnoses name resolution with systemd-resolved and clock synchronization with systemd-timesyncd on Arch Linux. Use for names that do not resolve, a wrong DNS server in use, or a clock that is off or not NTP-synchronized.
---

# Name resolution and clock synchronization

## Inputs and access

Establish whether names, the clock or both fail; one program or the whole host; one link or all. Run reads only within the policy and never assume a command ran. A sandbox may not see the host network or D-Bus; a failed read there does not prove a host failure.

## Investigation

1. If allowed, read `resolvectl status`: global and per-link servers, domains, DNSSEC and DNS over TLS in effect.
2. Check `/etc/resolv.conf`. In the recommended stub mode it links to `/run/systemd/resolve/stub-resolv.conf` with only the stub `127.0.0.53`; static, uplink and foreign modes also exist. The wiki warns that a wrongly configured file breaks resolution.
3. Run `resolvectl query NAME`: it shows the protocol, the link and whether data is authenticated. Compare a short name with a fully qualified one.
4. For server choice, read `resolvectl domain` and `resolvectl show-server-state`; `resolvectl statistics` shows DNSSEC state.
5. Read existing, allowed configuration: `/etc/systemd/resolved.conf`, `/etc/systemd/resolved.conf.d/`, and per-link DNS from systemd-networkd or NetworkManager. Do not read stored secrets.
6. Clock: `timedatectl status` (`System clock synchronized`, `NTP service`, `RTC in local TZ`), `timedatectl timesync-status`, `timedatectl show-timesync --all`, `/etc/systemd/timesyncd.conf` with `timesyncd.conf.d/`, and an allowed journal excerpt of systemd-timesyncd.

## Interpretation

- Global `Domains=~.` routes all queries to global servers; without it a link setting `~.` can get them. More specific per-link domains still use that link.
- With no servers from the network manager or configuration, resolved uses `FallbackDNS`.
- `DNSOverTLS=yes` fails all queries if the server lacks support; `opportunistic` falls back to plain DNS.
- Arch builds systemd with DNSSEC validation off by default; `DNSSEC=true` fails with servers that lack support, and the wiki reports problems with `allow-downgrade` too.
- `ResolveUnicastSingleLabel=true` sends single-label names to global DNS; the wiki warns of privacy and security risk.
- Synchronization can be slow; wait before calling it a problem. Per-interface NTP servers take precedence, then `NTP=`, then `FallbackNTP=`. An RTC in local time is not fully supported; prefer UTC.

## Change and verification

Changing DNS, domains, DNSSEC, DNS over TLS, `/etc/resolv.conf`, NTP servers or the time zone changes system state. Show current values, the proposed change and the exact restoration first. Prefer a drop-in in `resolved.conf.d/` or `timesyncd.conf.d/`; removing it is the way back. `resolvectl revert LINK` undoes per-link `resolvectl dns`/`domain` changes.

After separate consent, apply one change, repeat `resolvectl status` and `resolvectl query NAME` or `timedatectl timesync-status`, and test the original program.

## Result

Name the first layer with a confirmed problem (resolv.conf mode, server selection, protocol, NTP source or sync), the evidence and limits. Without evidence, give one deciding read instead of a reset.

## Sources

- [ArchWiki: systemd-resolved](https://wiki.archlinux.org/title/Systemd-resolved)
- [ArchWiki: systemd-timesyncd](https://wiki.archlinux.org/title/Systemd-timesyncd)
- [resolvectl(1)](https://man.archlinux.org/man/resolvectl.1)
- [resolved.conf(5)](https://man.archlinux.org/man/resolved.conf.5)
- [timedatectl(1)](https://man.archlinux.org/man/timedatectl.1)
- [timesyncd.conf(5)](https://man.archlinux.org/man/timesyncd.conf.5)
