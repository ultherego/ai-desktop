"""One question may search the web first and then do local work (an edit, commands, reads) with what was found.

The search never carries local data; its checked answer goes to the following planner round as a discovery step
`op=web`. One search per question, only as the first step (CachyOS check: "search the web and fix a.py" could not be
done in one question).
"""
import json
import unittest

from ai_desktop import assistant_contract as plans, assistant_flow as flow, discovery_contract as dc
from ai_desktop.analysis_contract import AnalysisDenied
from test_ask_discovery import Base, QUESTION

WEB_TEXT = "Python 3.14 jest najnowszy [1].\n\nSources:\n[1] Python — `https://www.python.org/downloads/`"
CONTINUE = json.dumps({"action": "web", "continue": True})
FINAL = "Na podstawie wyszukiwania i plików: Gizmo ma ustawienie mode=basic."
READ = json.dumps({"action": "read", "files": [{"id": "f0", "path": "gizmo/settings.conf"}], "systemd": [],
                   "weather": None})


class WebThenLocalTests(Base):
    def write_policy(self, extra="", deny=()):
        denied = ["~/.config/ai-desktop/", "~/.config/vault/", "~/.config/gizmo/token.txt", *deny]
        self.config.write_text('version = 2\n[network]\nclaude = true\ncodex = true\nweb = true\n[files]\n'
                               'read = ["~/.config/**"]\ndeny = ' + json.dumps(denied) + '\n' + extra,
                               encoding="utf-8")
        self.config.chmod(0o600)

    def modes(self):
        return [payload["mode"] for payload in self.wire]

    def test_the_search_answer_reaches_the_next_planner_round_and_the_question_goes_on(self):
        answer = json.dumps({"action": "answer", "text": FINAL})
        self.assertEqual(self.run_flow([CONTINUE, WEB_TEXT, answer]), FINAL)
        self.assertEqual(self.modes(), ["assistant", "web", "assistant"])
        web = self.wire[1]
        for local in ("access", "context", "discovery", "task", "files"):
            self.assertNotIn(local, web)                                              # the search carries nothing local
        self.assertEqual(web["followup"], "local")                                 # it knows the question goes on
        self.assertEqual(self.wire[2]["discovery"]["steps"], [{"op": "web", "text": WEB_TEXT}])
        shown = self.output.getvalue()
        self.assertIn("Python 3.14", shown)
        self.assertIn("continues with the local part", shown)

    def test_after_the_search_the_model_may_read_files_and_answer_from_both(self):
        question = "Sprawdź w sieci zalecany tryb i porównaj z ~/.config/gizmo/settings.conf"
        self.assertEqual(self.run_flow([CONTINUE, WEB_TEXT, READ, FINAL], question=question), FINAL)
        self.assertEqual(self.modes(), ["assistant", "web", "assistant", "question"])
        final = self.wire[3]
        self.assertEqual(final["discovery"]["steps"][0], {"op": "web", "text": WEB_TEXT})
        self.assertIn("mode=basic", json.dumps(final["context"]))
        self.assertNoDeniedData()

    def test_a_plain_web_plan_still_ends_the_question_with_the_search_answer(self):
        self.assertEqual(self.run_flow([json.dumps({"action": "web"}), WEB_TEXT]), WEB_TEXT)
        self.assertEqual(self.modes(), ["assistant", "web"])
        self.assertNotIn("followup", self.wire[1])

    def test_a_second_search_in_the_same_question_is_refused(self):
        for second in (CONTINUE, json.dumps({"action": "web"})):
            with self.subTest(second=second), self.assertRaises(plans.PlanError):
                self.run_flow([CONTINUE, WEB_TEXT, second])
            self.assertEqual(self.modes(), ["assistant", "web", "assistant"])

    def test_a_search_after_local_work_is_refused(self):
        listing = json.dumps({"action": "list", "files": [{"id": "f0", "path": "gizmo"}]})
        with self.assertRaises(plans.PlanError):
            self.run_flow([listing, CONTINUE])
        self.assertNotIn("web", self.modes())


class WebDisabledTests(Base):
    def test_continue_needs_web_access_like_any_search(self):
        with self.assertRaises(plans.PlanError):
            self.run_flow([CONTINUE])
        self.assertEqual([payload["mode"] for payload in self.wire], ["assistant"])


class ContractTests(unittest.TestCase):
    def test_the_followup_mark_is_part_of_the_exact_payload_the_service_checks(self):
        from ai_desktop import analysis_contract as contract
        with contract.web_followup():
            marked = contract.application_input(None, "Pytanie", web=True)
        plain = contract.application_input(None, "Pytanie", web=True)
        self.assertEqual(json.loads(marked)["followup"], "local")
        self.assertNotIn("followup", json.loads(plain))
        for payload in (marked, plain):
            model_input, sha = contract.parse_request(payload.encode())
            self.assertEqual(sha, contract.digest(payload))
            self.assertEqual(json.loads(model_input).get("followup"), json.loads(payload).get("followup"))
        forged = json.loads(plain)
        forged["followup"] = "anything"
        with self.assertRaises(contract.AnalysisDenied):
            contract.parse_request(contract.canonical(forged).encode())


    def test_the_web_plan_takes_only_a_true_continue(self):
        access = {"files": [], "systemd": [], "weather": False, "web": True}
        self.assertEqual(plans.parse_plan(CONTINUE, access), {"action": "web", "continue": True})
        self.assertEqual(plans.parse_plan('{"action":"web"}', access), {"action": "web"})
        for raw in ('{"action":"web","continue":false}', '{"action":"web","continue":1}',
                    '{"action":"web","continue":true,"files":[]}', '{"action":"web","then":"edit"}'):
            with self.subTest(raw=raw), self.assertRaises(plans.PlanError):
                plans.parse_plan(raw, access)

    def test_a_web_step_is_bounded_clean_text_and_appears_once(self):
        remaining = {"rounds": 4, "corrections": 2, "commands": 24}
        good = {"op": "web", "text": WEB_TEXT}
        self.assertEqual(dc.validate_discovery({"steps": [good], "remaining": remaining})["steps"], [good])
        for steps in ([good, good], [{"op": "web", "text": ""}], [{"op": "web", "text": "x\x1b[31m"}],
                      [{"op": "web", "text": "x" * (dc.MAX_WEB_TEXT + 1)}], [{"op": "web", "text": "x", "url": "y"}],
                      [{"op": "web", "text": 5}]):
            with self.subTest(steps=str(steps)[:60]), self.assertRaises(ValueError):
                dc.validate_discovery({"steps": steps, "remaining": remaining})
        self.assertEqual(dc.scope_ids({"steps": [good]}), set())


if __name__ == "__main__":
    unittest.main()
