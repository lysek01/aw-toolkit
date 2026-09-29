"""Claude Code hook -> ActivityWatch.

UserPromptSubmit: remembers when the turn started and what was asked.
Stop:             writes one event (turn start .. now) into bucket aw-watcher-claude_<host>.

Never prints anything and always exits 0, so it can't disturb Claude Code.
If ActivityWatch is not running the event is dropped after a 0.5 s timeout.
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AW = os.environ.get("AW_URL", "http://localhost:5600/api/0")
BUCKET = f"aw-watcher-claude_{socket.gethostname()}"
STATE_DIR = Path(os.environ.get("AW_CLAUDE_HOOK_STATE", Path.home() / ".claude" / "hooks" / "aw_state"))
TIMEOUT_S = 0.5


def post(path, payload):
    req = urllib.request.Request(
        AW + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT_S)
    except urllib.error.HTTPError as e:
        if e.code != 304:  # 304 = bucket already exists
            raise


def first_line(text, limit=150):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def user_prompt(text):
    """First line of what the user typed; '' for harness messages like <task-notification>."""
    line = first_line(text)
    return "" if line.startswith("<") else line


def session_title(transcript_path):
    """Last custom-title in the transcript (the session name shown in the app sidebar)."""
    try:
        data = Path(transcript_path).read_bytes()
    except (OSError, TypeError):
        return ""
    i = data.rfind(b'"type":"custom-title"')
    if i < 0:
        return ""
    start = data.rfind(b"\n", 0, i) + 1
    end = data.find(b"\n", i)
    try:
        return json.loads(data[start:end if end >= 0 else None]).get("customTitle", "")
    except ValueError:
        return ""


def main():
    # raw bytes: on Windows stdin would otherwise be decoded with the ANSI code page and mangle non-ASCII prompts
    hook = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    sid = hook.get("session_id", "unknown")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / f"{sid}.json"
    state = json.loads(state_file.read_text("utf-8")) if state_file.exists() else {}

    if hook.get("hook_event_name") == "UserPromptSubmit":
        prompt = user_prompt(hook.get("prompt"))
        state["start"] = time.time()
        state["prompt"] = prompt
        if prompt and not state.get("first_prompt"):
            state["first_prompt"] = prompt
        state_file.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
        return

    # Stop
    start = state.pop("start", None)
    if start is None:
        return
    state_file.write_text(json.dumps(state, ensure_ascii=False), "utf-8")

    post(f"/buckets/{BUCKET}", {
        "client": "aw-watcher-claude", "type": "app.claude.turn",
        "hostname": socket.gethostname()})
    post(f"/buckets/{BUCKET}/events", [{
        "timestamp": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "duration": round(time.time() - start, 1),
        "data": {
            "app": "Claude",
            "title": session_title(hook.get("transcript_path")) or state.get("first_prompt", ""),
            "prompt": state.get("prompt", ""),
            "first_prompt": state.get("first_prompt", ""),
            "cwd": hook.get("cwd", ""),
            "session_id": sid,
        },
    }])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
