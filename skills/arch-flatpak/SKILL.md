---
name: arch-flatpak
description: Diagnoses Flatpak applications, runtimes, remotes and sandbox permissions on Arch Linux. Use when an app cannot see a file or device, for updates, unused runtimes or repair, and to separate sandbox problems from host problems.
---

# Flatpak diagnostics

## Inputs and access

Establish one application (`APP_ID`) and one symptom: no start, a missing file or device, a failed update, or used space. Establish the installation: system-wide (`/var/lib/flatpak/`, the default) or per-user (`~/.local/share/flatpak/`, needs `--user`). Run reads only within the policy; a denial or an empty list for the wrong installation does not mean the app is absent.

## Investigation

1. If allowed, read `flatpak list` and `flatpak remotes`, with and without `--user`; `flatpak --installations` prints system installation paths.
2. Read `flatpak info APP_ID` and `flatpak info --show-permissions APP_ID`: the resources and paths the sandbox allows.
3. The dynamic permission store is separate: `flatpak permission-show APP_ID`. Files granted through the document portal: `flatpak documents`.
4. For updates, `flatpak remote-ls --updates` and `flatpak history`. Do not assume an update ran because a timer exists.
5. For a start failure: `bwrap: ... Permission denied` points to a `noexec` mount of the installation; `Failed to connect to Wayland display` to a missing `wayland` socket permission; desktop actions to a failing host `xdg-desktop-portal`.

## Interpretation

- A file outside the granted `filesystem` permissions, or a device without a `device` permission, is the sandbox working, not a host error; check permissions before host drivers. Paths under `/usr` are reserved and cannot be shared.
- A portal failure is a host problem, not an app permission problem.
- Many apps are not effectively sandboxed by default; report broad grants such as the whole home folder as a finding.
- Granting home access can make an app load older host-side profiles instead of those under `~/.var/app/APP_ID`.

## Change and verification

Every write needs separate user consent. First record the current `--show-permissions` output.

- Permissions: `flatpak override` with one narrow option, e.g. `--filesystem=PATH`, `--nofilesystem=home`, `--device=NAME`, `--nodevice=NAME` (all in `flatpak-override(1)`). Undo: `flatpak override --reset APP_ID`.
- Updates: `flatpak update [APP_ID]`; an update can add permissions, so compare after it. Return a version with `flatpak remote-info --log REMOTE APP_ID` and `flatpak update --commit=COMMIT APP_ID`; `flatpak mask APP_ID` blocks updates, `--remove` lifts it.
- Cleanup: `flatpak uninstall --unused`; `flatpak pin` protects a runtime. `flatpak uninstall --delete-data APP_ID` also deletes `~/.var/app` data and permission store entries and has no undo.
- Repair: `flatpak repair` for the affected installation only.
- Mount options, kernel settings or portal configuration are host changes; route them separately.

Verify by repeating the original action and reading `--show-permissions` again; a successful command alone is no confirmation.

## Result

Give the app, installation, remote, relevant permissions and whether the cause is the sandbox, the host or the app. Separate the plan from changes made; name the one missing read.

## Sources

- [ArchWiki: Flatpak](https://wiki.archlinux.org/title/Flatpak).
- [flatpak(1)](https://man.archlinux.org/man/flatpak.1).
