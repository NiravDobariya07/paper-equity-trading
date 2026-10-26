import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from india import config, ist, paper_trader

D = datetime.date(2026, 10, 27)   # a Tuesday
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class Stop(Exception):
    pass


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old, config.DATA = config.DATA, self.tmp
        self.slept = []

    def tearDown(self):
        config.DATA = self.old

    def main(self, argv, now):
        def sleep(s):
            self.slept.append(s)
            raise Stop()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = paper_trader.main(argv, clock=lambda: now, sleep=sleep)
        return code, out.getvalue()

    def test_weekend(self):
        code, out = self.main(["run"], ist.at(datetime.date(2026, 10, 25), "10:00"))
        self.assertEqual(code, 0)
        self.assertIn("not a trading day", out)

    def test_holiday_from_config(self):
        with open(os.path.join(self.tmp, "config.json"), "w") as fh:
            json.dump({"holidays": [D.isoformat()]}, fh)
        code, out = self.main(["run"], ist.at(D, "10:00"))
        self.assertEqual(code, 0)
        self.assertIn("not a trading day", out)

    def test_after_close(self):
        code, out = self.main(["run"], ist.at(D, "15:45"))
        self.assertEqual(code, 0)
        self.assertIn("market closed", out)

    def test_missing_equity_list(self):
        code, out = self.main(["run"], ist.at(D, "10:00"))
        self.assertEqual(code, 2)
        self.assertIn("nsearchives.nseindia.com", out)
        self.assertIn(os.path.join(self.tmp, "EQUITY_L.csv"), out)

    def test_early_start_waits_for_the_range(self):
        with open(os.path.join(self.tmp, "liquid-%s.json" % D.isoformat()), "w") as fh:
            json.dump({"day": D.isoformat(), "symbols": ["RELIANCE"]}, fh)
        with self.assertRaises(Stop):
            self.main(["run"], ist.at(D, "08:00"))
        self.assertEqual(self.slept, [5405.0])   # until 09:30:05

    def test_whole_day_offline(self):
        from india.tests.test_engine import bar, breakout_at
        from india.yahoo import Chart
        with open(os.path.join(self.tmp, "liquid-%s.json" % D.isoformat()), "w") as fh:
            json.dump({"day": D.isoformat(), "symbols": ["AAA", "BBB"]}, fh)
        day = {"AAA": breakout_at(9, extra=[bar(30, 110, h=110.5, l=108)]),   # target at the 11:45 bar
               "BBB": breakout_at(60, close=101.5, after=101.5)}               # never closes above 102
        clock = [ist.at(D, "10:00")]

        def fetch_chart(sym, rng, interval):
            if rng == "60d":
                raise paper_trader.yahoo.NoData(sym)
            bars = [b for b in day[sym] if b.t <= clock[0]]
            return Chart(bars, bars[-1].c, clock[0])

        def sleep(s):
            self.slept.append(s)
            clock[0] += datetime.timedelta(seconds=s)
        out = io.StringIO()
        with mock.patch.object(paper_trader.yahoo, "fetch_chart", fetch_chart), \
                contextlib.redirect_stdout(out):
            code = paper_trader.main(["run"], clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(code, 0, out.getvalue())
        with open(os.path.join(self.tmp, "state.json")) as fh:
            (t,) = json.load(fh)["trades"]
        self.assertEqual((t["symbol"], t["reason"], t["exit_time"][11:16]), ("AAA", "target", "11:45"))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "paper_trades.xlsx")))
        self.assertGreaterEqual(clock[0], ist.at(D, "15:15"))   # stayed until the close-all
        self.assertLess(clock[0], ist.at(D, "15:21"))
        self.assertIn("1 paper trade(s)", out.getvalue())

    def write_liquid(self, symbols):
        with open(os.path.join(self.tmp, "liquid-%s.json" % D.isoformat()), "w") as fh:
            json.dump({"day": D.isoformat(), "symbols": symbols}, fh)

    def test_unlisted_holiday_exits_with_a_reason(self):
        from india.tests.test_engine import bar
        from india.yahoo import Chart
        self.write_liquid(["AAA"])
        y = [bar(i, 101, day=datetime.date(2026, 10, 26)) for i in range(70)]
        clock = [ist.at(D, "10:00")]

        def sleep(s):
            clock[0] += datetime.timedelta(seconds=s)
        out = io.StringIO()
        with mock.patch.object(paper_trader.yahoo, "fetch_chart", lambda s, r, i: Chart(y, 101.0, clock[0])), \
                contextlib.redirect_stdout(out):
            code = paper_trader.main(["run"], clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(code, 0)
        self.assertIn("no 09:15 bar", out.getvalue())
        self.assertLess(clock[0], ist.at(D, "10:06"))

    def test_empty_list_exits_with_a_reason(self):
        self.write_liquid([])
        code, out = self.main(["run"], ist.at(D, "10:00"))
        self.assertEqual(code, 1)
        self.assertIn("no liquid stocks", out)

    def test_restart_after_close_settles_open_trades(self):
        from india import ledger
        from india.tests.test_engine import breakout_at
        from india.yahoo import Chart
        st = ledger.load(os.path.join(self.tmp, "state.json"), 20000)
        st["trades"].append(ledger.new_trade(D.isoformat(), "AAA", "orb", D.isoformat() + "T10:05:05+05:30",
                                             103.1, 64, 100.0, 109.3, None))
        ledger.save(os.path.join(self.tmp, "state.json"), st)
        bars = breakout_at(9)
        out = io.StringIO()
        with mock.patch.object(paper_trader.yahoo, "fetch_chart", lambda s, r, i: Chart(bars, 103.1, None)), \
                contextlib.redirect_stdout(out):
            code = paper_trader.main(["run"], clock=lambda: ist.at(D, "16:00"), sleep=lambda s: None)
        self.assertEqual(code, 0)
        with open(os.path.join(self.tmp, "state.json")) as fh:
            (t,) = json.load(fh)["trades"]
        self.assertEqual(t["reason"], "close-all")

    def test_prepare_after_close_is_for_the_next_trading_day(self):
        shutil.copy(os.path.join(FIX, "EQUITY_L-excerpt.csv"), os.path.join(self.tmp, "EQUITY_L.csv"))
        got = []
        with mock.patch.object(paper_trader.universe, "prepare",
                               lambda cfg, day, **kw: got.append(day) or "x"):
            code, _ = self.main(["prepare"], ist.at(datetime.date(2026, 10, 30), "18:00"))   # Friday evening
        self.assertEqual(code, 0)
        self.assertEqual(got, [datetime.date(2026, 11, 2)])                                # Monday
