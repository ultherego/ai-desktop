"""Public files downloaded by the program into a quarantine folder (S10, stage B).

The web broker (same sandbox and transport rules as a page: HTTPS, public addresses only, TLS for the original host,
no ambient proxy or credentials) streams one file into a `.part` file in the one directory it may write, a fresh
quarantine folder under ~/.local/state/ai-desktop/downloads. The controller decides what counts as done: a file gets
its final name only after the whole body arrived, its size matches what the server announced and the controller
itself hashed it (SHA-256). A partial file never counts as complete. A dropped connection is resumed only when the
server gave a strong validator (ETag) or a Last-Modified date and answers the If-Range request with exactly the
missing part; otherwise the file starts again from zero. Downloaded files are private (0600) and never executable,
and nothing is run, unpacked or installed here (stage C does inspection and installation).
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import time
from urllib.parse import unquote, urljoin, urlsplit

from . import web_broker
from .web_broker import WebError, clean_url

MAX_FILE = 16 * 1024 ** 3                # internal ceiling of one file: an endless body stops here
MARGIN = 64 * 1024 ** 2                  # free space that must stay on the disk, before and during a download
CHUNK = 256 * 1024
IDLE_SECONDS = 60                        # no byte for this long counts as a dropped connection
TOTAL_SECONDS = 6 * 3600                 # one file, however large
ATTEMPTS = 3                             # the first try and at most two resumes after a dropped connection
CODES = ("url", "address", "tls", "network", "response", "status", "redirects", "timeout", "space", "size",
         "incomplete", "cancelled")
_RANGE = re.compile(r"bytes (\d{1,20})-(\d{1,20})/(\d{1,20}|\*)\Z")


class DownloadError(WebError):
    pass


# -- the broker side (runs in the sandbox) -----------------------------------------------------------------------------

def _validator(response):
    """A strong ETag, or a Last-Modified date: what If-Range may carry. A weak ETag cannot prove the same bytes."""
    etag = (response.getheader("ETag") or "").strip()
    if etag and not etag.startswith("W/") and len(etag) <= 256 and etag.isprintable():
        return {"etag": etag}
    modified = (response.getheader("Last-Modified") or "").strip()
    if modified and len(modified) <= 64 and modified.isprintable():
        return {"last_modified": modified}
    return None


def _filename(header):
    """The server's suggested name from Content-Disposition (RFC 6266 filename* first), or None."""
    if not header or len(header) > 1024:
        return None
    match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", header, re.IGNORECASE)
    if match:
        return unquote(match.group(1).strip())
    match = re.search(r'filename\s*=\s*"([^"]*)"', header, re.IGNORECASE) or \
        re.search(r"filename\s*=\s*([^;\s]+)", header, re.IGNORECASE)
    return match.group(1) if match else None


def _free(path):
    info = os.statvfs(path)
    return info.f_bavail * info.f_frsize


def stream(response, fd, *, start, expected, free, limit=MAX_FILE, margin=MARGIN, clock=time.monotonic):
    """Copy the body to fd from offset `start`; returns the bytes written. Raises DownloadError("space") when the disk
    would keep less than `margin`, ("size") past `limit`, ("incomplete") when the body ends early, ("timeout") when
    reading stalls. `expected` is the full size when known."""
    if expected is not None and (expected > limit):
        raise DownloadError("size")
    if expected is not None and free() - (expected - start) < margin:
        raise DownloadError("space")
    written, unchecked = start, None
    while True:
        try:
            block = response.read(CHUNK)
        except TimeoutError:
            raise DownloadError("timeout") from None
        except OSError:
            raise DownloadError("incomplete") from None
        if not block:
            break
        if written + len(block) > limit or (expected is not None and written + len(block) > expected):
            raise DownloadError("size")
        if unchecked is None or unchecked >= 4 * CHUNK:
            if free() - len(block) < margin:
                raise DownloadError("space")
            unchecked = 0
        unchecked += len(block)
        view = memoryview(block)
        while view:
            done = os.write(fd, view)
            view = view[done:]
        written += len(block)
    if expected is not None and written != expected:
        raise DownloadError("incomplete")
    return written


def _open(url, context, headers):
    """GET with redirects, each target checked like the first; returns (connection, response, final_url)."""
    current = clean_url(url)
    for _ in range(web_broker.MAX_REDIRECTS + 1):
        connection, response = web_broker._get(current, context, headers, timeout=IDLE_SECONDS)
        if response.status in web_broker.REDIRECTS:
            location = response.getheader("Location")
            connection.close()
            if not location:
                raise DownloadError("response")
            current = clean_url(urljoin(current, location.strip()))
            continue
        return connection, response, current
    raise DownloadError("redirects")


def _worker():
    """Child process: {"url", "part", "offset", "validator"} on stdin; the result or {"error", "size", "validator"}."""
    import ssl
    state = {"size": 0, "validator": None}
    try:
        import sys
        request = json.loads(sys.stdin.buffer.read(8192))
        if (type(request) is not dict or set(request) != {"url", "part", "offset", "validator"}
                or type(request["part"]) is not str or not re.fullmatch(r"[a-f0-9]{16}\.part", request["part"])
                or type(request["offset"]) is not int or request["offset"] < 0):
            raise DownloadError("url")
        offset, validator = request["offset"], request["validator"]
        headers = {"Accept": "*/*"}
        if offset and type(validator) is dict and len(validator) == 1:
            headers["Range"] = "bytes=%d-" % offset
            headers["If-Range"] = validator.get("etag") or validator.get("last_modified")
        else:
            offset = 0
        connection, response, final = _open(request["url"], ssl.create_default_context(), headers)
        try:
            if response.status == 206 and offset:
                match = _RANGE.fullmatch((response.getheader("Content-Range") or "").strip())
                if not match or int(match.group(1)) != offset or match.group(3) == "*":
                    raise DownloadError("response")       # not exactly the missing part: start again from zero
                expected = int(match.group(3))
            elif 200 <= response.status < 300 and response.status != 206:
                offset = 0
                length = response.getheader("Content-Length")
                if length is not None and (not length.isascii() or not length.isdigit()):
                    raise DownloadError("response")
                expected = int(length) if length is not None else None
            else:
                raise DownloadError("status")
            encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding not in ("", "identity"):
                raise DownloadError("response")
            state["validator"] = _validator(response) if offset == 0 else validator
            path = "/download/" + request["part"]
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                if os.fstat(fd).st_size < offset:
                    raise DownloadError("response")
                os.ftruncate(fd, offset)
                os.lseek(fd, offset, os.SEEK_SET)
                state["size"] = offset
                try:
                    state["size"] = stream(response, fd, start=offset, expected=expected,
                                           free=lambda: _free("/download"))
                finally:
                    os.fsync(fd)
                    state["size"] = os.fstat(fd).st_size
            finally:
                os.close(fd)
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()[:100]
            name = _filename(response.getheader("Content-Disposition"))
            result = {"final_url": final, "size": state["size"], "content_type": content_type,
                      "filename": name if type(name) is str and len(name) <= 255 else None}
        finally:
            connection.close()
        os.write(1, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        return 0
    except WebError as exc:
        code = exc.code if exc.code in CODES else "response"
    except (OSError, ValueError, TypeError):
        code = "response"
    os.write(1, json.dumps({"error": code, "size": state["size"], "validator": state["validator"]}).encode())
    return 2


# -- the controller side ------------------------------------------------------------------------------------------------

def _home():
    return pwd.getpwuid(os.geteuid()).pw_dir


def quarantine(home=None):
    """A fresh private folder for one question's downloads: ~/.local/state/ai-desktop/downloads/<id> (0700)."""
    base = Path(home or _home()) / ".local/state/ai-desktop/downloads"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(base)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise DownloadError("space")
    folder = base / (time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(4))
    folder.mkdir(mode=0o700)
    return str(folder)


def safe_name(raw, url):
    """A plain file name from the server's suggestion or the address: no directories, no leading dot, no control
    characters, at most 120 bytes; "download" when nothing usable is left."""
    candidate = raw if type(raw) is str and raw.strip() else unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    candidate = candidate.replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(c if (c.isalnum() or c in "._-+() ") and c.isprintable() else "_" for c in candidate)
    candidate = candidate.strip(" .")
    while len(candidate.encode("utf-8")) > 120:
        stem, dot, extension = candidate.rpartition(".")
        candidate = (stem[:-1] + dot + extension) if dot and len(extension) <= 16 and stem else candidate[:-1]
    return candidate or "download"


def _unique(folder, name):
    match = re.fullmatch(r"(.+?)(\.tar\.(?:gz|xz|zst|bz2)|\.[^.]{1,16})?", name)
    stem, extension = match.group(1), match.group(2) or ""
    dot = ""
    if extension:
        dot, extension = ".", extension[1:]
    candidate, number = name, 2
    while os.path.lexists(os.path.join(folder, candidate)) or candidate.endswith(".part"):
        candidate = "%s (%d)%s%s" % (stem, number, dot, extension)
        number += 1
    return candidate


def _run(request, folder, cancelled):
    from .analysis_contract import AnalysisDenied
    from .process_io import bounded_process
    code_root = str(Path(__file__).resolve().parents[1])
    bootstrap = ("import sys;sys.path.insert(0," + repr(code_root)
                 + ");from ai_desktop.web_download import _worker;raise SystemExit(_worker())")
    try:
        _, output, _ = bounded_process(web_broker._sandbox(bootstrap, folder),
                                       env={"PATH": "/usr/bin", "LANG": "C.UTF-8"},
                                       input_bytes=json.dumps(request).encode(), timeout=TOTAL_SECONDS,
                                       limit=8192, cancelled=cancelled)
    except AnalysisDenied as exc:
        if exc.reason == "cancelled":
            return {"error": "cancelled", "size": 0, "validator": None}
        return {"error": "timeout" if exc.reason == "timeout" else "network", "size": 0, "validator": None}
    except OSError:
        return {"error": "network", "size": 0, "validator": None}
    try:
        value = json.loads(output)
    except ValueError:
        return {"error": "response", "size": 0, "validator": None}
    return value if type(value) is dict else {"error": "response", "size": 0, "validator": None}


def _checked_validator(value):
    if value is None:
        return None
    if (type(value) is dict and len(value) == 1 and set(value) <= {"etag", "last_modified"}
            and type(next(iter(value.values()))) is str and 0 < len(next(iter(value.values()))) <= 256
            and next(iter(value.values())).isprintable()):
        return value
    return None


def _hash(folder, name):
    """Size and SHA-256 of a private regular file in the folder, read without following links."""
    fd = os.open(os.path.join(folder, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise DownloadError("response")
        os.fchmod(fd, 0o600)
        digest = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        return info.st_size, digest.hexdigest()
    finally:
        os.close(fd)


def download(url, folder, *, cancelled=None, progress=None, run=_run):
    """One public file into the quarantine folder. Returns {"url", "final_url", "name", "size", "sha256",
    "content_type"}; raises DownloadError(code) and leaves no partial file behind."""
    url = clean_url(url)
    part = secrets.token_hex(8) + ".part"
    offset, validator, result = 0, None, None
    watched = _watch(folder, part, cancelled, progress)
    try:
        for attempt in range(ATTEMPTS):
            value = run({"url": url, "part": part, "offset": offset, "validator": validator}, folder, watched)
            if "error" not in value:
                result = value
                break
            code = value["error"] if value.get("error") in CODES else "response"
            size = value.get("size") if type(value.get("size")) is int and value.get("size") >= 0 else 0
            if code == "cancelled" or (cancelled is not None and cancelled()):
                raise DownloadError("cancelled")
            fresh = _checked_validator(value.get("validator"))
            if code in ("incomplete", "timeout", "network") and attempt + 1 < ATTEMPTS:
                # Resume only with a validator the server gave for these bytes; otherwise start again from zero.
                offset, validator = (size, fresh) if fresh is not None and size > 0 else (0, None)
                continue
            raise DownloadError(code)
        if (result is None or set(result) != {"final_url", "size", "content_type", "filename"}
                or type(result["size"]) is not int or result["size"] < 0):
            raise DownloadError("response")
        final = clean_url(result["final_url"])
        size, digest = _hash(folder, part)
        if size != result["size"]:
            raise DownloadError("incomplete")
        name = _unique(folder, safe_name(result["filename"], final))
        os.rename(os.path.join(folder, part), os.path.join(folder, name))
        content_type = result["content_type"] if type(result["content_type"]) is str else ""
        content_type = content_type if re.fullmatch(r"[a-z0-9!#$&^_.+-]{1,64}/[a-z0-9!#$&^_.+-]{1,64}", content_type) else ""
        return {"url": url, "final_url": final, "name": name, "size": size, "sha256": digest,
                "content_type": content_type}
    finally:
        try:
            os.unlink(os.path.join(folder, part))       # a partial file never stays where it could pass for a download
        except FileNotFoundError:
            pass


def _watch(folder, part, cancelled, progress):
    """The broker's cancellation poll, which also reports the size of the part file (at most every second)."""
    shown = [0.0]

    def poll():
        if progress is not None and time.monotonic() - shown[0] >= 1:
            shown[0] = time.monotonic()
            try:
                progress(os.lstat(os.path.join(folder, part)).st_size)
            except OSError:
                pass
        return cancelled() if cancelled is not None else False
    return poll
