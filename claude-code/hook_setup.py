"""Register / unregister aw_claude_hook.py in the user's Claude Code settings.json.

    python hook_setup.py install [HOOK_PATH]   # add UserPromptSubmit + Stop hooks (idempotent)
    python hook_setup.py uninstall             # remove them again

HOOK_PATH defaults to aw_claude_hook.py next to this file; install.ps1 passes the installed copy
in ~/.aw-toolkit. Only entries whose command mentions aw_claude_hook.py are touched, everything
else in settings.json is kept. A backup settings.json.bak is written before every change.
"""
import json
import shutil
import sys
from pathlib import Path

HOOK_NAME = "aw_claude_hook.py"
EVENTS = ("UserPromptSubmit", "Stop")


def settings_path():
    return Path.home() / ".claude" / "settings.json"


def _strip(settings):
    hooks = settings.get("hooks", {})
    for event in EVENTS:
        groups = []
        for group in hooks.get(event, []):
            group["hooks"] = [h for h in group.get("hooks", []) if HOOK_NAME not in h.get("command", "")]
            if group["hooks"]:
                groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)


def main(action, hook_path=None):
    path = settings_path()
    settings = json.loads(path.read_text("utf-8")) if path.exists() else {}
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    _strip(settings)
    if action == "install":
        hook = Path(hook_path or Path(__file__).resolve().parent / HOOK_NAME).resolve()
        command = f'python "{hook.as_posix()}"'
        for event in EVENTS:
            settings.setdefault("hooks", {}).setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": command, "timeout": 5}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"  {'+' if action == 'install' else '-'} Claude Code hook ({path})")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in ("install", "uninstall"):
        sys.exit(__doc__)
    main(*sys.argv[1:])
