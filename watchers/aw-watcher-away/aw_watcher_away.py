"""aw-watcher-away: when you come back to your PC, ask what you were doing and store it in ActivityWatch.

"Away" = no mouse/keyboard input for longer than `threshold_min`, a locked screen, or sleep.
The answer is free text - sort it out later. Up/Down arrows browse previous answers.

Run:        pythonw aw_watcher_away.py          (background, no console)
Try the UI: python  aw_watcher_away.py --test   (shows the popup now, writes to a -test bucket)
"""
import ctypes
import json
import os
import socket
import sys
import time
import tkinter as tk
import tkinter.font
import tomllib
import urllib.error
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# next to the script on purpose: Microsoft Store Python silently redirects writes to AppData\Local
DATA_DIR = HERE / "data"

DEFAULTS = {
    "aw_url": "http://localhost:5600/api/0",
    "language": "auto",        # auto | en | cs
    "threshold_min": 10,       # ask only after being away at least this long
    "autoclose_s": 120,        # popup closes by itself if left untouched
    "history_size": 50,
    "quick_buttons": None,     # [["label", "icon"], ...]; icon: coffee | food | pause | none
}

TEXTS = {
    "en": {"title": "Welcome back", "idle": "away", "locked": "locked", "sleep": "asleep",
           "hint": "Want to jot down what it was?", "save": "Save",
           "footer": "Enter saves  ·  Esc skips  ·  ↑↓ history",
           "quick": [["break", "coffee"], ["lunch", "food"]]},
    "cs": {"title": "Vítej zpátky", "idle": "mimo", "locked": "zamčeno", "sleep": "v režimu spánku",
           "hint": "Chceš si k tomu něco poznamenat?", "save": "Uložit",
           "footer": "Enter uloží  ·  Esc přeskočí  ·  ↑↓ historie",
           "quick": [["pauza", "coffee"], ["oběd", "food"]]},
}
GLYPHS = {"coffee": "", "food": "", "pause": "",
          "idle": "", "locked": "", "sleep": "", "close": ""}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(tomllib.loads((HERE / "config.toml").read_text("utf-8")))
    except FileNotFoundError:
        pass
    cfg["aw_url"] = os.environ.get("AW_URL", cfg["aw_url"])
    lang = cfg["language"]
    if lang == "auto":
        lang = "cs" if kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x05 else "en"
    cfg["texts"] = TEXTS.get(lang, TEXTS["en"])
    if cfg["quick_buttons"] is None:
        cfg["quick_buttons"] = cfg["texts"]["quick"]
    return cfg


CFG = load_config()
TEST = "--test" in sys.argv
BUCKET = f"aw-watcher-away{'-test' if TEST else ''}_{socket.gethostname()}"
HISTORY = DATA_DIR / "history.json"
PENDING = DATA_DIR / "pending.json"  # answers that could not be sent yet (ActivityWatch not running)


# --- Windows input / lock state ---------------------------------------------------

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds():
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def screen_locked():
    # while locked the input desktop is Winlogon's and we are not allowed to switch to it
    desk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desk:
        return True
    try:
        return not user32.SwitchDesktop(desk)
    finally:
        user32.CloseDesktop(desk)


class AwayDetector:
    """Pure state machine: feed it (now, last_input, locked) every few seconds, it returns
    (start, end, reason) once you are back after being away for at least `threshold` seconds."""

    def __init__(self, threshold, now, last_input):
        self.threshold = threshold
        self.prev_input = last_input
        self.last_step = now
        self.away_start = None
        self.reason = None

    def step(self, now, last_input, locked):
        if now - self.last_step > self.threshold:  # the loop stood still -> machine was asleep
            self.away_start = self.away_start or self.prev_input
            self.reason = "sleep"
        self.last_step = now

        if locked:  # never ask while locked, the popup would be hidden behind the lock screen
            if self.away_start is None:
                self.away_start, self.reason = self.prev_input, "locked"
            return None

        if self.away_start is not None:  # just unlocked / woke up
            start, reason = self.away_start, self.reason
            self.away_start = self.reason = None
            self.prev_input = max(last_input, start)
            return (start, now, reason) if now - start >= self.threshold else None

        event = None
        if last_input - self.prev_input >= self.threshold:  # input again after a long pause
            event = (self.prev_input, last_input, "idle")
        self.prev_input = last_input
        return event

    def resync(self, now, last_input):
        """Call after the (blocking) popup closes."""
        self.last_step = now
        self.prev_input = last_input


# --- ActivityWatch ------------------------------------------------------------------

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


def send(event):
    """Send the event plus anything that failed before; keep it for later if ActivityWatch is down."""
    queue = _load(PENDING, []) + [event]
    try:
        _post(f"/buckets/{BUCKET}", {"client": "aw-watcher-away", "type": "afktask",
                                     "hostname": socket.gethostname()})
        _post(f"/buckets/{BUCKET}/events", queue)
        queue = []
    except Exception:
        pass
    _save(PENDING, queue)


# --- popup ----------------------------------------------------------------------------

C = {
    "bg": "#FFFFFF", "border": "#E2E4EA", "text": "#1B1C20", "muted": "#6E717C", "faint": "#9A9DA6",
    "field": "#F4F5F8", "field_line": "#DCDFE6", "chip": "#F1F2F5", "chip_hover": "#E6E8EE",
    "accent": "#3D6FE0", "accent_hover": "#2F5CC4", "accent_soft": "#EAF0FD",
}
FONT = "Segoe UI Variable Text"
FONT_H = "Segoe UI Variable Display"
ICONS = "Segoe Fluent Icons"


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class Pill(tk.Canvas):
    """Rounded button drawn on a canvas (tk.Button can't be rounded)."""

    def __init__(self, master, text, command, s, primary=False, icon=None):
        f = (FONT, 10, "bold" if primary else "normal")
        w = int(tk.font.Font(font=f).measure(text) + s(28) + (s(22) if icon else 0))
        h = s(34)
        super().__init__(master, width=w, height=h, bg=C["bg"], highlightthickness=0, cursor="hand2")
        self.fill, self.hover = ((C["accent"], C["accent_hover"]) if primary else (C["chip"], C["chip_hover"]))
        fg = "#FFFFFF" if primary else C["text"]
        self.shape = _round_rect(self, 1, 1, w - 1, h - 1, h // 2, fill=self.fill, outline=self.fill)
        tx = w // 2
        if icon:
            self.create_text(s(16), h // 2, text=icon, font=(ICONS, 10), fill=fg if primary else C["muted"])
            tx += s(8)
        self.create_text(tx, h // 2, text=text, font=f, fill=fg)
        self.bind("<Enter>", lambda _: self.itemconfig(self.shape, fill=self.hover, outline=self.hover))
        self.bind("<Leave>", lambda _: self.itemconfig(self.shape, fill=self.fill, outline=self.fill))
        self.bind("<Button-1>", lambda _: command())


def _win11_corners(root):
    """Ask Windows 11 for rounded corners on the borderless window."""
    try:
        hwnd = user32.GetParent(root.winfo_id())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    except (AttributeError, OSError):
        pass


def ask(start, end, reason):
    """Show the popup in the bottom-right corner, return (text, status)."""
    T = CFG["texts"]
    autoclose = CFG["autoclose_s"]
    history = _load(HISTORY, [])
    result = {"text": "", "status": "timeout"}
    hist_i = {"v": len(history)}

    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=C["border"])
    k = root.winfo_fpixels("1i") / 96  # Windows display scaling (125 % -> 1.25)

    def s(px):
        return int(round(px * k))

    card = tk.Frame(root, bg=C["bg"], padx=s(20), pady=s(16))
    card.pack(padx=1, pady=1)

    # header: icon, title, close button
    head = tk.Frame(card, bg=C["bg"])
    head.pack(fill="x")
    badge = tk.Canvas(head, width=s(36), height=s(36), bg=C["bg"], highlightthickness=0)
    badge.create_oval(0, 0, s(36) - 1, s(36) - 1, fill=C["accent_soft"], outline=C["accent_soft"])
    badge.create_text(s(18), s(18), text=GLYPHS.get(reason, GLYPHS["idle"]), font=(ICONS, 12), fill=C["accent"])
    badge.pack(side="left", padx=(0, s(12)))

    mins = round((end - start) / 60)
    dur = f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60} min"
    span = f"{datetime.fromtimestamp(start):%H:%M} – {datetime.fromtimestamp(end):%H:%M}"
    titles = tk.Frame(head, bg=C["bg"])
    titles.pack(side="left", fill="x", expand=True)
    tk.Label(titles, text=T["title"], font=(FONT_H, 13, "bold"), fg=C["text"], bg=C["bg"]).pack(anchor="w")
    tk.Label(titles, text=f"{dur} {T.get(reason, T['idle'])}  ·  {span}", font=(FONT, 9),
             fg=C["muted"], bg=C["bg"]).pack(anchor="w")

    close = tk.Label(head, text=GLYPHS["close"], font=(ICONS, 9), fg=C["faint"], bg=C["bg"], cursor="hand2")
    close.pack(side="right", anchor="n")
    close.bind("<Enter>", lambda _: close.config(fg=C["text"]))
    close.bind("<Leave>", lambda _: close.config(fg=C["faint"]))

    # input field: rounded fill, accent underline when focused
    fw, fh = s(340), s(40)
    field = tk.Canvas(card, width=fw, height=fh, bg=C["bg"], highlightthickness=0)
    field.pack(fill="x", pady=(s(16), 0))
    fbox = _round_rect(field, 1, 1, fw - 1, fh - 1, s(8), fill=C["field"], outline=C["field_line"])
    fline = field.create_line(s(6), fh - 2, fw - s(6), fh - 2, fill=C["field"], width=2)
    entry = tk.Entry(field, relief="flat", bd=0, font=(FONT, 11), bg=C["field"], fg=C["text"],
                     insertbackground=C["accent"], highlightthickness=0)
    field.create_window(s(12), fh // 2, window=entry, anchor="w", width=fw - s(24))

    # placeholder lives inside the Entry (a canvas text item would be covered by it)
    hint = {"on": False}

    def show_hint():
        hint["on"] = True
        entry.delete(0, "end")
        entry.insert(0, T["hint"])
        entry.config(fg=C["faint"])
        entry.icursor(0)

    def clear_hint():
        if hint["on"]:
            hint["on"] = False
            entry.delete(0, "end")
            entry.config(fg=C["text"])

    def text_now():
        return "" if hint["on"] else entry.get().strip()

    def on_key(e):
        if hint["on"] and (e.char and e.char.isprintable() or e.keysym == "BackSpace"):
            clear_hint()

    def after_key(_):
        if not hint["on"] and not entry.get():
            show_hint()

    def refresh_field(_=None):
        focused = root.focus_get() is entry
        field.itemconfig(fline, fill=C["accent"] if focused else C["field"])
        field.itemconfig(fbox, fill="#FFFFFF" if focused else C["field"])
        entry.config(bg="#FFFFFF" if focused else C["field"])

    show_hint()
    entry.bind("<FocusIn>", refresh_field)
    entry.bind("<FocusOut>", refresh_field)
    entry.bind("<KeyRelease>", after_key)
    entry.bind("<Button-1>", lambda _: hint["on"] and root.after(1, entry.icursor, 0))

    def finish(text, status):
        result.update(text=text.strip(), status=status)
        root.destroy()

    def submit(_=None):
        text = text_now()
        finish(text, "answered" if text else "skipped")

    def history_step(delta):
        if history:
            clear_hint()
            hist_i["v"] = max(0, min(len(history), hist_i["v"] + delta))
            entry.delete(0, "end")
            if hist_i["v"] < len(history):
                entry.insert(0, history[hist_i["v"]])
            else:
                show_hint()
        return "break"

    entry.bind("<Return>", submit)
    entry.bind("<Escape>", lambda _: finish("", "skipped"))
    entry.bind("<Up>", lambda _: history_step(-1))
    entry.bind("<Down>", lambda _: history_step(+1))
    entry.bind("<Key>", on_key)
    close.bind("<Button-1>", lambda _: finish("", "skipped"))

    row = tk.Frame(card, bg=C["bg"])
    row.pack(fill="x", pady=(s(12), 0))
    for label, icon in CFG["quick_buttons"]:
        Pill(row, label, lambda q=label: finish(q, "answered"), s, icon=GLYPHS.get(icon)).pack(
            side="left", padx=(0, s(8)))
    Pill(row, T["save"], submit, s, primary=True).pack(side="right")

    tk.Label(card, text=T["footer"], font=(FONT, 8), fg=C["faint"], bg=C["bg"]).pack(anchor="w", pady=(s(12), 0))

    # countdown as a thin bar along the bottom edge
    bar = tk.Canvas(root, height=s(3), bg=C["bg"], highlightthickness=0)
    bar.pack(fill="x", padx=1, pady=(0, 1))
    bar_line = bar.create_rectangle(0, 0, 0, s(3), fill=C["accent"], outline="")
    timer = {"left": autoclose, "last": time.time()}

    def tick():
        # the countdown pauses only while there is real text in the field
        now = time.time()
        if not text_now():
            timer["left"] -= now - timer["last"]
        timer["last"] = now
        if timer["left"] <= 0:
            finish("", "timeout")
            return
        w = bar.winfo_width() * timer["left"] / autoclose if not text_now() else 0
        bar.coords(bar_line, 0, 0, w, s(3))
        root.after(250, tick)

    # drag the window by its header
    drag = {}
    for w in (head, titles, badge):
        w.bind("<ButtonPress-1>", lambda e: drag.update(x=e.x_root - root.winfo_x(), y=e.y_root - root.winfo_y()))
        w.bind("<B1-Motion>", lambda e: root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}"))

    root.update_idletasks()
    x = root.winfo_screenwidth() - root.winfo_reqwidth() - s(20)
    y = root.winfo_screenheight() - root.winfo_reqheight() - s(64)
    root.geometry(f"+{x}+{y}")
    root.attributes("-alpha", 0.0)
    root.deiconify()
    _win11_corners(root)

    def fade(a=0.0):
        a = min(1.0, a + 0.12)
        root.attributes("-alpha", a)
        if a < 1.0:
            root.after(15, fade, a)

    fade()
    root.after(60, lambda: (root.focus_force(), entry.focus_set(), refresh_field()))
    tick()
    root.mainloop()

    if result["status"] == "answered":
        history = [h for h in history if h != result["text"]] + [result["text"]]
        _save(HISTORY, history[-CFG["history_size"]:])
    return result["text"], result["status"]


def record(start, end, reason):
    text, status = ask(start, end, reason)
    send({
        "timestamp": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "duration": round(end - start, 1),
        "data": {"message": text, "status": status, "reason": reason},
    })


# --- main loop --------------------------------------------------------------------------

def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text with display scaling
    except (AttributeError, OSError):
        pass

    if TEST:
        now = time.time()
        record(now - 23 * 60, now, "idle")
        return

    kernel32.CreateMutexW(None, False, "aw-watcher-away-singleton")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS -> already running
        return

    now = time.time()
    detector = AwayDetector(CFG["threshold_min"] * 60, now, now - idle_seconds())
    while True:
        time.sleep(2)
        now = time.time()
        event = detector.step(now, now - idle_seconds(), screen_locked())
        if event:
            record(*event)
            now = time.time()
            detector.resync(now, now - idle_seconds())


if __name__ == "__main__":
    main()
