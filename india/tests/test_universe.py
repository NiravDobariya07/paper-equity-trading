import datetime
import json
import os
import tempfile
import unittest

from india import ist, universe, yahoo
from india.yahoo import Bar

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
DAY = datetime.date(2026, 10, 27)


def daily(close, volume, n=20, last=datetime.date(2026, 10, 26)):
    days, d = [], last
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= datetime.timedelta(days=1)
    return [Bar(ist.at(x, "09:15"), close, close, close, close, float(volume)) for x in reversed(days)]


class Universe(unittest.TestCase):
    def test_read_equity_list_keeps_eq_only(self):
        self.assertEqual(universe.read_equity_list(os.path.join(FIX, "EQUITY_L-excerpt.csv")),
                         ["RELIANCE", "TCS", "M&M"])

    def test_missing_list_says_where_to_get_it(self):
        with self.assertRaises(FileNotFoundError) as cm:
            universe.read_equity_list("/nonexistent/EQUITY_L.csv")
        self.assertIn("nsearchives.nseindia.com", str(cm.exception))
        self.assertIn("/nonexistent/EQUITY_L.csv", str(cm.exception))

    def test_liquid_filters(self):
        crore = 10000000
        data = {
            "CHEAP": daily(40, 100 * crore / 40),          # below ₹50
            "THIN": daily(100, 9 * crore / 100),            # ₹9 crore a day
            "OK": daily(100, 11 * crore / 100),             # ₹11 crore a day
            "BIG": daily(1000, 500 * crore / 1000),
            "SHORT": daily(100, 11 * crore / 100, n=19),    # not enough history
        }
        # a bar on the scan day itself must not count: make it huge for THIN
        data["THIN"] = data["THIN"] + [Bar(ist.at(DAY, "09:15"), 100, 100, 100, 100, 1e12)]
        got = universe.liquid(data, DAY, 50, 100000000, 20)
        self.assertEqual([r["symbol"] for r in got], ["BIG", "OK"])
        self.assertAlmostEqual(got[1]["avg_turnover"], 11 * crore)

    def test_liquid_rows_carry_the_atr(self):
        crore = 10000000
        bars = daily(100, 11 * crore / 100)
        bars = [b._replace(h=b.c + 1, l=b.c - 1) for b in bars]     # true range 2 every day
        (row,) = universe.liquid({"OK": bars}, DAY, 50, 100000000, 20)
        self.assertAlmostEqual(row["atr"], 2.0)

    def test_prepare_retries_failed_requests_once(self):
        import urllib.error
        tmp = tempfile.mkdtemp()
        cfg = {"equity_list": os.path.join(FIX, "EQUITY_L-excerpt.csv"), "min_price": 50,
               "min_turnover": 100000000, "turnover_days": 20}
        calls = []

        def fetch(sym, rng, interval):
            calls.append(sym)
            if sym == "TCS" and calls.count("TCS") == 1:
                raise urllib.error.URLError("timed out")
            if sym == "M&M":
                raise urllib.error.URLError("timed out")
            return yahoo.Chart(daily(1000, 1e6), 1000.0, None)
        path = universe.prepare(cfg, DAY, fetch=fetch, log=lambda *a: None, data_dir=tmp)
        with open(path) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["symbols"], ["RELIANCE", "TCS"])
        self.assertEqual(doc["failed"], 1)
        self.assertEqual(calls.count("TCS"), 2)
        self.assertEqual(calls.count("M&M"), 2)

    def test_prepare_writes_the_day_file(self):
        tmp = tempfile.mkdtemp()
        cfg = {"equity_list": os.path.join(FIX, "EQUITY_L-excerpt.csv"), "min_price": 50,
               "min_turnover": 100000000, "turnover_days": 20}

        def fetch(sym, rng, interval):
            if sym == "TCS":
                raise yahoo.NoData(sym)
            return yahoo.Chart(daily(1000, 1e6), 1000.0, None)
        path = universe.prepare(cfg, DAY, fetch=fetch, log=lambda *a: None, data_dir=tmp)
        self.assertEqual(os.path.basename(path), "liquid-2026-10-27.json")
        with open(path) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["symbols"], ["RELIANCE", "M&M"])
        self.assertEqual(doc["dropped_nodata"], 1)
        self.assertEqual(doc["day"], "2026-10-27")
