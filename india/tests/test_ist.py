import datetime
import unittest

from india import ist

D = datetime.date(2026, 10, 26)


class Clock(unittest.TestCase):
    def test_from_ts_is_ist(self):
        # the session start Yahoo reported for 2026-10-26
        t = ist.from_ts(1792986300)
        self.assertEqual((t.date(), t.hour, t.minute), (D, 9, 15))
        self.assertEqual(t.utcoffset(), datetime.timedelta(hours=5, minutes=30))

    def test_bar_index_and_start(self):
        self.assertEqual(ist.bar_index(ist.at(D, "09:15")), 0)
        self.assertEqual(ist.bar_index(ist.at(D, "10:07")), 10)
        self.assertEqual(ist.bar_start(D, 71), ist.at(D, "15:10"))
        self.assertEqual(ist.SESSION_BARS, 75)

    def test_trading_day(self):
        self.assertFalse(ist.is_trading_day(datetime.date(2026, 10, 25), []))  # Sunday
        self.assertFalse(ist.is_trading_day(D, ["2026-10-26"]))
        self.assertTrue(ist.is_trading_day(D, []))
