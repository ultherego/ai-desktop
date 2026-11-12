"""A project outside files.write is named by its real directory, not by a fixed example."""
import os
import unittest
from unittest.mock import patch

from ai_desktop import project_cli


class WriteHintTests(unittest.TestCase):
    def test_hint_names_the_discovered_project_directory(self):
        with patch.dict(os.environ, {"HOME": "/home/example"}):
            text = project_cli._not_writable("/home/example/code/app")
            self.assertIn("~/code/app (it needs an entry like ~/code/app/**)", text)
            self.assertIn("/srv/work/app/**", project_cli._not_writable("/srv/work/app"))
            self.assertNotIn("Projects", text)


if __name__ == "__main__":
    unittest.main()
