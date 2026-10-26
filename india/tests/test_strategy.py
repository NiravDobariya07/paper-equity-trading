import datetime
import unittest

from india import ist, strategy
from india.yahoo import Bar

D = datetime.date(2026, 10, 27)


def bar(i, o, h, l, c):
    return Bar(ist.bar_start(D, i), o, h, l, c, 1000.0)


RANGE = [bar(0, 100, 102, 99, 101), bar(1, 101, 103, 98, 102), bar(2, 102, 101, 100, 100.5)]


class Strategy(unittest.TestCase):
    def test_opening_range(self):
        self.assertEqual(strategy.opening_range(RANGE), (103, 98))
        self.assertIsNone(strategy.opening_range(RANGE[:2]))

    def test_first_trigger(self):
        bars = RANGE + [bar(3, 101, 103, 101, 102.9), bar(4, 103, 104, 102, 103.5), bar(5, 103, 106, 103, 105)]
        self.assertEqual(strategy.first_trigger(bars, 103, upto=5), 4)
        self.assertIsNone(strategy.first_trigger(bars, 103, upto=3))
        flat = RANGE + [bar(3, 102, 103.2, 102, 103.0)]
        self.assertIsNone(strategy.first_trigger(flat, 103, upto=3))  # equal is not above

    def test_make_plan(self):
        p = strategy.make_plan(104, 103, 98, 2, 0.05)
        self.assertEqual((p["stop"], p["target"], p["risk"]), (100.5, 111.0, 3.5))
        p = strategy.make_plan(100.52, 103, 98, 2, 0.05)
        self.assertEqual((p["stop"], p["target"], p["risk"]), (100.5, 100.6, 0.02))
        self.assertIsNone(strategy.make_plan(100.5, 103, 98, 2, 0.05))
        p = strategy.make_plan(1234.56, 1230.12, 1200.0, 2, 0.05)
        self.assertEqual(p["stop"], 1215.05)      # 1215.06 floored to the tick
        self.assertEqual(p["target"], 1273.6)     # 1273.58 ceiled to the tick

    def test_exit_on_bar(self):
        stop, target = 100.5, 111.0
        self.assertEqual(strategy.exit_on_bar(bar(9, 105, 112, 100, 106), stop, target), (100.5, "stop"))
        self.assertEqual(strategy.exit_on_bar(bar(9, 99, 104, 98, 103), stop, target), (99, "stop"))
        self.assertEqual(strategy.exit_on_bar(bar(9, 113, 114, 112, 113), stop, target), (113, "target"))
        self.assertEqual(strategy.exit_on_bar(bar(9, 108, 111.2, 107, 110), stop, target), (111.0, "target"))
        self.assertIsNone(strategy.exit_on_bar(bar(9, 104, 110, 101, 105), stop, target))

    def test_strength(self):
        self.assertAlmostEqual(strategy.strength(104, 100), 0.04)
