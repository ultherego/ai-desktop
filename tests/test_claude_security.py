import tempfile
import unittest
from pathlib import Path

from ai_desktop import launcher


class ClaudeSecurityTests(unittest.TestCase):
    def test_launcher_is_offline_and_readonly(self):
        with tempfile.TemporaryDirectory(prefix="ai-desktop-launch-", dir="/tmp") as workspace:
            args = launcher.build_command(Path(workspace), Path("/opt/claude"), "version")
        self.assertIn("--unshare-net", args)
        self.assertIn("--clearenv", args)
        self.assertIn("--ro-bind", args)
        self.assertNotIn("--share-net", args)
        self.assertEqual(args[-2:], ["/opt/claude", "--version"])


if __name__ == "__main__":
    unittest.main()
