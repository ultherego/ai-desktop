"""Actual ask request generation for bounded excerpts from large local sources."""
import copy
import json
import os
from unittest.mock import patch

from ai_desktop import analysis_contract as wire, ask_content, assistant_contract, assistant_flow as flow
from ai_desktop import conversation_state, discovery_contract, execution, permissions
from ai_desktop.question_consent import ConversationConsent
from test_ask_discovery import Base, options, sel, ANSWER, ANSWER_PLAN, contents as contents_of


class AskContentTests(Base):
    def run_flow(self, responses, *, question="Read large.log", before=None, consent=None):
        self.wire = []
        replies = iter(responses)

        def request(text, args, reader, **kwargs):
            if before is not None:
                before(len(self.wire) + 1, kwargs)
            fields = {name: kwargs[name] for name in ("access", "context", "discovery", "web") if name in kwargs}
            gate = wire.AnalysisConsent(None, question=text, **fields)
            permit = kwargs["question_consent"]
            permit.claim(gate, text, **fields)
            approval = permit.authorize(gate, text, **fields)
            decoded, digest = wire.parse_request(approval.payload.encode())
            self.assertEqual(digest, approval.sha256)
            self.wire.append(json.loads(decoded))
            reply = next(replies)
            return reply(self.wire[-1]) if callable(reply) else reply

        profile = {"provider": "claude", "model": "default", "effort": "default", "instructions": "",
                   "profile": "diagnostic", "access": "read-only"}
        with execution.using(profile):
            return flow.run(question, options(), self.reader, request, conversation_consent=consent)

    @staticmethod
    def search(path="large.log", needle="UNIQUE-MARKER", cursor=0):
        return json.dumps({"action": "search-content", "file": sel(path), "needle": needle, "cursor": cursor})

    @staticmethod
    def ranges(path="large.log", first=1, last=1):
        return json.dumps({"action": "read-range", "file": sel(path), "first": first, "last": last})

    def test_32mib_file_is_searched_and_read_without_sending_the_whole_file(self):
        size = 32 * 1024 * 1024
        suffix = b"\nUNIQUE-MARKER verified=yes\n"
        path = self.home / ".config/large.log"
        path.write_bytes(b"x" * (size - len(suffix)) + suffix)

        def followup(request):
            step = request["discovery"]["steps"][-1]
            self.assertEqual(step["size"], size)
            self.assertEqual(step["operation"], "search")
            self.assertTrue(step["search"]["complete"])
            self.assertFalse(step["complete"])
            self.assertIn("UNIQUE-MARKER", step["fragments"][0]["text"])
            return self.ranges(first=2, last=2)

        with conversation_state.recording() as sources:
            answer = self.run_flow([self.search(), followup, ANSWER_PLAN])
        self.assertEqual(answer, ANSWER)
        self.assertEqual(self.confirm.call_count, 1)
        self.assertEqual(list(sources), [str(path)])
        final = self.wire[-1]["discovery"]["steps"][-1]
        self.assertEqual(final["fragments"][0]["text"], "UNIQUE-MARKER verified=yes\n")
        self.assertFalse(final["complete"])
        self.assertLess(sum(len(json.dumps(request).encode()) for request in self.wire), 65536)

    def test_a_file_that_is_not_utf8_is_a_correction_for_the_model_and_a_marked_whole_read(self):
        path = self.home / ".config/legacy.txt"
        path.write_bytes(b"caf\xe9 and more\n")
        self.file("other.txt", "additional facts\n")
        final_read = json.dumps({"action": "read", "files": [sel("legacy.txt"), sel("other.txt")], "systemd": [],
                                 "weather": None})
        answer = self.run_flow([self.ranges(path="legacy.txt"), final_read, ANSWER],
                               question="Read legacy.txt and other.txt")
        self.assertEqual(answer, ANSWER)
        refusal = self.wire[1]["discovery"]["steps"][-1]
        self.assertEqual((refusal["op"], refusal["code"]), ("refusal", "files-text"))     # the question goes on
        contents = {item["path"].rsplit("/", 1)[1]: item["content"] for item in self.wire[-1]["context"][0]["files"]}
        self.assertTrue(contents["legacy.txt"].startswith("[AI Desktop: 1 character(s)"))
        self.assertEqual(contents["other.txt"], "additional facts\n")

    @staticmethod
    def go_on(*paths):
        return json.dumps({"action": "read", "files": [sel(path) for path in paths], "systemd": [], "weather": None,
                           "continue": True})

    def test_a_read_with_continue_comes_back_as_steps_and_the_model_reads_on_file_by_file(self):
        self.file("first.txt", "first facts\n")
        self.file("second.txt", "second facts\n")
        self.file("third.txt", "third facts\n")
        answer = self.run_flow([self.go_on("first.txt"), self.go_on("second.txt", "third.txt"), ANSWER_PLAN],
                               question="Read first.txt, second.txt and third.txt one by one")
        self.assertEqual(answer, ANSWER)
        self.assertEqual([request["mode"] for request in self.wire], ["assistant"] * 3)     # never a final read
        steps = self.wire[-1]["discovery"]["steps"]
        self.assertEqual([(step["path"], step["complete"], step["fragments"][0]["text"]) for step in steps],
                         [("first.txt", True, "first facts\n"), ("second.txt", True, "second facts\n"),
                          ("third.txt", True, "third facts\n")])
        self.assertEqual(self.confirm.call_count, 1)

    def test_a_file_too_large_for_a_step_is_left_to_the_model_and_then_read_the_ordinary_way(self):
        self.file("small.txt", "small facts\n")
        self.file("large.log", "x" * (ask_content.MAX_FRAGMENT_BYTES + 10) + "\n")
        answer = self.run_flow([self.go_on("small.txt", "large.log"), self.go_on("large.log"), ANSWER],
                               question="Read small.txt and large.log")
        self.assertEqual(answer, ANSWER)
        self.assertEqual([step["path"] for step in self.wire[1]["discovery"]["steps"]], ["small.txt"])
        request = self.wire[-1]
        self.assertEqual(request["mode"], "question")              # large.log alone does not fit: the final read
        self.assertEqual([step["path"] for step in request["discovery"]["steps"]], ["small.txt"])
        self.assertEqual([item["path"].rsplit("/", 1)[1] for item in request["context"][0]["files"]], ["large.log"])

    def rounds(self, count):
        from dataclasses import replace
        original = permissions.Policy.limits
        return patch.object(permissions.Policy, "limits",
                            lambda policy: replace(original(policy), discovery_rounds=count))

    def test_used_up_steps_while_reading_ask_to_continue_and_read_only_the_files_left(self):
        for name in ("first.txt", "second.txt", "third.txt"):
            self.file(name, name + " facts\n")
        with self.rounds(1):
            answer = self.run_flow([self.go_on("first.txt", "second.txt", "third.txt"), ANSWER_PLAN],
                                   question="Read first.txt, second.txt and third.txt one by one")
        self.assertEqual(answer, ANSWER)
        self.assertIn("more-steps", [call.args[0] for call in self.confirm.call_args_list])
        self.assertEqual(list(contents_of(self.wire[-1])), ["first.txt", "second.txt", "third.txt"])

    def test_on_no_the_model_answers_from_the_files_it_has_with_the_limit_shown(self):
        for name in ("first.txt", "second.txt"):
            self.file(name, name + " facts\n")
        self.confirm.side_effect = lambda action, **kwargs: action != "more-steps"
        with self.rounds(1):
            answer = self.run_flow([self.go_on("first.txt", "second.txt"), ANSWER_PLAN],
                                   question="Read first.txt and second.txt one by one")
        self.assertEqual(answer, ANSWER)
        self.assertEqual(list(contents_of(self.wire[-1])), ["first.txt"])
        self.assertEqual(self.wire[-1]["mode"], "assistant")            # never a silent final read of the rest
        self.assertIn("the answer below uses what was collected", self.output.getvalue())

    def test_a_final_answer_wrapped_in_an_answer_plan_is_shown_as_text(self):
        self.assertEqual(flow._plain_answer(json.dumps({"action": "answer", "text": "Plain text."})), "Plain text.")
        for kept in ("Plain text.", '{"action": "read"}', '{"note": 1}', "{not json", None):
            self.assertEqual(flow._plain_answer(kept), kept)

    def test_continue_is_only_for_up_to_four_exact_files(self):
        access = permissions.load_policy().access()
        good = json.loads(self.go_on("a.txt"))
        self.assertTrue(assistant_contract.parse_plan(json.dumps(good), access)["continue"])
        for change in ({"continue": False}, {"files": [sel(name) for name in "abcde"]}, {"files": [sel("*.txt")]},
                       {"systemd": [{"scope": "system", "view": "failed"}]}, {"weather": "Example City"}):
            with self.subTest(change=change), self.assertRaises(assistant_contract.PlanError):
                assistant_contract.parse_plan(json.dumps(dict(good, **change)), access)

    def test_selected_excerpts_survive_into_the_final_answer_request(self):
        self.file("large.log", "one\ntwo\nthree\n")
        self.file("other.txt", "additional facts\n" + "x" * 40000 + "\n")     # too large for a step: the final read
        final_read = json.dumps({"action": "read", "files": [sel("other.txt")], "systemd": [], "weather": None})
        answer = self.run_flow([self.ranges(first=2, last=2), final_read, ANSWER], question="Read large.log and other.txt")
        self.assertEqual(answer, ANSWER)
        request = self.wire[-1]
        self.assertEqual(request["mode"], "question")
        self.assertEqual(request["discovery"]["steps"][0]["fragments"][0]["text"], "two\n")
        self.assertTrue(request["context"][0]["files"][0]["content"].startswith("additional facts\n"))

    def test_exact_file_scope_is_usable_without_guessing_a_relative_path(self):
        self.file("large.log", "complete file\n")
        text = self.config.read_text().replace('read = ["~/.config/**"]', 'read = ["~/.config/large.log"]')
        self.config.write_text(text)
        request = json.dumps({"action": "read-range", "file": "f0", "first": 1, "last": 10})
        self.run_flow([request, ANSWER_PLAN], question="Read the available file")
        step = self.wire[-1]["discovery"]["steps"][-1]
        self.assertEqual(step["path"], "")
        self.assertTrue(step["complete"])
        self.assertEqual(step["fragments"][0]["text"], "complete file\n")

    def test_allowed_directory_alias_records_both_spellings_and_detects_retargeting(self):
        relative = "GizmoSuite/panels.json"
        with conversation_state.recording() as sources:
            self.run_flow([self.ranges(relative), ANSWER_PLAN], question="Read " + relative)
        self.assertEqual(set(sources), {str(self.home / ".config/GizmoSuite/panels.json"),
                                       str(self.home / ".config/gizmo-suite/panels.json")})

        def retarget(number, kwargs):
            if number == 2:
                alias = self.home / ".config/GizmoSuite"
                alias.unlink()
                alias.symlink_to("gizmo")

        with self.assertRaises(wire.AnalysisDenied):
            self.run_flow([self.ranges(relative), ANSWER_PLAN], question="Read " + relative, before=retarget)
        self.assertEqual(len(self.wire), 1)

    def test_denied_path_is_not_opened_and_no_excerpt_leaves(self):
        denied = self.file("large.log", "PRIVATE-DENIED")
        self.write_policy(deny=[str(denied)])
        with self.assertRaises(flow.file_context.FileReadDenied):
            self.run_flow([self.ranges()])
        self.assertNotIn(str(denied), self.reads)
        self.assertEqual(len(self.wire), 1)

    def test_symlink_and_hardlink_cannot_be_read_as_content(self):
        source = self.file("source.log", "UNIQUE-MARKER")
        link = self.home / ".config/large.log"
        os.symlink(source, link)
        # A recoverable symlink refusal can be described, but has no content.
        self.run_flow([self.ranges(), ANSWER_PLAN])
        self.assertEqual(self.wire[-1]["discovery"]["steps"][-1]["op"], "refusal")
        link.unlink()
        os.link(source, link)
        with self.assertRaises(flow.file_context.FileReadDenied):
            self.run_flow([self.ranges()])
        self.assertEqual(len(self.wire), 1)

    def test_source_change_after_read_blocks_the_next_handoff(self):
        path = self.file("large.log", "original\n")

        def mutate(number, kwargs):
            if number == 2:
                path.write_text("changed\n")

        with self.assertRaises(wire.AnalysisDenied):
            self.run_flow([self.ranges(), ANSWER_PLAN], before=mutate)
        self.assertEqual(len(self.wire), 1)

    def test_unobserved_path_requires_discovery_and_cannot_be_guessed(self):
        self.file("large.log", "UNIQUE-MARKER")
        self.run_flow([self.ranges(), ANSWER_PLAN], question="Find the missing setting")
        refusal = self.wire[-1]["discovery"]["steps"][-1]
        self.assertEqual(refusal["code"], "discovery-required")
        self.assertNotIn("UNIQUE-MARKER", json.dumps(self.wire))

    def test_owner_excerpt_budget_is_not_bypassed_by_search(self):
        self.file("large.log", "x" * 2048 + "UNIQUE-MARKER" + "x" * 2048)
        self.write_policy('read_file_size = "1KiB"\nread_total_size = "1KiB"\n')
        # A search snippet fits, but its repeated copies still consume the
        # same per-question excerpt budget.
        self.run_flow([self.search(), self.search(), ANSWER_PLAN])
        steps = self.wire[-1]["discovery"]["steps"]
        self.assertEqual(steps[0]["op"], "content")
        self.assertEqual(steps[1]["op"], "refusal")
        self.assertEqual(steps[1]["code"], "files-total")

    def test_search_pagination_and_multibyte_fragment_hashes_are_exact(self):
        self.file("large.log", ("Zażółć UNIQUE-MARKER\n" * 25))

        def next_page(request):
            step = request["discovery"]["steps"][-1]
            self.assertFalse(step["search"]["complete"])
            self.assertEqual(len(step["fragments"]), 20)
            for fragment in step["fragments"]:
                self.assertEqual(ask_content._hash(fragment["text"].encode()), fragment["sha256"])
            return self.search(cursor=step["search"]["next"])

        self.run_flow([self.search(), next_page, ANSWER_PLAN])
        step = self.wire[-1]["discovery"]["steps"][-1]
        self.assertTrue(step["search"]["complete"])
        self.assertEqual(len(step["fragments"]), 5)

    def test_worker_refuses_forged_fragment_hash_or_complete_flag(self):
        self.file("large.log", "one\ntwo\n")
        self.run_flow([self.ranges(first=2, last=2), ANSWER_PLAN])
        document = self.wire[-1]["discovery"]
        for field in ("hash", "complete", "unknown"):
            changed = copy.deepcopy(document)
            step = changed["steps"][0]
            if field == "hash":
                step["fragments"][0]["sha256"] = "0" * 64
            elif field == "complete":
                step["complete"] = True
            else:
                step["run"] = "arbitrary command"
            with self.subTest(field=field), self.assertRaises(ValueError):
                discovery_contract.validate_discovery(changed)

    def test_plan_rejects_directory_wildcard_invalid_cursor_and_unknown_fields(self):
        access = permissions.load_policy().access()
        valid = json.loads(self.search())
        for update in ({"file": "f0"}, {"file": sel("large/**")}, {"cursor": -1}, {"cursor": True},
                       {"needle": ""}, {"needle": "a\nb"}, {"command": "anything"}):
            with self.subTest(update=update), self.assertRaises(assistant_contract.PlanError):
                assistant_contract.parse_plan(json.dumps({**valid, **update}), access)
        for first, last in ((True, 1), (0, 1), (2, 1)):
            with self.assertRaises(assistant_contract.PlanError):
                assistant_contract.parse_plan(self.ranges(first=first, last=last), access)


class ExcerptEditFlowTests(Base):
    """S3: an edit of a file above read_file_size first gets a files-size correction, then only the read excerpts."""
    policy_extra = 'write = ["~/.config/**"]\n'

    def test_an_edit_without_excerpts_is_corrected_and_then_receives_the_excerpt(self):
        from types import SimpleNamespace
        from ai_desktop import ask_edit
        path = self.home / ".config/big.conf"
        path.write_text("".join("opcja_%05d = %d\n" % (i, i) for i in range(1, 110000)) + "limit = 50\n")
        self.assertGreater(path.stat().st_size, permissions.load_policy().limits().read_file_size)
        edit = json.dumps({"action": "edit", "write": "w0", "files": [sel("big.conf")], "create": [], "check": "text"})
        seen = {}

        def prepare(plan, snapshot, args, reader, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(source={"instruction": "x", "files": [{"path": "big.conf", "content": "x\n", "writable": True}]},
                                   write_grant=None)
        profile = {"provider": "claude", "model": "default", "effort": "default", "instructions": "",
                   "profile": "diagnostic", "access": "changes"}
        with patch.object(ask_edit, "prepare", side_effect=prepare), \
                patch.object(ask_edit, "finish", return_value="ZAPISANO"):
            self.wire = []
            replies = iter([edit, AskContentTests.search("big.conf", "limit = "), edit])

            def request(text, args, reader, **kwargs):
                if "task" in kwargs:
                    return "{}"
                fields = {name: kwargs[name] for name in ("access", "context", "discovery", "web") if name in kwargs}
                gate = wire.AnalysisConsent(None, question=text, **fields)
                permit = kwargs["question_consent"]
                permit.claim(gate, text, **fields)
                approval = permit.authorize(gate, text, **fields)
                self.wire.append(json.loads(wire.parse_request(approval.payload.encode())[0]))
                return next(replies)
            with execution.using(profile):
                result = flow.run("Change the limit in big.conf to 200", options(), self.reader, request)
        self.assertEqual(result, "ZAPISANO")
        refusal = self.wire[1]["discovery"]["steps"][0]
        self.assertEqual((refusal["op"], refusal["code"]), ("refusal", "files-size"))
        (entry,) = seen["excerpts"].values()
        self.assertTrue(any("limit = 50" in fragment["text"] for fragment in entry["fragments"]))
