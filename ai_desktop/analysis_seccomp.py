"""Apply the client's system-call filter only after Bubblewrap has finished setup.

Two parts, both inherited across exec and irreversible:
- mode bits: no SUID/SGID file or directory can be created (systemd's RestrictSUIDSGID; that also blocks openat2,
  which modern Bubblewrap requires, so it is installed here on the native client, not on the trusted setup process);
- kernel interfaces a chat client never needs, refused as a group in the spirit of systemd's SystemCallFilter
  (@clock @cpu-emulation @debug @module @mount @raw-io @reboot @swap @obsolete, plus bpf, keys, namespaces,
  userfaultfd and io_uring). A deny list, not an allow list: a client update that starts using a new ordinary call
  keeps working, while the attack surface these interfaces expose stays closed.
Only libseccomp's public ABI is used; no hand-written syscall numbers or BPF.
"""
import ctypes
import errno
import os
from pathlib import Path
import stat

from .analysis_contract import AnalysisDenied


# Refused with EPERM. Every name must resolve on the native ABI, or the filter is not installed at all.
DENIED = (
    # @clock
    "adjtimex", "clock_adjtime", "clock_settime", "settimeofday",
    # @cpu-emulation
    "modify_ldt",
    # @debug: reading or controlling other processes
    "ptrace", "process_vm_readv", "process_vm_writev", "perf_event_open", "pidfd_getfd", "kcmp", "lookup_dcookie",
    # @module
    "init_module", "finit_module", "delete_module", "create_module", "query_module", "get_kernel_syms",
    # @mount and namespaces
    "mount", "umount2", "mount_setattr", "move_mount", "open_tree", "fsopen", "fsconfig", "fsmount", "fspick",
    "pivot_root", "chroot", "setns", "unshare",
    # @raw-io, @reboot, @swap
    "ioperm", "iopl", "kexec_load", "kexec_file_load", "reboot", "swapon", "swapoff",
    # kernel keys, eBPF, page-fault handling, privileged leftovers
    "bpf", "userfaultfd", "keyctl", "add_key", "request_key", "acct", "quotactl", "quotactl_fd",
    "fanotify_init", "vhangup", "sethostname", "setdomainname", "open_by_handle_at",
    # @obsolete
    "_sysctl", "afs_syscall", "sysfs", "tuxcall", "uselib", "ustat", "vserver", "security",
)
# Refused with ENOSYS, so a runtime that probes for io_uring falls back to ordinary calls.
UNAVAILABLE = ("io_uring_setup", "io_uring_enter", "io_uring_register")


class _Comparison(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


def install_mode_filter():
    """Irreversible, inherited filter; failure prevents native exec or threads."""
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                  if ":" in line)
    if status.get("NoNewPrivs", "").strip() != "1" or len(list(Path("/proc/self/task").iterdir())) != 1:
        raise AnalysisDenied("sandbox")
    lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    pointer = ctypes.c_void_p
    lib.seccomp_init.argtypes, lib.seccomp_init.restype = [ctypes.c_uint32], pointer
    lib.seccomp_release.argtypes, lib.seccomp_release.restype = [pointer], None
    lib.seccomp_load.argtypes, lib.seccomp_load.restype = [pointer], ctypes.c_int
    lib.seccomp_attr_set.argtypes = [pointer, ctypes.c_int, ctypes.c_uint32]
    lib.seccomp_attr_set.restype = ctypes.c_int
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [pointer, ctypes.c_uint32, ctypes.c_int,
                                         ctypes.c_uint, ctypes.POINTER(_Comparison)]
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    context = lib.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW, native ABI only.
    if not context:
        raise AnalysisDenied("sandbox")
    try:
        # Kill incompatible ABIs; atomically synchronize any concurrent thread.
        if (lib.seccomp_attr_set(context, 2, 0x80000000) != 0
                or lib.seccomp_attr_set(context, 4, 1) != 0):
            raise AnalysisDenied("sandbox")

        def deny(name, error, comparisons=()):
            number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
            array = (_Comparison * len(comparisons))(*(
                _Comparison(arg, 7, mask, value) for arg, mask, value in comparisons))
            if number < 0 or lib.seccomp_rule_add_array(
                    context, 0x00050000 | error, number, len(array), array) != 0:
                raise AnalysisDenied("sandbox")

        # Match both chmod and creation, including O_TMPFILE (no O_CREAT bit).
        modes = (("chmod", 1), ("fchmod", 1), ("fchmodat", 2), ("fchmodat2", 2),
                 ("mkdir", 1), ("mkdirat", 2), ("mknod", 1), ("mknodat", 2), ("creat", 1))
        for bit in (stat.S_ISUID, stat.S_ISGID):
            for name, arg in modes:
                deny(name, errno.EPERM, ((arg, bit, bit),))
            for name, arg in (("open", 1), ("openat", 2)):
                for flag in (os.O_CREAT, os.O_TMPFILE):
                    deny(name, errno.EPERM, ((arg, flag, flag), (arg + 1, bit, bit)))
        # seccomp cannot inspect struct open_how; force clients onto the filtered
        # open/openat ABI, as systemd's RestrictSUIDSGID does. Setup already ended.
        deny("openat2", errno.ENOSYS)
        for name in DENIED:
            deny(name, errno.EPERM)
        for name in UNAVAILABLE:
            deny(name, errno.ENOSYS)
        if lib.seccomp_load(context) != 0:
            raise AnalysisDenied("sandbox")
    finally:
        lib.seccomp_release(context)
