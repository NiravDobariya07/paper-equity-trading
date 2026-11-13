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
        # while it was open, every sweep showed its live price and P&L
        self.assertRegex(out.getvalue(), r"\n  BUY +AAA +64 @ 103\.10")
        self.assertRegex(out.getvalue(), r"now 103\.10 .* P&L \+0\.00, net -[0-9.]+ after charges")
        self.assertIn("open net", out.getvalue())

    def test_very_early_start_hands_off_instead_of_timing_out(self):
        # GitHub fired a schedule at 02:18: waiting until 09:30 would outlive the job
        with open(os.path.join(self.tmp, "liquid-%s.json" % D.isoformat()), "w") as fh:
            json.dump({"day": D.isoformat(), "symbols": ["RELIANCE"]}, fh)
        clock = [ist.at(D, "02:18")]

        def sleep(s):
            self.slept.append(s)
            clock[0] += datetime.timedelta(seconds=s)
        with contextlib.redirect_stdout(io.StringIO()):
            code = paper_trader.main(["run", "--max-minutes", "320"], clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(code, paper_trader.HANDOFF)
        self.assertEqual(self.slept, [320 * 60.0])     # waited only as long as it may, then hands off

    def test_max_minutes_hands_off_and_a_second_run_carries_on(self):
        from india.tests.test_engine import bar, breakout_at
        from india.yahoo import Chart
        self.write_liquid(["AAA"])
        day = {"AAA": breakout_at(9, extra=[bar(30, 110, h=110.5, l=108)])}   # target at the 11:45 bar
        clock = [ist.at(D, "10:00")]

        def fetch_chart(sym, rng, interval):
            if rng == "60d":
                raise paper_trader.yahoo.NoData(sym)
            bars = [b for b in day[sym] if b.t <= clock[0]]
            return Chart(bars, bars[-1].c, clock[0])

        def sleep(s):
            clock[0] += datetime.timedelta(seconds=s)
        out = io.StringIO()
        with mock.patch.object(paper_trader.yahoo, "fetch_chart", fetch_chart), \
                contextlib.redirect_stdout(out):
            code = paper_trader.main(["run", "--max-minutes", "60"], clock=lambda: clock[0], sleep=sleep)
            self.assertEqual(code, paper_trader.HANDOFF, out.getvalue())
            self.assertLessEqual(clock[0], ist.at(D, "11:00"))
            with open(os.path.join(self.tmp, "state.json")) as fh:
                (t,) = json.load(fh)["trades"]
            self.assertIsNone(t["exit"])                                        # open, and saved
            code = paper_trader.main(["run"], clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(code, 0, out.getvalue())
        with open(os.path.join(self.tmp, "state.json")) as fh:
            (t,) = json.load(fh)["trades"]
        self.assertEqual((t["reason"], t["exit_time"][11:16]), ("target", "11:45"))

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
        with open(os.path.join(self.tmp, "state.json")) as fh:     # a later job will not retry today
            self.assertTrue(json.load(fh)["days"][D.isoformat()]["market_closed"])

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

    def test_stocks_dearer_than_capital_are_left_out(self):
        doc = {"symbols": ["MRF", "RELIANCE", "PAGEIND"],
               "rows": [{"symbol": "MRF", "close": 117380.0}, {"symbol": "RELIANCE", "close": 1201.6},
                        {"symbol": "PAGEIND", "close": 20000.0}]}
        self.assertEqual(paper_trader.tradable(doc, 20000), ["RELIANCE", "PAGEIND"])
        self.assertEqual(paper_trader.tradable({"symbols": ["A", "B"]}, 20000), ["A", "B"])

    def test_prepare_after_close_is_for_the_next_trading_day(self):
        shutil.copy(os.path.join(FIX, "EQUITY_L-excerpt.csv"), os.path.join(self.tmp, "EQUITY_L.csv"))
        got = []
        with mock.patch.object(paper_trader.universe, "prepare",
                               lambda cfg, day, **kw: got.append(day) or "x"):
            code, _ = self.main(["prepare"], ist.at(datetime.date(2026, 10, 30), "18:00"))   # Friday evening
        self.assertEqual(code, 0)
        self.assertEqual(got, [datetime.date(2026, 11, 2)])                                # Monday


class Follow(unittest.TestCase):
    """run --follow: after the day (or on a day off) prepare the next list, wait, hand off."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old, config.DATA = config.DATA, self.tmp
        self.prepared, self.slept = [], []

    def tearDown(self):
        config.DATA = self.old

    def go(self, start, argv):
        clock = [start]

        def sleep(s):
            self.slept.append(s)
            clock[0] += datetime.timedelta(seconds=max(0, s))

        def prepare(cfg, day):
            self.prepared.append((clock[0].strftime("%H:%M"), day))
            return 0
        out = io.StringIO()
        with mock.patch.object(paper_trader, "prepare", prepare), contextlib.redirect_stdout(out):
            code = paper_trader.main(argv, clock=lambda: clock[0], sleep=sleep)
        return code, out.getvalue(), clock[0]

    def test_after_the_close_prepares_tomorrow_then_hands_off(self):
        code, out, end = self.go(ist.at(D, "15:40"), ["run", "--follow", "--max-minutes", "320"])
        self.assertEqual(code, paper_trader.HANDOFF, out)
        self.assertEqual(self.prepared, [("15:50", datetime.date(2026, 10, 28))])
        self.assertEqual(end, ist.at(D, "15:40") + datetime.timedelta(minutes=320))

    def test_the_chain_reaches_the_next_morning(self):
        # Friday 23:00: Saturday and Sunday are skipped, Monday's list is prepared, wake at 08:45
        fri = datetime.date(2026, 10, 30)
        code, out, end = self.go(ist.at(fri, "23:00"), ["run", "--follow", "--max-minutes", "320"])
        self.assertEqual(code, paper_trader.HANDOFF, out)
        self.assertEqual(self.prepared, [("23:00", datetime.date(2026, 11, 2))])   # missing: made at once
        self.assertIn("Next session 2026-11-02", out)
        self.assertEqual(end, ist.at(fri, "23:00") + datetime.timedelta(minutes=320))
        # Monday 08:00: within reach of the 08:45 wake-up, so it sleeps until then and trades the day


    def test_no_prepare_right_before_the_hand_off(self):
        # started 10:40: its 320 minutes end at 16:00, 10 minutes after 15:50
        code, out, end = self.go(ist.at(datetime.date(2026, 10, 31), "10:40"),   # a Saturday
                                 ["run", "--follow", "--max-minutes", "320"])
        self.assertEqual((code, self.prepared), (paper_trader.HANDOFF, []))
        self.assertIn("the next job will", out)
        self.assertEqual(end, ist.at(datetime.date(2026, 10, 31), "16:00"))

    def test_without_follow_nothing_changes(self):
        code, out, _ = self.go(ist.at(D, "15:40"), ["run", "--max-minutes", "320"])
        self.assertEqual((code, self.prepared), (0, []))

    def test_market_found_closed_is_remembered(self):
        state = {"capital_start": 20000.0, "trades": [], "days": {D.isoformat(): {
            "done": [], "skipped": [], "market_closed": True}}}
        with open(os.path.join(self.tmp, "state.json"), "w") as fh:
            json.dump(state, fh)
        code, out, _ = self.go(ist.at(D, "09:50"), ["run", "--follow", "--max-minutes", "320"])
        self.assertIn("found closed earlier today", out)
        self.assertEqual(code, paper_trader.HANDOFF)
        # its 320 minutes end at 15:10, before 15:50: the next job in the chain prepares the list
        self.assertEqual(self.prepared, [])
        self.assertIn("stock list for 2026-10-28 comes next", out)
