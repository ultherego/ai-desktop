---
name: package-signing
description: Diagnosing pacman signature errors and the keyring without weakening signature checking.
tools: pacman, archlinux-keyring
source: https://wiki.archlinux.org/title/Pacman/Package_signing
section: Configuring pacman; Managing the keyring; Tips and tricks; Troubleshooting
checked: 2026-11-11
version: rolling
---

# Package signatures and the keyring

Applies to errors such as `signature from "..." is invalid`, `is unknown trust`, `invalid or corrupted package (PGP signature)` or `GPGME error: No data`.

## Read first

- System time: a wrong clock makes keys look expired or invalid.
- `SigLevel` and `LocalFileSigLevel` in `/etc/pacman.conf`, globally and per repository. The default for remote packages is `Required DatabaseOptional` with `TrustedOnly`.
- Installed `archlinux-keyring` version: `pacman -Qi archlinux-keyring`.
- Key details: `pacman-key --list-keys`, `pacman-key --finger KEYID` (the keyring lives in `/etc/pacman.d/gnupg`).
- The `/var/log/pacman.log` excerpt and how long ago the last full upgrade ran.

## Interpretation

Unknown trust usually means a new packager key, an expired key or a replaced master key that the local keyring lacks; an outdated keyring after a long gap is the common cause. Since pacman 7.1, expired known keys are refreshed automatically. `GPGME error: No data` during sync can mean a network login page replaced the downloaded file. A corrupt or unsigned cached package can also fail verification.

## Changes needing consent

Each is a system change requiring the user's consent:
- After a delay, sync and upgrade the keyring first, then the system: `pacman -Sy --needed archlinux-keyring && pacman -Su`. This is not a partial upgrade only because the full upgrade follows at once.
- Refresh keys: `pacman-key --refresh-keys` (needs the network).
- Remove one named corrupt package from `/var/cache/pacman/pkg/` so it is downloaded again.
- Switching network and removing `/var/lib/pacman/sync/*.sig` for the GPGME case.

Removing `/etc/pacman.d/gnupg` and rerunning `pacman-key --init` and `--populate` resets all keys and cannot be undone; propose it only as a last resort with explicit consent. Importing and locally signing an unofficial key (`--recv-keys`, `--lsign-key`) needs a verified fingerprint from its owner.

## Never propose

`SigLevel = Never`, `TrustAll`, or locally signing an untrusted key to silence an error: these break the chain of trust.

## Stop when

Stop at the diagnosis if the time cannot be confirmed, the key's origin is unknown, or the fix would weaken signature checking.
