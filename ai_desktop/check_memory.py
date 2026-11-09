"""Results of the per-request client checks, remembered for a short time inside the service worker.

Before this, every request launched the client four extra times offline (version and protocol compatibility) and
once online (login status) before the one launch that answers. Compatibility is a property of the exact bound client:
it is remembered for that client's fingerprint (path, file identity including its change time, and resource tree), so
any change of the client is a new fingerprint and is checked again. A login status is remembered for a short time per
account and client, and forgotten as soon as any request of that account reports a login failure. Every request still
binds, rechecks and mounts its own sealed client snapshot and runs in a fresh sandbox; only repeated observations are
skipped. Nothing here is persisted: a new worker starts empty.
"""
import threading
import time

COMPAT_SECONDS = 15 * 60
STATUS_SECONDS = 2 * 60
MAX_ENTRIES = 16

_lock = threading.Lock()
_compat = {}
_status = {}


def _get(store, key, seconds, clock):
    with _lock:
        found = store.get(key)
        if found is None:
            return None
        value, when = found
        if clock() - when >= seconds or clock() < when:
            del store[key]
            return None
        return value


def _put(store, key, value, clock):
    with _lock:
        if key not in store and len(store) >= MAX_ENTRIES:
            del store[min(store, key=lambda name: store[name][1])]          # the oldest goes
        store[key] = (value, clock())


def compatible(fingerprint, check, *, clock=time.monotonic):
    """The compatibility result for this exact client, from `check()` when not remembered."""
    if type(fingerprint) is not str:
        return check()
    found = _get(_compat, fingerprint, COMPAT_SECONDS, clock)
    if found is not None:
        return found
    result = check()
    if result is not None:
        _put(_compat, fingerprint, result, clock)
    return result


def signed_in(key, check, *, clock=time.monotonic):
    """The login status result (e.g. the plan) for this account and client, from `check()` when not remembered.
    A failure raised by `check()` is never remembered."""
    if type(key) is not tuple or len(key) != 2 or not all(type(part) is str for part in key):
        return check()                         # no exact client identity: nothing is remembered
    found = _get(_status, key, STATUS_SECONDS, clock)
    if found is not None:
        return found[0]
    result = check()
    _put(_status, key, (result,), clock)
    return result


def forget_account(account):
    """Any login problem of this account: its next request checks the status again."""
    with _lock:
        for key in [key for key in _status if key[0] == account]:
            del _status[key]


def clear():
    with _lock:
        _compat.clear()
        _status.clear()
