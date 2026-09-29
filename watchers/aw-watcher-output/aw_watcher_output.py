"""aw-watcher-output: record what you actually produced - saved files and git commits - in ActivityWatch.

It does not watch the whole disk. Every 30 s it asks ActivityWatch for the active window, finds the
project folder behind it (KiCad file history, Arduino IDE recent sketches, VS Code open folders,
Windows "Recent items") and watches only that folder plus a few other recently active ones.
Watching is event driven (ReadDirectoryChangesW, works on network shares too); commits are read
from .git/logs/HEAD without running git.

Run:        pythonw aw_watcher_output.py
Diagnose:   python  aw_watcher_output.py --once   (prints which project matches the active window)
"""
import ctypes
import functools
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# next to the script on purpose: Microsoft Store Python silently redirects writes to AppData\Local
DATA_DIR = HERE / "data"
HOME = Path.home()
APPDATA = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
HOST = socket.gethostname()
BUCKET = f"aw-watcher-output_{HOST}"

DEFAULTS = {
    "aw_url": "http://localhost:5600/api/0",
    "poll_s": 30,             # how often to check the active window
    "flush_s": 300,           # how often to write a summary to ActivityWatch
    "git_check_s": 60,        # how often to look for new commits
    "max_watched": 6,         # how many project folders to watch at once
    "forget_after_min": 120,  # stop watching a project after this long without activity
    "fallback_poll_s": 120,   # folders without change notifications (some NAS) are rescanned this often
    "ignore_apps": [],        # extra process names to skip, e.g. ["slack.exe"]
    "ignore_dirs": [],        # extra folder names to ignore inside projects, e.g. ["output"]
    "non_project_dirs": [],   # extra folders that are never a project, e.g. ["~/Scratch"]
}

# windows we never try to map to a project (browsers, chat, shell)
IGNORE_APPS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "claude.exe",
               "explorer.exe", "aw-qt.exe", "searchhost.exe", "shellexperiencehost.exe", "lockapp.exe",
               "ms-teams.exe", "teams.exe", "olk.exe", "outlook.exe", "spotify.exe", "discord.exe"}
# folders that are not a project themselves - a file lying directly in them is not watched
NON_PROJECT_DIRS = {HOME, HOME / "Downloads", HOME / "Desktop", HOME / "Documents", HOME / "OneDrive"}
MARKERS = ("platformio.ini", "package.json", "pyproject.toml", "CMakeLists.txt", "composer.json", "Cargo.toml")
IGNORE_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pio", "build", "dist",
                ".vs", ".vscode", ".idea", ".history", ".cache", "obj", "bin"}
IGNORE_SUFFIX = (".lck", ".tmp", ".bak", ".swp", ".kicad_prl", ".log", ".pyc", ".o", ".d", ".obj")
IGNORE_NAMES = {"fp-info-cache", "_autosave", "desktop.ini", "thumbs.db"}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(tomllib.loads((HERE / "config.toml").read_text("utf-8")))
    except FileNotFoundError:
        pass
    cfg["aw_url"] = os.environ.get("AW_URL", cfg["aw_url"])
    IGNORE_APPS.update(a.lower() for a in cfg["ignore_apps"])
    IGNORE_PARTS.update(cfg["ignore_dirs"])
    NON_PROJECT_DIRS.update(Path(os.path.expanduser(d)) for d in cfg["non_project_dirs"])
    return cfg


CFG = load_config()
log = logging.getLogger("aw-watcher-output")

kernel32 = ctypes.windll.kernel32
# 64-bit handles: without argtypes ctypes would truncate them to int
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                           wintypes.LPVOID, wintypes.LPVOID]
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
INVALID_HANDLE = wintypes.HANDLE(-1).value


# --- ActivityWatch -----------------------------------------------------------------------

def aw_get(path):
    with urllib.request.urlopen(CFG["aw_url"] + path, timeout=3) as r:
        return json.load(r)


def _post(path, payload):
    req = urllib.request.Request(CFG["aw_url"] + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as e:
        if e.code != 304:  # 304 = bucket already exists
            raise


def _load(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def send(events):
    """Send events plus anything that failed before; keep them for later if ActivityWatch is down."""
    queue = _load(DATA_DIR / "pending.json", []) + events
    if not queue:
        return
    try:
        _post(f"/buckets/{BUCKET}", {"client": "aw-watcher-output", "type": "app.output", "hostname": HOST})
        _post(f"/buckets/{BUCKET}/events", queue)
        queue = []
    except Exception as e:
        log.warning("ActivityWatch unreachable, %d events queued: %s", len(queue), e)
    _save(DATA_DIR / "pending.json", queue)


def current_window():
    """(app, title) of the active window, or None while you are AFK."""
    afk = aw_get(f"/buckets/aw-watcher-afk_{HOST}/events?limit=1")
    if afk and afk[0]["data"].get("status") == "afk":
        return None
    win = aw_get(f"/buckets/aw-watcher-window_{HOST}/events?limit=1")
    return (win[0]["data"].get("app", ""), win[0]["data"].get("title", "")) if win else None


# --- "recently opened" sources -------------------------------------------------------------

def uri_to_path(uri):
    p = urllib.parse.unquote(uri)
    if p.startswith("file:///"):
        return Path(p[8:].replace("/", "\\"))
    if p.startswith("file://"):  # UNC path file://server/share
        return Path("\\\\" + p[7:].replace("/", "\\"))
    return None


class Cached:
    """Reload a source only when the file(s) it reads from change."""

    def __init__(self, files, loader):
        self.files, self.loader, self.mtime, self.value = files, loader, None, []

    def get(self):
        paths = [f for f in self.files() if f.exists()]
        mt = tuple(f.stat().st_mtime for f in paths)
        if mt != self.mtime:
            self.mtime = mt
            try:
                self.value = self.loader(paths) if paths else []
            except Exception:
                log.exception("source %s", paths)
                self.value = []
        return self.value


def _kicad_files():
    return sorted((APPDATA / "kicad").glob("*/kicad.json"), reverse=True)  # newest KiCad version first


def _kicad_load(paths):
    out = []
    for f in paths:
        hist = json.loads(f.read_text("utf-8")).get("system", {}).get("file_history", [])
        out += [Path(h) for h in hist if Path(h) not in out]
    return out


def _arduino_load(paths):
    return [p for p in (uri_to_path(u) for u in json.loads(paths[0].read_text("utf-8"))) if p]


def _vscode_load(paths):
    j = json.loads(paths[0].read_text("utf-8"))
    ws = j.get("windowsState", {})
    wins = [ws.get("lastActiveWindow", {})] + ws.get("openedWindows", [])
    uris = [w.get("folder") for w in wins if w.get("folder")]
    uris += [f.get("folderUri") for f in j.get("backupWorkspaces", {}).get("folders", []) if f.get("folderUri")]
    return [p for p in (uri_to_path(u) for u in uris) if p]


def lnk_target(data):
    """Target path of a .lnk shortcut (LinkInfo only - enough for local and network files)."""
    flags = int.from_bytes(data[0x14:0x18], "little")
    pos = 0x4C
    if flags & 1:  # HasLinkTargetIDList
        pos += 2 + int.from_bytes(data[pos:pos + 2], "little")
    if not flags & 2:  # HasLinkInfo
        return None

    def u32(o):
        return int.from_bytes(data[o:o + 4], "little")

    def cstr(o):
        return data[o:data.index(b"\0", o)].decode("mbcs", "replace")

    li, li_flags = pos, u32(pos + 8)
    suffix = cstr(li + u32(li + 24))
    if li_flags & 1:  # VolumeIDAndLocalBasePath
        return Path(cstr(li + u32(li + 16)) + suffix)
    if li_flags & 2:  # CommonNetworkRelativeLinkAndPathSuffix
        cnrl = li + u32(li + 20)
        return Path(cstr(cnrl + u32(cnrl + 8)) + "\\" + suffix)
    return None


def _recent_load(paths):
    """Windows Recent items: {file name: path}, newest 300 only."""
    folder = APPDATA / "Microsoft" / "Windows" / "Recent"
    lnks = sorted(folder.glob("*.lnk"), key=lambda p: p.stat().st_mtime, reverse=True)[:300]
    out = {}
    for lnk in lnks:
        try:
            t = lnk_target(lnk.read_bytes())
        except Exception:
            continue
        if t and t.suffix:
            out.setdefault(t.name.lower(), t)
    return [out]


KICAD = Cached(_kicad_files, _kicad_load)
ARDUINO = Cached(lambda: [HOME / ".arduinoIDE" / "recent-sketches.json"], _arduino_load)
VSCODE = Cached(lambda: [APPDATA / "Code" / "User" / "globalStorage" / "storage.json"], _vscode_load)
RECENT = Cached(lambda: [APPDATA / "Microsoft" / "Windows" / "Recent"], _recent_load)


def best_name_match(title, paths, name_of):
    """The path whose name appears in the window title (longest name wins)."""
    t = title.lower()
    hits = [p for p in paths if name_of(p) and name_of(p).lower() in t]
    return max(hits, key=lambda p: len(name_of(p)), default=None)


def resolve(app, title):
    """Folder behind the window, or None."""
    a = app.lower()
    if a in IGNORE_APPS or a.startswith("python"):
        return None
    if "kicad" in a or "eeschema" in a or "pcbnew" in a:
        p = best_name_match(title, KICAD.get(), lambda p: p.stem)
        return p.parent if p else None
    if "arduino" in a:
        return best_name_match(title, ARDUINO.get(), lambda p: p.name)
    if a == "code.exe":
        return best_name_match(title, VSCODE.get(), lambda p: p.name)
    recent = RECENT.get()
    recent = recent[0] if recent else {}
    t = title.lower()
    hits = [name for name in recent if name in t]
    return recent[max(hits, key=len)].parent if hits else None


@functools.lru_cache(maxsize=256)
def project_root(folder):
    """Nearest git repo above the folder, else nearest folder with a project marker, else the folder."""
    if folder is None:
        return None
    marked = None
    d = Path(folder)
    for _ in range(8):
        try:
            if (d / ".git").is_dir():
                return d
            if marked is None and (any(d.glob("*.kicad_pro")) or any((d / m).exists() for m in MARKERS)):
                marked = d
        except OSError:
            break
        if d.parent == d:
            break
        d = d.parent
    root = marked or Path(folder)
    if root in NON_PROJECT_DIRS or root.parent == root:
        return None
    return root


# --- watching folders ------------------------------------------------------------------------

def interesting(rel):
    """Is a change of this (project-relative) path real work, not backups/caches/lock files?"""
    parts = Path(rel).parts
    name = parts[-1].lower() if parts else ""
    if any(p in IGNORE_PARTS or p.endswith("-backups") for p in parts[:-1]):
        return False
    return not (name in IGNORE_NAMES or name.startswith(("~", ".#", "~$")) or name.endswith(IGNORE_SUFFIX))


class FolderWatch:
    """Watch one folder with ReadDirectoryChangesW; fall back to slow mtime polling if that fails."""

    def __init__(self, root, on_change):
        self.root, self.on_change = root, on_change
        self.handle = None
        self.stop_flag = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag.set()
        if self.handle:
            kernel32.CancelIoEx(self.handle, None)

    def _emit(self, rel):
        if interesting(rel) and not os.path.isdir(os.path.join(self.root, rel)):  # a dir change is not a save
            self.on_change(self.root, rel)

    def _run(self):
        h = kernel32.CreateFileW(str(self.root), 0x0001, 0x7, None, 3, 0x02000000, None)
        if h in (None, INVALID_HANDLE):
            log.warning("cannot open %s, falling back to polling", self.root)
            return self._poll()
        self.handle = h
        buf = ctypes.create_string_buffer(64 * 1024)  # 64 KB is the maximum for network shares
        got = wintypes.DWORD()
        try:
            while not self.stop_flag.is_set():
                ok = kernel32.ReadDirectoryChangesW(h, buf, len(buf), True, 0x1 | 0x10, ctypes.byref(got),
                                                    None, None)  # FILE_NAME | LAST_WRITE
                if not ok:
                    if not self.stop_flag.is_set():
                        log.warning("no change notifications for %s, falling back to polling", self.root)
                        kernel32.CloseHandle(h)
                        self.handle = None
                        return self._poll()
                    break
                off = 0
                while got.value:
                    nxt, action, ln = (int.from_bytes(buf.raw[off + i:off + i + 4], "little") for i in (0, 4, 8))
                    if action in (1, 3, 4):  # added, modified, renamed-to
                        self._emit(buf.raw[off + 12:off + 12 + ln].decode("utf-16-le", "replace"))
                    if not nxt:
                        break
                    off += nxt
        finally:
            if self.handle:
                kernel32.CloseHandle(self.handle)

    def _poll(self):
        seen, first = {}, True
        while not self.stop_flag.wait(0 if first else CFG["fallback_poll_s"]):
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [d for d in dirnames if d not in IGNORE_PARTS and not d.endswith("-backups")]
                for f in filenames:
                    full = os.path.join(dirpath, f)
                    rel = os.path.relpath(full, self.root)
                    try:
                        mt = os.stat(full).st_mtime
                    except OSError:
                        continue
                    if not first and seen.get(rel) != mt:
                        self._emit(rel)
                    seen[rel] = mt
            first = False


class GitLog:
    """New commits from .git/logs/HEAD (reads only the appended part, never runs git)."""

    def __init__(self, root):
        self.path = root / ".git" / "logs" / "HEAD"
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def new_commits(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size <= self.offset:
            self.offset = size
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read().decode("utf-8", "replace")
        self.offset = size
        return parse_reflog(chunk)


def parse_reflog(text):
    """Commits from reflog lines '<old> <new> <who> <ts> <tz>\\t<action>: <message>'."""
    out = []
    for line in text.splitlines():
        meta, _, msg = line.partition("\t")
        if msg.startswith("commit"):  # commit, commit (amend), commit (initial), commit (merge)
            out.append({"hash": meta.split(" ")[1][:10], "msg": msg.split(": ", 1)[-1][:200]})
    return out


# --- tracking -----------------------------------------------------------------------------------

class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.watched = {}   # root -> {"watch", "git", "last_active"}
        self.pending = {}   # root -> {"first", "last", "saves": set, "files": set, "commits": []}
        self.unmatched = _load(DATA_DIR / "unmatched.json", {})

    def _entry(self, root, now):
        p = self.pending.setdefault(root, {"first": now, "last": now, "saves": set(), "files": set(), "commits": []})
        p["last"] = now
        return p

    def on_change(self, root, rel):
        now = time.time()
        with self.lock:
            p = self._entry(root, now)
            p["saves"].add((rel, int(now // 10)))  # several events from one save count once
            p["files"].add(rel.replace("\\", "/"))

    def activate(self, root):
        now = time.time()
        if root in self.watched:
            self.watched[root]["last_active"] = now
            return
        if len(self.watched) >= CFG["max_watched"]:
            self.release(min(self.watched, key=lambda r: self.watched[r]["last_active"]))
        log.info("watching %s", root)
        git = GitLog(root) if (root / ".git").is_dir() else None
        self.watched[root] = {"watch": FolderWatch(root, self.on_change), "git": git, "last_active": now}

    def release(self, root):
        log.info("stop watching %s", root)
        self.watched.pop(root)["watch"].stop()

    def check_git(self):
        for root, w in list(self.watched.items()):
            commits = w["git"].new_commits() if w["git"] else []
            if commits:
                with self.lock:
                    self._entry(root, time.time())["commits"] += commits

    def forget_idle(self):
        now = time.time()
        for root in [r for r, w in self.watched.items() if now - w["last_active"] > CFG["forget_after_min"] * 60]:
            self.release(root)

    def note_unmatched(self, app, title):
        a = app.lower()
        if a in IGNORE_APPS or a.startswith("python"):
            return
        titles = self.unmatched.setdefault(app, {})
        if title in titles or len(titles) < 200:
            titles[title] = titles.get(title, 0) + 1

    def flush(self):
        with self.lock:
            pending, self.pending = self.pending, {}
        events = []
        for root, p in pending.items():
            saves, commits = len(p["saves"]), p["commits"]
            summary = f"{root.name}: {saves} saves" + (f" · {len(commits)} commits" if commits else "")
            events.append({
                "timestamp": datetime.fromtimestamp(p["first"], timezone.utc).isoformat(),
                "duration": round(max(1.0, p["last"] - p["first"]), 1),
                "data": {"title": summary, "project": root.name, "root": str(root), "saves": saves,
                         "files": sorted(p["files"])[:20], "commits": commits},
            })
        send(events)
        _save(DATA_DIR / "unmatched.json", self.unmatched)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(DATA_DIR / "log.txt", maxBytes=200_000, backupCount=1,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    if "--once" in sys.argv:
        w = current_window()
        print("window: ", w)
        if w:
            folder = resolve(*w)
            print("folder: ", folder)
            print("project:", project_root(folder))
        return

    kernel32.CreateMutexW(None, False, "aw-watcher-output-singleton")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS -> already running
        return
    log.info("start")

    t = Tracker()
    last_flush = last_git = time.time()
    last_window = None
    while True:
        try:
            w = current_window()
            if w:
                folder = resolve(*w)
                root = project_root(folder) if folder else None
                if root:
                    t.activate(root)
                elif w != last_window:
                    t.note_unmatched(*w)
            last_window = w
        except Exception as e:
            log.warning("active window: %s", e)
        now = time.time()
        if now - last_git >= CFG["git_check_s"]:
            t.check_git()
            last_git = now
        if now - last_flush >= CFG["flush_s"]:
            t.forget_idle()
            t.flush()
            last_flush = now
        time.sleep(CFG["poll_s"])


if __name__ == "__main__":
    main()
