"""Checks already passed are not repeated for every question: fewer client launches, the same isolation."""
import unittest

from ai_desktop import analysis_sandbox as sandbox, check_memory
from ai_desktop.analysis_contract import AnalysisDenied
from test_system_claude_runtime import ANSWER, PROBES, SIGNED_OUT, SystemClaudeRuntimeTests


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class MemoryTests(unittest.TestCase):
    def setUp(self):
        check_memory.clear()
        self.addCleanup(check_memory.clear)
        self.clock, self.calls = Clock(), []

    def check(self, value="ok"):
        def run():
            self.calls.append(value)
            return value
        return run

    def test_compatibility_is_remembered_per_exact_client_for_a_while(self):
        self.assertEqual(check_memory.compatible("fp-a", self.check(), clock=self.clock), "ok")
        self.assertEqual(check_memory.compatible("fp-a", self.check(), clock=self.clock), "ok")
        self.assertEqual(len(self.calls), 1)
        check_memory.compatible("fp-b", self.check(), clock=self.clock)       # a changed client is checked again
        self.assertEqual(len(self.calls), 2)
        self.clock.now += check_memory.COMPAT_SECONDS
        check_memory.compatible("fp-a", self.check(), clock=self.clock)
        self.assertEqual(len(self.calls), 3)

    def test_without_an_exact_identity_nothing_is_remembered(self):
        for _ in range(2):
            check_memory.compatible(None, self.check(), clock=self.clock)
            check_memory.signed_in(("account", None), self.check(), clock=self.clock)
        self.assertEqual(len(self.calls), 4)

    def test_a_failed_check_is_never_remembered(self):
        def failing():
            self.calls.append("x")
            raise AnalysisDenied("auth-required")
        for _ in range(2):
            with self.assertRaises(AnalysisDenied):
                check_memory.signed_in(("acc", "fp"), failing, clock=self.clock)
            with self.assertRaises(AnalysisDenied):
                check_memory.compatible("fp", failing, clock=self.clock)
        self.assertEqual(len(self.calls), 4)

    def test_a_login_status_is_short_lived_and_forgotten_on_a_login_problem(self):
        key = ("/profile", "fp")
        check_memory.signed_in(key, self.check("max"), clock=self.clock)
        self.assertEqual(check_memory.signed_in(key, self.check("other"), clock=self.clock), "max")
        check_memory.forget_account("/profile")
        self.assertEqual(check_memory.signed_in(key, self.check("pro"), clock=self.clock), "pro")
        self.clock.now += check_memory.STATUS_SECONDS
        self.assertEqual(check_memory.signed_in(key, self.check("again"), clock=self.clock), "again")
        self.assertEqual(self.calls, ["max", "pro", "again"])

    def test_a_none_plan_is_remembered_too(self):
        check_memory.signed_in(("a", "fp"), self.check(None), clock=self.clock)
        self.assertIsNone(check_memory.signed_in(("a", "fp"), self.check("x"), clock=self.clock))
        self.assertEqual(self.calls, [None])

    def test_the_memory_is_bounded(self):
        for index in range(check_memory.MAX_ENTRIES + 5):
            self.clock.now += 1
            check_memory.compatible("fp-%d" % index, self.check(), clock=self.clock)
        self.assertLessEqual(len(check_memory._compat), check_memory.MAX_ENTRIES)
        self.assertNotIn("fp-0", check_memory._compat)                         # the oldest went first


class ClaudeRequestTests(SystemClaudeRuntimeTests):
    """The real request path: the second question of the same client and account launches only the answer."""

    def setUp(self):
        check_memory.clear()
        self.addCleanup(check_memory.clear)

    def ask(self, fingerprint="fp-a", **kwargs):
        with self.fixture(**kwargs) as fixture:
            fixture.bound.fingerprint = fingerprint
            result = sandbox.run_client("analyze", b"synthetic request")
        return fixture, result

    def test_the_second_request_skips_the_compatibility_and_login_launches(self):
        first, _ = self.ask()
        self.assertEqual(first.events, [*PROBES, "account", "proxy", "status", "analyze"])
        second, result = self.ask()
        self.assertEqual(result, "synthetic answer")
        self.assertEqual(second.events, ["account", "proxy", "analyze"])
        self.assertTrue(second.bound.closed)                                   # still its own binding

    def test_a_changed_client_is_checked_again_in_full(self):
        self.ask()
        changed, _ = self.ask(fingerprint="fp-b")
        self.assertEqual(changed.events, [*PROBES, "account", "proxy", "status", "analyze"])

    def test_a_login_failure_makes_the_next_request_check_the_status_again(self):
        self.ask()
        signed_out = (1, b'{"type":"result","subtype":"success","is_error":true,"result":"Not logged in",'
                         b'"num_turns":0,"permission_denials":[],"api_error_status":401}', b"")
        with self.assertRaises(AnalysisDenied) as refused:
            self.ask(responses={"analyze": signed_out})
        self.assertEqual(refused.exception.reason, "auth-required")
        again, _ = self.ask()
        self.assertIn("status", again.events)


if __name__ == "__main__":
    unittest.main()
