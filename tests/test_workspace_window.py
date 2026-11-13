"""The S3 engine in the ordinary conversation: a request carries a window of steps, the task goes on past it.

Older steps leave the window (one cleared trace names them, so they can be read again), the model's own notes stay in
every request, a file larger than one excerpt comes as pages instead of ending the question, and the question's consent
accepts a window only when every step that left it is counted and every step that stayed is unchanged.
"""
import json
import unittest

from ai_desktop import ask_discovery, assistant_contract, discovery_contract as dc, permissions
from ai_desktop.question_consent import _added_steps
from test_ask_content import AskContentTests
from test_ask_discovery import Base, sel, ANSWER, ANSWER_PLAN, contents as contents_of

LIST = {"op": "list", "directories": [{"id": "f0", "path": "", "entries": [], "truncated": False, "next": None}],
        "truncated": False}
REFUSAL = {"op": "refusal", "code": "files-missing", "selectors": [], "limit": None}


def read(*paths, notes=None):
    plan = {"action": "read", "files": [sel(path) for path in paths], "systemd": [], "weather": None}
    if notes is not None:
        plan["notes"] = notes
    return json.dumps(plan)


class ContractTests(unittest.TestCase):
    def document(self, *steps):
        return {"steps": list(steps), "remaining": {"rounds": 1, "corrections": 1, "commands": 0}}

    def test_notes_and_the_cleared_trace_are_closed_and_come_first(self):
        cleared = {"op": "cleared", "count": 3, "items": [{"op": "content", "what": "f0:a.txt lines 1-9 of 9"}]}
        notes = {"op": "notes", "text": "a.txt: settings.\nleft: b.txt"}
        self.assertEqual(dc.validate_discovery(self.document(cleared, notes, LIST))["steps"][:2], [cleared, notes])
        for steps in ((notes, cleared, LIST), (LIST, notes), (notes, notes), (cleared, cleared, LIST)):
            with self.subTest(order=[step["op"] for step in steps]), self.assertRaises(ValueError):
                dc.validate_discovery(self.document(*steps))
        for bad in (dict(notes, text="x" * (dc.NOTES_BYTES + 1)), dict(notes, text="a\x1b[0m"), dict(notes, text=" "),
                    dict(cleared, count=0), dict(cleared, items=[]), dict(cleared, items=[{"op": "skill", "what": "x"}]),
                    dict(cleared, items=[{"op": "list", "what": "x" * (dc.CLEARED_TEXT + 1)}]),
                    dict(cleared, count=1, items=cleared["items"] * 2)):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(ValueError):
                dc.validate_discovery(self.document(bad, LIST))

    def test_a_plan_may_carry_notes_that_grant_nothing(self):
        access = {"files": [{"id": "f0", "pattern": "~/.config/**"}], "systemd": [], "weather": False}
        plan = assistant_contract.parse_plan(read("a.txt", notes="found: a"), access)
        self.assertEqual(plan["notes"], "found: a")
        self.assertEqual(plan["action"], "read")
        answer = assistant_contract.parse_plan(json.dumps({"action": "answer", "text": "x", "notes": ""}), access)
        self.assertEqual(answer, {"action": "answer", "text": "x", "notes": ""})
        for notes in (1, "a\x00", "x" * (dc.NOTES_BYTES + 1)):
            with self.subTest(notes=str(notes)[:20]), self.assertRaises(assistant_contract.PlanError):
                assistant_contract.parse_plan(read("a.txt", notes=notes), access)
        with self.assertRaises(assistant_contract.PlanError):        # notes never hide a malformed plan
            assistant_contract.parse_plan(json.dumps({"action": "read", "notes": "x"}), access)


class ConsentWindowTests(unittest.TestCase):
    """bind_discovery: what stayed is unchanged and in order, what left is counted, and something new follows."""
    A, B, C = (dict(REFUSAL, limit=n) for n in (1, 2, 3))

    @staticmethod
    def cleared(count):
        return [{"op": "cleared", "count": count, "items": [{"op": "refusal", "what": "files-missing"}] * min(count, 1)}]

    def test_a_window_that_cleared_counted_steps_and_added_one_is_accepted(self):
        self.assertEqual(_added_steps([self.A, self.B], [self.A, self.B, self.C]), [self.C])
        self.assertEqual(_added_steps([self.A, self.B], self.cleared(1) + [self.B, self.C]), [self.C])
        self.assertEqual(_added_steps(self.cleared(1) + [self.B], self.cleared(2) + [self.C]), [self.C])
        notes = [{"op": "notes", "text": "n"}]
        self.assertEqual(_added_steps(notes + [self.A], notes[:0] + [self.A, self.B]), [self.B])
        self.assertEqual(_added_steps([self.A, self.A], self.cleared(1) + [self.A, self.A]), [self.A])

    def test_a_changed_reordered_uncounted_or_empty_round_is_refused(self):
        for before, after in (([self.A, self.B], [self.A, self.B]),                     # nothing new
                              ([self.A, self.B], [self.B, self.C]),                     # A left uncounted
                              ([self.A, self.B], [self.B, self.A, self.C]),             # reordered
                              ([self.A, self.B], [dict(self.A, limit=9), self.B, self.C]),   # changed
                              (self.cleared(2) + [self.A], self.cleared(1) + [self.A, self.B]),   # count went down
                              ([self.A], self.cleared(2) + [self.B])):                  # more counted than existed
            with self.subTest(after=[s.get("limit", s["op"]) for s in after]), self.assertRaises(ValueError):
                _added_steps(before, after)


class WindowFlowTests(Base):
    run_flow = AskContentTests.run_flow
    go_on = staticmethod(AskContentTests.go_on)
    rounds = AskContentTests.rounds

    def test_a_long_file_by_file_task_reads_past_the_window_without_a_limit(self):
        names = ["n%02d.txt" % index for index in range(24)]
        for name in names:
            self.file(name, name + " " + "x" * 1500 + "\n")
        self.write_policy("discovery_bytes = \"16KiB\"\n")
        seen = set()

        def collect(request):                       # like a model: asks for the next files it has not received yet
            seen.update(contents_of(request))
            left = [name for name in names if name not in seen]
            return self.go_on(*left[:4]) if left else ANSWER_PLAN
        answer = self.run_flow([collect] * 40, question="Read one by one: " + " ".join(names))
        self.assertEqual(answer, ANSWER)
        self.assertEqual(seen, set(names))                               # every file reached the model
        self.assertTrue(all(request["mode"] == "assistant" for request in self.wire))
        for request in self.wire[1:]:
            self.assertLessEqual(len(json.dumps(request["discovery"])), 16 * 1024 + 4096)
        self.assertNotIn("discovery-budget", json.dumps(self.wire))
        self.assertNotIn("the answer uses what was collected", self.output.getvalue())
        self.assertGreater(self.wire[-1]["discovery"]["steps"][0]["count"], 10)

    def test_notes_come_back_in_every_request_and_survive_the_window(self):
        self.file("first.txt", "a" * 3000 + "\n")
        self.file("second.txt", "b" * 3000 + "\n")
        self.write_policy("discovery_bytes = \"16KiB\"\n")
        answer = self.run_flow([read("first.txt", notes="first.txt: only a"), read("second.txt"), ANSWER_PLAN],
                               question="Read first.txt and second.txt one by one")
        self.assertEqual(answer, ANSWER)
        for request in self.wire[1:]:
            notes = [step for step in request["discovery"]["steps"] if step["op"] == "notes"]
            self.assertEqual(notes, [{"op": "notes", "text": "first.txt: only a"}])
        self.assertEqual([step["op"] for step in self.wire[-1]["discovery"]["steps"]], ["cleared", "notes", "content"])

    def test_notes_longer_than_a_quarter_of_a_small_window_are_shortened_visibly(self):
        self.write_policy("discovery_bytes = \"16KiB\"\n")
        ledger = ask_discovery.DiscoveryLedger(permissions.load_policy(), "question")
        ledger.note("z" * 8000)
        self.assertLessEqual(len(ledger.notes.encode()), 4096)
        self.assertTrue(ledger.notes.endswith("…[notes shortened: keep them shorter]"))
        ledger.note("   ")
        self.assertEqual(ledger.notes, "")
        with self.assertRaises(ValueError):
            ledger.note("z" * (dc.NOTES_BYTES + 1))

    def test_a_file_larger_than_one_excerpt_comes_as_pages_and_the_question_goes_on(self):
        lines = ["line %05d %s\n" % (index, "y" * 60) for index in range(1, 1201)]       # about 85 KiB
        self.file("big.c", "".join(lines))
        self.file("small.h", "int small;\n")
        rest = json.dumps({"action": "read-range", "file": sel("big.c"), "first": 0, "last": 1200})

        def next_page(request):
            step = contents_step(request, "big.c")
            self.assertFalse(step["complete"])
            self.assertEqual(step["fragments"][0]["first"], 1)
            last = step["fragments"][0]["last"]
            self.assertTrue(step["fragments"][0]["text"].endswith("\n"))         # whole lines only
            self.assertLessEqual(len(step["fragments"][0]["text"].encode()), 32 * 1024)
            return rest.replace('"first": 0', '"first": %d' % (last + 1)).replace('"last": 1200', '"last": %d' % (last + 300))
        answer = self.run_flow([self.go_on("big.c", "small.h"), next_page, ANSWER_PLAN],
                               question="Read big.c and small.h")
        self.assertEqual(answer, ANSWER)
        self.assertTrue(all(request["mode"] == "assistant" for request in self.wire))
        self.assertEqual(contents_of(self.wire[1])["small.h"], "int small;\n")
        second = [s for s in self.wire[2]["discovery"]["steps"] if s["op"] == "content" and s["path"] == "big.c"][-1]
        self.assertGreater(second["fragments"][0]["first"], 1)

    def test_excerpts_that_left_the_window_still_count_for_an_edit(self):
        self.file("first.txt", "a" * 3000 + "\n")
        self.file("second.txt", "b" * 3000 + "\n")
        self.write_policy("discovery_bytes = \"16KiB\"\n")
        policy = permissions.load_policy()
        ledger = ask_discovery.DiscoveryLedger(policy, "Read first.txt and second.txt")
        permit = PermitStub()
        ledger.whole(sel("first.txt"), permit)
        ledger.snapshot()
        ledger.whole(sel("second.txt"), permit)
        self.assertEqual(ledger.cleared["count"], 1)
        self.assertEqual(len(ledger.contents), 1)                                 # only second.txt is in the window
        self.assertEqual(sorted(path.rsplit("/", 1)[1] for path in ledger.excerpts()), ["first.txt", "second.txt"])

    def test_a_step_that_has_not_reached_the_model_never_leaves_the_window(self):
        self.file("first.txt", "a" * 3000 + "\n")
        self.file("second.txt", "b" * 3000 + "\n")
        self.write_policy("discovery_bytes = \"16KiB\"\n")
        ledger = ask_discovery.DiscoveryLedger(permissions.load_policy(), "Read first.txt and second.txt")
        ledger.whole(sel("first.txt"), PermitStub())
        with self.assertRaises(ask_discovery.WindowFull):
            ledger.whole(sel("second.txt"), PermitStub())
        self.assertEqual(ledger.cleared["count"], 0)
        self.assertEqual(len(ledger.steps), 1)


def contents_step(request, name):
    return [step for step in request["discovery"]["steps"] if step["op"] == "content" and step["path"] == name][-1]


class PermitStub:
    def require_files(self, policy, selections):
        pass

    def bind_file_selection(self, policy, paths):
        pass

    def check(self):
        pass


if __name__ == "__main__":
    unittest.main()
