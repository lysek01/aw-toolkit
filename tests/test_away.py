import unittest

from _load import load

away = load("watchers/aw-watcher-away/aw_watcher_away.py", "aw_watcher_away")
T = 600  # threshold: 10 min


def run(detector, steps):
    """steps: iterable of (now, last_input, locked); returns all emitted events."""
    return [e for e in (detector.step(*s) for s in steps) if e]


class AwayDetectorTest(unittest.TestCase):
    def test_short_idle_is_ignored(self):
        d = away.AwayDetector(T, 0, 0)
        steps = [(t, 0, False) for t in range(2, 300, 2)] + [(300, 300, False)]
        self.assertEqual(run(d, steps), [])

    def test_long_idle_reported_when_input_returns(self):
        d = away.AwayDetector(T, 0, 0)
        steps = [(t, 0, False) for t in range(2, 700, 2)] + [(700, 700, False), (702, 701, False)]
        self.assertEqual(run(d, steps), [(0, 700, "idle")])

    def test_lock_reported_on_unlock_only(self):
        d = away.AwayDetector(T, 0, 90)
        steps = [(100, 90, False)] + [(t, 90, True) for t in range(102, 900, 2)] + [(902, 901, False)]
        self.assertEqual(run(d, steps), [(90, 902, "locked")])

    def test_short_lock_is_ignored(self):
        d = away.AwayDetector(T, 0, 90)
        steps = [(100, 90, False)] + [(t, 90, True) for t in range(102, 300, 2)] + [(302, 301, False)]
        self.assertEqual(run(d, steps), [])

    def test_sleep_without_lock(self):
        d = away.AwayDetector(T, 0, 90)
        self.assertEqual(run(d, [(100, 90, False), (2000, 1999, False)]), [(90, 2000, "sleep")])

    def test_sleep_while_locked_is_reported_once_after_unlock(self):
        d = away.AwayDetector(T, 0, 90)
        steps = [(100, 90, True), (2000, 90, True), (2002, 90, True), (2100, 2099, False),
                 (2102, 2101, False), (2104, 2103, False)]
        self.assertEqual(run(d, steps), [(90, 2100, "sleep")])


class TextsTest(unittest.TestCase):
    def test_languages_have_the_same_keys(self):
        self.assertEqual(set(away.TEXTS["en"]), set(away.TEXTS["cs"]))

    def test_quick_button_icons_exist(self):
        for lang in away.TEXTS.values():
            for _, icon in lang["quick"]:
                self.assertIn(icon, away.GLYPHS)


if __name__ == "__main__":
    unittest.main()
