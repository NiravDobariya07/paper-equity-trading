"""Rebuild paper_trades.xlsx (Trades + Summary) from the ledger state."""

import os
import tempfile

import openpyxl
from openpyxl.styles import Font

from india import ledger

TRADE_COLUMNS = ["Date", "Symbol", "Setup", "Entry time", "Entry price", "Shares", "Position value",
                 "Stop", "Target", "Risk ₹", "TM target first %", "TM stop first %", "Exit time",
                 "Exit price", "Exit reason", "Gross P&L", "Charges", "Net P&L", "Capital after"]


def _hm(iso):
    return iso[11:16] if iso else None


def _pct(x):
    return round(x * 100, 1) if x is not None else None


def _trades_sheet(ws, state):
    ws.append(TRADE_COLUMNS)
    running = state["capital_start"]
    after = {}
    for t in sorted((t for t in state["trades"] if t["exit"] is not None), key=lambda t: t["exit_time"]):
        running += t["net"]
        after[t["id"]] = round(running, 2)
    for t in state["trades"]:
        ws.append([t["date"], t["symbol"], t["setup"], _hm(t["entry_time"]), t["entry"], t["shares"],
                   t["value"], t["stop"], t["target"], t["risk"], _pct(t["tm_target"]), _pct(t["tm_stop"]),
                   _hm(t["exit_time"]), t["exit"], t["reason"] or "open", t["gross"], t["charges"],
                   t["net"], after.get(t["id"])])


def _summary_sheet(ws, state, today):
    closed = [t for t in state["trades"] if t["exit"] is not None]
    wins = [t["net"] for t in closed if t["net"] > 0]
    losses = [t["net"] for t in closed if t["net"] <= 0]
    st = ledger.day_stats(state, today) if today else {"realized_net": 0, "trades": 0}
    rows = [
        ("Starting capital", state["capital_start"]),
        ("Current capital", round(ledger.capital(state), 2)),
        ("Total net P&L", round(sum(t["net"] for t in closed), 2)),
        ("Trades (closed)", len(closed)),
        ("Open trades", len(ledger.open_trades(state))),
        ("Win rate %", round(100.0 * len(wins) / len(closed), 1) if closed else None),
        ("Average win", round(sum(wins) / len(wins), 2) if wins else None),
        ("Average loss", round(sum(losses) / len(losses), 2) if losses else None),
        ("Today", today),
        ("Today's net P&L", round(st["realized_net"], 2)),
        ("Today's trades", st["trades"]),
    ]
    for r in rows:
        ws.append(list(r))
    ws.append([])
    ws.append(["Date", "Trades", "Net P&L", "Capital at close"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    running = state["capital_start"]
    for d in sorted({t["date"] for t in state["trades"]}):
        ts = [t for t in closed if t["date"] == d]
        net = round(sum(t["net"] for t in ts), 2)
        running += net
        ws.append([d, len([t for t in state["trades"] if t["date"] == d]), net, round(running, 2)])


def write(state, path, today=None):
    """Write atomically. False (never an exception) when the file cannot be written."""
    try:
        wb = openpyxl.Workbook()
        trades = wb.active
        trades.title = "Trades"
        _trades_sheet(trades, state)
        for c in trades[1]:
            c.font = Font(bold=True)
        trades.freeze_panes = "A2"
        _summary_sheet(wb.create_sheet("Summary"), state, today)
        d = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".paper-", suffix=".xlsx", dir=d)
        os.close(fd)
        try:
            wb.save(tmp)
            os.chmod(tmp, ledger.file_mode())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except OSError:
        return False
