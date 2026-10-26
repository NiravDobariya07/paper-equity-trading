import datetime
import os
import tempfile
import unittest

import openpyxl

from india import config, engine, ist, ledger, yahoo
from india.yahoo import Bar, Chart

D = datetime.date(2026, 10, 27)
Y = datetime.date(2026, 10, 26)


def at(hhmm, sec=5, day=D):
    return ist.at(day, hhmm) + datetime.timedelta(seconds=sec)


def bar(i, c, h=None, l=None, o=None, day=D):
    o = c if o is None else o
    return Bar(ist.bar_start(day, i), o, max(o, c) + 0.2 if h is None else h,
               min(o, c) - 0.2 if l is None else l, c, 1000.0)


RANGE = [bar(0, 100, h=102, l=98), bar(1, 101, h=102, l=99), bar(2, 100, h=101, l=98.5)]  # 102 / 98


def day_of(closes_from_3, day=D, extra=()):
    """Range bars, then one bar per close from index 3, then explicit extra bars."""
    bars = [b._replace(t=ist.bar_start(day, ist.bar_index(b.t))) for b in RANGE]
    bars += [bar(3 + j, c, day=day) for j, c in enumerate(closes_from_3)]
    by = {ist.bar_index(b.t): b for b in bars}
    for b in extra:
        by[ist.bar_index(b.t)] = b
    return [by[i] for i in sorted(by)]


def breakout_at(i, close=103.0, after=103.1, extra=()):
    """Flat at 101 until bar i closes at `close`, then `after` for the rest of the 75 bars."""
    return day_of([101.0] * (i - 3) + [close] + [after] * (75 - i - 1), extra=extra)


class Feed(object):
    """fetch_today: the bars that exist at `now` (the in-progress bar included), price = last close."""

    def __init__(self, days, nodata=()):
        self.days, self.nodata, self.now, self.calls = days, set(nodata), None, []

    def today(self, sym):
        self.calls.append(sym)
        if sym in self.nodata:
            raise yahoo.NoData(sym)
        bars = [b for b in self.days[sym] if b.t <= self.now]
        return Chart(bars, bars[-1].c, self.now)

    def sixty(self, sym):
        raise yahoo.NoData(sym)


class Engine(unittest.TestCase):
    def setUp(self):
        self.cfg = config.defaults()
        self.tmp = tempfile.mkdtemp()
        self.state = ledger.load(os.path.join(self.tmp, "state.json"), 20000)

    def run_engine(self, days, times, nodata=(), sixty=None):
        feed = Feed(days, nodata)
        eng = engine.Engine(self.cfg, self.state, sorted(days), feed.today, sixty or feed.sixty,
                            log=lambda *a: None, state_path=os.path.join(self.tmp, "state.json"),
                            book_path=os.path.join(self.tmp, "paper_trades.xlsx"))
        events = []
        for t in times:
            feed.now = t
            events += eng.sweep(t)
        return eng, events

    def test_breakout_enters_at_live_price(self):
        self.run_engine({"AAA": breakout_at(9)}, [at("10:05")])
        (t,) = self.state["trades"]
        self.assertEqual((t["symbol"], t["entry"], t["stop"]), ("AAA", 103.1, 100.0))
        self.assertEqual(t["shares"], 64)                # floor(200 / 3.1)
        self.assertEqual(t["target"], 109.3)             # 103.1 + 2 x 3.1
        self.assertEqual(t["entry_time"][:16], "2026-10-27T10:05")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "paper_trades.xlsx")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "state.json")))

    def test_one_shot_per_symbol(self):
        self.run_engine({"AAA": breakout_at(9)}, [at("10:05"), at("10:10"), at("10:15")])
        self.assertEqual(len(self.state["trades"]), 1)

    def test_strongest_first_then_trade_limit(self):
        self.cfg["max_daily_loss"] = 100000
        days = {"A": breakout_at(9, 103, 103.1), "B": breakout_at(9, 104, 104.1),
                "C": breakout_at(9, 105, 105.1), "D": breakout_at(9, 102.5, 102.6)}
        self.cfg["max_loss_per_trade"] = 60   # keep cash from being the limit
        self.run_engine(days, [at("10:05")])
        self.assertEqual([t["symbol"] for t in self.state["trades"]], ["C", "B", "A"])
        skipped = self.state["days"][D.isoformat()]["skipped"]
        self.assertEqual([(s["symbol"], s["reason"]) for s in skipped], [("D", "trade limit")])

    def test_daily_loss_counts_open_risk(self):
        days = {"A": breakout_at(9, 103, 103.1), "B": breakout_at(9, 104, 104.1),
                "C": breakout_at(9, 105, 105.1)}
        self.run_engine(days, [at("10:05")])
        # two full ₹200 risks are open; a third would put ₹600 + charges at risk
        self.assertEqual(len(self.state["trades"]), 2)
        skipped = self.state["days"][D.isoformat()]["skipped"]
        self.assertIn(("A", "daily loss"), [(s["symbol"], s["reason"]) for s in skipped])

    def test_skip_reasons_say_cash_or_stop_distance(self):
        self.cfg["max_daily_loss"] = 100000
        self.cfg["max_loss_per_trade"] = 100000         # so only cash limits A's size
        days = {"A": breakout_at(9, 103, 103.1), "B": breakout_at(9, 102.5, 102.6)}
        self.run_engine(days, [at("10:05")])
        self.assertEqual([t["symbol"] for t in self.state["trades"]], ["A"])
        self.assertEqual(self.state["days"][D.isoformat()]["skipped"][0]["reason"], "not enough free cash")
        self.cfg["max_loss_per_trade"] = 2              # less than one share's risk
        self.state = ledger.load(os.path.join(self.tmp, "none.json"), 20000)
        self.run_engine({"A": breakout_at(9, 103, 103.1)}, [at("10:05")])
        self.assertEqual(self.state["days"][D.isoformat()]["skipped"][0]["reason"], "stop too far for max loss")

    def test_catch_up_skips_a_breakout_that_faded(self):
        days = {"AAA": day_of([101, 101, 101, 103.0, 101.5, 101.2, 101, 101, 101, 101, 101])}
        self.run_engine(days, [at("10:05")])
        self.assertEqual(self.state["trades"], [])
        dd = self.state["days"][D.isoformat()]
        self.assertEqual(dd["done"], ["AAA"])
        self.assertEqual(dd["skipped"][0]["reason"], "back below range high")

    def test_catch_up_enters_at_current_price(self):
        days = {"AAA": day_of([101, 101, 103.0] + [104.0] * 10)}   # broke out at 09:40
        self.run_engine(days, [at("10:05")])
        (t,) = self.state["trades"]
        self.assertEqual(t["entry"], 104.0)

    def test_restart_replays_missed_bars(self):
        bars = breakout_at(9, extra=[bar(13, 101, h=103.5, l=99.5)])   # 10:20 bar hits the 100.0 stop
        self.run_engine({"AAA": bars}, [at("10:05")])
        # the program "crashed" after 10:05 and comes back at 11:00 with what it saved
        self.state = ledger.load(os.path.join(self.tmp, "state.json"), 1)
        self.assertIsNone(self.state["trades"][0]["exit"])
        self.run_engine({"AAA": bars}, [at("11:00")])
        (t,) = self.state["trades"]
        self.assertEqual((t["reason"], t["exit"]), ("stop", 100.0))
        self.assertEqual(t["exit_time"][11:16], "10:20")
        self.assertLess(t["net"], 0)

    def test_both_touched_counts_as_stop(self):
        bars = breakout_at(9, extra=[bar(11, 104, h=110, l=99)])
        self.run_engine({"AAA": bars}, [at("10:05"), at("10:10"), at("10:15")])
        self.assertEqual(self.state["trades"][0]["reason"], "stop")

    def test_target(self):
        bars = breakout_at(9, extra=[bar(12, 109, h=109.5, l=108)])
        self.run_engine({"AAA": bars}, [at("10:05"), at("10:20")])
        t = self.state["trades"][0]
        self.assertEqual((t["reason"], t["exit"], t["exit_time"][11:16]), ("target", 109.3, "10:15"))

    def test_close_all_at_1515(self):
        bars = breakout_at(9, after=103.1)
        bars[71] = bar(71, 102.4)
        self.run_engine({"AAA": bars}, [at("10:05"), at("15:16")])
        t = self.state["trades"][0]
        self.assertEqual((t["reason"], t["exit"], t["exit_time"][11:16]), ("close-all", 102.4, "15:15"))

    def test_nodata_symbol_is_skipped(self):
        eng, events = self.run_engine({"AAA": breakout_at(9), "ZOMATO": breakout_at(9)}, [at("10:05")],
                                      nodata=["ZOMATO"])
        self.assertEqual([t["symbol"] for t in self.state["trades"]], ["AAA"])
        self.assertTrue(any("1 symbol" in e for e in events))

    def test_no_entries_after_1430(self):
        self.run_engine({"AAA": breakout_at(63)}, [at("14:35")])   # 14:30 bar breaks out
        self.assertEqual(self.state["trades"], [])

    def test_open_trade_from_an_earlier_day_is_closed_from_that_day(self):
        old = ledger.new_trade(Y.isoformat(), "AAA", "orb", Y.isoformat() + "T10:05:05+05:30",
                               103.1, 64, 100.0, 109.3, None)
        self.state["trades"].append(old)
        yday = [bar(i, 104.0, day=Y) for i in range(72)]

        def sixty(sym):
            return yday + breakout_at(9)
        self.run_engine({"AAA": breakout_at(30)}, [at("10:05")], sixty=sixty)
        self.assertEqual((old["reason"], old["exit"], old["exit_time"][:16]),
                         ("close-all", 104.0, "2026-10-26T15:15"))

    def test_spark_prefilter_for_big_lists(self):
        days = {"A": breakout_at(20), "B": breakout_at(11), "C": breakout_at(40)}
        feed = Feed(days)
        asked = []

        def spark(syms):
            asked.append(list(syms))
            return {s: [(b.t, b.c) for b in days[s] if b.t <= feed.now] for s in syms}
        eng = engine.Engine(self.cfg, self.state, sorted(days), feed.today, feed.sixty, log=lambda *a: None,
                            fetch_spark=spark, spark_over=1)
        feed.now = at("10:05")
        eng.sweep(feed.now)                 # first sweep: full charts for all, ranges learned
        self.assertEqual(sorted(feed.calls), ["A", "B", "C"])
        feed.calls[:] = []
        feed.now = at("10:15")
        eng.sweep(feed.now)                 # B's 10:10 bar closed above its range: only B is fetched
        self.assertEqual(asked, [["A", "B", "C"]])
        self.assertEqual(feed.calls, ["B"])
        self.assertEqual([t["symbol"] for t in self.state["trades"]], ["B"])

    def test_mid_bar_entry_ignores_that_bars_earlier_prices(self):
        # catch-up entry at 10:02: the 10:00 bar's high touched the target before the position existed
        bars = day_of([101, 101, 103.0] + [104.0] * 4 + [104.0] * 6,
                      extra=[bar(9, 104, h=120, l=103.5), bar(10, 104.5, h=105, l=104)])
        self.run_engine({"AAA": bars}, [at("10:02", sec=0), at("10:10")])
        self.assertIsNone(self.state["trades"][0]["exit"])

    def test_entry_on_the_bar_open_checks_that_bar(self):
        bars = breakout_at(9, extra=[bar(10, 104, h=120, l=103.5)])   # 10:05 bar reaches the target
        self.run_engine({"AAA": bars}, [at("10:05"), at("10:10")])
        self.assertEqual(self.state["trades"][0]["reason"], "target")

    def test_any_fetch_error_is_per_symbol(self):
        import http.client
        days = {"AAA": breakout_at(9), "BAD": breakout_at(9)}
        feed = Feed(days)

        def today(sym):
            if sym == "BAD":
                raise http.client.IncompleteRead(b"")
            return feed.today(sym)
        eng = engine.Engine(self.cfg, self.state, sorted(days), today, feed.sixty, log=lambda *a: None)
        feed.now = at("10:05")
        events = eng.sweep(feed.now)
        self.assertEqual([t["symbol"] for t in self.state["trades"]], ["AAA"])
        self.assertTrue(any("1 symbol" in e for e in events))

    def test_entry_time_is_when_the_price_was_fetched(self):
        feed = Feed({"AAA": breakout_at(9)})
        late = at("10:08", sec=40)
        eng = engine.Engine(self.cfg, self.state, ["AAA"], feed.today, feed.sixty, log=lambda *a: None,
                            clock=lambda: late)
        feed.now = at("10:05")
        eng.sweep(feed.now)
        self.assertEqual(self.state["trades"][0]["entry_time"][11:19], "10:08:40")

    def test_range_is_not_cached_before_the_last_range_bar_settles(self):
        early = day_of([], extra=[bar(2, 100, h=101, l=98.5)])        # 09:25 bar as first seen at 09:30:05
        final = day_of([101] * 10, extra=[bar(2, 100, h=104, l=98.5)])  # Yahoo's settled 09:25 bar
        feed = Feed({"AAA": early})
        eng = engine.Engine(self.cfg, self.state, ["AAA"], feed.today, feed.sixty, log=lambda *a: None)
        feed.now = at("09:30")
        eng.sweep(feed.now)
        feed.days["AAA"] = final
        feed.now = at("09:35")
        eng.sweep(feed.now)
        self.assertEqual(eng.ranges["AAA"][0], 104)

    def test_no_bars_today_is_reported(self):
        y = [bar(i, 101, day=Y) for i in range(70)]
        eng, events = self.run_engine({"AAA": y, "BBB": y}, [at("10:05")])
        self.assertTrue(any("2 symbol(s) have no bars today" in e for e in events))
        self.assertEqual(eng.last_sweep["with_bars_today"], 0)

    def test_workbook_matches_state(self):
        self.run_engine({"AAA": breakout_at(9)}, [at("10:05")])
        wb = openpyxl.load_workbook(os.path.join(self.tmp, "paper_trades.xlsx"))
        self.assertEqual(wb["Trades"].max_row, 2)
