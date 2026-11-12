"""Closed, bounded shape of the local discovery observations sent to the model during one question.

List/find discovery is metadata only; explicit content operations add bounded,
source-hashed excerpts. Names are untrusted observations, never source
authority; every content read still goes through the normal file-policy boundary. The client and the worker both
validate this exact shape against the ceilings below (the owner's budgets in permissions.toml can only be lower or equal).
"""
import json
import re
import unicodedata

MAX_SKILL_ROUNDS = 3
MAX_REUSED_SKILLS = 4
MAX_COMMAND_ROUNDS = 128      # ceiling of [files] command_rounds; output stays bounded by discovery_bytes
MAX_STEPS = 8 + 4 + MAX_COMMAND_ROUNDS + MAX_SKILL_ROUNDS * 4 + MAX_REUSED_SKILLS + 1   # + one web answer
MAX_WEB_TEXT = 32 * 1024      # the checked web answer with its source list
# A page step: opened ("ok"); the user declined a model-composed address ("declined"); the broker refused the address
# (not https, credentials, a private or local host: "refused") or could not read it ("failed").
PAGE_STATUSES = ("ok", "declined", "refused", "failed")
DOWNLOAD_STATUSES = ("ok", "declined", "refused", "failed", "cancelled")
ARCHIVE_KEYS = ("op", "archive", "status", "reason", "format", "files", "folders", "bytes", "top", "top_count",
                "executables", "examples")
MAX_DIRECTORIES = 4            # directories in one list step
CEILING_ENTRIES = 512          # entries of one list/find step
CEILING_TOTAL_ENTRIES = 1024   # entries of the whole document
MAX_DISCOVERY_ROUNDS = 32      # = permissions.MAX_DISCOVERY_ROUNDS (an internal bound, not a setting)
CEILING_DIAGNOSTIC_BYTES = 256 * 1024
CEILING_SKILL_BYTES = 192 * 1024
CEILING_BYTES = CEILING_DIAGNOSTIC_BYTES + CEILING_SKILL_BYTES
MAX_QUERIES = 4
MIN_QUERY_CHARS = 2
MAX_QUERY_BYTES = 64
MAX_PATH_BYTES = 512
MAX_SELECTORS = 4
KINDS = ("any", "file", "directory", "text")   # text: files whose contents contain a query (names only)
TEXT_SCAN_BYTES = 64 * 1024 * 1024              # contents one text find may read locally; never sent
ENTRY_TYPES = ("file", "directory", "alias")
RUN_STATUSES = ("ok", "failed", "timeout", "declined", "not-allowed", "unavailable", "missing")
RUN_ROLES = ("read", "change", "test", "undo")
CEILING_OUTPUT = 131072
# Recoverable selection problems: the model may correct its plan (once per correction budget). Security refusals
# (denied, changed, owner, permission, private-key, policy) are never listed here: they stop the operation.
REFUSAL_CODES = ("discovery-required", "discovery-budget", "files-missing", "files-count", "files-total", "files-size", "files-empty",
                 "files-depth", "files-entries", "files-type", "files-symlink", "command-invalid",
                 # a plan whose file selector broke the selector rules; the model may correct it like a command
                 "files-scope", "files-path", "files-directory", "files-selector",
                 # a web search asked for after local data was collected (only allowed first)
                 "web-late",
                 # a Skill asked for again (already in discovery) or after the Skill rounds of a question are used up
                 "skill-repeat", "skill-limit")
_ID = re.compile(r"f(?:[0-9]|[1-5][0-9]|6[0-3])\Z", re.ASCII)
_SKILL = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?\Z", re.ASCII)
MAX_SKILL_TEXT = 96 * 1024


def clean_path(value, *, empty=False, wildcard=False):
    """A scope-relative path: no absolute, home, variable, backslash or dot segments; optional terminal /** or /*."""
    if type(value) is not str or (not value and not empty):
        raise ValueError("discovery")
    body = value
    if wildcard:
        for suffix in ("/**", "/*"):
            if body.endswith(suffix):
                body = body[:-len(suffix)]
                break
    if (len(value.encode("utf-8")) > MAX_PATH_BYTES or value.startswith("/")
            or any(char in body for char in "~$\\?[]{}*")
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") for char in value)
            or body and any(part in ("", ".", "..") for part in body.split("/"))):
        raise ValueError("discovery")
    return value


def clean_query(value):
    if (type(value) is not str or len(value.strip()) < MIN_QUERY_CHARS or value != value.strip()
            or len(value.encode("utf-8")) > MAX_QUERY_BYTES or "/" in value
            or any(char in value for char in "*?[]{}\\")
            or any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") for char in value)):
        raise ValueError("discovery")
    return value


def _entry(value):
    if type(value) is not dict or set(value) not in ({"path", "type", "size"}, {"path", "type", "size", "target"}):
        raise ValueError("discovery")
    kind, size = value["type"], value["size"]
    if type(kind) is not str or kind not in ENTRY_TYPES:
        raise ValueError("discovery")
    if kind == "file":
        if type(size) is not int or isinstance(size, bool) or not 0 <= size < 2**63 or "target" in value:
            raise ValueError("discovery")
    elif size is not None or (kind == "alias") != ("target" in value):
        raise ValueError("discovery")
    result = {"path": clean_path(value["path"]), "type": kind, "size": size}
    if kind == "alias":
        result["target"] = clean_path(value["target"])
    return result


def _entries(values, total):
    if type(values) is not list or len(values) > CEILING_ENTRIES:
        raise ValueError("discovery")
    seen, result = set(), []
    for item in values:
        entry = _entry(item)
        if entry["path"] in seen:
            raise ValueError("discovery")
        seen.add(entry["path"])
        result.append(entry)
    total[0] += len(result)
    return result


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("discovery")
    return value


def _step(value, total):
    if type(value) is not dict or type(value.get("op")) is not str:
        raise ValueError("discovery")
    op = value["op"]
    if op == "list":
        if (set(value) != {"op", "directories", "truncated"} or type(value["truncated"]) is not bool
                or type(value["directories"]) is not list or not 1 <= len(value["directories"]) <= MAX_DIRECTORIES):
            raise ValueError("discovery")
        directories, keys = [], set()
        for directory in value["directories"]:
            if (type(directory) is not dict or set(directory) != {"id", "path", "entries", "truncated", "next"}
                    or type(directory["truncated"]) is not bool):
                raise ValueError("discovery")
            ident, path = _identifier(directory["id"]), clean_path(directory["path"], empty=True)
            if (ident, path) in keys:
                raise ValueError("discovery")
            keys.add((ident, path))
            following = directory["next"]
            if following is not None:
                clean_path(following)
                if not directory["truncated"]:
                    raise ValueError("discovery")
            if directory["truncated"] and not value["truncated"]:
                raise ValueError("discovery")
            directories.append({"id": ident, "path": path, "entries": _entries(directory["entries"], total),
                                "truncated": directory["truncated"], "next": following})
        return {"op": "list", "directories": directories, "truncated": value["truncated"]}
    if op == "find":
        if (set(value) != {"op", "id", "path", "queries", "kind", "matches", "truncated", "incomplete"}
                or type(value["truncated"]) is not bool or type(value["incomplete"]) is not bool
                or type(value["kind"]) is not str or value["kind"] not in KINDS
                or type(value["queries"]) is not list or not 1 <= len(value["queries"]) <= MAX_QUERIES):
            raise ValueError("discovery")
        queries = [clean_query(item) for item in value["queries"]]
        if len({item.casefold() for item in queries}) != len(queries):
            raise ValueError("discovery")
        return {"op": "find", "id": _identifier(value["id"]), "path": clean_path(value["path"], empty=True),
                "queries": queries, "kind": value["kind"], "matches": _entries(value["matches"], total),
                "truncated": value["truncated"], "incomplete": value["incomplete"]}
    if op == "run":
        from .command_ops import clean_argv
        role = value.get("role", "read")
        if (set(value) - {"role"} != {"op", "argv", "status", "exit", "output", "truncated"}
                or type(value["truncated"]) is not bool or type(role) is not str or role not in RUN_ROLES
                or type(value["status"]) is not str or value["status"] not in RUN_STATUSES):
            raise ValueError("discovery")
        code, output = value["exit"], value["output"]
        if code is not None and (type(code) is not int or isinstance(code, bool) or not -255 <= code <= 255):
            raise ValueError("discovery")
        if (type(output) is not str or len(output.encode("utf-8")) > CEILING_OUTPUT
                or any(char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                       for char in output)):
            raise ValueError("discovery")
        step = {"op": "run", "argv": clean_argv(value["argv"]), "status": value["status"], "exit": code,
                "output": output, "truncated": value["truncated"]}
        if "role" in value:
            step["role"] = role
        return step
    if op == "content":
        from .ask_content import validate_step
        return validate_step(value)
    if op == "manual":
        from .manuals import MAX_FRAGMENT_BYTES, MAX_FRAGMENTS, PAGE, SECTION
        if (set(value) != {"op", "page", "section", "status", "lines", "fragments", "complete"}
                or type(value["page"]) is not str or not PAGE.fullmatch(value["page"])
                or type(value["section"]) is not str or not SECTION.fullmatch(value["section"])
                or value["status"] not in ("ok", "missing") or type(value["complete"]) is not bool
                or type(value["lines"]) is not int or isinstance(value["lines"], bool) or not 0 <= value["lines"] < 2**31
                or type(value["fragments"]) is not list or len(value["fragments"]) > MAX_FRAGMENTS
                or (value["status"] == "missing" and value["fragments"])):
            raise ValueError("discovery")
        for item in value["fragments"]:
            if (type(item) is not dict or set(item) != {"first", "last", "text"}
                    or any(type(item[key]) is not int or isinstance(item[key], bool) for key in ("first", "last"))
                    or not 1 <= item["first"] <= item["last"] or type(item["text"]) is not str
                    or len(item["text"].encode("utf-8")) > MAX_FRAGMENT_BYTES
                    or any(char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in item["text"])):
                raise ValueError("discovery")
        return {key: value[key] for key in ("op", "page", "section", "status", "lines", "fragments", "complete")} | {
            "fragments": [dict(item) for item in value["fragments"]]}
    if op == "skill":
        if (set(value) != {"op", "name", "status", "text"} or type(value["name"]) is not str
                or not _SKILL.fullmatch(value["name"]) or value["status"] not in ("ok", "missing")
                or type(value["text"]) is not str or len(value["text"].encode("utf-8")) > MAX_SKILL_TEXT
                or (value["status"] == "missing" and value["text"])
                or any(char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value["text"])):
            raise ValueError("discovery")
        return dict(value)
    if op == "web":
        # The program-checked answer of this question's web search (its source list is the program's own).
        if (set(value) != {"op", "text"} or type(value["text"]) is not str or not value["text"].strip()
                or len(value["text"].encode("utf-8")) > MAX_WEB_TEXT
                or any(char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                       for char in value["text"])):
            raise ValueError("discovery")
        return {"op": "web", "text": value["text"]}
    if op == "page":
        # A public page the program's web broker opened (web_broker.validate_page) or that the user declined to open.
        from .web_broker import MAX_LINKS, MAX_TEXT, WebError, clean_url
        if (set(value) != {"op", "url", "status", "title", "text", "links", "truncated"}
                or value["status"] not in PAGE_STATUSES or type(value["title"]) is not str or len(value["title"]) > 200
                or type(value["text"]) is not str or len(value["text"].encode("utf-8")) > MAX_TEXT
                or type(value["truncated"]) is not bool or type(value["links"]) is not list
                or len(value["links"]) > MAX_LINKS
                or value["status"] != "ok" and (value["text"] or value["links"] or value["title"])
                or any(char not in "\n\t" and unicodedata.category(char) in ("Cc", "Cf", "Cs")
                       for char in value["title"] + value["text"])):
            raise ValueError("discovery")
        try:
            # An address that is not a public https URL is not repeated back: a refused step has an empty url.
            if not (value["url"] == "" and value["status"] == "refused") and clean_url(value["url"]) != value["url"]:
                raise ValueError("discovery")
            if any(type(link) is not str or clean_url(link) != link
                                                             for link in value["links"]):
                raise ValueError("discovery")
        except WebError:
            raise ValueError("discovery") from None
        return {key: (list(item) if key == "links" else item) for key, item in value.items()}
    if op == "download":
        # A public file the program's broker downloaded into the quarantine folder (web_download.download), or why not.
        from .web_broker import WebError, clean_url
        keys = ("op", "url", "status", "reason", "name", "size", "sha256", "content_type", "folder")
        if (set(value) != set(keys) or value["status"] not in DOWNLOAD_STATUSES
                or type(value["reason"]) is not str or not re.fullmatch(r"[a-z-]{0,20}", value["reason"])
                or type(value["name"]) is not str or len(value["name"].encode("utf-8")) > 120
                or type(value["size"]) is not int or value["size"] < 0
                or type(value["sha256"]) is not str or not re.fullmatch(r"(?:[a-f0-9]{64})?", value["sha256"])
                or type(value["content_type"]) is not str or len(value["content_type"]) > 129
                or any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value["name"] + value["content_type"])
                or (value["status"] == "ok") != bool(value["name"] and value["sha256"])
                or value["status"] != "ok" and (value["size"] or value["content_type"])
                or type(value["folder"]) is not str or len(value["folder"].encode("utf-8")) > 512
                or not re.fullmatch(r"(?:~/\.local/state/ai-desktop/downloads/[0-9]{8}-[0-9]{6}-[a-f0-9]{8})?", value["folder"])
                or (value["status"] == "ok") != bool(value["folder"])):
            raise ValueError("discovery")
        try:
            if not (value["url"] == "" and value["status"] == "refused") and clean_url(value["url"]) != value["url"]:
                raise ValueError("discovery")
        except WebError:
            raise ValueError("discovery") from None
        return {key: value[key] for key in keys}
    if op == "archive":
        # What a downloaded archive holds (archive_install.inspect), or why it cannot be installed. Names are data.
        if (set(value) != set(ARCHIVE_KEYS) or value["status"] not in ("ok", "refused")
                or type(value["archive"]) is not str or len(value["archive"].encode("utf-8")) > 512
                or type(value["reason"]) is not str or not re.fullmatch(r"[a-z]{0,12}", value["reason"])
                or (value["status"] == "ok") == bool(value["reason"])
                or value["format"] not in ("", "zip", "tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst")
                or any(type(value[key]) is not int or value[key] < 0
                       for key in ("files", "folders", "bytes", "top_count", "executables"))
                or any(type(value[key]) is not list or len(value[key]) > 32 for key in ("top", "examples"))
                or any(type(item) is not str or len(item.encode("utf-8")) > 1024
                       or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in item)
                       for item in value["top"] + value["examples"] + [value["archive"]])):
            raise ValueError("discovery")
        return {key: (list(value[key]) if key in ("top", "examples") else value[key]) for key in ARCHIVE_KEYS}
    if op == "refusal":
        if (set(value) != {"op", "code", "selectors", "limit"} or type(value["code"]) is not str
                or value["code"] not in REFUSAL_CODES or type(value["selectors"]) is not list
                or len(value["selectors"]) > MAX_SELECTORS):
            raise ValueError("discovery")
        limit = value["limit"]
        if limit is not None and (type(limit) is not int or isinstance(limit, bool) or not 0 <= limit < 2**31):
            raise ValueError("discovery")
        selectors = []
        for item in value["selectors"]:
            if type(item) is not dict or set(item) != {"id", "path"}:
                raise ValueError("discovery")
            selectors.append({"id": _identifier(item["id"]), "path": clean_path(item["path"], empty=True, wildcard=True)})
        return {"op": "refusal", "code": value["code"], "selectors": selectors, "limit": limit}
    raise ValueError("discovery")


def validate_discovery(value):
    """Copy a closed document without I/O or interpretation of names."""
    try:
        if (type(value) is not dict or set(value) != {"steps", "remaining"} or type(value["steps"]) is not list
                or not 1 <= len(value["steps"]) <= MAX_STEPS
                or type(value["remaining"]) is not dict or set(value["remaining"]) != {"rounds", "corrections", "commands"}):
            raise ValueError()
        remaining = value["remaining"]
        for name, ceiling in (("rounds", MAX_DISCOVERY_ROUNDS), ("corrections", 4), ("commands", MAX_COMMAND_ROUNDS)):
            if (type(remaining[name]) is not int or isinstance(remaining[name], bool)
                    or not 0 <= remaining[name] <= ceiling):
                raise ValueError()
        total = [0]
        steps = [_step(item, total) for item in value["steps"]]
        if sum(1 for step in steps if step["op"] == "web") > 1:
            raise ValueError()
        if total[0] > CEILING_TOTAL_ENTRIES:
            raise ValueError()
        result = {"steps": steps, "remaining": {"rounds": remaining["rounds"], "corrections": remaining["corrections"],
                                   "commands": remaining["commands"]}}
        # An unused Skill allocation must never increase diagnostic authority,
        # nor may a small diagnostic context subsidize oversized procedures.
        # The worker enforces the same partitions as the controller ledger.
        if (diagnostic_bytes(result) > CEILING_DIAGNOSTIC_BYTES
                or skill_bytes(result) > CEILING_SKILL_BYTES
                or document_bytes(result) > CEILING_BYTES):
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("discovery") from None


WEB_SHORTENED = "\n…[shortened]"
WEB_SOURCES = "\n\nSources:\n"


def web_data(text, limit=MAX_WEB_TEXT):
    """The checked web answer as a valid `web` step text of at most `limit` UTF-8 bytes (and never more than
    MAX_WEB_TEXT). Unicode line and paragraph separators become newlines; a longer answer is shortened visibly and
    keeps its program-made source list whenever that list fits in half of the room. None when nothing usable fits."""
    if type(text) is not str:
        return None
    text = str(text).replace("\u2028", "\n").replace("\u2029", "\n")
    text = "".join(char for char in text if char in "\n\t" or unicodedata.category(char) not in ("Cc", "Cf", "Cs"))
    limit = min(limit, MAX_WEB_TEXT)
    if len(text.encode("utf-8")) <= limit:
        return text if text.strip() else None

    def head(value, room):
        cut = value.encode("utf-8")[:max(0, room)].decode("utf-8", "ignore")
        line = cut.rfind("\n")
        return cut[:line] if line > len(cut) // 2 else cut       # end at a line when that does not lose much

    answer, marker, sources = text.rpartition(WEB_SOURCES)
    tail = (marker + sources).encode("utf-8") if marker else b""
    if marker and len(tail) <= limit // 2:
        kept = head(answer, limit - len(tail) - len(WEB_SHORTENED.encode("utf-8")))
        shortened = kept.rstrip() + WEB_SHORTENED + marker + sources
    else:
        shortened = head(text, limit - len(WEB_SHORTENED.encode("utf-8"))).rstrip() + WEB_SHORTENED
    return shortened if shortened.strip() and len(shortened.encode("utf-8")) <= limit else None


def document_bytes(document):
    return len(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def diagnostic_bytes(document):
    """Diagnostic steps and their complete envelope, excluding Skill steps."""
    return document_bytes({**document, "steps": [step for step in document["steps"] if step["op"] != "skill"]})


def skill_bytes(document):
    """Procedures/references and their resource-list envelope, independently bounded."""
    return document_bytes({"resources": [step for step in document["steps"] if step["op"] == "skill"]})


def scope_ids(document):
    """Every scope id a validated document mentions (directories, finds, refused selectors)."""
    found = set()
    for step in document["steps"]:
        if step["op"] == "list":
            found.update(item["id"] for item in step["directories"])
        elif step["op"] in ("find", "content"):
            found.add(step["id"])
        elif step["op"] in ("run", "skill", "web", "manual", "page", "download", "archive"):
            continue
        else:
            found.update(item["id"] for item in step["selectors"])
    return found
