---
name: arch-firewall
description: Diagnoses the host firewall managed with ufw or nftables on Arch Linux. Use for finding which firewall is really active, reading rules and policies, a blocked port, or a safe rule change without losing SSH access.
---

# Firewall diagnostics

## Scope and access

Establish one symptom: an unreachable `PORT`/protocol, unexpected open access, or unclear rules. Establish whether the user works over SSH; a change can cut off the session. Run only reads the policy allows; a denial or empty result does not prove no firewall is active.

## Investigation

1. Establish the framework first. If allowed, check `ufw.service`, `nftables.service` and `iptables.service`. ufw does not work at boot if `iptables.service` is also enabled. `nftables.service` loads `/etc/nftables.conf`.
2. For ufw, if allowed, read `ufw status verbose`: state, default policies and user rules only, not built-in rules or files under `/etc/ufw`. `ufw status numbered` shows order; `ufw show raw` the complete firewall.
3. For nftables, if allowed, read `nft list ruleset` (`-a` handles, `-n` numeric). Note each base chain's `hook`, `priority` and `policy`; tables are usually named after the tool that created them.
4. ufw can use iptables or nftables as back end; the `iptables` package acts on nftables rules (iptables-nft). If allowed, `iptables -S` and `ip6tables -S` show them in iptables syntax. `iptables-legacy` rules are separate objects; `iptables` warns about them.
5. For a blocked port, `ufw show listening` (if allowed) lists listening ports with the rules affecting each, in evaluation order. A service not listening is not a firewall problem.

## Interpretation

- ufw defaults: incoming `deny`, forward `deny`, outgoing `allow`; built-in rules still accept some ICMP, DHCP client, mDNS and UPnP. The first matching rule wins, so specific rules go first; a rule without direction is incoming.
- IPv6: ufw handles it when `IPV6=yes` in `/etc/default/ufw`; `ufw allow 22` then covers both versions. In nftables only `inet` covers both; an `ip` table does not filter IPv6.
- nftables: an `accept` ends only the current base chain; a `drop` (also `policy drop`) ends the whole ruleset and cannot be overridden. Base chains on one hook run by ascending priority.
- Several tools can load rules at once. Docker writes its own iptables rules and ignores ufw ones.

## Change and verification

Change only after separate user consent, with current rules saved: `nft -s list ruleset` is valid input for `nft -f` (variables are lost). Check a file with `nft -c -f FILE`; loading does not flush existing rules unless it starts with `flush ruleset`. `nft flush ruleset` leaves no firewall.

`ufw --dry-run` shows changes without applying them. `ufw enable` flushes its chains and may drop SSH connections; add the SSH rule (e.g. `ufw allow proto tcp from any to any port 22`) first, and do not skip the SSH prompt with `--force` without a way back. A new default policy needs rules migrated by hand. With IPv6 enabled, `ufw delete NUM` of a rule that applies to both versions removes only the numbered one; `ufw delete RULE` removes both. `ufw reset` restores installation defaults.

Persistence: ufw keeps rules in `/etc/ufw/user.rules` and `user6.rules`; nftables keeps only `/etc/nftables.conf` across restarts. Verify with a new connection to `PORT` while the old session stays open, then re-read the rules.

## Result

Name the active framework, default policies, the deciding rule and the evidence. Separate a plan from a change made. If the decisive read is missing, name it.

## Sources

- [ArchWiki: Uncomplicated Firewall](https://wiki.archlinux.org/title/Uncomplicated_Firewall)
- [ArchWiki: nftables](https://wiki.archlinux.org/title/Nftables)
- [ufw(8)](https://man.archlinux.org/man/ufw.8)
- [nft(8)](https://man.archlinux.org/man/nft.8)
