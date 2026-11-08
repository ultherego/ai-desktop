"""Separate systemd activation endpoint for the explicitly approved A+ path."""
import os
import json
import re
import select
import socket

from .analysis_contract import (SERVICE_MARGIN, request_seconds, AnalysisDenied, Approval, MAX_PACKET, REASONS,
                                digest, parse_request, read_result, result_packet)
from .analysis_sandbox import run_client, transport_supervisor_ready as supervisor_ready
from .analysis_framing import PARTIAL_BYTES, send_message, receive_message
from .answer_stream import MAX_PARTIAL_TOTAL, STATUS
from . import activation_socket, e2_account, e2_ipc

DIRECTORY = "/run/ai-desktop-analysis"
READY = b"ANALYSIS2 READY"
REFUSED = b"ANALYSIS2 REFUSED "
MAX_CONTROL = 128
UID = re.compile(r"[1-9][0-9]{0,9}\Z", re.ASCII)


def socket_path(uid):
    if type(uid) is not int or not 0 < uid < 2**32 - 1:
        raise AnalysisDenied("identity")
    return DIRECTORY + "/" + str(uid) + ".sock"


def endpoint(uid):
    return activation_socket.endpoint(socket_path(uid), uid)


def manager_peer(channel, uid):
    return activation_socket.manager_peer(channel, socket_path(uid))


def local_readiness():
    """Metadata-only check. No activation, profile access or native execution."""
    from .analysis_sandbox import runtime_ready
    try:
        uid = os.geteuid()
        if uid == 0 or uid != os.getuid() or not e2_account.account_ready():
            raise AnalysisDenied("identity")
        endpoint(uid)
    except (OSError, ValueError, KeyError):
        raise AnalysisDenied("endpoint") from None
    try:
        runtime_ready()
    except (OSError, ValueError):
        raise AnalysisDenied("runtime") from None


def packet(channel):
    """One small readiness/refusal record; application messages are framed."""
    body, ancillary, flags, _ = channel.recvmsg(MAX_CONTROL + 1, 0)
    if not 0 < len(body) <= MAX_CONTROL or ancillary or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise AnalysisDenied("input")
    return body


def send_packet(channel, body):
    if type(body) is not bytes or not 0 < len(body) <= MAX_CONTROL or channel.send(body) != len(body):
        raise AnalysisDenied("result")


def _live_text(on_partial, on_status=None):
    """Decode live frames for the display: `T` + text, `S` + a fixed progress code and count. A bad frame or a display
    failure only ends the preview; without a display the frames are read and dropped."""
    state = {"total": 0, "on": on_partial is not None or on_status is not None}

    def receive(body):
        if not state["on"]:
            return
        state["total"] += len(body)
        try:
            if state["total"] > MAX_PARTIAL_TOTAL + 64 * 1024:
                raise ValueError("partial budget")
            kind, rest = body[:1], body[1:]
            if kind == b"T" and on_partial is not None:
                on_partial(rest.decode("utf-8"))
            elif kind == b"S" and on_status is not None:
                code, _, number = rest.decode("ascii").partition(":")
                if code not in STATUS or not number.isdigit() or len(number) > 2:
                    raise ValueError("status")
                on_status(code, int(number))
            elif kind not in (b"T", b"S"):
                raise ValueError("kind")
        except Exception:                   # never lose the final answer to the preview
            state["on"] = False
    return receive


def send_once(approval, *, on_partial=None, on_status=None):
    """Send one approved request. `on_partial(text)` may show live answer text and `on_status(code, number)` web search
    progress; the returned answer is what counts."""
    send_once.last_meta = None
    try:
        uid = os.geteuid()
        if (type(approval) is not Approval or os.getuid() != uid or uid == 0
                or digest(approval.payload) != approval.sha256):
            raise AnalysisDenied("consent")
        body = approval.payload.encode("utf-8")
        model_input, _ = parse_request(body)
        parsed = json.loads(model_input)
        mode = parsed["mode"]
        selected = parsed.get("execution")
        provider = selected["provider"] if selected is not None else "claude"
        if provider == "local":
            from .local_provider import complete
            return complete(parsed, approval)
        if not e2_account.account_ready():
            raise AnalysisDenied("consent")
        before = endpoint(uid)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
            conn.settimeout(5)
            conn.connect(socket_path(uid))
            manager_peer(conn, uid)
            if endpoint(uid) != before:
                raise AnalysisDenied("endpoint")
            ready = packet(conn)
            if ready != READY:
                if ready.startswith(REFUSED):
                    reason = ready[len(REFUSED):].decode("ascii")
                    if reason in REASONS:
                        raise AnalysisDenied(reason)
                raise AnalysisDenied("endpoint")
            approval.recheck_sources()
            send_message(conn, body, kind="request")
            result = read_result(receive_message(conn, kind="response", timeout=request_seconds(
                                         selected["effort"] if selected else None) + SERVICE_MARGIN,
                                         partial=_live_text(on_partial, on_status)), approval.sha256,
                                 mode=mode, provider=provider)
            send_once.last_meta = getattr(result, "meta", None)      # closed model metadata, kept apart from the text
            if send_once.last_meta and ("quota" in send_once.last_meta or "plan" in send_once.last_meta):
                from .account_cli import remember
                remember(provider, (selected or {}).get("account", "default"), send_once.last_meta.get("quota"),
                         plan=send_once.last_meta.get("plan"))
            return str(result)                                       # callers get the plain text they always got
    except socket.timeout:
        raise AnalysisDenied("timeout") from None
    except (OSError, ValueError, TypeError, KeyError):
        raise AnalysisDenied("endpoint") from None


class _live_sender:
    """Forward live answer text and web progress in bounded frames. A slow or closed terminal ends the preview, never
    the request: closure is still noticed by `cancelled`."""

    def __init__(self, conn):
        self._conn, self._total, self._on = conn, 0, True

    def _send(self, body):
        try:
            send_message(self._conn, body, kind="partial", timeout=2)
        except AnalysisDenied:
            self._on = False

    def text(self, text):
        if not self._on or type(text) is not str or not text:
            return
        body = text.encode("utf-8")
        self._total += len(body)
        if self._total > MAX_PARTIAL_TOTAL:
            self._on = False
            return
        offset, room = 0, PARTIAL_BYTES - 1
        while offset < len(body) and self._on:
            end = min(offset + room, len(body))
            while end < len(body) and (body[end] & 0xC0) == 0x80:
                end -= 1                            # never split a UTF-8 character between frames
            self._send(b"T" + body[offset:end])
            offset = end

    def status(self, code, number=0):
        if self._on and code in STATUS and type(number) is int and 0 <= number <= 99:
            self._send(b"S" + ("%s:%d" % (code, number)).encode("ascii"))


def activation_listener(uid):
    return activation_socket.listener(socket_path(uid), "analysis")


def receive_once(uid):
    socket_path(uid)
    with activation_listener(uid) as listener:
        if not select.select([listener], [], [], 5)[0]:
            raise AnalysisDenied("timeout")
        with listener.accept()[0] as conn:
            conn.settimeout(5)
            e2_ipc._peer_uid(conn, uid)
            try:
                supervisor_ready()
                if uid == os.geteuid():
                    raise AnalysisDenied("identity")
            except (AnalysisDenied, OSError, ValueError, KeyError) as exc:
                reason = exc.reason if isinstance(exc, AnalysisDenied) else "runtime"
                send_packet(conn, REFUSED + reason.encode("ascii"))
                return
            send_packet(conn, READY)
            model_input, sha256 = parse_request(receive_message(conn, kind="request"))
            parsed = json.loads(model_input)
            mode = parsed["mode"]
            selected = parsed.get("execution")
            provider = selected["provider"] if selected is not None else "claude"

            def cancelled():
                # After the single request, closure OR a second packet cancels.
                return bool(select.select([conn], [], [], 0)[0])

            try:
                if cancelled():
                    raise AnalysisDenied("cancelled")
                if provider == "local":
                    # Only the owner controller can consult its policy and reach the loopback endpoint.
                    raise AnalysisDenied("authorization")
                action = "web" if mode == "web" else "analyze"
                from contextlib import nullcontext
                from . import account_runtime
                account = (selected or {}).get("account")
                with (account_runtime.selected(uid, provider, account) if account else nullcontext()):
                    if selected is not None and selected["provider"] == "codex":
                        from .codex_runtime import run_client as run_codex
                        result = run_codex(action, model_input.encode("utf-8"), cancelled=cancelled, mode=mode)
                    else:
                        live = _live_sender(conn)
                        result = run_client(action, model_input.encode("utf-8"), cancelled=cancelled, mode=mode,
                                            partial=live.text, status=live.status)
                response = result_packet(sha256, text=result, mode=mode, meta=getattr(result, "meta", None))
            except (AnalysisDenied, OSError, ValueError, KeyError) as exc:
                reason = exc.reason if isinstance(exc, AnalysisDenied) else "internal"
                diagnostic = exc.diagnostic if isinstance(exc, AnalysisDenied) else None
                response = result_packet(sha256, reason=reason, mode=mode, diagnostic=diagnostic, provider=provider)
            send_message(conn, response, kind="response")


def worker_main(argv):
    try:
        if argv in (["login"], ["login-verbose"]):
            # Entered only by the explicit root-admin command, on a private TTY.
            if not os.isatty(0) or not os.isatty(1):
                raise AnalysisDenied("consent", stage="terminal")
            run_client("login")
            if argv == ["login-verbose"]:
                print("LOGIN: PASS auth=claude.ai")
            return 0
        if len(argv) != 1 or not UID.fullmatch(argv[0]):
            raise AnalysisDenied("input")
        receive_once(int(argv[0]))
        return 0
    except (AnalysisDenied, e2_ipc.HandoffDenied, OSError, ValueError, TypeError,
            KeyError, KeyboardInterrupt) as exc:
        if argv in (["login"], ["login-verbose"]):
            failure = exc if isinstance(exc, AnalysisDenied) else AnalysisDenied("internal")
            print("LOGIN: REFUSED stage=" + failure.stage + " reason=" + failure.reason, flush=True)
        return 2
