"""Identity check of the dedicated, non-login ai-desktop service account.

Only account database metadata and this process's credentials are read; no
attempt is made to open the account HOME, OAuth state or any socket.
"""
import os
import pwd

from . import e2_account


def _worker_identity():
    # /var is hidden by the service, including the entire OAuth home. Only
    # account database metadata is needed; there is no attempt to open HOME.
    user = pwd.getpwnam(e2_account.ACCOUNT)
    return (user.pw_name == e2_account.ACCOUNT
            and user.pw_uid > 0 and user.pw_gid > 0
            and os.getuid() == user.pw_uid == os.geteuid()
            and os.getgid() == user.pw_gid == os.getegid()
            and set(os.getgroups()) <= {user.pw_gid}
            and user.pw_dir == e2_account.HOME
            and user.pw_shell in e2_account._NOLOGIN)
