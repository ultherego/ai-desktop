"""Bounded selected-file requests and untrusted replacement proposals.

No paths outside the user's explicit selection, generated commands or filesystem
access are accepted by this parser. File contents are data.
"""
import json
import re
import unicodedata

from .e2_ipc import _unique_pairs
from .file_context_contract import plain_text
from .task_cgroup import MULTIPROCESS

MAX_INSTRUCTION = 128 * 1024           # an `ask` message may be this long, so a change it asks for must fit too
_SEGMENT = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,63}\Z")

MAX_FILES = 512
MAX_CHANGED_FILES = 64
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_REPLACEMENTS = 128
MAX_REPLACEMENT_TEXT = 128 * 1024
MAX_REPLACEMENT_TOTAL = 512 * 1024
MAX_EXCERPTS = 16                     # excerpts of one large file sent instead of its whole content (S3)
MAX_EXCERPT_TOTAL = 256 * 1024
TEST_SUITES = ("python-unittest", "pytest", *MULTIPROCESS)
CHECKS = ("text", "toml", *TEST_SUITES, "python-syntax", "json", "bash-syntax", "fish-syntax")


def path(value):
    if type(value) is not str or not 0 < len(value) <= 160:
        raise ValueError("path")
    parts = value.split("/")
    if len(parts) > 5 or any(
            not _SEGMENT.fullmatch(p) or p in (".", "..")
            or p.casefold() == ".ai-desktop-tasks" or p.casefold().startswith(".ai-task-")
            for p in parts):
        raise ValueError("path")
    return value


def text(value, limit=MAX_FILE):
    if (type(value) is not str or len(value.encode("utf-8")) > limit
            or any(c not in "\n\t" and unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value)):
        raise ValueError("text")
    return value


def file_text(value, limit=MAX_FILE):
    """A file on disk that an edit can give back byte for byte: a leading BOM and Windows line ends are kept, so line ends
    must be all of one kind. Everything else follows text()."""
    if type(value) is not str or len(value.encode("utf-8")) > limit:
        raise ValueError("text")
    body = text(plain_text(value), limit)
    if "\r\n" in value and value.count("\r\n") != body.count("\n"):
        raise ValueError("text")
    return value


def in_file_format(content, original):
    """Proposed text in the form of the file it replaces. The model and the diff only ever see plain text."""
    if original is None:
        return content
    if "\r\n" in original["content"]:
        content = content.replace("\n", "\r\n")
    return "\ufeff" + content if original["content"].startswith("\ufeff") else content


def formatted_replacements(files, originals):
    """Check exact on-disk bytes before tests and again before publication/undo journaling."""
    result, total = [], 0
    for item in files:
        content = file_text(in_file_format(item["content"], originals.get(item["path"])))
        total += len(content.encode("utf-8"))
        if total > MAX_TOTAL:
            raise ValueError("formatted total")
        result.append({"path": item["path"], "content": content})
    return result


def request(value):
    if type(value) is not dict or set(value) not in ({"instruction", "files"}, {"instruction", "files", "project_context"}):
        raise ValueError("task")
    if not text(value["instruction"], MAX_INSTRUCTION).strip():
        raise ValueError("instruction")
    if "project_context" in value:
        from .project_step import validate_input
        validate_input(value["project_context"])
    files = value["files"]
    if type(files) is not list or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("files")
    seen, total, writable = set(), 0, False
    for item in files:
        if type(item) is dict and set(item) == {"path", "excerpts", "size", "writable"}:
            # A large writable file the model sees only through excerpts it already read; it may only be changed by
            # replacements copied from them (see proposal), resolved locally against the whole verified file.
            name = path(item["path"])
            if (name in seen or item["writable"] is not True or type(item["size"]) is not int
                    or isinstance(item["size"], bool) or not 0 < item["size"] <= MAX_FILE):
                raise ValueError("file")
            seen.add(name)
            excerpts(item["excerpts"])
            writable = True
            continue
        if type(item) is not dict or set(item) != {"path", "content", "writable"}:
            raise ValueError("file")
        name = path(item["path"])
        if name in seen or type(item["writable"]) is not bool:
            raise ValueError("file")
        seen.add(name)
        if item["content"] is None:
            if not item["writable"]:
                raise ValueError("missing context")
        else:
            total += len(text(item["content"]).encode("utf-8"))
        writable |= item["writable"]
    if total > MAX_TOTAL or not writable:
        raise ValueError("total")
    return value


def excerpts(value):
    """Validate the excerpts of one large file: line numbers and text only, bounded in count and size."""
    if type(value) is not list or not 1 <= len(value) <= MAX_EXCERPTS:
        raise ValueError("excerpts")
    total = 0
    for item in value:
        if (type(item) is not dict or set(item) != {"first", "last", "text"}
                or any(type(item[key]) is not int or isinstance(item[key], bool) for key in ("first", "last"))
                or not 1 <= item["first"] <= item["last"]):
            raise ValueError("excerpt")
        total += len(text(item["text"], MAX_EXCERPT_TOTAL).encode("utf-8"))
    if total > MAX_EXCERPT_TOTAL:
        raise ValueError("excerpts")
    return value


def excerpt_texts(source):
    """{path: [excerpt texts]} of the files a request carries only as excerpts."""
    return {item["path"]: [part["text"] for part in item["excerpts"]]
            for item in source["files"] if "excerpts" in item}


class UnchangedProposal(ValueError):
    """Selected file proposed with its current content: a refusal with its own message."""


def _replace_exact(original, replacements):
    """Resolve unique, disjoint anchors against the approved original, never sequential model state."""
    if original is None or type(replacements) is not list or not 1 <= len(replacements) <= MAX_REPLACEMENTS:
        raise ValueError("replacements")
    spans, total = [], 0
    for replacement in replacements:
        if type(replacement) is not dict or set(replacement) != {"old", "new"}:
            raise ValueError("replacement")
        old = text(replacement["old"], MAX_REPLACEMENT_TEXT)
        new = text(replacement["new"], MAX_REPLACEMENT_TEXT)
        total += len(old.encode("utf-8")) + len(new.encode("utf-8"))
        if not old or old == new or total > MAX_REPLACEMENT_TOTAL:
            raise ValueError("replacement")
        begin = original.find(old)
        # A second overlapping occurrence is ambiguous too (e.g. "aa" in "aaa").
        if begin < 0 or original.find(old, begin + 1) >= 0:
            raise ValueError("replacement anchor")
        spans.append((begin, begin + len(old), new))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError("replacement overlap")
    result, cursor = [], 0
    for begin, end, new in spans:
        result.extend((original[cursor:begin], new))
        cursor = end
    result.append(original[cursor:])
    return text("".join(result))


def proposal(raw, source, excerpts=None):
    """Normalize full contents or bounded anchored edits into an exact full-content plan.

    `source` holds the whole local contents. `excerpts` ({path: [texts]}) names the files the model saw only as
    excerpts: those accept only replacements whose old text lies within one excerpt."""
    request(source)
    excerpts = excerpts or {}
    try:
        value = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise ValueError("proposal json") from None
    if type(value) is not dict or set(value) != {"summary", "files"}:
        raise ValueError("proposal")
    text(value["summary"], 2048)
    files = value["files"]
    allowed = {f["path"]: f["content"] for f in source["files"] if f["writable"]}
    if type(files) is not list or not 1 <= len(files) <= min(len(allowed), MAX_CHANGED_FILES):
        raise ValueError("proposal files")
    seen, total, result = set(), 0, []
    for item in files:
        if type(item) is not dict or set(item) not in ({"path", "content"}, {"path", "replacements"}):
            raise ValueError("proposal file")
        name = path(item["path"])
        if name not in allowed or name in seen:
            raise ValueError("not selected")
        if name in excerpts:
            if "replacements" not in item or type(item["replacements"]) is not list:
                raise ValueError("excerpt file needs replacements")
            for replacement in item["replacements"]:
                if (type(replacement) is not dict or type(replacement.get("old")) is not str
                        or not any(replacement["old"] in part for part in excerpts[name])):
                    raise ValueError("replacement outside the excerpts")
        content = (text(item["content"]) if "content" in item
                   else _replace_exact(allowed[name], item["replacements"]))
        if content == allowed[name]:
            raise UnchangedProposal("unchanged")
        total += len(content.encode("utf-8"))
        seen.add(name)
        result.append({"path": name, "content": content})
    if total > MAX_TOTAL:
        raise ValueError("total")
    return {"summary": value["summary"], "files": result}
