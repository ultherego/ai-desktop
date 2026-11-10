"""The offline compatibility result of the bound client, remembered for a short time inside the service worker.

Before this, every request launched the client four extra times offline (version and protocol compatibility) before
the login check and the launch that answers. Compatibility is a property of the exact bound client: it is remembered
for that client's fingerprint (path, file identity including its change time, and resource tree), so any change of the
client is a new fingerprint and is checked again. The login status is never remembered: its method is a boundary and
an account can change in another process (the real-systemd CI stage `wrong-auth-method` showed it). Every request
still binds, rechecks and mounts its own sealed client snapshot and runs in a fresh sandbox. Nothing is persisted.
"""
import threading
import time

COMPAT_SECONDS = 15 * 60
MAX_ENTRIES = 16

_lock = threading.Lock()
_compat = {}


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


def clear():
    with _lock:
        _compat.clear()
