"""S2 acceptance (synthetic): a 200-turn conversation that is saved, resumed and still knows what was decided,
corrected, refused and done."""
import json
import unittest

from ai_desktop import conversation_memory as memory, conversation_state


def ops(*adds, marks=()):
    return {"add": [dict(item, supersedes=list(item.get("supersedes", ()))) for item in adds], "mark": list(marks)}


def entry(talk, text):
    return next(item for item in talk.task_memory()["entries"] if text in item["text"])


class LongConversationTests(unittest.TestCase):
    def build(self):
        talk = conversation_state.Conversation()
        talk.finish("Prepare a CSV export of orders", "Okay.", ops({"kind": "goal", "status": "active",
                    "text": "CSV export of orders"}), ())
        talk.finish("Separator?", "Semicolon.", ops({"kind": "decision", "status": "active",
                    "text": "SYNTHETIC separator: semicolon"}), ())
        early = entry(talk, "separator: semicolon")["id"]
        talk.finish("Comma after all", "Changing it.", ops({"kind": "correction", "status": "active",
                    "text": "SYNTHETIC separator: comma", "supersedes": [early]}), ())
        talk.finish("What about JSON?", "Proposal.", ops({"kind": "proposal", "status": "active",
                    "text": "SYNTHETIC switch the export to JSON"}), ())
        proposal = entry(talk, "switch the export to JSON")["id"]
        talk.finish("No, we stay with CSV", "Understood.", ops(marks=[{"id": proposal, "status": "rejected"}]), ())
        talk.finish("Save", "Saved.", None, (), actions=(
            "Change applied (test text passed; undo: ai-desktop task rollback): SYNTHETIC export.py [export.py]",))
        for number in range(7, 201):                     # long working phase that fills the memory with notes
            talk.finish("Working question %d" % number, "Answer %d" % number, ops(
                {"kind": "observation", "status": "active", "text": "SYNTHETIC note %d %s" % (number, "x" * 600)}), ())
        return talk

    def test_two_hundred_turns_keep_what_was_decided_corrected_refused_and_done_after_resume(self):
        talk = self.build()
        self.assertEqual(talk.number, 200)
        resumed = conversation_state.load(json.loads(json.dumps(conversation_state.export(talk))))
        for current in (talk, resumed):
            state = current.task_memory()
            self.assertGreater(state["dropped"], 0)                          # the budget really was exceeded
            goal = entry(current, "Prepare a CSV export")                    # the goal in the user's own words
            self.assertEqual((goal["kind"], goal["status"]), ("goal", "active"))
            self.assertEqual(entry(current, "separator: comma")["status"], "active")
            self.assertFalse([item for item in state["entries"]               # never both versions as true
                              if "separator: semicolon" in item["text"] and item["status"] == "active"])
            self.assertEqual(entry(current, "switch the export to JSON")["status"], "rejected")
            applied = entry(current, "Change applied")
            self.assertEqual((applied["status"], applied["kind"]), ("done", "observation"))
        self.assertEqual(len(resumed.transcript), 200)                       # the read-back thread keeps every turn
        self.assertIn("Working question 7", resumed.transcript[6]["question"])

    def test_working_notes_go_before_refused_and_completed_entries(self):
        entries = [{"id": "m%d.1" % n, "turn": n, "kind": kind, "status": status, "text": "t"}
                   for n, (kind, status) in enumerate([("observation", "done"), ("proposal", "rejected"),
                                                        ("decision", "superseded"), ("observation", "active"),
                                                        ("decision", "active"), ("goal", "active")], 1)]
        order = sorted(entries, key=memory._drop_order)
        self.assertEqual([(item["kind"], item["status"]) for item in order],
                         [("decision", "superseded"), ("observation", "active"), ("proposal", "rejected"),
                          ("observation", "done"), ("decision", "active"), ("goal", "active")])



class ContextViewTests(unittest.TestCase):
    def test_context_shows_what_is_sent_and_what_stays_local(self):
        import contextlib
        import io
        from ai_desktop import ask_commands
        talk = LongConversationTests("test_working_notes_go_before_refused_and_completed_entries").build()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(ask_commands.Controls().handle("/context", None, talk=talk))
        text = out.getvalue()
        self.assertIn("Turns in this conversation: 200", text)
        self.assertIn("Older turns not sent word for word: 136", text)
        self.assertIn("Left out of the task memory", text)


if __name__ == "__main__":
    unittest.main()
