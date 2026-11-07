# `permissions.toml`: settings reference

File: `~/.config/ai-desktop/permissions.toml`, owned by you, mode `0600`. Template: [`config/permissions.example.toml`](../config/permissions.example.toml). A missing key means "off"; an unknown key or bad value stops the program and names the problem. Details and examples: [README](../README.md#commands-commands).

## Top level

- `version`: `2` (current; `deny` is the only protection for secrets) or `1` (older; adds a built-in list of blocked places).

## `[network]`

All `true`/`false`, default `false`.

- `claude`: requests may go to Claude.
- `codex`: requests may go to Codex.
- `local`: requests may go to a model you run yourself on this computer (an OpenAI-compatible server such as llama.cpp, listening on `127.0.0.1`). Nothing leaves the computer. Choose it with `ai-desktop agent use local`.
- `weather`: weather from Open-Meteo (sends the place name).
- `web`: Claude may search the Internet for a question that needs current information. Only your question is sent, without files, command output or conversation; it is a separate request.

## `[local]`

- `port`: the port of that local server on `127.0.0.1`, default `8080`. Used only when `local = true`.

## `[system]`

- `systemd`: systemd views the model may read: `["system"]`, `["user"]` or both.

## `[files]`: what may be read and written

- `read`: places that may be read, e.g. `"~/"`, `"~/Projects/**"`, `"/etc/"`.
- `write`: places where changes may be saved (each save shows a diff and asks).
- `deny`: never read or written, wins over `read` and `write`, e.g. `"~/.ssh/"`, `"~/.claude/"`, `"~/.codex/"`.

Rule forms: `~/x` one file, `~/x/*` files directly in it, `~/x/` or `~/x/**` the whole tree.

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
- `search_results`: matches in one search, `40` → `200`.
- `search_scan`: directory entries examined per question, `20000` → `100000`.
- `discovery_rounds`: listing/search steps per question, `4` → `8`.
- `plan_corrections`: corrected read plans per question, `2` → `4`.
- `command_rounds`: commands run per question, `24` → `128` (`0` = none). Their output still shares `discovery_bytes`.
- `command_output`: output of one command sent to the model, `"8KiB"` → `"128KiB"`. All commands of a question share `discovery_bytes`; when it fills up, no more commands run and the model answers from what it has. With many commands keep this small, e.g. `"16KiB"`.
- `command_timeout`: seconds per command, `20` → `60`.

## `[commands]`: what the model may run

- `allow`: read-only commands, run at once without asking, e.g. `"systemctl status <unit> --no-pager"`. Placeholders: `<unit>` systemd unit, `<int>` number, `<word>` one word.
- `...` at the end: any further plain words after the fixed ones, e.g. `"docker ps ..."` allows `docker ps -a --format=json`. A path among them must be inside `files.read` (and, in `change`, `files.write`) and outside `files.deny`. In `allow`, a template that changes things (`run`, `rm`, `pull`, `build`, `start`, …) cannot end with `...`, and such a word is refused among the open words too (`"docker image ..."` allows `docker image ls`, not `docker image rm`): put changes in `change`.
- `change`: commands that change the system; each needs you to type `yes`, a test (an exact `allow` command, no `...`) and an undo (a `change` command or an exact `allow` command). `...` works here too, e.g. `"docker run --rm ..."`. For `"docker pull ..."` keep `"docker images"` in `allow` as the test and add `"docker image rm <word>"` to `change` as the undo.
- `deny`: commands never run, as word prefixes, e.g. `"docker rm"` blocks every `docker rm …`. It wins over `allow` and `change`. It does not reliably block a flag (`"docker ps -a"` still lets `--all` through), so do not open a template with `...` if some flag must stay off.
- `share_processes`: `true` lets `allow` commands see host processes and their command lines. Default `false`.
- `host_sockets`: list of extra sockets under `/run` that `allow` commands may reach (at most 8; never `/run/user`). Default `[]`.

`allow` commands see an empty `/run` except journals, systemd and login state, udev data, mounts, `utmp` and NetworkManager state. D-Bus is available read-only, through a filter. Anything else needs `host_sockets`:

| Socket | For |
|---|---|
| `/run/docker.sock` | `docker` (root-equivalent: list only if you need it) |
| `/run/podman/podman.sock` | `podman` as root (root-equivalent) |
| `/run/containerd/containerd.sock` | `ctr`, `nerdctl` (root-equivalent) |
| `/run/example-daemon.sock` | `exampletool` (example service) |
| `/run/libvirt/libvirt-sock-ro` | `virsh` read-only queries |
| `/run/systemd/resolve/io.systemd.Resolve` | `resolvectl` (also allows DNS lookups) |

`allow` and `change` can also be named groups. They only organise the file; the program sees one list:

```toml
[commands]
host_sockets = ["/run/docker.sock", "/run/example-daemon.sock"]
deny = ["docker rm", "docker rmi"]

[commands.allow]
general = ["uptime", "free -h"]
docker = ["docker ps ...", "docker images"]
gpu = ["exampletool status"]

[commands.change]
docker = ["docker run --rm ..."]
```

The exact path depends on the package; check it with `ls -l /run`.
