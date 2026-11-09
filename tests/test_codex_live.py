"""Codex answers appear while they are written: display only, never the answer that counts."""
import io
import json
import unittest
from unittest.mock import patch

from ai_desktop import analysis_contract as contract, answer_stream, codex_child, codex_protocol as codex
from test_codex_protocol import application, item_event, notification, ready


def started(protocol, ident, phase):
    protocol.feed(notification("item/started", threadId="thr_a", turnId="turn_a", startedAtMs=1,
                               item={"type": "agentMessage", "id": ident, "text": "", "phase": phase}))


def delta(protocol, ident, text):
    protocol.feed(notification("item/agentMessage/delta", threadId="thr_a", turnId="turn_a", itemId=ident, delta=text))


class ProtocolDeltaTests(unittest.TestCase):
    def protocol(self):
        protocol = codex.Protocol(application())
        ready(protocol)
        seen = []
        protocol.on_delta = seen.append
        return protocol, seen

    def test_only_the_answer_message_is_shown_never_commentary(self):
        protocol, seen = self.protocol()
        started(protocol, "note", "commentary")
        delta(protocol, "note", "Myślę nad tym…")
        started(protocol, "answer", "final_answer")
        delta(protocol, "answer", "Odpo")
        delta(protocol, "answer", "wiedź")
        self.assertEqual(seen, ["Odpo", "wiedź"])

    def test_a_second_answer_message_is_not_mixed_into_the_first(self):
        protocol, seen = self.protocol()
        started(protocol, "one", None)
        started(protocol, "two", None)
        delta(protocol, "two", "drugi")
        delta(protocol, "one", "pierwszy")
        self.assertEqual(seen, ["pierwszy"])

    def test_a_failing_display_never_ends_the_request(self):
        protocol, _ = self.protocol()
        calls = []

        def broken(text):
            calls.append(text)
            raise RuntimeError("display")
        protocol.on_delta = broken
        started(protocol, "answer", "final_answer")
        delta(protocol, "answer", "a")
        delta(protocol, "answer", "b")
        self.assertEqual(calls, ["a"])
        self.assertIsNone(protocol.on_delta)

    def test_the_delta_budget_still_applies(self):
        protocol, _ = self.protocol()
        started(protocol, "answer", "final_answer")
        with self.assertRaises(contract.AnalysisDenied) as raised:
            delta(protocol, "answer", "x" * (protocol.output_limit + 1))
        self.assertEqual(raised.exception.reason, "output-limit")


class ChildWriterTests(unittest.TestCase):
    def test_deltas_are_their_own_lines_and_never_carry_the_token(self):
        out = io.StringIO()
        with patch.object(codex_child.sys, "stdout", out):
            write = codex_child._live_writer("TOKEN-SYNTHETIC")
            write("Część ")
            write("z TOKEN-SYNTHETIC w środku")
            write('"cudzysłów"\n')
        lines = out.getvalue().splitlines()
        self.assertEqual([json.loads(line) for line in lines], [{"partial": "Część "}, {"partial": '"cudzysłów"\n'}])

    def test_only_answer_modes_are_live(self):
        self.assertTrue(codex_child._live_mode(application()))
        workspace = json.dumps({"mode": "workspace"}).encode()
        self.assertFalse(codex_child._live_mode(workspace))
        self.assertFalse(codex_child._live_mode(b"not json"))


class CodexStreamTests(unittest.TestCase):
    def final(self):
        return contract.canonical({"text": '{"action":"answer","text":"Gotowe."}', "meta": None}).encode() + b"\n"

    def test_partial_lines_are_removed_and_the_answer_text_is_shown(self):
        shown = []
        stream = answer_stream.CodexStream(shown.append, json_answer=True, clock=iter(range(0, 100, 1)).__next__)
        raw = b"".join(contract.canonical({"partial": piece}).encode() + b"\n"
                       for piece in ('{"action":"answer","text":"Got', 'owe."}')) + self.final()
        kept = stream(raw) + stream(None)
        self.assertEqual(kept, self.final())                      # the result checks see exactly the final line
        self.assertEqual("".join(shown), "Gotowe.")

    def test_a_command_plan_is_never_previewed(self):
        shown = []
        stream = answer_stream.CodexStream(shown.append, json_answer=True)
        stream(contract.canonical({"partial": '{"action":"run","argv":["rm"]}'}).encode() + b"\n")
        stream(None)
        self.assertEqual(shown, [])

    def test_anything_else_is_kept_for_the_result_checks(self):
        stream = answer_stream.CodexStream(None)
        for line in (b'{"partial": 5}', b'{"partial":"x","more":1}', b'{"partial":', b"CLIENT: REFUSED"):
            with self.subTest(line=line):
                self.assertEqual(stream(line + b"\n"), line + b"\n")

    def test_without_a_display_the_lines_are_still_removed(self):
        stream = answer_stream.CodexStream(None)
        self.assertEqual(stream(b'{"partial":"x"}\n'), b"")


class ServiceTests(unittest.TestCase):
    def test_a_codex_request_gets_the_bounded_live_sender_and_its_frames_reach_the_terminal(self):
        from unittest.mock import Mock
        from ai_desktop import analysis_service as service, codex_runtime
        from ai_desktop.analysis_contract import AnalysisConsent
        from test_analysis_service import records
        execution = {"provider": "codex", "model": "default", "effort": "default", "instructions": "",
                     "profile": "diagnostic", "access": "read-only"}
        gate = AnalysisConsent(None, question="Pytanie", context=[], execution=execution)
        conn, listener = Mock(), Mock()
        for obj in (conn, listener):
            obj.__enter__ = Mock(return_value=obj)
            obj.__exit__ = Mock(return_value=False)
        conn.gettimeout.return_value = 5
        listener.accept.return_value = (conn, None)
        sent = []
        conn.send.side_effect = lambda raw: sent.append(raw) or len(raw)
        conn.recvmsg.side_effect = [(raw, [], 0, None) for raw in records(gate.payload.encode())]
        selected = lambda readers, *_: (readers if readers == [listener] else [], [], [])

        def run(action, body, **kwargs):
            kwargs["partial"]("Na żywo")
            return "Odpowiedź końcowa"
        with patch.object(service, "activation_listener", return_value=listener), \
                patch.object(service.select, "select", side_effect=selected), \
                patch.object(service.e2_ipc, "_peer_uid"), patch.object(service, "supervisor_ready"), \
                patch.object(service.os, "geteuid", return_value=2000), \
                patch.object(service, "send_partial", side_effect=lambda conn, body, **_: sent.append(b"P:" + body)), \
                patch.object(codex_runtime, "run_client", side_effect=run) as codex_run, \
                patch.object(service, "run_client") as claude:
            service.receive_once(1000)
        claude.assert_not_called()
        codex_run.assert_called_once()
        self.assertIn(b"P:T" + "Na żywo".encode(), sent)


if __name__ == "__main__":
    unittest.main()
