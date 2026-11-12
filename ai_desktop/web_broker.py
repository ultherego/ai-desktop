"""Public web pages for the model, fetched by the program itself (S10, stage A).

The broker opens one public HTTPS address per request in a separate process: no ambient proxy, cookies, browser profile
or host credentials, no JavaScript. Every connection, also after a redirect, resolves the host itself, accepts only
public addresses and connects to the checked address with TLS verified for the original host name. The page is reduced
to bounded plain text with its title and links; the text is untrusted data for the model, never instructions.
The isolated Claude/Codex client and its egress proxy are not involved.
"""
from html.parser import HTMLParser
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import sys
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_URL = 2048
MAX_REDIRECTS = 10
MAX_BODY = 4 * 1024 * 1024               # bytes of one page read before it is reduced to text
MAX_TEXT = 24 * 1024                     # bytes of page text given to the model
MAX_LINKS = 64
MAX_HEADERS = 32 * 1024
CONNECT_SECONDS = 10
REQUEST_SECONDS = 45
USER_AGENT = "AI-Desktop/1.0 (+https://github.com/ultherego/ai-desktop)"
READABLE = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json", "text/xml",
            "application/xml", "text/x-markdown")
REDIRECTS = (301, 302, 303, 307, 308)
_HOST = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}\Z")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class WebError(ValueError):
    """A refusal with a short code: url, address, tls, network, response, status (not 2xx, e.g. a bot check that needs
    JavaScript), type, size, redirects, timeout."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def clean_url(value):
    """The canonical https URL the broker may request, or WebError("url"). The fragment is dropped (never sent)."""
    if type(value) is not str or not value or len(value.encode("utf-8")) > MAX_URL or any(c.isspace() for c in value):
        raise WebError("url")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise WebError("url") from None
    if parts.scheme.lower() != "https" or parts.username is not None or parts.password is not None:
        raise WebError("url")
    if port not in (None, 443) or not parts.hostname:
        raise WebError("url")
    host = parts.hostname.rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise WebError("url") from None
        if not _HOST.fullmatch(host):
            raise WebError("url")
        netloc = host
    else:
        if not _public(literal):
            raise WebError("address")
        netloc = "[%s]" % literal.compressed if literal.version == 6 else literal.compressed
    path = parts.path or "/"
    if any(ord(c) < 0x21 or ord(c) == 0x7f for c in path + parts.query):
        raise WebError("url")
    return urlunsplit(("https", netloc, path, parts.query, ""))


def _public(ip):
    if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback
            or ip.is_link_local or ip.is_private):
        return False
    if ip.version == 6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip in _NAT64):
        return False
    return True


def addresses(host):
    """Every DNS answer for host:443 must be a public TCP address; returns the checked sockaddr tuples."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not _public(literal):
            raise WebError("address")
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                 (literal.compressed, 443) if family == socket.AF_INET else (literal.compressed, 443, 0, 0))]
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError:
        raise WebError("network") from None
    if not answers or len(answers) > 32:
        raise WebError("network")
    checked = []
    for family, kind, protocol, _, address in answers:
        if (family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM
                or protocol != socket.IPPROTO_TCP or address[1] != 443):
            raise WebError("address")
        ip = ipaddress.ip_address(address[0])
        if not _public(ip) or (family == socket.AF_INET6 and address[2:] != (0, 0)):
            raise WebError("address")               # one non-public answer refuses the host (DNS rebinding)
        checked.append((family, kind, protocol, address))
    return checked


class _Pinned(http.client.HTTPSConnection):
    """Connects only to an address checked by `addresses`; TLS verifies the host name with the system CA store."""

    def connect(self):
        error = None
        for family, kind, protocol, address in addresses(self.host):
            stream = socket.socket(family, kind, protocol)
            try:
                stream.settimeout(CONNECT_SECONDS)
                stream.connect(address)
                self.sock = self._context.wrap_socket(stream, server_hostname=self.host)
                return
            except ssl.SSLError:
                stream.close()
                raise WebError("tls") from None
            except OSError as exc:
                stream.close()
                error = exc
        raise WebError("network") from error


def _get(url, context):
    parts = urlsplit(url)
    host = parts.hostname
    connection = _Pinned(host, timeout=CONNECT_SECONDS, context=context)
    target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    try:
        connection.request("GET", target, headers={
            "Host": parts.netloc, "User-Agent": USER_AGENT, "Accept": ", ".join(READABLE) + ";q=0.9, */*;q=0.1",
            "Accept-Encoding": "identity", "Connection": "close"})
        response = connection.getresponse()
        headers = response.getheaders()
        if sum(len(k) + len(v) for k, v in headers) > MAX_HEADERS:
            raise WebError("response")
        return connection, response
    except WebError:
        connection.close()
        raise
    except ssl.SSLError:
        connection.close()
        raise WebError("tls") from None
    except (OSError, http.client.HTTPException):
        connection.close()
        raise WebError("network") from None


def fetch(url, *, context=None):
    """GET one public page, following at most MAX_REDIRECTS redirects, each checked like the first address.
    Returns {"url", "final_url", "redirects", "status", "content_type", "body"}."""
    current = clean_url(url)
    context = context or ssl.create_default_context()
    chain = []
    for _ in range(MAX_REDIRECTS + 1):
        connection, response = _get(current, context)
        try:
            if response.status in REDIRECTS:
                location = response.getheader("Location")
                if not location:
                    raise WebError("response")
                chain.append(current)
                current = clean_url(urljoin(current, location.strip()))   # a redirect to http or a private host stops
                continue
            if not 200 <= response.status < 300:
                # 403 from a bot check (a page that needs a browser with JavaScript), 404, 5xx: not the page asked for.
                raise WebError("status")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            encoding = response.getheader("Content-Encoding", "identity").strip().lower()
            if encoding not in ("", "identity"):
                raise WebError("response")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isascii() or not length.isdigit() or int(length) > MAX_BODY):
                raise WebError("size")
            body = response.read(MAX_BODY + 1)
            truncated = len(body) > MAX_BODY
            charset = _charset(response.getheader("Content-Type", ""))
            return {"url": clean_url(url), "final_url": current, "redirects": chain, "status": response.status,
                    "content_type": content_type, "charset": charset, "body": body[:MAX_BODY], "cut": truncated}
        except ssl.SSLError:
            raise WebError("tls") from None
        except (OSError, http.client.HTTPException):
            raise WebError("network") from None
        finally:
            connection.close()
    raise WebError("redirects")


def _charset(header):
    match = re.search(r"charset=([A-Za-z0-9._-]{1,40})", header)
    return match.group(1).lower() if match else None


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "object", "head"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer", "pre",
             "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "dd", "dt", "nav", "main", "aside", "hr"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.links, self.title = base, [], [], ""
        self._skip, self._in_title = 0, False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP and tag != "head":
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and len(self.links) < MAX_LINKS * 4:
            href = dict(attrs).get("href")
            if href:
                try:
                    self.links.append(clean_url(urljoin(self.base, href.strip())))
                except WebError:
                    pass

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and tag != "head" and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _plain(text):
    """Printable text only: control and format characters (bidi overrides) are removed, blank runs shortened."""
    import unicodedata
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co"))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \n]+", "\n\n", text)
    return text.strip()


def page(fetched):
    """The page as the model sees it: {"url", "final_url", "title", "text", "links", "truncated", "content_type"}."""
    content_type = fetched["content_type"]
    if content_type not in READABLE:
        raise WebError("type")
    raw = fetched["body"]
    try:
        decoded = raw.decode(fetched.get("charset") or "utf-8", errors="replace")
    except LookupError:
        decoded = raw.decode("utf-8", errors="replace")
    title, links = "", []
    if content_type in ("text/html", "application/xhtml+xml"):
        parser = _Text(fetched["final_url"])
        try:
            parser.feed(decoded)
            parser.close()
        except (ValueError, AssertionError):
            raise WebError("response") from None
        text, title = "".join(parser.parts), _plain(parser.title)[:200]
        links = list(dict.fromkeys(parser.links))[:MAX_LINKS]
    else:
        text = decoded
    text = _plain(text)
    encoded = text.encode("utf-8")
    cut = fetched.get("cut", False) or len(encoded) > MAX_TEXT
    text = encoded[:MAX_TEXT].decode("utf-8", "ignore")
    return {"url": fetched["url"], "final_url": fetched["final_url"], "title": title, "text": text, "links": links,
            "truncated": bool(cut), "content_type": content_type}


def _worker():
    """Child process: one URL on stdin, the reduced page (or {"error": code}) on stdout."""
    try:
        request = json.loads(sys.stdin.buffer.read(MAX_URL + 64))
        if type(request) is not dict or set(request) != {"url"}:
            raise WebError("url")
        result = page(fetch(request["url"]))
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        return 0
    except WebError as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    except (ValueError, TypeError, RecursionError):
        print(json.dumps({"error": "response"}))
        return 2


def _sandbox(bootstrap):
    """The broker process: Bubblewrap with the network but without the home directory, user runtime directory or
    capabilities; the system is read-only and the program's own code is the only extra mount."""
    import shutil
    bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
    code = str(Path(__file__).resolve().parents[1])
    if bwrap is None:
        raise WebError("network")
    binds = []
    for path in ("/etc/ssl", "/etc/ca-certificates", "/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/hosts",
                 "/etc/gai.conf", "/etc/host.conf"):
        binds += ["--ro-bind-try", path, path]
    return [bwrap, "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib", "/lib64", "--symlink", "usr/bin", "/sbin", *binds,
            "--ro-bind", code, code, "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
            "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
            "--setenv", "PATH", "/usr/bin", "--setenv", "LANG", "C.UTF-8", "--setenv", "HOME", "/tmp",
            "--chdir", "/tmp", sys.executable, "-I", "-B", "-c", bootstrap]


def open_page(url, *, cancelled=None):
    """Fetch and reduce one public page in the broker sandbox; WebError on any refusal or failure."""
    clean_url(url)
    from .analysis_contract import AnalysisDenied
    from .process_io import bounded_process
    code_root = str(Path(__file__).resolve().parents[1])
    bootstrap = ("import sys;sys.path.insert(0," + repr(code_root)
                 + ");from ai_desktop.web_broker import _worker;raise SystemExit(_worker())")
    try:
        code, output, _ = bounded_process(_sandbox(bootstrap), env={"PATH": "/usr/bin", "LANG": "C.UTF-8"},
                                          input_bytes=json.dumps({"url": url}).encode(), timeout=REQUEST_SECONDS,
                                          limit=MAX_TEXT * 2 + MAX_LINKS * (MAX_URL + 8) + 4096,
                                          cancelled=cancelled)
    except AnalysisDenied as exc:
        raise WebError("timeout" if exc.reason == "timeout" else "network") from None
    except OSError:
        raise WebError("network") from None
    try:
        value = json.loads(output)
    except ValueError:
        raise WebError("response") from None
    if code != 0 or type(value) is not dict:
        reason = value.get("error") if type(value) is dict else None
        raise WebError(reason if reason in ("url", "address", "tls", "network", "response", "status", "type", "size",
                                            "redirects") else "response")
    return validate_page(value)


def validate_page(value):
    """The broker's output is checked again by the controller before it reaches the model."""
    keys = {"url", "final_url", "title", "text", "links", "truncated", "content_type"}
    if type(value) is not dict or set(value) != keys:
        raise WebError("response")
    for key in ("url", "final_url"):
        if clean_url(value[key]) != value[key]:
            raise WebError("response")
    if (type(value["title"]) is not str or len(value["title"]) > 200 or type(value["text"]) is not str
            or len(value["text"].encode("utf-8")) > MAX_TEXT or type(value["truncated"]) is not bool
            or value["content_type"] not in READABLE or type(value["links"]) is not list
            or len(value["links"]) > MAX_LINKS):
        raise WebError("response")
    for link in value["links"]:
        if type(link) is not str or clean_url(link) != link:
            raise WebError("response")
    return value


_URL_IN_TEXT = re.compile(r"https://[^\s<>\"'`)\]]+")


def urls_in(text):
    """Addresses written in a text (the user's question, a search answer): candidates for provenance."""
    found = []
    for match in _URL_IN_TEXT.findall(text if type(text) is str else ""):
        try:
            found.append(clean_url(match.rstrip(".,;:")))
        except WebError:
            pass
    return found
