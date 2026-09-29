import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from _load import ROOT, load

HOOK = ROOT / "claude-code" / "aw_claude_hook.py"
hook = load("claude-code/aw_claude_hook.py", "aw_claude_hook")


def dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class PromptTest(unittest.TestCase):
    def test_user_prompt(self):
        self.assertEqual(hook.user_prompt("\n  navrhni pravidla\nřádek 2"), "navrhni pravidla")
        self.assertEqual(hook.user_prompt("<task-notification>\n<task-id>x</task-id>"), "")
        self.assertEqual(hook.user_prompt(""), "")


class HookProcessTest(unittest.TestCase):
    """Run the hook exactly like Claude Code does, with ActivityWatch unreachable."""

    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, AW_URL=f"http://127.0.0.1:{dead_port()}/api/0",
                        AW_CLAUDE_HOOK_STATE=str(self.state))

    def call(self, event, prompt=None):
        payload = {"session_id": "s1", "hook_event_name": event, "cwd": "C:/x"}
        if prompt is not None:
            payload["prompt"] = prompt
        t0 = time.time()
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload, ensure_ascii=False).encode(),
                           capture_output=True, env=self.env, timeout=10)
        return r, time.time() - t0

    def test_state_and_silent_failure(self):
        self.call("UserPromptSubmit", "<task-notification>")
        self.call("UserPromptSubmit", "žluťoučký kůň\nsecond line")
        self.call("UserPromptSubmit", "<agent-message from=x>")
        state = json.loads((self.state / "s1.json").read_text("utf-8"))
        self.assertEqual(state["first_prompt"], "žluťoučký kůň")
        self.assertEqual(state["prompt"], "")

        r, took = self.call("Stop")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b"")  # stdout of a UserPromptSubmit hook would be injected into the chat
        self.assertLess(took, 5)  # Claude Code's hook timeout
        self.assertNotIn("start", json.loads((self.state / "s1.json").read_text("utf-8")))


if __name__ == "__main__":
    unittest.main()
