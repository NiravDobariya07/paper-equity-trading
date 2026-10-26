import datetime
import gzip
import json
import os
import unittest

from india import tm, yahoo

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def bars60():
    with gzip.open(os.path.join(FIX, "RELIANCE-60d-5m.json.gz")) as fh:
        return yahoo.parse_chart(json.load(fh)).bars


class TimeMachine(unittest.TestCase):
    def setUp(self):
        self.bars = bars60()
        self.day = self.bars[-1].t.date()   # the fixture's last session plays "today"
        self.entry = self.bars[-1].c

    def odds(self, bars, replays=2000):
        return tm.odds(bars, self.day, self.entry, round(self.entry * 0.99, 2), round(self.entry * 1.02, 2),
                       k=10, replays=replays)

    def test_odds_sum_to_one_and_repeat(self):
        a = self.odds(self.bars)
        self.assertEqual(sorted(a), ["neither", "sessions", "stop", "target"])
        self.assertGreaterEqual(a["sessions"], 50)
        self.assertAlmostEqual(a["target"] + a["stop"] + a["neither"], 1.0, delta=0.001)
        self.assertEqual(a, self.odds(self.bars))

    def test_too_little_history_is_none(self):
        days = sorted({b.t.date() for b in self.bars})
        recent = [b for b in self.bars if b.t.date() >= days[-11]]
        self.assertIsNone(self.odds(recent))

    def test_upstream_file_is_untouched(self):
        tm.odds(self.bars, self.day, self.entry, self.entry * 0.99, self.entry * 1.02, k=10, replays=100)
        self.assertEqual(tm.upstream().SESSION_BARS, tm.SESSION)
        with open(tm.UPSTREAM, encoding="utf-8") as fh:
            self.assertNotIn("SESSION_BARS = 72", fh.read())
