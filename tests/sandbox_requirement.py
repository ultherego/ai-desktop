"""Real-sandbox tests skip where bubblewrap or xdg-dbus-proxy are missing, except in the CI job that requires them.

With AI_DESKTOP_REQUIRE_SANDBOX_TESTS=1 a missing prerequisite fails the test instead of skipping it, so a green job
proves these boundary tests actually ran.
"""
import os
import unittest

REQUIRED = os.environ.get("AI_DESKTOP_REQUIRE_SANDBOX_TESTS") == "1"


def skip_or_fail(test, reason):
    if REQUIRED:
        test.fail("a required sandbox test could not run: " + reason)
    test.skipTest(reason)


def requires(condition, reason):
    """Like unittest.skipUnless, but a required run never skips: the test then fails on what is missing."""
    if REQUIRED:
        return lambda item: item
    return unittest.skipUnless(condition, reason)
