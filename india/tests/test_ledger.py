import os
import stat
import tempfile
import unittest

import openpyxl

from india import config, ledger, workbook

RATES = config.defaults()["charges"]


def trade(sym="RELIANCE", date="2026-10-27", entry=1000.0, shares=10, stop=990.0, target=1020.0):
    return ledger.new_trade(date, sym, "orb", date + "T10:05:05+05:30", entry, shares, stop, target,
                            {"target": 0.3, "stop": 0.25, "neither": 0.45})


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "state.json")

    def test_missing_file_is_empty_state(self):
        s = ledger.load(self.path, 20000)
        self.assertEqual((s["capital_start"], s["trades"], s["days"]), (20000, [], {}))

    def test_round_trip_and_no_temp_left(self):
        s = ledger.load(self.path, 20000)
        s["trades"].append(trade())
        ledger.save(self.path, s)
        self.assertEqual(ledger.load(self.path, 1)["trades"], s["trades"])
        self.assertEqual(os.listdir(self.tmp), ["state.json"])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), ledger.file_mode())
        self.assertTrue(ledger.file_mode() & stat.S_IRUSR)

    def test_cash_and_close(self):
        s = ledger.load(self.path, 20000)
        t = trade()
        s["trades"].append(t)
        self.assertEqual(ledger.cash(s), 10000)
        self.assertEqual(ledger.open_trades(s), [t])
        ledger.close(t, 1020.0, "2026-10-27T11:00:00+05:30", "target", RATES)
        self.assertEqual(t["gross"], 200.0)
        self.assertAlmostEqual(t["net"], 200.0 - t["charges"])
        self.assertGreater(t["charges"], 0)
        self.assertEqual(ledger.open_trades(s), [])
        self.assertAlmostEqual(ledger.cash(s), 20000 + t["net"])

    def test_gross_uses_the_recorded_exit(self):
        t = trade()
        ledger.close(t, 1020.004, "2026-10-27T11:00:00+05:30", "target", RATES)
        self.assertEqual((t["exit"], t["gross"]), (1020.0, 200.0))

    def test_day_stats(self):
        s = ledger.load(self.path, 20000)
        a, b, c = trade(), trade("TCS", stop=995.0), trade("INFY", date="2026-10-26")
        s["trades"] += [a, b, c]
        ledger.close(a, 990.0, "2026-10-27T10:30:00+05:30", "stop", RATES)
        st = ledger.day_stats(s, "2026-10-27")
        self.assertEqual(st["trades"], 2)
        self.assertAlmostEqual(st["realized_net"], a["net"])
        self.assertEqual(st["open_risk"], 50.0)  # TCS: 10 shares x ₹5


class Workbook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = {"capital_start": 20000, "trades": [], "days": {}}
        a, b = trade(), trade("TCS")
        ledger.close(a, 1020.0, "2026-10-27T11:00:00+05:30", "target", RATES)
        self.state["trades"] += [a, b]

    def test_sheets_rows_and_capital(self):
        path = os.path.join(self.tmp, "paper_trades.xlsx")
        self.assertTrue(workbook.write(self.state, path, today="2026-10-27"))
        wb = openpyxl.load_workbook(path)
        self.assertEqual(wb.sheetnames, ["Trades", "Summary"])
        rows = list(wb["Trades"].iter_rows(values_only=True))
        self.assertEqual(list(rows[0]), workbook.TRADE_COLUMNS)
        self.assertEqual(len(rows), 4)   # header, 2 trades, TOTAL
        summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True) if r and r[0]}
        self.assertAlmostEqual(summary["Current capital"], ledger.cash(self.state) + 10000)
        self.assertEqual(summary["Trades (closed)"], 1)
        self.assertEqual(os.listdir(self.tmp), ["paper_trades.xlsx"])

    def test_total_row_at_the_bottom(self):
        c = trade("INFY")
        ledger.close(c, 990.0, "2026-10-27T12:00:00+05:30", "stop", RATES)
        self.state["trades"].append(c)
        path = os.path.join(self.tmp, "paper_trades.xlsx")
        workbook.write(self.state, path, today="2026-10-27")
        ws = openpyxl.load_workbook(path)["Trades"]
        last = dict(zip(workbook.TRADE_COLUMNS, next(ws.iter_rows(min_row=ws.max_row, values_only=True))))
        closed = [t for t in self.state["trades"] if t["exit"] is not None]
        self.assertEqual(ws.max_row, 1 + 3 + 1)
        self.assertEqual(last["Date"], "TOTAL")
        self.assertEqual(last["Symbol"], "3 trades (2 closed)")
        self.assertAlmostEqual(last["Gross P&L"], sum(t["gross"] for t in closed))
        self.assertAlmostEqual(last["Charges"], sum(t["charges"] for t in closed))
        self.assertAlmostEqual(last["Net P&L"], sum(t["net"] for t in closed))
        self.assertIsNone(last["Entry price"])

    def test_net_pnl_is_green_for_profit_red_for_loss(self):
        loss = trade("INFY")
        ledger.close(loss, 990.0, "2026-10-27T12:00:00+05:30", "stop", RATES)
        self.state["trades"].append(loss)
        path = os.path.join(self.tmp, "paper_trades.xlsx")
        workbook.write(self.state, path, today="2026-10-27")
        ws = openpyxl.load_workbook(path)["Trades"]
        col = workbook.TRADE_COLUMNS.index("Net P&L") + 1
        fill = {ws.cell(row=r, column=2).value: ws.cell(row=r, column=col).fill.fgColor.rgb
                for r in range(2, ws.max_row + 1)}
        self.assertEqual(fill["RELIANCE"][-6:], workbook.GREEN)     # +200 gross, closed at target
        self.assertEqual(fill["INFY"][-6:], workbook.RED)          # stopped out
        self.assertNotIn(fill["TCS"][-6:], (workbook.GREEN, workbook.RED))   # still open: no net yet
        self.assertEqual(fill["3 trades (2 closed)"][-6:], workbook.GREEN if sum(
            t["net"] for t in self.state["trades"] if t["net"] is not None) > 0 else workbook.RED)

    def test_unwritable_returns_false(self):
        ro = os.path.join(self.tmp, "ro")
        os.mkdir(ro)
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
        try:
            self.assertFalse(workbook.write(self.state, os.path.join(ro, "x.xlsx")))
        finally:
            os.chmod(ro, stat.S_IRWXU)
