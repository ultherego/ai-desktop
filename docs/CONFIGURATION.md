# Configuration: `permissions.toml`

## First run and permissions

Every setting in one short list: [PERMISSIONS.md](PERMISSIONS.md), including grouped command lists, templates that end with `...` and `[commands] deny`.

`ai-desktop ask --preview` shows the access without the model and without reading data. Without a `permissions.toml` file only general questions work: no files, systemd, commands, weather or web search.

Example policy (`config/permissions.example.toml` is the full template):

```toml
version = 2

[network]
claude = true
codex = false
weather = true       # Open-Meteo
web = false          # web search through Claude

[system]
systemd = ["system", "user"]

[files]
read = ["~/Projects/my-project/"]
write = ["~/Projects/my-project/"]
deny = ["~/.ssh/", "~/.gnupg/", "~/.claude/", "~/.config/ai-desktop/"]

[commands]
allow = ["uptime", "systemctl status <unit> --no-pager", "journalctl -b -p err --no-pager -n <int>"]
```

How to read this file:

- A missing field means refusal, including `network.claude`. An unknown key or a bad value stops the program, and the message points to the problem.
- The file must belong to you and have mode `0600`. Its directories must not be links or writable by others. The limit is 256 KiB and 64 rules per file list. Command templates have no count limit; the file size bounds them. All of them, with the file scopes, go to the model with every question, so a very long list makes every request longer.
- `~/x` is one file, `~/x/*` is the files directly in the directory, and `~/x/` and `~/x/**` are the directory with its subdirectories. `~/` is the whole home directory. Two rules with the same scope (`~/` and `~/**`) are an error.
- **`deny` wins and protects everything under the given path**, regardless of how it is written: `~/x`, `~/x/`, `~/x/*` and `~/x/**` work the same.
- In `version = 2` the program **protects common secrets by default** (`files.protect = true`), even under a broad rule such as `~/**`: `~/.ssh`, `~/.gnupg`, `~/.password-store`, `~/.aws`, `~/.azure`, `~/.kube`, `~/.docker`, `~/.claude`, `~/.codex`, `~/.config/gh`, `~/.config/gcloud`, `~/.config/rclone`, `~/.local/share/keyrings`, `~/.config/borg`, `~/.config/borgmatic`, browser and mail profiles (`~/.mozilla`, `~/.librewolf`, `~/.thunderbird`, `~/.config/chromium`, `~/.config/google-chrome`, `~/.config/BraveSoftware`, `~/.config/vivaldi`, `~/.config/opera`, `~/.config/microsoft-edge`), AI Desktop's own `~/.config/ai-desktop`, `~/.local/state/ai-desktop` and `~/.local/share/ai-desktop`, the files `~/.netrc`, `~/.git-credentials`, `~/.npmrc`, `~/.pypirc`, `~/.env`, and `/etc/ssh`, `/etc/shadow`, `/etc/gshadow`, `/etc/NetworkManager/system-connections`, `/etc/wireguard`, `/etc/borgmatic`, `/etc/borgmatic.d`. `/permissions` in a conversation prints the list in force. A `read` rule that names one of these locations, or something inside it, lifts the protection for exactly what it names (`"~/.ssh/config"` makes that file readable, the keys beside it stay protected; `"~/.ssh/**"` lifts the whole directory). `protect = false` switches the list off; then `deny` alone protects secrets. The list does not recognise every secret: add your own with `deny`. The older `version = 1` format keeps its own built-in exclusions.
- Editing an existing file requires `read` and `write`. A new file requires `write` and an existing directory: the program does not create directories. Deleting requires `write`.


## Optional limits

The fields below are optional. Add them in the given section when you want to change the default value. The program rejects out-of-range values.

| Section and field | Default | At most | Meaning |
|---|---|---|---|
| `[files] file_size` | `"32MiB"` | `"64MiB"` | largest locally inspected text file |
| `[files] part_size` | `"240KiB"` | `"512KiB"` | portion when analyzing a whole file |
| `[files] read_files` | 128 | 512 | files in one context |
| `[files] read_file_size` | `"2MiB"` | `"8MiB"` | bytes per file |
| `[files] read_total_size` | `"4MiB"` | `"16MiB"` | bytes of all context files |
| `[files] test_copy_files` | 2000 | 20000 | files in the local test copy |
| `[files] test_copy_file_size` | `"8MiB"` | `"32MiB"` | local copy bytes per file |
| `[files] test_copy_total_size` | `"64MiB"` | `"128MiB"` | local copy total, independent of model data |
| `[files] listing_entries` | 128 | 512 | entries in a list/find step |
| `[files] discovery_bytes` | `"128KiB"` | `"256KiB"` | bytes of discovery steps sent to the model in one question |
| `[files] search_results` | 40 | 200 | matches in a find step |
| `[files] search_scan` | 20000 | 100000 | directory entries per question |
| `[files] discovery_rounds` | 4 | 8 | list/find steps per question |
| `[files] plan_corrections` | 2 | 4 | read plan corrections per question |
| `[files] command_rounds` | 24 | 128 | commands per question (0 turns them off) |
| `[files] command_output` | `"8KiB"` | `"128KiB"` | bytes of the output of one command |
| `[files] command_timeout` | 20 | 60 | seconds per command |
| `[commands] share_processes` | `false` | | `true`: commands see host processes and their command lines |
| `[commands] host_sockets` | `[]` | 8 | extra sockets under `/run` that commands may reach, e.g. `"/run/docker.sock"` for `docker` templates |
| `[commands] allow` | none | 128 | read templates, run without asking; a list or named groups |
| `[commands] change` | none | 128 | change templates: they run on the live system and require `yes`, a test (an exact `allow` template) and an undo |
| `[commands] deny` | `[]` | | commands never run, as word prefixes, e.g. `"docker rm"`; wins over `allow` and `change` |

`read_file_size` must not exceed `read_total_size`; the same rule applies to the two test-copy byte settings. Existing explicitly configured smaller values remain in force. The wire ceiling is 34 MiB of serialized context and 36 MiB for a complete request, including JSON escaping. These local ceilings do not guarantee that a selected model accepts that much text. Prefer content search and range reads for large files; selected excerpts carry source hashes and coverage. Up to 64 files can change in one proposal, bounded by 8 MiB per resulting file and 16 MiB combined. Anchored replacements let the model change a small part of a large file without generating its entire replacement. A file larger than `read_file_size` (up to `file_size` and 8 MiB) can still be edited in `ai-desktop` and `ai-desktop project`: the model first searches or reads the part to change, the edit request then carries only those excerpts, and its replacements must be copied from them; the whole file is changed locally, tested and shown as a full diff before the usual save question. A `find` with kind `text` lists the files of a directory whose contents contain a phrase (names only), reading at most 64 MiB locally and each file within `file_size`. Test-copy-only files are never attached to the model request.

## Commands: `[commands]`

`preset = "diagnostics"` (not in the example file: you switch it on yourself) switches on a built-in set of read-only commands for the topics of the `arch` and `cachyos` Skills (about 140 templates: system, systemd and logs, hardware, storage and Btrfs, network and DNS, firewall status, packages and Flatpak, Docker status). It is updated with the program, so you do not need to know each command or flag: a safe tool takes any arguments (`journalctl ...`), and a tool whose free arguments could change something is limited to its read commands (`nft list ...`, `ufw status`). Your own `allow` list adds to it, and `commands.deny` removes commands from it. The preset never contains a command that changes the system; those stay in your `change` list, which asks every time. It does read logs (`journalctl`, `dmesg`), process lists, network and device names and package lists, and their output goes to the model with your questions: that is why it is off until you add the line.

`ai-desktop commands` lists every command your file allows, read (runs without asking) or change (asks every time), whether its program is installed and which package and version own it; `ai-desktop commands --json` gives the same list for scripts. It runs nothing but a local `pacman -Qo` query.

By default the model runs nothing. `allow` is a list of templates, without a shell. `<unit>` means one systemd unit name, with or without its type (`sshd` or `sshd.service`, as `systemctl` takes it), `<int>` a number up to 99999, and `<word>` one simple word. A template may end with `...`: then it also accepts any further plain words, so `"docker ps ..."` allows `docker ps -a --format=json`. A path among those words must be inside `files.read` and outside `files.deny`. In `allow`, a template with a word that changes things (`run`, `rm`, `pull`, `start`, …) cannot end with `...`, and such a word is refused among the open words too; put those in `change`. Templates starting with a shell, an interpreter, `sudo`, a network client or a writing program (`rm`, `tee`, `git`, `find`, `sed` and similar) are rejected when the policy is loaded.

For Docker, Btrfs and nmcli, give the complete command path before a placeholder, for example `docker container inspect <word>`. Spell out unknown subcommands and plugin arguments literally. Docker and similar daemons are reachable only after you list their socket in `host_sockets`, for example `host_sockets = ["/run/docker.sock"]`; review the operation itself before adding it to `allow`, because the Docker socket is root-equivalent.

**Commands from `allow` run without asking, and their output goes to the model.** Remove from the list what you do not want to share, including any extra `journalctl`, `ps aux` or `docker ps` rules you add. `deny` does not filter command output.

Commands from `allow` run in a sandbox: read-only file system, hidden `/home` and `/root`, an empty `/run` apart from plain state files, D-Bus only for reading (through `xdg-dbus-proxy`), no network and no input. Still list only commands that change nothing. Some programs will not see the host state (e.g. `ip`, `ss`, `wpctl`, `glxinfo`), and some need root (e.g. `snapper list`, `btrfs scrub status`).

A long list can be split into named groups. They only organise the file; the program sees one list. `deny` blocks whole commands by their first words and wins over both lists:

```toml
[commands]
host_sockets = ["/run/docker.sock"]
deny = ["docker rm", "docker rmi"]

[commands.allow]
system = ["uptime", "free -h"]
docker = ["docker ps ...", "docker images"]

[commands.change]
docker = ["docker run --rm ...", "docker pull ...", "docker image rm <word>"]
```

Every setting with one line on what it does is in [PERMISSIONS.md](PERMISSIONS.md).

A placeholder must not stand where a tool's command could be. Write an option that takes a value together with it (`--output=json`) or give the command further on: `systemctl -H host restart <unit>` is valid, and `systemctl -H host <word>` is not.

`change` holds commands that change the system, e.g.:

```toml
[commands]
allow = ["systemctl status <unit> --no-pager"]
change = ["systemctl restart <unit>"]
```

The model must give the change together with a test and an undo. The test is a read command matching an `allow` template exactly (one ending in `...` does not count); the undo matches the `change` list or an exact `allow` template. A refusal says which of them is missing, both to you and to the model. So that `docker pull ...` can run, keep `"docker images"` in `allow` as its test and add `"docker image rm <word>"` to `change` as its undo. You see all three and type the whole word `yes`. The program runs the change, then the test. When either fails, it shows the undo and again requires `yes`: it never undoes anything by itself. **The change, test and undo run on the live system, with your permissions, without a sandbox**, with a cleaned environment, no input and a time limit.

## Which commands are in the template

The `allow` groups in the template (`system`, `systemd`, `hardware`, `packages`) are general commands that work in the sandbox on any system and do not send logs, network names or login data. They run without asking, and their output goes to the model. Deliberately not in it:

- `journalctl` with log content, `dmesg`, `who`, `last`, `id`: logs and data about users;
- `nmcli`, `bluetoothctl`: network and device names;
- `docker`: reaches the daemon on the host despite the sandbox;
- `ip`, `ss`, `ps`, `top`, `df`, `findmnt`: in the sandbox they show its view, not the system state;
- `systemctl --user`, `pactl`, `wpctl`, `glxinfo`: they have no access to the user session.

You can add them on your own system if you consciously want to. A program not installed on the system is skipped.
