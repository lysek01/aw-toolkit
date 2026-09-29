# Windows gotchas

Problems hit while setting up ActivityWatch and building these watchers on Windows 11,
with the cause and the fix. Versions: ActivityWatch 0.13.2, Python 3.13 (Microsoft Store), KiCad 9/10.

## aw-notify crashes on start

The bundled `aw-notify` module fails twice in a row:

1. `TypeError: 'NoneType' object is not iterable` in `aw_client/classes.py` - the server has no
   categories saved yet (`GET /api/0/settings/classes` returns `null`).
   **Fix:** open *Settings → Categories* in the web UI and press *Save* once.
2. After that: `OSError: [WinError -2147023728] Element not found` in `desktop_notifier/winrt.py`
   `request_authorisation` - Windows toast notifications need a registered AppUserModelID, which
   aw-notify doesn't have. No fix found; remove `aw-notify` from `autostart_modules`, otherwise aw-qt
   restarts it in a crash loop.

## aw-qt.toml ships commented out

`%LOCALAPPDATA%\activitywatch\activitywatch\aw-qt\aw-qt.toml` contains only commented lines, so the
defaults apply: `aw-server`, `aw-watcher-afk`, `aw-watcher-window`. `aw-watcher-input` is bundled but
not started - enabling it from the tray menu doesn't survive a restart. Uncomment `autostart_modules`
in the `[aw-qt]` section (not `[aw-qt-testing]`) and add it there.

Don't enable `aw-server` and `aw-server-rust` together - both want port 5600 and they have separate
databases.

## Microsoft Store Python redirects AppData

Python from the Microsoft Store runs packaged: writes to `%LOCALAPPDATA%` silently go to
`%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.x_…\LocalCache\Local\` instead. Your
script reads them back fine, but you won't find the files where you expect them.
That's why the watchers keep their `data\` folder next to the script.

## Non-ASCII text on stdin (Claude Code hooks)

`json.load(sys.stdin)` decodes stdin with the ANSI code page (cp1250 on Czech Windows), so prompts
with diacritics are mangled. Read bytes and decode yourself:
`json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))` (`-sig` also handles the BOM that
PowerShell adds when you test by piping).

## Hook output ends up in the conversation

Whatever a `UserPromptSubmit` hook prints to stdout is added to Claude's context. A tracking hook
must stay silent - wrap everything in `try/except` and `sys.exit(0)`.

## ctypes truncates 64-bit handles

Without `restype = wintypes.HANDLE` ctypes returns `CreateFileW` handles as a 32-bit `int`. Set
`restype`/`argtypes` for every function that takes or returns a handle.

## Directory changes look like saves

`ReadDirectoryChangesW` with `FILE_NOTIFY_CHANGE_LAST_WRITE` also reports the *folder* whose content
changed. Skip paths that are directories, otherwise every save counts twice.

## Tkinter is blurry and mis-positioned with display scaling

Call `ctypes.windll.shcore.SetProcessDpiAwareness(1)` before creating the window, scale your pixel
sizes by `root.winfo_fpixels("1i") / 96`, and position the window with `winfo_reqwidth()` -
`winfo_width()` is still `1` before the window is mapped, which puts it off-screen.

## Placeholder text in a Tk Entry inside a Canvas

A canvas text item can't be raised above a widget placed with `create_window`, so a placeholder
drawn on the canvas is hidden as soon as the Entry exists. Put the placeholder into the Entry itself
(grey text, cleared on the first key press).

## Detecting a locked screen

`OpenInputDesktop` + `SwitchDesktop` fails while the Winlogon desktop is active - a cheap way to
poll the lock state without a message loop. Don't show UI while locked: it opens behind the lock
screen and its timers keep running.

## The window title of the Claude desktop app

It is always just `Claude`, so window tracking can't tell sessions apart. That's what the Claude Code
hook is for.
