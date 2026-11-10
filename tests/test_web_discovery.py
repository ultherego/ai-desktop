"""The checked web answer as a discovery step: normalized and bounded like its contract, shortened to the owner's budget."""
import unittest
from types import SimpleNamespace

from ai_desktop import ask_discovery, discovery_contract as dc, permissions


def ledger(discovery_bytes=None):
    limits = permissions.Limits() if discovery_bytes is None else permissions.Limits(discovery_bytes=discovery_bytes)
    policy = SimpleNamespace(limits=lambda: limits, scopes=lambda: (), home="")
    return ask_discovery.DiscoveryLedger(policy, "pytanie")


def answer(body_bytes, sources=2):
    lines = ["[%d] Tytuł %d — `https://example.org/%d`" % (n, n, n) for n in range(1, sources + 1)]
    return ("Zdanie odpowiedzi.\n" * (body_bytes // 19 + 1)) + "\n\nSources:\n" + "\n".join(lines)


class WebStepTests(unittest.TestCase):
    def test_line_and_paragraph_separators_become_newlines(self):
        book = ledger()
        step = book.add_web("Pierwsza druga trzecia [1].\n\nSources:\n[1] T — `https://example.org/`")
        self.assertNotIn(" ", step["text"])
        self.assertIn("Pierwsza\ndruga\ntrzecia", step["text"])
        dc.validate_discovery(book.document())                 # the contract accepts what was stored

    def test_a_long_answer_is_cut_at_the_contract_limit_and_keeps_its_sources(self):
        book = ledger(262144)
        text = answer(41 * 1024)
        step = book.add_web(text)
        self.assertLessEqual(len(step["text"].encode("utf-8")), dc.MAX_WEB_TEXT)
        self.assertIn("…[shortened]", step["text"])
        self.assertTrue(step["text"].endswith(text[text.rindex("\n\nSources:\n"):]))
        dc.validate_discovery(book.document())

    def test_a_small_owner_budget_stores_a_shortened_answer_instead_of_stopping(self):
        book = ledger(4096)
        step = book.add_web(answer(20 * 1024))
        self.assertIn("…[shortened]", step["text"])
        self.assertIn("[2] Tytuł 2", step["text"])
        self.assertLessEqual(dc.diagnostic_bytes(book.document()), 4096)
        book.snapshot()                                         # the document still fits and validates

    def test_an_answer_that_fits_is_kept_unchanged_and_only_once(self):
        book = ledger()
        text = answer(1000)
        self.assertEqual(book.add_web(text)["text"], text)
        with self.assertRaises(ValueError):
            book.add_web(text)

    def test_without_room_for_a_useful_part_the_budget_is_reported(self):
        book = ledger(2048)
        book.steps.append({"op": "run", "argv": ["x"], "status": "ok", "exit": 0, "output": "o" * 1500,
                           "truncated": False})
        with self.assertRaises(ask_discovery.BudgetExhausted):
            book.add_web(answer(20 * 1024))

    def test_web_data_never_exceeds_its_limit(self):
        for limit in (600, 2000, dc.MAX_WEB_TEXT):
            text = dc.web_data(answer(50 * 1024, sources=8), limit)
            self.assertLessEqual(len(text.encode("utf-8")), limit)


if __name__ == "__main__":
    unittest.main()
