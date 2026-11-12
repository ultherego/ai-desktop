# `permissions.toml`: settings reference

File: `~/.config/ai-desktop/permissions.toml`, owned by you, mode `0600`. Template: [`config/permissions.example.toml`](../config/permissions.example.toml). `version` and `[files]` with its `read` and `deny` lists are required (an empty list is allowed); any other missing key means "off" or its default. An unknown key or bad value stops the program and names the problem. Details and examples: [CONFIGURATION.md](CONFIGURATION.md).

## Top level

- `version`: `2` (current: common secrets protected by default, see `protect`) or `1` (older: a fixed built-in list of blocked places instead).

## `[network]`

All `true`/`false`, default `false`.

- `claude`: requests may go to Claude.
- `codex`: requests may go to Codex.
- `local`: requests may go to a model you run yourself on this computer (an OpenAI-compatible server such as llama.cpp, listening on `127.0.0.1`). No model request leaves the computer (weather and downloads, when enabled, still do). Choose it with `ai-desktop agent use local`.
- `weather`: weather from Open-Meteo (sends the place name).
- `web` (Claude only): Claude may search the Internet for a question that needs current information. It is a separate request with your question and the latest exchanges of the conversation (so "the error above" is understood), without files, task memory or Skills. It also lets the model open public pages: the program fetches them itself (HTTPS to public addresses only, no cookies or logins, no JavaScript) and gives the model their text and links. An address from you, from the search sources or from an opened page opens at once; any other address is shown to you first and opens only after `y`.
- `downloads`: the model may ask to download public files (at most 8 at a time). You see the list, with any address that did not come from you, the search or an opened page marked, and approve it once. The program downloads them itself, like a page (HTTPS to public addresses only, no cookies or logins), into a new private folder `~/.local/state/ai-desktop/downloads/<date-time-id>/`. A file appears there only when it arrived whole; its size and SHA-256 are shown. Files are never run and are not executable; an archive is unpacked only when you approve installing it into a `write` scope (plan, consent, undo). There is no size setting: the disk must keep 64 MiB free.

## `[local]`

- `port`: the port of that local server on `127.0.0.1`, default `8080`. Used only when `local = true`.

## `[system]`

- `systemd`: systemd views the model may read: `["system"]`, `["user"]` or both.

## `[files]`: what may be read and written

- `read`: places that may be read, e.g. `"~/"`, `"~/Projects/**"`, `"/etc/"`.
- `write`: places where changes may be saved (each save shows a diff and asks), and where files and folders may be moved or copied (each move or copy shows the plan and asks; the source must also be readable), and where a downloaded archive may be installed. The folder must be yours; a shared disk with mode 0777 is fine.
- `deny`: never read or written, wins over `read` and `write`, e.g. `"~/private/"`. At most 64 rules in each of `read`, `write`, `deny`.
- `protect`: `true` (default) keeps common secrets out even under a broad rule such as `"~/**"`: SSH and GnuPG keys, password stores, cloud and client logins (`.aws`, `.kube`, `.claude`, `.codex`, `gh`, `rclone`, `borg`…), browser and mail profiles, `.netrc`, `.env`, `/etc/ssh`, `/etc/shadow`, Wi-Fi and WireGuard secrets. `/permissions` prints the list. Naming such a place in `read` opens exactly what you named; `false` switches the list off.

Rule forms: `~/x` one file, `~/x/*` files directly in it, `~/x/` or `~/x/**` the whole tree.

The model gets the rules as written, not the files. It also gets the directory you started the program in (e.g. `~/Projects/example`), so that `notes/todo.txt` in a question means a file there; only when a whole-tree `read` rule covers that directory and no `deny` rule or default protection does. Elsewhere it stays on the computer.

Limits (default → highest allowed):

- `file_size`: largest local text file to inspect, `"32MiB"` → `"64MiB"`.
- `part_size`: one portion of a whole-file analysis, `"240KiB"` → `"512KiB"`.
- `read_files`: files in one context, `128` → `512`.
- `read_file_size`: bytes per file sent, `"2MiB"` → `"8MiB"`.
- `read_total_size`: bytes of all files sent, `"4MiB"` → `"16MiB"`.
- `test_copy_files`: files in the local test copy, `2000` → `20000`.
- `test_copy_file_size`: bytes per file in the test copy, `"8MiB"` → `"32MiB"`.
- `test_copy_total_size`: bytes of the whole test copy, `"64MiB"` → `"128MiB"`.
- `listing_entries`: entries in one directory listing, `128` → `512`.
- `discovery_bytes`: listing and search results sent per question, `"128KiB"` → `"256KiB"`.
- `search_scan`: directory entries examined per question, `20000` → `100000`.
- The number of matches in one search and the number of looking-around steps per question are set by the program, not here: the byte budget above decides how much fits. An older file may still hold `search_results` or `discovery_rounds`; they are accepted and have no effect.
- `plan_corrections`: corrected read plans per question, `2` → `4`.
- `command_rounds`: commands run per question, `24` → `128` (`0` = none). Their output still shares `discovery_bytes`.
- `command_output`: output of one command sent to the model, `"8KiB"` → `"128KiB"`. All commands of a question share `discovery_bytes`; when it fills up, no more commands run and the model answers from what it has. With many commands keep this small, e.g. `"16KiB"`.
- `command_timeout`: seconds per command, `20` → `60`.

## `[commands]`: what the model may run

- `preset`: `"diagnostics"` switches on about 150 built-in read-only commands for system, systemd and logs, hardware, disks and Btrfs, network, firewall status, packages, Flatpak, Docker status and offline compositor checks (`niri validate -c ...`, `sway -C -c ...`, `i3 -C -c ...`). Updated with the program; your `allow` adds to it, `deny` removes from it. `ai-desktop commands` lists what is on.
- `allow`: read-only commands, run at once without asking, e.g. `"systemctl status <unit> --no-pager"`. Placeholders: `<unit>` systemd unit, with or without its type (`sshd`, `sshd.service`), `<int>` number, `<word>` one word. Any number of templates.
- `...` at the end: any further plain words after the fixed ones, e.g. `"docker ps ..."` allows `docker ps -a --format=json`. A path among them must be inside `files.read` (and, in `change`, `files.write`) and outside `files.deny`. In `allow`, a template that changes things (`run`, `rm`, `pull`, `build`, `start`, …) cannot end with `...`, and such a word is refused among the open words too (`"docker image ..."` allows `docker image ls`, not `docker image rm`): put changes in `change`.
- `change`: commands that change the system; each needs you to type `yes`, a test (an exact `allow` command, no `...`) and an undo (a `change` command or an exact `allow` command). `...` works here too, e.g. `"docker run --rm ..."`. For `"docker pull ..."` keep `"docker images"` in `allow` as the test and add `"docker image rm <word>"` to `change` as the undo.
- `deny`: commands never run, as word prefixes, e.g. `"docker rm"` blocks every `docker rm …`. It wins over `allow` and `change`. It does not reliably block a flag (`"docker ps -a"` still lets `--all` through), so do not open a template with `...` if some flag must stay off.
- Commands run in a sandbox without your home directory. A path in your home that a command names (`~/` is expanded) and `read` allows is shown to that command read-only, so your compositor's offline check (for example `niri validate -c ~/.config/niri/config.kdl` or `sway -C -c ~/.config/sway/config`) sees its file.
- `share_processes`: `true` lets `allow` commands see host processes and their command lines. Default `false`.
- `host_sockets`: list of extra sockets under `/run` that `allow` commands may reach (at most 8; never `/run/user` or `/run/dbus`, since D-Bus goes through its filter). Default `[]`.

`allow` commands see an empty `/run` except journals, systemd and login state, udev data, mounts, `utmp` and NetworkManager state. D-Bus is available read-only, through a filter. Anything else needs `host_sockets`:

| Socket | For |
|---|---|
| `/run/docker.sock` | `docker` (root-equivalent: list only if you need it) |
| `/run/podman/podman.sock` | `podman` as root (root-equivalent) |
| `/run/containerd/containerd.sock` | `ctr`, `nerdctl` (root-equivalent) |
| `/run/libvirt/libvirt-sock-ro` | `virsh` read-only queries |
| `/run/systemd/resolve/io.systemd.Resolve` | `resolvectl` (also allows DNS lookups) |

`allow` and `change` can also be named groups. They only organise the file; the program sees one list:

```toml
[commands]
host_sockets = ["/run/docker.sock"]
deny = ["docker rm", "docker rmi"]

[commands.allow]
general = ["uptime", "free -h"]
docker = ["docker ps ...", "docker images"]

[commands.change]
docker = ["docker run --rm ..."]
```

The exact path depends on the package; check it with `ls -l /run`.

## Recipes

Each block goes into `~/.config/ai-desktop/permissions.toml`; keep the sections you already have and add the lines. `ai-desktop commands` shows the result.

**Logs (`journalctl`).** The preset already allows `journalctl ...`. Without it, open only what you need:

```toml
[commands.allow]
logs = [
    "journalctl -b -p err --no-pager",            # errors of this boot
    "journalctl -u <unit> -b --no-pager",         # one service: sshd, docker.service
    "journalctl -k -b --no-pager",                # kernel messages
    "journalctl --user -u <unit> -b --no-pager",  # one of your user services
]
```

A pattern after `-g` is one plain word (`-g btrfs`): `|`, `(`, `*` are not allowed, so ask for one pattern per command.

**Docker.** The daemon is reached through its socket; listing it is root-equivalent.

```toml
[commands]
host_sockets = ["/run/docker.sock"]
deny = ["docker rm", "docker rmi"]                # never, even if a template below would allow it

[commands.allow]
docker = ["docker ps ...", "docker images", "docker logs ...", "docker inspect ..."]

[commands.change]                                 # each asks for yes, with a test and an undo
docker = ["docker pull ...", "docker image rm <word>", "docker run --rm ..."]
```

**Podman.** Rootless Podman keeps its data in your home and its socket in `/run/user`, which commands never see. A root Podman service is reached through its socket, if your user may open it (by default only root may):

```toml
[commands]
host_sockets = ["/run/podman/podman.sock"]

[commands.allow]
podman = ["podman --url=unix:///run/podman/podman.sock ps ...",
          "podman --url=unix:///run/podman/podman.sock images"]
```

**Other services with a socket** (for example libvirt): add the socket and the read commands.

```toml
[commands]
host_sockets = ["/run/libvirt/libvirt-sock-ro"]

[commands.allow]
services = ["virsh -r -c qemu:///system list --all"]
```

**Restarting a service.** Commands run as you, without `sudo`: a system service (`systemctl restart sshd`) needs root, so the model gives you the command instead. Your own user services can be changed:

```toml
[commands.allow]
units = ["systemctl --user is-active <unit>"]     # the test after the change

[commands.change]
units = ["systemctl --user restart <unit>"]
```

**Desktop configuration check.** In the preset; without it:

```toml
[commands.allow]
desktop = ["niri validate -c ...", "sway -C -c ...", "i3 -C -c ..."]
```

The path after `-c` must be inside `[files] read`.
