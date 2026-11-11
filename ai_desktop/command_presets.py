"""Built-in command sets an owner can switch on with one line: `[commands] preset = "diagnostics"`.

Only read templates, checked by the same parser as the owner's own (tests/test_command_presets.py): no program from
the forbidden list, no state-changing verb before an open "...", and for tools whose free arguments could change
something (nft, ufw, smartctl, flatpak, sensors, ss, powerprofilesctl) only a fixed read command, optionally followed
by "...". The set follows the topic guides of the packaged arch and cachyos Skills and is updated with the program;
the owner's own commands.allow adds to it and commands.deny removes from it.
"""
from types import MappingProxyType

DIAGNOSTICS = (
    # general
    "uname ...", "uptime", "nproc", "id", "who", "last -n <int>", "hostnamectl", "localectl", "cat /etc/os-release",
    "timedatectl", "timedatectl status", "timedatectl show", "timedatectl timesync-status", "timedatectl show-timesync --all",
    # processes and memory
    "ps ...", "pstree ...", "top -b -n 1", "vmstat ...", "iostat ...", "free ...", "swapon --show", "zramctl",
    # systemd and logs
    "systemd-analyze ...", "systemctl status <unit> --no-pager", "systemctl is-active <unit>", "systemctl is-enabled <unit>",
    "systemctl is-failed <unit>", "systemctl show <unit>", "systemctl cat <unit>", "systemctl list-units --no-pager",
    "systemctl list-units --type=service --state=running --no-pager", "systemctl list-unit-files --no-pager",
    "systemctl --failed --no-pager", "systemctl list-units --failed --no-pager", "systemctl list-timers --no-pager",
    "systemctl list-sockets --no-pager", "systemctl --user status <unit> --no-pager",
    "systemctl --user list-units --no-pager", "systemctl --user list-timers --no-pager", "journalctl ...", "dmesg ...",
    # hardware
    "lscpu ...", "lspci ...", "lsusb ...", "lsmem ...", "lsmod", "lsblk ...", "sensors", "upower ...",
    "bluetoothctl show", "bluetoothctl devices", "bluetoothctl info <word>",
    "powerprofilesctl get", "powerprofilesctl list", "powerprofilesctl list-holds", "powerprofilesctl query-battery-aware",
    "cpupower frequency-info ...", "cpupower idle-info ...", "scxctl get", "scxctl list", "scxctl modes <word>",
    "chwd --list", "chwd --list-installed", "chwd --list-all",
    # storage
    "df ...", "du ...", "findmnt ...", "fstrim -n ...", "smartctl --scan",
    "btrfs filesystem usage ...", "btrfs filesystem show ...", "btrfs filesystem df ...", "btrfs device stats ...",
    "btrfs subvolume list ...", "btrfs subvolume show ...", "btrfs scrub status ...", "btrfs balance status ...",
    "snapper list ...", "snapper list-configs",
    # The root filesystem and the boot partitions by fixed path: a path among "..." words must lie in files.read, which
    # seldom holds "/" or "/boot". These read only mount and filesystem metadata; /home is left out because the
    # sandbox shows an empty tmpfs there.
    "findmnt /", "findmnt /boot", "findmnt /efi", "findmnt /boot/efi",
    "btrfs filesystem usage /", "btrfs filesystem show /", "btrfs filesystem df /", "btrfs device stats /",
    "btrfs subvolume list /", "btrfs subvolume show /", "btrfs scrub status /", "btrfs balance status /",
    # desktop: offline configuration checks of the compositor (the file must lie in files.read; it is shown read-only)
    "niri validate -c ...", "sway -C -c ...", "i3 -C -c ...",
    "fwupdmgr get-devices ...", "fwupdmgr get-updates ...", "fwupdmgr get-history ...", "fwupdmgr check-reboot-needed ...",
    # network
    "ip ...", "ss -tulpn", "ss -tunap", "ss -s", "nmcli general status", "nmcli device status ...",
    "nmcli connection show ...", "resolvectl status ...", "resolvectl query <word>", "resolvectl dns", "resolvectl domain",
    "resolvectl statistics", "resolvectl show-server-state",
    # firewall and security
    "ufw status", "ufw status verbose", "ufw status numbered", "ufw show listening", "ufw show raw",
    "nft list ...", "nft -a list ruleset", "iptables -S", "ip6tables -S",
    "sshd -t", "sshd -T", "aa-status ...", "aa-enabled",
    # packages
    "pacman -Q ...", "pacman -Qi ...", "pacman -Qs ...", "pacman -Qe ...", "pacman -Qm ...", "pacman -Qu ...",
    "pacman -Qk ...", "pacman -Ql ...", "pacman -Qo ...", "pacman -Qdt", "pacman -Qtdq", "pacman -Si ...",
    "pacman -Ss ...", "pacman -Sl ...", "paccache -d ...", "pacdiff -o",
    "flatpak list ...", "flatpak info ...", "flatpak remotes ...", "flatpak permission-show ...",
    "flatpak documents ...", "flatpak history ...", "flatpak --installations",
    # containers
    "docker ps ...", "docker images", "docker image ls ...", "docker info", "docker inspect ...", "docker logs ...",
    "docker stats --no-stream ...",
)

PRESETS = MappingProxyType({"diagnostics": DIAGNOSTICS})
