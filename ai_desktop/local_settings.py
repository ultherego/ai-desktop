"""Small local choices of the user in ~/.config/ai-desktop (no data, no authority).

`save`: whether interactive conversations save themselves ("on", the default, or "off"). With "off" a conversation is
saved only when asked for with `--session new`. `weather-city`: the place used when a weather question names none
(`ai-desktop settings weather-city`). Reads never follow a link and fall back to the default; writes are atomic,
private (0600) and never through a link.
"""
import os
import pwd
import secrets

SAVE_VALUES = ("on", "off")


def _directory(home=None):
    base = home if home is not None else pwd.getpwuid(os.geteuid()).pw_dir
    return os.path.join(base, ".config", "ai-desktop")


def _read(name, allowed, default, home=None):
    try:
        descriptor = os.open(os.path.join(_directory(home), name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            value = os.read(descriptor, 64).decode("ascii").strip()
        finally:
            os.close(descriptor)
    except (OSError, ValueError):
        return default
    return value if value in allowed else default


def _read_text(name, home=None, limit=128):
    try:
        descriptor = os.open(os.path.join(_directory(home), name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            value = os.read(descriptor, limit + 1)
        finally:
            os.close(descriptor)
        if len(value) > limit:
            return None
        return value.decode("utf-8").strip() or None
    except (OSError, ValueError):
        return None


def _write(name, value, home=None):
    directory = _directory(home)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if os.path.islink(directory):
        raise OSError("settings directory is a link")
    temporary = os.path.join(directory, ".%s.%s" % (name, secrets.token_hex(8)))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.write(descriptor, (value + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, os.path.join(directory, name))
    except OSError:
        os.unlink(temporary)
        raise


def saving(home=None):
    """True unless the user turned automatic saving off (`ai-desktop settings save off`)."""
    import sys
    if home is None and "unittest" in sys.modules and os.environ.get("AI_DESKTOP_LOCAL_SETTINGS") != "on":
        return True                    # like the model catalog: tests never read this computer's own settings
    return _read("save", SAVE_VALUES, "on", home) == "on"


def set_saving(enabled, home=None):
    _write("save", "on" if enabled else "off", home)


def weather_city(home=None):
    """The remembered place for weather questions that name none, or None. Never guessed (no GeoClue or IP lookup)."""
    import sys
    if home is None and "unittest" in sys.modules and os.environ.get("AI_DESKTOP_LOCAL_SETTINGS") != "on":
        return None
    from .weather_context import WeatherError, validate_city
    value = _read_text("weather-city", home)
    try:
        return validate_city(value) if value is not None else None
    except WeatherError:
        return None


def set_weather_city(city, home=None):
    """Remember a place (validated like a typed one) or forget it with None."""
    if city is None:
        try:
            os.unlink(os.path.join(_directory(home), "weather-city"))
        except FileNotFoundError:
            pass
        return
    from .weather_context import validate_city
    _write("weather-city", validate_city(city), home)
