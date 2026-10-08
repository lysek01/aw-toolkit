import unittest
from datetime import datetime

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


    def test_no_popup_for_an_empty_chair(self):
        # PC wakes up by itself at t=2000, nobody touches it until t=2500
        d = away.AwayDetector(T, 0, 90)
        steps = [(100, 90, False)] + [(t, 90, False) for t in range(2000, 2500, 2)]
        self.assertEqual(run(d, steps), [])
        # the absence ends when the PC came back, not when the first key was pressed
        self.assertEqual(run(d, [(2500, 2499, False)]), [(90, 2000, "sleep")])

    def test_relock_before_any_input_is_one_absence(self):
        d = away.AwayDetector(T, 0, 90)
        steps = ([(t, 90, True) for t in range(100, 1000, 2)]
                 + [(t, 90, False) for t in range(1000, 1010, 2)]  # unlocked, but nobody touched anything
                 + [(t, 90, True) for t in range(1010, 1500, 2)] + [(1500, 1499, False)])
        self.assertEqual(run(d, steps), [(90, 1500, "locked")])

    def test_same_absence_is_never_reported_twice(self):
        # popup timed out; GetLastInputInfo still shows input from before the absence
        d = away.AwayDetector(T, 0, 90)
        steps = [(t, 90, True) for t in range(100, 1000, 2)] + [(1000, 995, False)]
        self.assertEqual(run(d, steps), [(90, 1000, "locked")])
        d.resync(1120, 995)
        self.assertEqual(run(d, [(t, 995, False) for t in range(1122, 4000, 2)]), [])
        # first input much later: a new absence that starts where the reported one ended
        self.assertEqual(run(d, [(4000, 4000, False)]), [(1000, 4000, "idle")])

    def test_resync_never_moves_prev_input_back(self):
        d = away.AwayDetector(T, 0, 500)
        d.resync(600, 100)
        self.assertEqual(d.prev_input, 500)


class NightTest(unittest.TestCase):
    @staticmethod
    def ts(day, hour, minute=0):
        return datetime(2026, 10, day, hour, minute).timestamp()

    def test_overnight_is_night(self):
        self.assertTrue(away.is_night(self.ts(5, 22, 25), self.ts(6, 9, 0), 3, 4))
        self.assertTrue(away.is_night(self.ts(5, 23, 0), self.ts(6, 3, 30), 3, 4))

    def test_long_daytime_absence_is_still_asked(self):
        self.assertFalse(away.is_night(self.ts(2, 13, 19), self.ts(2, 20, 1), 3, 4))  # 6.7 h field test

    def test_short_absence_at_night_is_asked(self):
        self.assertFalse(away.is_night(self.ts(6, 2, 30), self.ts(6, 3, 30), 3, 4))

    def test_rule_can_be_disabled(self):
        self.assertFalse(away.is_night(self.ts(5, 22, 0), self.ts(6, 9, 0), -1, 4))


class TextsTest(unittest.TestCase):
    def test_languages_have_the_same_keys(self):
        self.assertEqual(set(away.TEXTS["en"]), set(away.TEXTS["cs"]))

    def test_quick_button_icons_exist(self):
        for lang in away.TEXTS.values():
            for _, icon in lang["quick"]:
                self.assertIn(icon, away.GLYPHS)


if __name__ == "__main__":
    unittest.main()
