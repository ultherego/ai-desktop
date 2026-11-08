"""Live answer text: the client's partial events, the answer-text preview, frames and the terminal blocks."""
import json
import socket
import sys
import unittest
from unittest.mock import patch

from ai_desktop import analysis_framing, analysis_sandbox, analysis_service, answer_stream, live_answer, process_io
from ai_desktop.analysis_contract import AnalysisDenied


def delta(text, session="s"):
    return json.dumps({"type": "stream_event", "session_id": session,
                       "event": {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}}).encode() + b"\n"


class AnswerPrefixTests(unittest.TestCase):
    def test_the_answer_text_is_decoded_as_far_as_it_arrived(self):
        raw = '{"action": "answer", "text": "Za\\u017c\\u00f3\\u0142\\n\\"x\\" \\ud83d\\ude00 end'
        self.assertEqual(answer_stream.answer_prefix(raw), 'Zażół\n"x" 😀 end')
        self.assertEqual(answer_stream.answer_prefix('{"action":"answer","text":"done","sources":[]}'), "done")

    def test_a_cut_escape_waits_for_more_data(self):
        for raw in ('{"action":"answer","text":"ab\\', '{"action":"answer","text":"ab\\u00',
                    '{"action":"answer","text":"ab\\ud83d', '{"action":"answer","text":"ab\\ud83d\\u'):
            with self.subTest(raw=raw):
                self.assertEqual(answer_stream.answer_prefix(raw), "ab")

    def test_nothing_is_shown_unless_action_answer_comes_first(self):
        for raw in ('{"action":"command","text":"rm -rf"', '{"text":"x","action":"answer"}', 'prose',
                    '{"action":"ans', '{"action":"answer","text":', '[1]', '', '{"action":"answer" "text":"x"',
                    '{"action":"answer","text":"bad \\q escape"', '{"action":"answer","text":"\x01"'):
            with self.subTest(raw=raw):
                self.assertEqual(answer_stream.answer_prefix(raw), "")

    def test_other_keys_between_action_and_text_are_skipped(self):
        raw = '{"action":"answer","sources":[{"path":"a\\"}b","n":[1,2]}],"n":1,"text":"ok'
        self.assertEqual(answer_stream.answer_prefix(raw), "ok")


class ClaudeStreamTests(unittest.TestCase):
    def test_partial_events_are_dropped_and_their_text_is_forwarded(self):
        seen, now = [], [0.0]
        stream = answer_stream.ClaudeStream(seen.append, clock=lambda: now[0])
        kept = stream(b'{"type":"system","session_id":"s"}\n' + delta('{"action":"answer",'))
        kept += stream(delta('"text":"Hel'))
        now[0] += 1
        kept += stream(delta('lo"}') + b'{"type":"result","session_id":"s"}')
        kept += stream(None)
        self.assertEqual(kept, b'{"type":"system","session_id":"s"}\n{"type":"result","session_id":"s"}\n')
        self.assertEqual("".join(seen), "Hello")

    def test_updates_are_throttled_and_the_end_flushes(self):
        seen, now = [], [0.0]
        stream = answer_stream.ClaudeStream(seen.append, json_answer=False, clock=lambda: now[0])
        stream(delta("a"))
        stream(delta("b"))
        stream(delta("c"))
        self.assertEqual(seen, ["a"])
        stream(None)
        self.assertEqual(seen, ["a", "bc"])

    def test_without_a_receiver_lines_are_only_filtered(self):
        stream = answer_stream.ClaudeStream()
        self.assertEqual(stream(delta("x") + b"not json\n"), b"not json\n")
        self.assertEqual(stream(b'{"type":"stream_event" broken\n'), b'{"type":"stream_event" broken\n')

    def test_an_endless_line_is_refused_and_too_much_text_only_stops_the_preview(self):
        stream = answer_stream.ClaudeStream()
        with self.assertRaises(AnalysisDenied):
            stream(b"x" * (answer_stream.MAX_LINE + 1))
        seen = []
        stream = answer_stream.ClaudeStream(seen.append, json_answer=False, clock=lambda: 0.0)
        with patch.object(answer_stream, "MAX_RAW_TEXT", 4):
            stream(delta("abc"))
            stream(delta("defgh"))
            stream(None)
        self.assertEqual("".join(seen), "abc")


class CompleteBlocksTests(unittest.TestCase):
    def test_blocks_end_at_a_blank_line_outside_a_code_fence(self):
        text = "A\n\n```\nx\n\ny\n```\n\nB\n\nC"
        blocks, consumed = answer_stream.complete_blocks(text)
        self.assertEqual(blocks, ["A", "```\nx\n\ny\n```", "B"])
        self.assertEqual(text[consumed:], "C")
        self.assertEqual(answer_stream.complete_blocks("only one line"), ([], 0))
        self.assertEqual(answer_stream.complete_blocks("~~~\na\n\nb"), ([], 0))


class LiveAnswerTests(unittest.TestCase):
    def live(self):
        lines = []
        live = live_answer.LiveAnswer(render=lambda text: "<" + text + ">", rule=lambda: "--", write=lines.append)
        return live, lines

    def test_finished_blocks_appear_and_the_final_answer_adds_only_the_rest(self):
        live, lines = self.live()
        stopped = []
        live.attach(lambda: stopped.append(True))
        live.feed("One.\n\nTw")
        live.feed("o.\n\nThr")
        self.assertEqual(stopped, [True])
        self.assertTrue(live.finish("One.\n\nTwo.\n\nThree."))
        self.assertEqual(lines, ["\n--\n", "<One.>", "", "<Two.>", "", "<Three.>"])

    def test_a_different_final_answer_is_printed_whole_with_a_note(self):
        live, lines = self.live()
        live.feed("Draft.\n\n")
        self.assertTrue(live.finish("Something else."))
        self.assertIn("differs", lines[-2])
        self.assertEqual(lines[-1], "<Something else.>")

    def test_nothing_shown_leaves_the_answer_to_the_caller_and_a_memory_line_stops_the_preview(self):
        live, lines = self.live()
        self.assertFalse(live.finish("Answer."))
        live.feed("@@MEMORY {}\n\nlater\n\n")
        self.assertEqual(lines, [])
        with live_answer.turn(render=str, rule=str) as current:
            self.assertIs(live_answer.current(), current)
        self.assertIsNone(live_answer.current())


class FilteredProcessTests(unittest.TestCase):
    def run_child(self, code, **kwargs):
        return process_io.bounded_process([sys.executable, "-c", code], env={}, timeout=20, **kwargs)

    def test_only_kept_bytes_count_against_the_limit_and_raw_output_is_bounded(self):
        code = "import sys; sys.stdout.write('drop\\n' * 5000)"
        drop = lambda block: b""
        events = "import sys, json; sys.stdout.write(%r * 3000 + 'keep')" % delta("x").decode()
        status, out, _ = self.run_child(events, limit=100, stdout_filter=answer_stream.ClaudeStream(),
                                        raw_limit=1 << 20)
        self.assertEqual((status, out), (0, b"keep\n"))
        with self.assertRaises(AnalysisDenied):
            self.run_child(code, limit=100, stdout_filter=drop, raw_limit=1000)
        with self.assertRaises(AnalysisDenied):
            self.run_child(code, limit=100, stdout_filter=drop)            # a filter needs a raw limit


class PartialFrameTests(unittest.TestCase):
    def pair(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        return left, right

    def test_live_frames_precede_the_response_only_when_asked_for(self):
        left, right = self.pair()
        analysis_framing.send_message(left, "Zażół".encode(), kind="partial")
        analysis_framing.send_message(left, b"final", kind="response")
        seen = []
        self.assertEqual(analysis_framing.receive_message(right, kind="response", partial=seen.append), b"final")
        self.assertEqual(seen, ["Zażół".encode()])
        analysis_framing.send_message(left, b"live", kind="partial")
        with self.assertRaises(AnalysisDenied):
            analysis_framing.receive_message(right, kind="response")
        with self.assertRaises(AnalysisDenied):
            analysis_framing.receive_message(right, kind="request", partial=seen.append)
        with self.assertRaises(AnalysisDenied):
            analysis_framing.send_message(left, b"x" * (analysis_framing.PARTIAL_BYTES + 1), kind="partial")

    def test_the_number_of_live_frames_is_bounded(self):
        left, right = self.pair()
        with patch.object(analysis_framing, "MAX_PARTIALS", 2):
            for _ in range(3):
                analysis_framing.send_message(left, b"x", kind="partial")
            with self.assertRaises(AnalysisDenied):
                analysis_framing.receive_message(right, kind="response", partial=lambda body: None)

    def test_the_service_splits_text_on_character_boundaries_and_stops_on_failure_or_budget(self):
        left, right = self.pair()
        live = analysis_service._live_sender(left)
        text = "ż" * analysis_framing.PARTIAL_BYTES
        live.text(text)
        live.status("search", 2)
        analysis_framing.send_message(left, b"end", kind="response")
        frames = []
        analysis_framing.receive_message(right, kind="response", partial=frames.append)
        self.assertGreater(len(frames), 2)
        self.assertTrue(all(frame[:1] == b"T" for frame in frames[:-1]))
        self.assertEqual(b"".join(frame[1:] for frame in frames[:-1]).decode(), text)
        for frame in frames[:-1]:
            frame[1:].decode("utf-8")
        self.assertEqual(frames[-1], b"Ssearch:2")
        with patch.object(analysis_service, "send_message", side_effect=AnalysisDenied("timeout")) as sent:
            live = analysis_service._live_sender(left)
            live.text("a")
            live.text("b")
            live.status("results", 1)
        self.assertEqual(sent.call_count, 1)
        with patch.object(analysis_service, "MAX_PARTIAL_TOTAL", 3), \
                patch.object(analysis_service, "send_message") as sent:
            live = analysis_service._live_sender(left)
            live.text("ab")
            live.text("cd")
            live.text("e")
            live.status("search", 1)
        self.assertEqual(sent.call_count, 1)
        with patch.object(analysis_service, "send_message") as sent:
            live = analysis_service._live_sender(left)
            for code, number in (("other", 1), ("search", -1), ("search", 100), ("search", True)):
                live.status(code, number)
        self.assertEqual(sent.call_count, 0)

    def test_the_terminal_side_never_loses_the_answer_to_the_preview(self):
        analysis_service._live_text(None)(b"Tread and dropped")
        shown, steps = [], []
        receive = analysis_service._live_text(shown.append, lambda code, number: steps.append((code, number)))
        receive(b"Tok")
        receive(b"Sresults:3")
        receive(b"T\xff")
        receive(b"Tlater")
        self.assertEqual((shown, steps), (["ok"], [("results", 3)]))
        for bad in (b"Sother:1", b"Ssearch:x", b"Ssearch:123", b"Xabc", b""):
            with self.subTest(bad=bad):
                shown, steps = [], []
                receive = analysis_service._live_text(shown.append, lambda code, number: steps.append(code))
                receive(bad)
                receive(b"Tafter")
                self.assertEqual((shown, steps), ([], []))
        analysis_service._live_text(lambda text: 1 / 0)(b"Tx")


class WebProgressTests(unittest.TestCase):
    def test_searches_results_and_writing_are_counted_without_their_content(self):
        steps = []
        stream = answer_stream.ClaudeStream(on_status=lambda code, number: steps.append((code, number)))
        search = {"type": "assistant", "session_id": "s", "message": {"content": [
            {"type": "text", "text": "secret query text"},
            {"type": "tool_use", "id": "a", "name": "WebSearch", "input": {"query": "q"}}]}}
        result = {"type": "user", "session_id": "s", "message": {"content": [{"type": "tool_result", "tool_use_id": "a"}]}}
        kept = stream(json.dumps(search).encode() + b"\n" + delta("early") + json.dumps(result).encode() + b"\n")
        kept += stream(delta('{"answer":"x') + delta("y"))
        kept += stream(None)
        self.assertEqual(steps, [("search", 1), ("results", 1), ("writing", 0)])
        self.assertEqual(kept.count(b"\n"), 2)                  # both kept events stay for the final checks
        self.assertEqual(answer_stream.STATUS, ("search", "results", "writing"))

    def test_a_web_transcript_with_partial_events_passes_its_checks_only_after_the_filter(self):
        from test_web_search_result import stream as web_stream, web_events
        from ai_desktop.web_search_result import web_text
        events = web_events()
        answer = events[-1]["result"]
        deltas = [{"type": "stream_event", "session_id": events[0]["session_id"], "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": answer[i:i + 7]}}}
            for i in range(0, len(answer), 7)]
        raw = web_stream(events[:-2] + deltas + events[-2:])
        with self.assertRaises(AnalysisDenied):
            web_text(raw)                                        # unfiltered, the events are not part of the contract
        steps, shown = [], []
        stream = answer_stream.ClaudeStream(on_status=lambda code, number: steps.append(code))
        kept = stream(raw) + stream(None)
        self.assertIn("Python to język programowania", str(web_text(kept)))
        self.assertEqual(steps, ["search", "results", "writing"])

    def test_malformed_or_other_events_report_nothing(self):
        steps = []
        stream = answer_stream.ClaudeStream(on_status=lambda code, number: steps.append(code))
        stream(b'{"type":"assistant","message":{"content":"tool_use"}}\n'
               b'{"type":"tool_result" broken\n'
               b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}}\n')
        stream(None)
        self.assertEqual(steps, [])

    def test_the_waiting_indicator_shows_fixed_labels(self):
        from ai_desktop import terminal_progress
        indicator = terminal_progress.WaitingIndicator()
        with self.assertRaises(ValueError):
            indicator.status("free text")
        with self.assertRaises(ValueError):
            indicator.status("search", 100)
        indicator.status("search", 1)                            # not drawing: ignored
        indicator._enabled, indicator._started = True, 0.0
        with patch.object(indicator, "_paint") as paint:
            indicator.status("search", 2)
            self.assertIn("Searching the web (2)", indicator._label)
            indicator.status("writing")
            self.assertTrue(indicator._label.endswith("Writing the answer"))
        self.assertEqual(paint.call_count, 2)


class ClaudeCommandTests(unittest.TestCase):
    def test_analysis_and_web_ask_for_partial_messages(self):
        self.assertIn("--include-partial-messages", analysis_sandbox.client_command("analyze"))
        self.assertIn("--include-partial-messages", analysis_sandbox.client_command("web"))
        self.assertEqual(analysis_sandbox.LIVE_MODES, {"assistant": True, "question": False})


if __name__ == "__main__":
    unittest.main()
