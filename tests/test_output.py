import struct
import tempfile
import time
import unittest
from pathlib import Path

from _load import load

out = load("watchers/aw-watcher-output/aw_watcher_output.py", "aw_watcher_output")


def make_lnk(base=None, net=None, suffix=""):
    """Minimal .lnk with only a LinkInfo block (local base path or network share + suffix)."""
    header = bytearray(0x4C)
    header[0x14:0x18] = struct.pack("<I", 2)  # HasLinkInfo
    if base is not None:
        body = base.encode("mbcs") + b"\0"
        suffix_off = 0x1C + len(body)
        info = struct.pack("<7I", 0, 0x1C, 1, 0, 0x1C, 0, suffix_off) + body + suffix.encode("mbcs") + b"\0"
    else:
        cnrl = struct.pack("<5I", 0, 0, 0x14, 0, 0) + net.encode("mbcs") + b"\0"
        suffix_off = 0x1C + len(cnrl)
        info = struct.pack("<7I", 0, 0x1C, 2, 0, 0, 0x1C, suffix_off) + cnrl + suffix.encode("mbcs") + b"\0"
    return bytes(header) + info


class LnkTest(unittest.TestCase):
    def test_local_path(self):
        self.assertEqual(out.lnk_target(make_lnk(base=r"C:\proj\board.kicad_sch")), Path(r"C:\proj\board.kicad_sch"))

    def test_network_path(self):
        self.assertEqual(out.lnk_target(make_lnk(net=r"\\nas\share", suffix=r"docs\spec.pdf")),
                         Path(r"\\nas\share\docs\spec.pdf"))

    def test_no_link_info(self):
        self.assertIsNone(out.lnk_target(bytes(0x4C)))


class ReflogTest(unittest.TestCase):
    def test_only_commits_are_reported(self):
        text = (
            "0000000 abcdef1234567 A <a@x> 1790000000 +0200\tcommit (initial): first\n"
            "abcdef1234567 1111111111111 A <a@x> 1790000100 +0200\tcheckout: moving from main to dev\n"
            "1111111111111 2222222222222 A <a@x> 1790000200 +0200\tcommit: fix: antenna match\n"
            "2222222222222 3333333333333 A <a@x> 1790000300 +0200\tcommit (amend): fix antenna\n"
        )
        self.assertEqual(out.parse_reflog(text), [
            {"hash": "abcdef1234", "msg": "first"},
            {"hash": "2222222222", "msg": "fix: antenna match"},
            {"hash": "3333333333", "msg": "fix antenna"},
        ])


class FilterTest(unittest.TestCase):
    def test_work_files_count(self):
        for p in ["hw/board.kicad_sch", "src/main.c", "thesis/chapter1.tex", "report.docx"]:
            self.assertTrue(out.interesting(p), p)

    def test_noise_is_ignored(self):
        for p in ["hw/board.kicad_prl", "hw/board-backups/x.zip", ".git/index", "node_modules/a/b.js",
                  "~$report.docx", "build/fw.o", "fp-info-cache", "src/__pycache__/m.pyc",
                  "src/gw/gw.ino.tmp.15932.450f0f9c0084",  # Arduino IDE save
                  "_autosave-board.kicad_pcb", ".$diagram.drawio.dtmp"]:
            self.assertFalse(out.interesting(p), p)


class DriveLetterTest(unittest.TestCase):
    def test_lower_case_drive_is_the_same_project(self):
        self.assertEqual(out.norm_drive(Path("c:/Users/me/repo")), out.norm_drive(Path("C:/Users/me/repo")))
        self.assertEqual(str(out.norm_drive(Path("c:/x")))[0], "C")

    def test_unc_and_none_pass_through(self):
        self.assertEqual(out.norm_drive(Path(r"\\nas\share\x")), Path(r"\\nas\share\x"))
        self.assertIsNone(out.norm_drive(None))


class SketchSearchTest(unittest.TestCase):
    def setUp(self):
        out._sketch_cache.clear()
        self.base = Path(tempfile.mkdtemp())
        sketch = self.base / "src" / "node" / "LoRa_TX"
        sketch.mkdir(parents=True)
        (sketch / "LoRa_TX.ino").write_text("void setup(){}")
        (self.base / "build" / "LoRa_RX").mkdir(parents=True)  # ignored folder, no .ino anyway

    def test_finds_sketch_folder_by_name(self):
        self.assertEqual(out.find_sketch("LoRa_TX", [self.base]), self.base / "src" / "node" / "LoRa_TX")

    def test_miss_is_cached_and_serial_monitor_title_is_harmless(self):
        self.assertIsNone(out.find_sketch("COM6", [self.base]))
        self.assertIn("COM6", out._sketch_cache)

    def test_arduino_window_not_in_recent_list(self):
        saved = out.ARDUINO, out.sketch_bases
        out.ARDUINO = ResolveTest.Fixed([])
        out.sketch_bases = lambda: [self.base]
        try:
            self.assertEqual(out.resolve("Arduino IDE.exe", "LoRa_TX | Arduino IDE 2.3.6"),
                             out.norm_drive(self.base / "src" / "node" / "LoRa_TX"))
        finally:
            out.ARDUINO, out.sketch_bases = saved


class ProjectRootTest(unittest.TestCase):
    def setUp(self):
        out.project_root.cache_clear()
        self.tmp = Path(tempfile.mkdtemp())

    def test_git_repo_wins_over_nested_kicad_project(self):
        board = self.tmp / "repo" / "hw" / "board"
        board.mkdir(parents=True)
        (self.tmp / "repo" / ".git").mkdir()
        (board / "board.kicad_pro").write_text("{}")
        self.assertEqual(out.project_root(board), self.tmp / "repo")

    def test_marker_without_git(self):
        proj = self.tmp / "fw"
        (proj / "src").mkdir(parents=True)
        (proj / "platformio.ini").write_text("")
        self.assertEqual(out.project_root(proj / "src"), proj)

    def test_non_project_dir_is_rejected(self):
        out.NON_PROJECT_DIRS.add(self.tmp)
        try:
            self.assertIsNone(out.project_root(self.tmp))
        finally:
            out.NON_PROJECT_DIRS.discard(self.tmp)


class ResolveTest(unittest.TestCase):
    class Fixed:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    def setUp(self):
        self.saved = out.KICAD, out.ARDUINO, out.VSCODE, out.RECENT
        out.KICAD = self.Fixed([Path(r"C:\p\LoRa\hw\gw\gw.kicad_pro"), Path(r"C:\p\LoRa\hw\gw_v2\gw_v2.kicad_pro")])
        out.ARDUINO = self.Fixed([Path(r"C:\a\anemometer")])
        out.VSCODE = self.Fixed([Path(r"C:\code\web")])
        out.RECENT = self.Fixed([{"spec.docx": Path(r"C:\docs\thesis\spec.docx")}])

    def tearDown(self):
        out.KICAD, out.ARDUINO, out.VSCODE, out.RECENT = self.saved

    def test_sources(self):
        cases = {
            ("kicad.exe", "gw_v2 — PCB Editor"): Path(r"C:\p\LoRa\hw\gw_v2"),  # longest name wins
            ("kicad.exe", "gw — Schematic Editor"): Path(r"C:\p\LoRa\hw\gw"),
            ("Arduino IDE.exe", "anemometer | Arduino IDE 2.3.6"): Path(r"C:\a\anemometer"),
            ("Code.exe", "main.ts - web - Visual Studio Code"): Path(r"C:\code\web"),
            ("WINWORD.EXE", "spec.docx - Word"): Path(r"C:\docs\thesis"),
            ("chrome.exe", "spec.docx - Google Chrome"): None,  # browsers are ignored
            ("notepad.exe", "unknown.txt - Notepad"): None,
        }
        for (app, title), expected in cases.items():
            self.assertEqual(out.resolve(app, title), expected, title)


class FolderWatchTest(unittest.TestCase):
    def test_saves_are_reported_and_noise_is_not(self):
        root = Path(tempfile.mkdtemp())
        (root / "hw" / "hw-backups").mkdir(parents=True)
        seen = []
        watch = out.FolderWatch(root, lambda r, rel: seen.append(rel.replace("\\", "/")))
        time.sleep(0.3)
        (root / "hw" / "board.kicad_sch").write_text("v1")
        (root / "main.c").write_text("int main(){}")
        (root / "hw" / "board.kicad_prl").write_text("ui")
        (root / "hw" / "hw-backups" / "b.zip").write_text("x")
        time.sleep(1.0)
        watch.stop()
        self.assertEqual(set(seen), {"hw/board.kicad_sch", "main.c"})


if __name__ == "__main__":
    unittest.main()
