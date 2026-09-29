# AGENTS.md

Guide for AI coding agents (Claude Code, Codex, Cursor, …) and humans working on this repository.
Read it before changing anything. User-facing docs are in [README.md](README.md).

## What this is

Windows add-ons for [ActivityWatch](https://activitywatch.net) (AW). Each component writes events into
its own AW bucket through the local REST API (`http://localhost:5600/api/0`):

```
watchers/aw-watcher-away/aw_watcher_away.py      popup after idle/lock/sleep  -> aw-watcher-away_<host>
watchers/aw-watcher-output/aw_watcher_output.py  saved files + git commits    -> aw-watcher-output_<host>
claude-code/aw_claude_hook.py                    Claude Code UserPromptSubmit/Stop hook -> aw-watcher-claude_<host>
claude-code/hook_setup.py                        (un)registers the hook in ~/.claude/settings.json
install.ps1 / uninstall.ps1                      per-user install, Startup shortcuts, migration
tests/                                           unittest suite (python -m unittest, run from tests/)
docs/windows-gotchas.md                          non-obvious platform problems and their fixes - read it
docs/mcp-server.md                               querying AW from Claude via an MCP server
```

## Repository vs. installed copy - important

The code does **not** run from the repository. `install.ps1` copies it to **`~\.aw-toolkit\`** and runs it there:

```
~\.aw-toolkit\
├─ INSTALL-INFO.txt            source repo path + commit of the installed version
├─ uninstall.ps1               works without the repository
├─ aw-watcher-away\            aw_watcher_away.py, config.example.toml, config.toml (user), data\
├─ aw-watcher-output\          aw_watcher_output.py, config.example.toml, config.toml (user), data\
└─ claude-code\                aw_claude_hook.py, hook_setup.py, state\ (per-session hook state)
```

- Startup shortcuts (`shell:startup\aw-watcher-*.lnk`) and the hook command in `~/.claude/settings.json`
  point into `~\.aw-toolkit`. Deleting or moving the repository breaks nothing.
- **Never edit files in `~\.aw-toolkit`.** Change the repository, run the tests, then deploy with
  `powershell -ExecutionPolicy Bypass -File .\install.ps1` - it stops the running watchers, replaces
  the code, keeps `data\` and `config.toml`, restarts everything.
- To see which version is running: `~\.aw-toolkit\INSTALL-INFO.txt`.
- The user's personal data (answers, file paths, commit messages, prompts) lives only in `data\`/`state\`
  of the installed copy and in the AW database. Never commit it, never print it into logs you share.

## Rules

1. **Standard library only**, Python ≥ 3.11 (`tomllib`). No `pip install` - users run this with whatever
   Python they have, including Microsoft Store Python.
2. **Windows only.** Win32 via `ctypes`; declare `restype`/`argtypes` for anything handle-shaped.
3. **Cheap in the background.** Watchers must sleep between polls or block on OS notifications.
   No busy loops, no whole-disk scans, no spawning processes (e.g. git) in a loop. Measure after changes:
   CPU time of the `pythonw` process over 60 s should stay near zero.
4. **Never lose data.** Anything that talks to AW queues events in `data\pending.json` when AW is down.
5. **Data next to the script** (`Path(__file__).parent / "data"`), never `%LOCALAPPDATA%`/`%APPDATA%` -
   Microsoft Store Python silently redirects those writes (see gotchas).
6. **The Claude hook must be invisible:** no stdout (it is injected into the conversation), always exit 0,
   network timeout ≤ 0.5 s, read stdin as bytes and decode UTF-8.
7. **Single instance:** watchers take a named mutex (`aw-watcher-*-singleton`); keep that.
8. **Bucket names and event `data` fields are a contract** - other tools and the user's analyses read them.
   Add fields freely; don't rename or remove existing ones without a migration note in the README.
9. **Config:** every tunable goes into `DEFAULTS` + `config.example.toml`; user overrides live in
   `config.toml` next to the installed script (git-ignored). `AW_URL` env var overrides `aw_url` (tests use it).
10. **UI text** of the away popup exists in `TEXTS["en"]` and `TEXTS["cs"]` - add every new string to both.
11. `install.ps1` / `uninstall.ps1` must stay **ASCII-only** (Windows PowerShell 5.1 misreads UTF-8 without BOM)
    and idempotent (running twice = same result).

## Testing

```powershell
cd tests
python -m unittest -v
```

Tests never touch the user's real AW buckets or settings (dead ports, temp dirs, temp `USERPROFILE`).
Keep it that way. Manual checks after changing behaviour:

- popup: `python watchers\aw-watcher-away\aw_watcher_away.py --test` (writes to `aw-watcher-away-test_<host>`;
  delete that bucket afterwards: `DELETE /api/0/buckets/aw-watcher-away-test_<host>?force=1`)
- output mapping for the current window: `python watchers\aw-watcher-output\aw_watcher_output.py --once`
- after `install.ps1`: `Get-CimInstance Win32_Process -Filter "Name like 'python%'"` shows exactly one
  process per watcher, running from `~\.aw-toolkit`

## Common tasks

**Teach aw-watcher-output a new app** (look at `~\.aw-toolkit\aw-watcher-output\data\unmatched.json` first -
it lists window titles that could not be mapped, per process name):
1. Find where the app keeps its recent files/projects with full paths (config JSON, `.lnk`, registry MRU).
2. Add a loader + a `Cached(...)` source next to `KICAD`/`ARDUINO`/`VSCODE`, and a branch in `resolve()`
   keyed on the process name. Match names against the window title with `best_name_match`.
3. Add a case to `ResolveTest` in `tests/test_output.py`.

**Add a quick button / language to the popup:** `TEXTS` and `GLYPHS` in `aw_watcher_away.py`
(glyphs are code points of the *Segoe Fluent Icons* font).

**Change detection logic of the popup:** it is the pure class `AwayDetector` - extend `tests/test_away.py`
with the scenario first.
