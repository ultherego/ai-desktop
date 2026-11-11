---
name: ssh
description: Diagnoses OpenSSH client and server problems on Arch Linux. Use for key login failures, "Permission denied (publickey)", a changed host key, sshd configuration and drop-ins, or hardening without locking yourself out.
---

# OpenSSH diagnostics

## Scope and access

Establish the side: client login, server refusal or unreachability, a host key warning, or planned hardening. Establish whether the user works over SSH now; a server change can cut off the only session. Run only reads the policy allows. Never read or ask for private keys (`~/.ssh/id_*` without `.pub`).

## Investigation

1. Client side: if allowed, run `ssh -v -p PORT USER@HOST` and note the keys offered and methods listed. Client options come from `~/.ssh/config`, `/etc/ssh/ssh_config` and `/etc/ssh/ssh_config.d/*.conf`; a `Host` block can change user, port or key.
2. Server: `/etc/ssh/sshd_config` plus drop-ins `/etc/ssh/sshd_config.d/*.conf` read through `Include`. If allowed, run `sshd -t`; a valid configuration prints nothing. `sshd -T` (extended test mode) also prints the effective configuration after all files are read; `-C` applies the `Match` rules for given connection parameters.
3. For most keywords the first obtained value is used; `Include` reads its files where it appears, in lexical order, so the file that sets a keyword first wins. A satisfied `Match` block overrides the global section.
4. For "Permission denied (publickey)", check on the server: the public key is in `~/.ssh/authorized_keys` of `USER` (default `AuthorizedKeysFile` is `.ssh/authorized_keys .ssh/authorized_keys2`); `PubkeyAuthentication` is not `no`; `AllowUsers`, `AllowGroups` or `AuthenticationMethods` do not exclude the user.
5. On both sides `~/.ssh` and its contents must be accessible only by the user, the home directory writable only by the user; `StrictModes` (default `yes`) checks this.
6. If allowed, read the server journal for the attempt. For an unreachable server check that sshd listens (`ss --tcp --listening`) and no firewall drops the port.

## Interpretation

- Defaults: `PasswordAuthentication yes` (fallback to passwords), `PermitRootLogin prohibit-password`.
- `REMOTE HOST IDENTIFICATION HAS CHANGED` means the key differs from `known_hosts`; treat it as unverified until the operator confirms the new key through an authenticated channel. `ssh-keygen -F HOST` lists stored entries; `ssh-keygen -l -f FILE` shows a fingerprint.
- `Connection closed ... [preauth]` in the server log points to a missing valid `HostKey`. `LogLevel DEBUG` violates users' privacy; use it briefly.

## Change and verification

Change only after separate user consent, with the previous file preserved and the restore step stated. Prefer a new drop-in in `/etc/ssh/sshd_config.d/`; check that no earlier file sets the same keyword.

Hardening such as `PasswordAuthentication no` with `AuthenticationMethods publickey`, or `PermitRootLogin no`, locks out every account without a working key in `authorized_keys`; confirm key login for each needed account first. Run `sshd -t` before restarting `sshd.service`; on failure do not restart.

Keep the current session open. After the restart, confirm in a second session that login works and the intended method is refused; only then close the first. If it fails, restore from the open session.

For a confirmed host key change, remove the old entry with `ssh-keygen -R HOST` and accept the verified key on the next connection.

## Result

Name the failing stage (network, host key, authentication, account restriction, permissions), the responsible option or file and the evidence. Separate a plan from a change made. If the decisive read is missing, name it.

## Sources

- [ArchWiki: OpenSSH](https://wiki.archlinux.org/title/OpenSSH)
- [sshd_config(5)](https://man.archlinux.org/man/sshd_config.5)
- [sshd(8)](https://man.archlinux.org/man/sshd.8)
- [ssh-keygen(1)](https://man.archlinux.org/man/ssh-keygen.1)
