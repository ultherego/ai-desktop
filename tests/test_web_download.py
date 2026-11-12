"""S10 stage B: public files into a quarantine folder; a partial file never counts as complete. No real network."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_desktop import assistant_contract, assistant_flow, discovery_contract, web_download as d

URL = "https://files.example.org/pkg/tool-1.0.tar.gz"


class Body:
    """A response body in chunks; `drop` raises after them, `endless` never ends."""

    def __init__(self, chunks, *, drop=False, stall=False, endless=False):
        self.chunks, self.drop, self.stall, self.endless = list(chunks), drop, stall, endless

    def read(self, size):
        if self.endless:
            return b"x" * size
        if self.chunks:
            return self.chunks.pop(0)
        if self.stall:
            raise TimeoutError()
        if self.drop:
            raise ConnectionResetError()
        return b""


class Headers:
    def __init__(self, **headers):
        self.headers = {key.replace("_", "-"): value for key, value in headers.items()}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp()
        self.addCleanup(os.unlink, self.path)
        self.addCleanup(os.close, self.fd)

    def run_stream(self, body, **options):
        settings = dict(start=0, expected=None, free=lambda: 10 ** 12, margin=1000)
        settings.update(options)
        return d.stream(body, self.fd, **settings)

    def test_a_whole_body_is_written_and_its_size_returned(self):
        self.assertEqual(self.run_stream(Body([b"abc", b"def"]), expected=6), 6)
        self.assertEqual(Path(self.path).read_bytes(), b"abcdef")

    def test_an_early_end_a_stall_or_a_reset_is_incomplete_never_complete(self):
        for body, code in ((Body([b"abc"]), "incomplete"), (Body([b"abc"], drop=True), "incomplete"),
                           (Body([b"abc"], stall=True), "timeout")):
            with self.subTest(code=code), self.assertRaises(d.DownloadError) as caught:
                self.run_stream(body, expected=6)
            self.assertEqual(caught.exception.code, code)

    def test_an_endless_or_oversized_body_stops_at_the_ceiling(self):
        with self.assertRaises(d.DownloadError) as caught:
            self.run_stream(Body([], endless=True), limit=d.CHUNK * 3)
        self.assertEqual(caught.exception.code, "size")
        with self.assertRaises(d.DownloadError) as caught:
            self.run_stream(Body([b"abcdefg"]), expected=6)                     # more than announced
        self.assertEqual(caught.exception.code, "size")
        with self.assertRaises(d.DownloadError):
            self.run_stream(Body([b"a"]), expected=d.MAX_FILE + 1)

    def test_no_space_before_or_during_the_download(self):
        with self.assertRaises(d.DownloadError) as caught:
            self.run_stream(Body([b"abc"]), expected=3, free=lambda: 1002)       # 3 bytes would leave less than 1000
        self.assertEqual(caught.exception.code, "space")
        self.assertEqual(Path(self.path).read_bytes(), b"")                    # nothing written
        free = iter([10 ** 6, 10 ** 6, 10])
        chunks = [b"x" * d.CHUNK] * 12
        with self.assertRaises(d.DownloadError) as caught:
            self.run_stream(Body(chunks), free=lambda: next(free))
        self.assertEqual(caught.exception.code, "space")

    def test_a_resume_appends_after_the_offset(self):
        os.write(self.fd, b"abc")
        self.assertEqual(self.run_stream(Body([b"def"]), start=3, expected=6), 6)
        self.assertEqual(Path(self.path).read_bytes(), b"abcdef")


class HeaderTests(unittest.TestCase):
    def test_only_a_strong_etag_or_a_date_can_prove_the_same_bytes(self):
        self.assertEqual(d._validator(Headers(ETag='"abc"')), {"etag": '"abc"'})
        self.assertEqual(d._validator(Headers(ETag='W/"abc"', Last_Modified="Wed, 21 Oct 2026 07:28:00 GMT")),
                         {"last_modified": "Wed, 21 Oct 2026 07:28:00 GMT"})
        self.assertIsNone(d._validator(Headers(ETag='W/"abc"')))
        self.assertIsNone(d._validator(Headers()))

    def test_the_suggested_name_and_a_safe_file_name(self):
        self.assertEqual(d._filename("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"), "résumé.pdf")
        self.assertEqual(d._filename('attachment; filename="tool.zip"'), "tool.zip")
        self.assertIsNone(d._filename("inline"))
        self.assertEqual(d.safe_name(None, URL), "tool-1.0.tar.gz")
        self.assertEqual(d.safe_name("../../.bashrc", URL), "bashrc")
        self.assertEqual(d.safe_name("a/b\\c.txt", URL), "c.txt")
        self.assertEqual(d.safe_name("bad\x1bname‮.txt", URL), "bad_name_.txt")
        self.assertEqual(d.safe_name("", "https://files.example.org/"), "download")
        long = d.safe_name("x" * 300 + ".tar.gz", URL)
        self.assertLessEqual(len(long.encode()), 120)
        self.assertTrue(long.endswith(".gz"))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        home = pwd.getpwuid(os.getuid()).pw_dir
        self.home = self.enterContext(tempfile.TemporaryDirectory(prefix="ai-download-", dir=home))
        self.folder = d.quarantine(self.home)

    def broker(self, *results, body=b"PAYLOAD"):
        """A fake broker run: writes `body` (or a part of it) into the part file and returns each result in turn."""
        calls = []
        queue = list(results)

        def run(request, folder, watched):
            calls.append(dict(request))
            result = queue.pop(0)
            written = result.pop("_write", body)
            with open(os.path.join(folder, request["part"]), "r+b" if request["offset"] else "wb") as handle:
                handle.seek(request["offset"])
                handle.truncate()
                handle.write(written)
            return result
        return run, calls

    def ok(self, size=7, filename=None):
        return {"final_url": URL, "size": size, "content_type": "application/gzip", "filename": filename}

    def test_the_quarantine_folder_is_private_and_fresh(self):
        info = os.stat(self.folder)
        self.assertEqual(info.st_mode & 0o777, 0o700)
        self.assertNotEqual(d.quarantine(self.home), self.folder)

    def test_a_complete_file_gets_its_name_hash_and_private_mode(self):
        run, _ = self.broker(self.ok())
        found = d.download(URL, self.folder, run=run)
        self.assertEqual(found["name"], "tool-1.0.tar.gz")
        self.assertEqual(found["sha256"], hashlib.sha256(b"PAYLOAD").hexdigest())
        self.assertEqual((found["size"], found["content_type"]), (7, "application/gzip"))
        path = Path(self.folder, "tool-1.0.tar.gz")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)                    # never executable
        self.assertEqual(os.listdir(self.folder), ["tool-1.0.tar.gz"])          # no part file left
        run, _ = self.broker(self.ok())
        self.assertEqual(d.download(URL, self.folder, run=run)["name"], "tool-1.0 (2).tar.gz")

    def test_a_dropped_connection_resumes_only_with_a_validator(self):
        run, calls = self.broker({"error": "incomplete", "size": 3, "validator": {"etag": '"v1"'}, "_write": b"PAY"},
                                 dict(self.ok(), _write=b"LOAD"))
        self.assertEqual(d.download(URL, self.folder, run=run)["sha256"], hashlib.sha256(b"PAYLOAD").hexdigest())
        self.assertEqual([(c["offset"], c["validator"]) for c in calls], [(0, None), (3, {"etag": '"v1"'})])
        run, calls = self.broker({"error": "network", "size": 3, "validator": None, "_write": b"PAY"}, self.ok())
        d.download(URL, self.folder, run=run)
        self.assertEqual([c["offset"] for c in calls], [0, 0])                  # no validator: from zero again

    def test_failures_cancellation_and_a_wrong_size_leave_no_file(self):
        for results, code in (([{"error": "incomplete", "size": 3, "validator": None, "_write": b"PAY"}] * 3, "incomplete"),
                              ([{"error": "status", "size": 0, "validator": None, "_write": b""}], "status"),
                              ([{"error": "space", "size": 0, "validator": None, "_write": b""}], "space"),
                              ([{"error": "cancelled", "size": 3, "validator": None, "_write": b"PAY"}], "cancelled"),
                              ([dict(self.ok(size=99))], "incomplete"),
                              ([{"error": "made-up", "size": 0, "validator": None, "_write": b""}], "response")):
            with self.subTest(code=code):
                run, _ = self.broker(*[dict(item) for item in results])
                with self.assertRaises(d.DownloadError) as caught:
                    d.download(URL, self.folder, run=run)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(os.listdir(self.folder), [])

    def test_an_address_that_is_not_public_https_never_reaches_the_broker(self):
        run = Mock()
        for url in ("http://files.example.org/a", "https://127.0.0.1/a", "https://user:pw@files.example.org/a"):
            with self.subTest(url=url), self.assertRaises(d.WebError):
                d.download(url, self.folder, run=run)
        run.assert_not_called()

    def test_progress_reports_the_part_size(self):
        seen = []
        watch = d._watch(self.folder, "abc.part", None, seen.append)
        Path(self.folder, "abc.part").write_bytes(b"12345")
        self.assertFalse(watch())
        self.assertEqual(seen, [5])

    def test_the_sandbox_gets_only_the_quarantine_folder_writable(self):
        from ai_desktop import web_broker
        with patch("shutil.which", return_value="/usr/bin/bwrap"):
            argv = web_broker._sandbox("pass", self.folder)
        binds = [(argv[i + 1], argv[i + 2]) for i, item in enumerate(argv) if item == "--bind"]
        self.assertEqual(binds, [(self.folder, "/download")])
        self.assertFalse([p for p in argv if p in ("/home", "/run") or p.startswith("/run/user")])


class ContractTests(unittest.TestCase):
    ACCESS = {"files": [], "systemd": [], "weather": False, "downloads": True}

    def test_a_download_plan_needs_access_and_a_bounded_list(self):
        plan = {"action": "download", "urls": [URL]}
        self.assertEqual(assistant_contract.parse_plan(json.dumps(plan), self.ACCESS), plan)
        without = {"files": [], "systemd": [], "weather": False}
        for access, value in ((without, plan), (self.ACCESS, {"action": "download", "urls": []}),
                              (self.ACCESS, {"action": "download", "urls": [URL] * 2}),
                              (self.ACCESS, {"action": "download", "urls": [URL + str(i) for i in range(9)]}),
                              (self.ACCESS, {"action": "download", "urls": [URL], "to": "~"})):
            with self.subTest(value=value), self.assertRaises(Exception):
                assistant_contract.parse_plan(json.dumps(value), access)
        self.assertEqual(assistant_contract.validate_access(self.ACCESS)["downloads"], True)
        with self.assertRaises(ValueError):
            assistant_contract.validate_access(dict(self.ACCESS, downloads=False))

    def test_the_download_step_the_model_receives(self):
        ok = {"op": "download", "url": URL, "status": "ok", "reason": "", "name": "tool.tar.gz", "size": 7,
              "sha256": "a" * 64, "content_type": "application/gzip",
              "folder": "~/.local/state/ai-desktop/downloads/20261112-114452-5ca00f18"}
        document = {"steps": [ok], "remaining": {"rounds": 1, "corrections": 1, "commands": 1}}
        self.assertEqual(discovery_contract.validate_discovery(document)["steps"][0], ok)
        failed = dict(ok, status="failed", reason="space", name="", size=0, sha256="", content_type="", folder="")
        discovery_contract.validate_discovery(dict(document, steps=[failed]))
        for bad in (dict(ok, sha256="x"), dict(ok, status="failed"), dict(failed, size=3),
                    dict(ok, url="http://files.example.org/a"), dict(ok, name="a\x1b"), dict(ok, extra=1),
                    dict(ok, folder=""), dict(ok, folder="~/elsewhere"), dict(failed, folder=ok["folder"])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                discovery_contract.validate_discovery(dict(document, steps=[bad]))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Mock()
        self.ledger.known_urls = {URL}
        self.steps = []
        self.ledger.add_download.side_effect = self.steps.append
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_a_declined_set_downloads_nothing_and_marks_a_composed_address(self):
        other = "https://elsewhere.example.org/file.bin"
        with patch("ai_desktop.terminal_consent.confirm", return_value=False), \
                patch.object(assistant_flow, "confirm", return_value=False), \
                patch.object(d, "download") as download, contextlib.redirect_stdout(io.StringIO()) as out:
            assistant_flow._download(self.ledger, [URL, other, "http://x.example.org/a"], Mock(), None, Mock())
        download.assert_not_called()
        self.assertIn("composed by the model", out.getvalue().split(other)[1].splitlines()[0])
        self.assertNotIn("composed by the model", out.getvalue().split(URL)[1].splitlines()[0])
        self.assertEqual([(s["status"], s["url"]) for s in self.steps],
                         [("refused", ""), ("declined", URL), ("declined", other)])

    def test_approved_files_become_steps_and_ctrl_c_cancels_the_rest(self):
        found = {"url": URL, "final_url": URL, "name": "tool.tar.gz", "size": 7, "sha256": "a" * 64,
                 "content_type": "application/gzip"}
        second, third = URL + "?2", URL + "?3"
        self.ledger.known_urls = {URL, second, third}
        with patch.object(assistant_flow, "confirm", return_value=True), \
                patch.object(d, "quarantine", return_value=d._home() + "/.local/state/ai-desktop/downloads/20261112-114452-5ca00f18"), \
                patch.object(d, "download", side_effect=[found, KeyboardInterrupt()]) as download, \
                patch.object(assistant_flow.conversation_state, "note_action"):
            assistant_flow._download(self.ledger, [URL, second, third], Mock(), None, Mock())
        self.assertEqual(download.call_count, 2)
        self.assertEqual([s["status"] for s in self.steps], ["ok", "cancelled", "cancelled"])
        self.assertEqual(self.steps[0]["sha256"], "a" * 64)
        self.assertEqual(self.steps[0]["folder"], "~/.local/state/ai-desktop/downloads/20261112-114452-5ca00f18")
        self.assertEqual(self.steps[1]["folder"], "")


class PolicyTests(unittest.TestCase):
    def test_downloads_is_an_explicit_network_switch(self):
        from ai_desktop import permissions
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="ai-download-policy-", dir=real_home)))
        self.enterContext(patch.object(permissions, "_identity", return_value=(os.getuid(), str(home))))
        path = home / ".config/ai-desktop/permissions.toml"
        path.parent.mkdir(parents=True, mode=0o700)
        for setting, expected in (("", False), ("downloads = false\n", False), ("downloads = true\n", True)):
            with self.subTest(setting=setting):
                path.write_text("version = 2\n[network]\nclaude = true\n" + setting
                                + "[files]\nread = [\"~/.config/**\"]\ndeny = []\n", encoding="utf-8")
                path.chmod(0o600)
                policy = permissions.load_policy()
                self.assertEqual(policy.access().get("downloads", False), expected)
                self.assertEqual(policy.sources().get("downloads", False), expected)
        path.write_text("version = 2\n[network]\ndownloads = 1\n[files]\nread = []\ndeny = []\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            permissions.load_policy()


if __name__ == "__main__":
    unittest.main()
