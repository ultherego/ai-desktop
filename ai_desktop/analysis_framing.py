"""Finite messages over the authenticated SOCK_SEQPACKET connection.

Only transport framing lives here: no JSON, model execution or peer grants.
Every message has one versioned header and a bounded number of exact records.
The complete length and digest must match before application parsing starts.
"""
import hashlib
import hmac
import math
import socket
import struct
import time

from .analysis_contract import AnalysisDenied, MAX_PACKET_LARGE, MAX_RESPONSE_BYTES

CHUNK_BYTES = 32768
MAGIC = b"AIDFRM2\0"
HEADER = struct.Struct("!8scI32s")
PARTIAL_BYTES = 16384
MAX_PARTIALS = 4096                 # live-text frames before one response; their text is bounded again by the caller
_KINDS = {"request": (b"Q", MAX_PACKET_LARGE), "response": (b"R", MAX_RESPONSE_BYTES)}
PARTIAL_TAG = b"P"


def _settings(kind, timeout):
    if (type(kind) is not str or kind not in _KINDS or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or not 0 < timeout <= 1200):
        raise AnalysisDenied("input")
    return _KINDS[kind]


def _remaining(channel, deadline, clock):
    remaining = deadline - clock()
    if remaining <= 0:
        raise AnalysisDenied("timeout")
    channel.settimeout(remaining)


def _receive_record(channel, size, deadline, clock):
    _remaining(channel, deadline, clock)
    raw, ancillary, flags, _ = channel.recvmsg(size + 1, 0)
    _remaining(channel, deadline, clock)
    if len(raw) != size or ancillary or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise AnalysisDenied("input")
    return raw


def _send_record(channel, raw, deadline, clock):
    _remaining(channel, deadline, clock)
    if channel.send(raw) != len(raw):
        raise AnalysisDenied("input")
    _remaining(channel, deadline, clock)


def send_message(channel, body, *, kind, timeout=5, clock=time.monotonic):
    tag, limit = _settings(kind, timeout)
    if type(body) is not bytes or not 0 < len(body) <= limit:
        raise AnalysisDenied("input")
    header = HEADER.pack(MAGIC, tag, len(body), hashlib.sha256(body).digest())
    deadline, previous = clock() + timeout, channel.gettimeout()
    try:
        _send_record(channel, header, deadline, clock)
        for offset in range(0, len(body), CHUNK_BYTES):
            _send_record(channel, body[offset:offset + CHUNK_BYTES], deadline, clock)
    except socket.timeout:
        raise AnalysisDenied("timeout") from None
    except OSError:
        raise AnalysisDenied("endpoint") from None
    finally:
        channel.settimeout(previous)


def send_partial(channel, body, *, timeout=2, clock=time.monotonic):
    """One live-text frame as ONE record: header and body together. A SOCK_SEQPACKET send queues a whole record or
    nothing, so a timeout never leaves half a frame in front of the response that follows."""
    if type(body) is not bytes or not 0 < len(body) <= PARTIAL_BYTES:
        raise AnalysisDenied("input")
    record = HEADER.pack(MAGIC, PARTIAL_TAG, len(body), hashlib.sha256(body).digest()) + body
    deadline, previous = clock() + timeout, channel.gettimeout()
    try:
        _send_record(channel, record, deadline, clock)
    except socket.timeout:
        raise AnalysisDenied("timeout") from None
    except OSError:
        raise AnalysisDenied("endpoint") from None
    finally:
        channel.settimeout(previous)


def _partial_record(raw):
    """The body of a one-record live-text frame, or AnalysisDenied for anything else."""
    magic, tag, length, expected = HEADER.unpack(raw[:HEADER.size])
    body = raw[HEADER.size:]
    if (magic != MAGIC or tag != PARTIAL_TAG or length != len(body) or not 0 < length <= PARTIAL_BYTES
            or not hmac.compare_digest(hashlib.sha256(body).digest(), expected)):
        raise AnalysisDenied("input")
    return body


def receive_message(channel, *, kind, timeout=5, clock=time.monotonic, partial=None):
    """One message of `kind`. With `partial`, a response may be preceded by live-text frames, each handed over as
    bytes; the whole exchange keeps one deadline and a fixed frame count."""
    tag, limit = _settings(kind, timeout)
    if partial is not None and (kind != "response" or not callable(partial)):
        raise AnalysisDenied("input")
    deadline, previous = clock() + timeout, channel.gettimeout()
    try:
        for _ in range(MAX_PARTIALS + 1):
            if partial is None:
                raw = _receive_record(channel, HEADER.size, deadline, clock)
            else:
                # A header record, or one whole live-text frame (header and body in a single record).
                _remaining(channel, deadline, clock)
                raw, ancillary, flags, _ = channel.recvmsg(HEADER.size + PARTIAL_BYTES + 1, 0)
                _remaining(channel, deadline, clock)
                if (ancillary or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                        or not HEADER.size <= len(raw) <= HEADER.size + PARTIAL_BYTES):
                    raise AnalysisDenied("input")
                if len(raw) > HEADER.size:
                    partial(_partial_record(raw))
                    continue
            magic, actual_tag, length, expected = HEADER.unpack(raw)
            if magic != MAGIC or actual_tag != tag or not 0 < length <= limit:
                raise AnalysisDenied("input")
            body = bytearray()
            while len(body) < length:
                body.extend(_receive_record(channel, min(CHUNK_BYTES, length - len(body)), deadline, clock))
            if not hmac.compare_digest(hashlib.sha256(body).digest(), expected):
                raise AnalysisDenied("input")
            return bytes(body)
        raise AnalysisDenied("input")
    except socket.timeout:
        raise AnalysisDenied("timeout") from None
    except OSError:
        raise AnalysisDenied("endpoint") from None
    finally:
        channel.settimeout(previous)
