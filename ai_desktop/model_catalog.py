"""Model choices reported by the system Claude and Codex clients themselves, read in a closed sandbox.

Claude: the client's answer to the SDK `initialize` request (its own /model list: aliases with the model each one is
now, e.g. "Opus 5.5"). Codex: below.

The catalog is the client's own (`model/list` of its app-server), so it follows the client instead of a list baked into
this program. The query needs no account: the client runs with an empty private HOME, every namespace unshared (no
network), no credentials, no user data and no tools, and is killed as soon as it answers. The result is a convenience for
menus, never authority: a chosen id is still shape-checked and the client alone decides whether it accepts it.
"""
import json
import os
import math
from pathlib import Path
import tempfile
import time
import sys
import unicodedata

from .analysis_contract import AnalysisDenied
from .analysis_sandbox import _identity_files
from . import client_binding, codex_runtime
from .process_io import bounded_process
from .execution import CODEX_MODEL, MODELS

MAX_ENTRIES = 64
MAX_LABEL = 64
MAX_OUTPUT = 256 * 1024
TIMEOUT = 20.0


class CatalogUnavailable(Exception):
    """No usable catalog; callers fall back to automatic selection and free-text ids."""


def _label(value, fallback):
    if (type(value) is not str or not value.strip() or len(value) > MAX_LABEL
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") for char in value)):
        return fallback
    return value.strip()


def parse(response):
    """Validate one `model/list` result into ((id, label), ...). Anything unexpected makes the catalog unavailable."""
    data = response.get("data") if type(response) is dict else None
    if type(data) is not list or not data:
        raise CatalogUnavailable()
    seen, entries = set(), []
    for item in data[:MAX_ENTRIES]:
        if type(item) is not dict:
            raise CatalogUnavailable()
        ident = item.get("id")
        if (type(ident) is not str or not CODEX_MODEL.fullmatch(ident) or ident in MODELS or ident in seen
                or item.get("hidden") is True):
            continue
        seen.add(ident)
        entries.append((ident, _label(item.get("displayName"), ident)))
    if not entries:
        raise CatalogUnavailable()
    return tuple(entries)


def fetch(*, run=None, timeout=TIMEOUT):
    """Read a compatible bound system client's catalog, or CatalogUnavailable."""
    if run is None and (os.environ.get("AI_DESKTOP_MODEL_CATALOG") == "off"
                         or "unittest" in sys.modules and os.environ.get("AI_DESKTOP_MODEL_CATALOG") != "on"):
        raise CatalogUnavailable()          # tests and CI must never start the real client
    try:
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= TIMEOUT):
            raise CatalogUnavailable()
        codex_runtime.runtime_ready()
        with client_binding.bind("codex") as binding, tempfile.TemporaryDirectory(prefix="ai-desktop-models-") as temp:
            # The protocol budget starts after the initial immutable binding.
            # Filesystem operations themselves cannot be interrupted here.
            deadline = time.monotonic() + timeout
            def remaining():
                value = deadline - time.monotonic()
                if value <= 0:
                    raise CatalogUnavailable()
                return value
            directory = Path(temp)
            _identity_files(directory)
            codex_runtime.compatibility(directory, binding, timeout=remaining())
            command = codex_runtime.namespace_command("catalog", directory, binding=binding)
            code, output, errors = (run or bounded_process)(command,
                env={"PATH": "/usr/bin", "LANG": "C.UTF-8"}, pass_fds=binding.pass_fds,
                timeout=remaining(), limit=MAX_OUTPUT)
            if code:
                raise CatalogUnavailable()
            binding.recheck()
            result = parse(json.loads(output))
            remaining()
            return result
    except CatalogUnavailable:
        raise
    except (AnalysisDenied, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise CatalogUnavailable() from None


CLAUDE_REQUEST = b'{"type":"control_request","request_id":"ai-desktop-models","request":{"subtype":"initialize"}}\n'
_PRICE = __import__("re").compile(r"\s*\$[^·]*per Mtok.*$")


def parse_claude(output):
    """((alias, label, note), ...) from the client's stream: only the control response to our request, only aliases this
    program accepts, bounded plain labels. Anything else makes the catalog unavailable."""
    response = None
    for line in output.splitlines()[:64]:
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if (type(value) is dict and value.get("type") == "control_response" and type(value.get("response")) is dict
                and value["response"].get("request_id") == "ai-desktop-models"
                and value["response"].get("subtype") == "success"):
            response = value["response"].get("response")
            break
    models = response.get("models") if type(response) is dict else None
    if type(models) is not list or not models:
        raise CatalogUnavailable()
    seen, entries = set(), []
    for item in models[:MAX_ENTRIES]:
        if type(item) is not dict or type(item.get("value")) is not str:
            raise CatalogUnavailable()
        alias = item["value"]
        if alias not in MODELS or alias in seen:
            continue                                   # this program sends only the client's own aliases
        seen.add(alias)
        raw = item.get("description")
        description = _PRICE.sub("", raw).strip(" ·") if type(raw) is str and len(raw) <= 8 * MAX_LABEL else ""
        if any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") for char in description):
            description = ""
        name = _label(item.get("displayName"), alias)
        if alias == "default":
            label, note = name, description.replace("Use the default model ", "").strip("() ")
        else:
            head, _, rest = description.partition(" · ")
            label, note = (head, rest) if head.lower().startswith(name.lower()) else (name, description)
        entries.append((alias, _label(label, alias), _label(note, "") if len(note) <= MAX_LABEL else ""))
    if not entries:
        raise CatalogUnavailable()
    return tuple(entries)


def fetch_claude(*, run=None, timeout=TIMEOUT):
    """The bound Claude client's own model list, read offline with no account, or CatalogUnavailable."""
    if run is None and (os.environ.get("AI_DESKTOP_MODEL_CATALOG") == "off"
                         or "unittest" in sys.modules and os.environ.get("AI_DESKTOP_MODEL_CATALOG") != "on"):
        raise CatalogUnavailable()          # tests and CI must never start the real client
    from . import analysis_sandbox
    try:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= TIMEOUT:
            raise CatalogUnavailable()
        analysis_sandbox.runtime_ready()
        with client_binding.bind("claude") as bound, tempfile.TemporaryDirectory(prefix="ai-desktop-models-") as temp:
            deadline = time.monotonic() + timeout
            directory = Path(temp)
            _identity_files(directory)
            analysis_sandbox._check_client(bound, directory)
            left = deadline - time.monotonic()
            if left <= 0:
                raise CatalogUnavailable()
            command = analysis_sandbox.namespace_command("catalog", directory, online=False, bound=bound)
            code, output, _ = (run or bounded_process)(command, env={"PATH": "/usr/bin", "LANG": "C.UTF-8"},
                                                       pass_fds=bound.pass_fds, input_bytes=CLAUDE_REQUEST,
                                                       timeout=left, limit=MAX_OUTPUT)
            bound.recheck()
            if code:
                raise CatalogUnavailable()
            return parse_claude(output)
    except CatalogUnavailable:
        raise
    except (AnalysisDenied, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise CatalogUnavailable() from None
