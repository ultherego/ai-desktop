"""Bounded WebSearch transcript proof; never fetch URLs or trust model links.

The native stream carries SDKUserMessage.tool_use_result. WebSearchOutput is
documented at https://code.claude.com/docs/en/agent-sdk/typescript . Only its
structured result metadata establishes URL provenance, never tool-result prose.
The pinned CLI's exact stream shape still requires native host acceptance;
unknown shapes fail closed rather than falling back to an unverified answer.
"""
import ipaddress
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit

from .analysis_contract import AnalysisDenied, MAX_TEXT, ModelText, terminal_text
from .e2_ipc import _unique_pairs
from .provider_errors import classify_failure

MAX_STREAM_BYTES = 512 * 1024      # the service keeps the same; `max` reasoning writes many thinking blocks
MAX_EVENTS = 512                    # `max` reasoning adds many thinking-token notices
MAX_CALLS = 2
MAX_TURNS = 4
MAX_SOURCES = 8


def _invalid_constant(value):
    raise ValueError("non-JSON number")


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)


def _string(value, limit, *, lines=False):
    if (type(value) is not str or not value.strip() or len(value.encode("utf-8")) > limit
            or terminal_text(value) != value or (not lines and any(c in value for c in "\r\n\t"))):
        raise ValueError("text")
    return value


MAX_TITLE = 200                     # characters of one shown source title


def _title(value):
    """A source title as plain, bounded display text: format and control characters (a soft hyphen, a zero-width
    space) are dropped, any line break or other spacing becomes one space, a long title is shortened visibly.
    None when nothing printable is left; the caller then shows the host name."""
    if type(value) is not str:
        return None
    kept = []
    for char in value[:4 * MAX_TITLE]:
        category = unicodedata.category(char)
        if category in ("Zl", "Zp") or char.isspace():
            kept.append(" ")
        elif category[0] != "C":
            kept.append(char)
    title = " ".join("".join(kept).split())
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 1].rstrip() + "…"
    return title or None


def _host(value):
    """Syntax only: a public-looking source is not a DNS or content attestation."""
    if type(value) is not str or not value or value != value.strip() or len(value) > 253:
        raise ValueError("host")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        labels = value.rstrip(".").split(".")
        if (len(labels) < 2 or value.endswith(".")
                or labels[-1].isdigit()
                or labels[-1].lower() in {"local", "localhost", "internal", "invalid", "test", "onion"}
                or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                       for label in labels)):
            raise ValueError("host") from None
    else:
        if not address.is_global:
            raise ValueError("host")


def _url(value):
    _string(value, 2048)
    if any(c.isspace() or c in "\\`<>\"" for c in value):
        raise ValueError("URL")
    parsed = urlsplit(value)
    if (parsed.scheme not in ("https", "http") or not parsed.netloc
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)):
        raise ValueError("URL")
    _host(parsed.hostname)
    return value


def _events(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_STREAM_BYTES:
        raise ValueError("stream")
    lines = raw.splitlines()
    if not 1 <= len(lines) <= MAX_EVENTS or any(not line.strip() for line in lines):
        raise ValueError("events")
    events = [_json(line) for line in lines]
    if any(type(event) is not dict for event in events):
        raise ValueError("event")
    return events


def _ui_invalidation(event, *, session=None):
    """Validate the five-field UI-only notice observed with CLI 2.1.312.

    Its event/uuid values are bounded opaque text, never instructions, tool
    results, URLs or authority. The original stream budget still counts it.
    No optional plugin, subagent or background payload is accepted here.
    """
    if (set(event) != {"type", "subtype", "event", "session_id", "uuid"}
            or event.get("type") != "system" or event.get("subtype") != "ui_invalidate"):
        raise ValueError("UI notice")
    for name in ("event", "session_id", "uuid"):
        _string(event[name], 256)
    if session is not None and event["session_id"] != session:
        raise ValueError("UI session")


def _init_index(events):
    """Only validated UI notices may precede init; never scan past other data."""
    for index, event in enumerate(events):
        if event.get("type") == "system" and event.get("subtype") == "ui_invalidate":
            _ui_invalidation(event)
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            return index
        raise ValueError("init")
    raise ValueError("missing init")


def web_failure(code, raw):
    """Classify only the final structured provider error, without its prose."""
    try:
        events = _events(raw)
        if any(event.get("type") == "result" for event in events[:-1]):
            return None
        reason = classify_failure(code, json.dumps(events[-1]).encode("utf-8"))
        if reason is not None:
            return reason
        if type(code) is int and code in (0, 1):
            start = _init_index(events)
            session = _string(events[start].get("session_id"), 256)
            for notice in events[:start]:
                _ui_invalidation(notice, session=session)
            for event in events[start + 1:]:
                if (event.get("type") == "rate_limit_event" and event.get("session_id") == session
                        and _rate_status(event) == "rejected"):
                    return "rate-limit"
        return None
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        return None


def _rate_status(event):
    """Documented quota telemetry grants no tools, results, URLs or turns."""
    info = event.get("rate_limit_info")
    if type(info) is not dict or info.get("status") not in ("allowed", "allowed_warning", "rejected"):
        raise ValueError("rate metadata")
    for field in ("resetsAt", "utilization"):
        if field in info and (type(info[field]) not in (int, float)
                              or not math.isfinite(info[field]) or info[field] < 0):
            raise ValueError("rate metadata")
    for field in ("canUserPurchaseCredits", "hasChargeableSavedPaymentMethod"):
        if field in info and type(info[field]) is not bool:
            raise ValueError("rate metadata")
    if "errorCode" in info and (info["errorCode"] != "credits_required" or info["status"] != "rejected"):
        raise ValueError("rate metadata")
    return info["status"]



class TelemetryRefused(ValueError):
    """Which part of a progress event was refused: a fixed code for the diagnostic, never a native value."""


def _telemetry(event, pending, done=()):
    """Accept only bounded, inert SDK progress; it never proves a search.

    SDKThinkingTokensMessage and SDKToolProgressMessage are documented at
    https://code.claude.com/docs/en/agent-sdk/typescript . The caller has already
    checked session_id. No URL, tool result, permission or turn is derived from
    these messages. Background/subagent progress is deliberately unsupported.
    """
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "thinking_tokens":
        allowed = {"type", "subtype", "session_id", "uuid", "user_message_uuid",
                   "estimated_tokens", "estimated_tokens_delta"}
        numbers = ("estimated_tokens", "estimated_tokens_delta")
        if set(event) - allowed:
            raise TelemetryRefused("thinking")
        if "user_message_uuid" in event:
            _string(event["user_message_uuid"], 256)
    elif kind == "tool_progress":
        # Inert progress of a search: it never supplies a result, a URL or a turn, so only what would mean other
        # activity is refused: another tool, a subagent's work, or a malformed value. Extra simple fields (a newer client)
        # are ignored, and so is a call id this transcript has not announced (long `max` reasoning, CachyOS check of
        # candidate 153); the answer's sources still come only from paired WebSearch results.
        allowed = {"type", "session_id", "uuid", "tool_use_id", "tool_name",
                   "parent_tool_use_id", "elapsed_time_seconds", "heartbeat"}
        numbers = ("elapsed_time_seconds",)
        if event.get("tool_name") != "WebSearch":
            raise TelemetryRefused("tool-name")
        parent = event.get("parent_tool_use_id", None)
        # A non-null parent marks progress from inside another tool call. With WebSearch as the only tool, the one
        # accepted parent is a WebSearch call of this transcript (its own search runs nested; CachyOS check of
        # candidate 154 with `max` reasoning). Any other parent, an agent, a task or a replay is other activity.
        if parent is not None and (type(parent) is not str or (parent not in pending and parent not in done)):
            raise TelemetryRefused("tool-parent")
        if any(name in event for name in ("agent_id", "task_id", "subagent_type")):
            raise TelemetryRefused("tool-agent")
        if event.get("isReplay", False) is not False:
            raise TelemetryRefused("tool-replay")
        if type(event.get("tool_use_id")) is not str:
            raise TelemetryRefused("tool-call")
        _string(event["tool_use_id"], 256)
        if "heartbeat" in event and type(event["heartbeat"]) is not bool:
            raise TelemetryRefused("tool-heartbeat")
        for name in set(event) - allowed:
            if (type(name) is not str or len(name) > 64
                    or type(event[name]) not in (bool, int, float, str, type(None))
                    or type(event[name]) is str and len(event[name]) > 256):
                raise TelemetryRefused("tool-field")
    else:
        return False
    if "uuid" in event:
        _string(event["uuid"], 256)
    for name in numbers:
        if name not in event:
            continue                                   # a counter may be left out; nothing depends on it
        value = event[name]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or (value < 0 and not name.endswith("_delta"))):
            raise TelemetryRefused("number")
    return True

def _query(value):
    if (type(value) is not dict or not {"query"} <= set(value)
            or set(value) - {"query", "allowed_domains", "blocked_domains"}
            or {"allowed_domains", "blocked_domains"} <= set(value)):
        raise ValueError("query")
    query = _string(value["query"], 2048)
    for name in ("allowed_domains", "blocked_domains"):
        if name in value:
            domains = value[name]
            if type(domains) is not list or not 1 <= len(domains) <= 16:
                raise ValueError("domains")
            for domain in domains:
                _host(domain)
    return query


def _sources(value, query):
    if type(value) is not dict:
        raise AnalysisDenied("result", diagnostic="web-search-shape")
    if value.get("query") != query:
        raise AnalysisDenied("result", diagnostic="web-search-query")
    if type(value.get("results")) is not list or len(value["results"]) > 32:
        raise AnalysisDenied("result", diagnostic="web-search-results")
    if (type(value.get("durationSeconds")) not in (int, float)
            or not math.isfinite(value["durationSeconds"]) or not 0 <= value["durationSeconds"] <= 120):
        raise AnalysisDenied("result", diagnostic="web-search-duration")
    if ("searchCount" in value and (type(value["searchCount"]) is not int
                                    or not 0 <= value["searchCount"] <= 8)):
        raise AnalysisDenied("result", diagnostic="web-search-count")
    sources, backend_ids = {}, set()
    for result in value["results"]:
        if type(result) is str:
            # Backend prose may contain arbitrary text or links. It is not
            # structured evidence and never supplies the displayed source list.
            continue
        if type(result) is not dict or type(result.get("content")) is not list:
            raise ValueError("search result")
        backend_id = _string(result.get("tool_use_id"), 256)
        if backend_id in backend_ids or len(backend_ids) >= 8 or len(result["content"]) > 32:
            raise ValueError("search results")
        backend_ids.add(backend_id)
        for item in result["content"]:
            if type(item) is not dict:
                raise ValueError("source")
            # One unusual result (an international or underscore host, a port, quotes in the address) is left out
            # instead of refusing the whole answer. It can never be cited: a cited address must have been observed.
            try:
                url = _url(item.get("url"))
            except (ValueError, TypeError, UnicodeError):
                continue
            sources.setdefault(url, _title(item.get("title")) or urlsplit(url).hostname)
    if len(sources) > 256:
        raise ValueError("sources")
    return sources


def _message(event, role):
    if (event.get("parent_tool_use_id", "missing") is not None or event.get("agent_id") is not None
            or event.get("isReplay", False) is not False):
        raise ValueError("subagent")
    message = event.get("message")
    if (type(message) is not dict or message.get("role") != role
            or type(message.get("content")) is not list or not 1 <= len(message["content"]) <= 16):
        raise ValueError("message")
    return message["content"]


# The destination may hold one level of balanced parentheses (a Wikipedia "Foo_(bar)" page); the optional link title
# may be in double or single quotes.
_INLINE_LINK = re.compile(r"\[([^\[\]\n]*)\]\(\s*((?:[^()\s]|\([^()\s]*\))*)\s*(?:\"[^\"\n]*\"|'[^'\n]*')?\s*\)")
_DEFINITION = re.compile(r"^ {0,3}\[[^\]\n]+\]:[^\n]*(?:\n|$)", re.M)
_ADDRESS = re.compile(r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.)[^\s<>()\[\]`]*")
_TRAILING = ".,;:!?'\""


def _without_addresses(answer, sources):
    """The answer text with the model's own links and addresses taken out. Inline links keep their text, reference
    definitions are removed, and a bare address becomes the number of the listed source it names, or is dropped."""
    def number(url):
        url = url.rstrip("/")
        for index, source in enumerate(sources, 1):
            if source.rstrip("/") == url:
                return "[%d]" % index
        return None

    def inline(match):
        text, cited = match.group(1).strip(), number(match.group(2))
        if text.isdigit():
            return "[%s]" % text                  # "[1](...)" is the model's reference to source 1
        if cited and cited not in text:
            return (text + " " + cited).strip()
        return text or (cited or "")

    def bare(match):
        url = match.group(0)
        tail = ""
        while url and url[-1] in _TRAILING:
            url, tail = url[:-1], url[-1] + tail
        return (number(url) or "(address removed)") + tail

    def unknown(match):
        return "" if not 1 <= int(match.group(2)) <= len(sources) else match.group(0)

    def clean(prose):
        prose = _INLINE_LINK.sub(inline, prose)
        # A number that names no listed source is taken out; the sources are listed below the answer anyway.
        prose = re.sub(r"( ?)(?<![\w\])\]])\[([0-9]+)\](?!\()", unknown, prose)
        prose = _DEFINITION.sub("", prose)
        prose = re.sub(r"<((?i:[a-z][a-z0-9+.-]*://|www\.)[^<>\s]*)>", r"\1", prose)   # autolinks
        return _ADDRESS.sub(bare, prose)

    parts, last = [], 0
    for code in _CODE.finditer(answer):                   # code stays exactly as written
        parts += [clean(answer[last:code.start()]), code.group(0)]
        last = code.end()
    parts.append(clean(answer[last:]))
    return "".join(parts).strip()


_CODE = re.compile(r"(?ms)^ {0,3}(```|~~~).*?(?:^ {0,3}\1[^\n]*$|\Z)|`[^`\n]*`")


def _prose(answer):
    """The answer without its code (fenced blocks and inline code): code is shown as written, never rewritten or
    read as citations (CachyOS check of candidate 141: a prime-number script was refused for `sieve[0]`)."""
    return _CODE.sub("", answer)


def _references(answer):
    """Source numbers the prose cites. An index right after a name or a bracket (`a[1][2]`) is not a citation."""
    return {int(number) for number in re.findall(r"(?<![\w\])\]])\[([0-9]+)\]", _prose(answer))}


def _answer(raw, observed):
    check = 'web-answer-json'
    try:
        value = _json(_string(raw, MAX_TEXT, lines=True))
        check = "web-answer-shape"
        if type(value) is not dict or set(value) != {"answer", "sources"}:
            raise ValueError("answer")
        check = "web-answer-text"
        answer = _string(value["answer"], 8192, lines=True).strip()
        check = "web-answer-sources"
        sources = value["sources"]
        if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
            raise ValueError("cited sources")
        check = "web-answer-provenance"
        if any(type(url) is not str or url not in observed for url in sources) or len(set(sources)) != len(sources):
            raise ValueError("invented source")
        check = "web-answer-url"
        # Addresses are shown only in the program's list of proven sources. A link or address the model wrote into
        # the text is removed instead of refusing the whole answer: a listed source becomes its number, anything else
        # is dropped.
        answer = _without_addresses(answer, sources)
        if re.search(r"(?i)[a-z][a-z0-9+.-]*://|www\.", _prose(answer)):
            raise ValueError("references")
        check = "web-answer-link"
        if re.search(r"\]\(", _prose(answer)) or re.search(r"^ {0,3}\[[0-9]+\]:", _prose(answer), re.M):
            raise ValueError("references")
        check = "web-answer-references"
        # Source numbers in the text are optional (the maintainer: the sources at the end are enough); one that names
        # no listed source was already taken out above, so this only guards that removal.
        if not _references(answer) <= set(range(1, len(sources) + 1)):
            raise ValueError("references")
        check = "web-answer-render"
        lines = [answer, "", "Sources:"]
        for index, url in enumerate(sources, 1):
            title = re.sub(r"([\\`*_{}\[\]<>()#+.!|~-])", r"\\\1", observed[url])
            # Inline code preserves literal URL punctuation/entities in Markdown
            # renderers; _url excludes backticks and terminal controls.
            lines.append(f"[{index}] {title} — `{url}`")
        result = "\n".join(lines)
        if len(result.encode("utf-8")) > MAX_TEXT:
            raise AnalysisDenied("output-limit")
        return result
    except (ValueError, UnicodeError, TypeError, KeyError, OverflowError, RecursionError):
        raise AnalysisDenied("result", diagnostic=check) from None


def web_text(raw):
    """Return a cited answer only after a complete, bounded search transcript."""
    check = "web-stream"
    try:
        events = _events(raw)
        failure = web_failure(0, raw)
        if failure is not None:
            raise AnalysisDenied(failure)
        check = "web-init-event"
        start = _init_index(events)
        initial = events[start]
        check = "web-init-tools"
        if initial.get("tools") != ["WebSearch"]:
            raise ValueError("init")
        check = "web-init-mcp"
        if initial.get("mcp_servers") != []:
            raise ValueError("init")
        check = "web-session"
        session = _string(initial.get("session_id"), 256)
        for notice in events[:start]:
            _ui_invalidation(notice, session=session)
        pending, calls, completed, observed, done = {}, set(), 0, {}, set()
        final = None
        windows = {}
        for index, event in enumerate(events[start + 1:], start + 1):
            kind = event.get("type")
            # SDKUserMessage alone permits omission of session_id. Subagents
            # and resumed/injected sessions are not part of this one-shot flow.
            check = "web-session"
            if event.get("session_id", session if kind == "user" else None) != session:
                raise ValueError("session")
            if kind == "assistant":
                check = "web-assistant-message"
                if event.get("error") is not None or event.get("aborted", False) is not False:
                    raise ValueError("assistant error")
                for block in _message(event, "assistant"):
                    check = "web-assistant-tool"
                    if type(block) is not dict:
                        raise ValueError("assistant block")
                    block_type = block.get("type")
                    if block_type in ("text", "thinking", "redacted_thinking"):
                        continue
                    if block_type != "tool_use" or block.get("name") != "WebSearch":
                        raise ValueError("unexpected tool")
                    check = "web-tool-call"
                    call_id = _string(block.get("id"), 256)
                    if call_id in calls or len(calls) >= MAX_CALLS:
                        raise ValueError("calls")
                    calls.add(call_id)
                    check = "web-query"
                    pending[call_id] = _query(block.get("input"))
            elif kind == "user":
                check = "web-tool-result-message"
                blocks = _message(event, "user")
                if len(blocks) != 1 or type(blocks[0]) is not dict:
                    raise ValueError("tool result")
                check = "web-tool-result-pair"
                block = blocks[0]
                call_id = block.get("tool_use_id")
                if (block.get("type") != "tool_result" or type(call_id) is not str or call_id not in pending
                        or ("is_error" in block and block["is_error"] is not False)):
                    raise ValueError("tool result")
                check = "web-search-source"
                observed.update(_sources(event.get("tool_use_result"), pending.pop(call_id)))
                done.add(call_id)
                completed += 1
            elif kind == "result":
                check = "web-final-order"
                if index != len(events) - 1:
                    raise ValueError("incomplete result")
                check = "web-final-status"
                if event.get("subtype") != "success" or event.get("is_error") is not False:
                    raise ValueError("incomplete result")
                check = "web-final-turns"
                if type(event.get("num_turns")) is not int or not 1 <= event["num_turns"] <= MAX_TURNS:
                    raise ValueError("incomplete result")
                check = "web-final-permissions"
                if (event.get("permission_denials") != [] or event.get("api_error_status") is not None
                        or event.get("deferred_tool_use") is not None):
                    raise ValueError("incomplete result")
                check = "web-final-pending"
                if pending:
                    raise ValueError("incomplete result")
                final = event.get("result")
            elif kind == "rate_limit_event":
                check = "web-rate-metadata"
                if _rate_status(event) == "rejected":
                    raise AnalysisDenied("rate-limit")
                from .account_store import claude_windows
                windows.update(claude_windows(event["rate_limit_info"]))
            elif kind == "system" and event.get("subtype") == "ui_invalidate":
                check = "web-telemetry"
                _ui_invalidation(event, session=session)
            else:
                check = "web-telemetry"
                try:
                    known = _telemetry(event, pending, done)
                except TelemetryRefused as refused:
                    check = "web-telemetry-" + str(refused)
                    raise
                if not known:
                    check = "web-event-type"
                    raise ValueError("unexpected event")
        check = "web-missing-result"
        if final is None or pending:
            raise ValueError("missing result")
        if not completed or not observed:
            raise AnalysisDenied("web-no-results", diagnostic="web-no-search" if not completed else "web-no-links")
        answer = _answer(final, observed)
        return ModelText(answer, {"models": [], "quota": windows}) if windows else answer
    except (ValueError, UnicodeError, TypeError, KeyError, OverflowError, RecursionError):
        raise AnalysisDenied("result", diagnostic=check) from None
