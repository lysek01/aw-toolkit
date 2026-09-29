"""Register / unregister aw_claude_hook.py in the user's Claude Code settings.json.

    python hook_setup.py install     # add UserPromptSubmit + Stop hooks (idempotent)
    python hook_setup.py uninstall   # remove them again

Only entries whose command mentions aw_claude_hook.py are touched; everything else is kept.
A backup settings.json.bak is written before every change.
"""
import json
import shutil
import sys
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK = Path(__file__).resolve().parent / "aw_claude_hook.py"
EVENTS = ("UserPromptSubmit", "Stop")


def _strip(settings):
    hooks = settings.get("hooks", {})
    for event in EVENTS:
        groups = []
        for group in hooks.get(event, []):
            group["hooks"] = [h for h in group.get("hooks", []) if "aw_claude_hook.py" not in h.get("command", "")]
            if group["hooks"]:
                groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)


def main(action):
    settings = json.loads(SETTINGS.read_text("utf-8")) if SETTINGS.exists() else {}
    if SETTINGS.exists():
        shutil.copy2(SETTINGS, SETTINGS.with_suffix(".json.bak"))
    _strip(settings)
    if action == "install":
        command = f'python "{HOOK.as_posix()}"'
        for event in EVENTS:
            settings.setdefault("hooks", {}).setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": command, "timeout": 5}]})
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"{action}ed Claude Code hook in {SETTINGS}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("install", "uninstall"):
        sys.exit(__doc__)
    main(sys.argv[1])
