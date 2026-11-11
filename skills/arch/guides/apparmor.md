---
name: apparmor
description: Diagnoses AppArmor on Arch Linux: whether it is enabled, loaded profiles and their modes, and denials in the audit or kernel log. Use for a program that fails under a profile or a question about AppArmor state.
---

# AppArmor diagnostics

## Inputs and access

Establish the failing program, what it tried to do and when. Run reads only within the policy and never assume a command ran. `aa-status` needs root to read loaded policy; a denied read is an access limit, not proof that AppArmor is off.

## Investigation

1. If allowed, run `aa-enabled` or read `/sys/kernel/security/lsm`. The wiki enables AppArmor with an `lsm=` kernel parameter listing `apparmor` as the first major module; `apparmor=0` prevents loading.
2. Read `aa-status`: module state, profiles in enforce and complain mode, confined processes. Exit status 1 means not enabled, 2 no policy loaded, 3 no control files, 4 insufficient privileges. `--filter.exe=` narrows to one program.
3. Check that `apparmor.service`, which loads profiles at startup, is enabled.
4. Find the profile in `/etc/apparmor.d/` (path with `/` replaced by `.`, e.g. `usr.bin.NAME`) and its `#include` abstractions.
5. Read denials from the failure time: `apparmor="DENIED"` records with `operation=`, `profile=`, `name=`, `comm=`, `denied_mask=` or `capname=`. With auditd they are in `/var/log/audit/audit.log`; otherwise in the kernel log in the system journal. Read only an allowed excerpt.

## Interpretation

- Tie a denial to the failure time and the same `comm`; a denial from another profile does not explain the symptom.
- In complain mode, accesses are allowed and logged as `apparmor="ALLOWED"`, but `deny` rules are still enforced. Deny rules are not logged by default, so no denial in the log does not rule one out.
- Profiles apply at `exec`. A replaced profile applies at once to processes it confines; a process started unconfined stays unconfined until restarted.
- AppArmor cannot grant more than normal file permissions; a plain permission error may not be AppArmor.
- Much advice concerns Ubuntu's patched kernel. Compare the ABI with `aa-features-abi --extract`; the Arch `dbus` package is built without AppArmor support, so D-Bus rules do not apply.

## Change and verification

Do not disable AppArmor, run `aa-teardown` or set complain mode globally as a first step; the man page warns global complain mode disables all protection. Complain mode gives no security, only auditing.

After separate consent, with the current mode and a copy of the profile recorded, put one profile into complain mode with `aa-complain /etc/apparmor.d/PROFILE`, reproduce the failure and read the `ALLOWED` records. The way back is `aa-enforce` on the same profile. Reload an edited profile with `apparmor_parser -r PROFILE_FILE` or `systemctl reload apparmor`, keeping the original file. Turning off deny audit quieting via `/sys/module/apparmor/parameters/audit` also needs consent.

Verify the mode with `aa-status`, repeat the task, check for new denials, and return the profile to enforce mode once the cause is found.

## Result

Give AppArmor state, the profile and mode, the matching denial or why none is visible, and the smallest change with its rollback. Without logs, name the one read you need.

## Sources

- [ArchWiki: AppArmor](https://wiki.archlinux.org/title/AppArmor)
- [apparmor(7)](https://man.archlinux.org/man/apparmor.7)
- [aa-status(8)](https://man.archlinux.org/man/aa-status.8)
