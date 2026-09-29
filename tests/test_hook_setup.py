import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _load import ROOT

SETUP = ROOT / "claude-code" / "hook_setup.py"


class HookSetupTest(unittest.TestCase):
    """hook_setup.py edits someone's real settings.json - it must keep everything that isn't ours."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir()
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.write_text(json.dumps({
            "model": "opus",
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "notify-me.exe"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.py"}]}],
            },
        }))

    def run_setup(self, *args):
        env = dict(os.environ, USERPROFILE=str(self.home), HOME=str(self.home))
        subprocess.run([sys.executable, str(SETUP), *args], check=True, capture_output=True, env=env)
        return json.loads(self.settings.read_text("utf-8"))

    def commands(self, settings, event):
        return [h["command"] for g in settings.get("hooks", {}).get(event, []) for h in g["hooks"]]

    def test_install_twice_then_uninstall(self):
        hook = self.home / ".aw-toolkit" / "claude-code" / "aw_claude_hook.py"
        self.run_setup("install", str(hook))
        s = self.run_setup("install", str(hook))
        ours = f'python "{hook.as_posix()}"'
        self.assertEqual(self.commands(s, "Stop"), ["notify-me.exe", ours])
        self.assertEqual(self.commands(s, "UserPromptSubmit"), [ours])
        self.assertEqual(self.commands(s, "PreToolUse"), ["guard.py"])
        self.assertEqual(s["model"], "opus")
        self.assertTrue(self.settings.with_suffix(".json.bak").exists())

        s = self.run_setup("uninstall")
        self.assertEqual(self.commands(s, "Stop"), ["notify-me.exe"])
        self.assertNotIn("UserPromptSubmit", s["hooks"])
        self.assertEqual(self.commands(s, "PreToolUse"), ["guard.py"])


if __name__ == "__main__":
    unittest.main()
