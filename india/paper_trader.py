"""NSE opening-range-breakout paper trader. Paper only: nothing here can place an order.

  python3 india/paper_trader.py prepare   today's liquid stock list (or the next trading day's, after 15:30)
  python3 india/paper_trader.py run       start around 10:00 IST; enters, exits and records paper trades
  python3 india/paper_trader.py report    rebuild paper_trades.xlsx and print the summary

Files live in trader/india/ next to this folder: config.json, EQUITY_L.csv (save it from
https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv), state.json, paper_trades.xlsx.
"""

import argparse
import datetime
import json
import os
import sys
import time
import traceback

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from india import config, engine, ist, ledger, universe, workbook, yahoo  # noqa: E402

GRACE = datetime.timedelta(seconds=5)   # after a bar closes, before asking Yahoo for it


def paths():
    return (os.path.join(config.DATA, "state.json"), os.path.join(config.DATA, "paper_trades.xlsx"))


def next_trading_day(day, holidays):
    day += datetime.timedelta(days=1)
    while not ist.is_trading_day(day, holidays):
        day += datetime.timedelta(days=1)
    return day


def session_day(now, holidays):
    if ist.is_trading_day(now.date(), holidays) and now < ist.at(now.date(), "15:30"):
        return now.date()
    return next_trading_day(now.date(), holidays)


def tradable(doc, capital):
    """Today's symbols minus any whose last close is above the whole paper capital."""
    close = {r["symbol"]: r["close"] for r in doc.get("rows") or ()}
    return [s for s in doc["symbols"] if close.get(s, 0) <= capital]


def prepare(cfg, day):
    try:
        universe.prepare(cfg, day, data_dir=config.DATA)
    except FileNotFoundError as e:
        print(e)
        return 2
    except RuntimeError as e:
        print("prepare failed: %s" % e)
        return 1
    return 0


def summary(state, day):
    d = day.isoformat()
    ts = [t for t in state["trades"] if t["date"] == d]
    print("\n%s: %d paper trade(s)" % (d, len(ts)))
    for t in ts:
        if t["exit"] is None:
            print("  %-12s %4d @ %.2f  open" % (t["symbol"], t["shares"], t["entry"]))
        else:
            print("  %-12s %4d @ %.2f -> %.2f  %-9s net %8.2f" % (
                t["symbol"], t["shares"], t["entry"], t["exit"], t["reason"], t["net"]))
    st = ledger.day_stats(state, d)
    print("Today's net P&L: %.2f   Capital: %.2f (started at %.2f)"
          % (st["realized_net"], ledger.capital(state), state["capital_start"]))


def make_engine(cfg, state, symbols, clock):
    state_path, book_path = paths()
    return engine.Engine(
        cfg, state, symbols,
        fetch_today=lambda s: yahoo.fetch_chart(s, "1d", "5m"),
        fetch_60d=lambda s: yahoo.fetch_chart(s, "60d", "5m").bars,
        fetch_spark=lambda syms: yahoo.fetch_spark(syms, "1d", "5m"),
        state_path=state_path, book_path=book_path, clock=clock)


def settle_open(cfg, clock):
    """Close paper trades left open by a crash, from the real bars of their day."""
    state = ledger.load(paths()[0], cfg["capital"])
    if ledger.open_trades(state):
        print("Settling %d paper trade(s) left open..." % len(ledger.open_trades(state)))
        make_engine(cfg, state, [], clock).settle(clock())


def run(cfg, clock, sleep):
    now = clock()
    today = now.date()
    if not ist.is_trading_day(today, cfg["holidays"]):
        settle_open(cfg, clock)
        print("%s is not a trading day (weekend or a holiday in config.json). Nothing to do." % today)
        return 0
    if now >= ist.at(today, "15:30"):
        settle_open(cfg, clock)
        print("The market closed for today at 15:30. Start it tomorrow around 10:00.")
        return 0
    liquid = universe.liquid_path(today, config.DATA)
    if not os.path.exists(liquid):
        print("No stock list for today yet: preparing it (several minutes)...")
        code = prepare(cfg, today)
        if code:
            return code
    with open(liquid, encoding="utf-8") as fh:
        symbols = tradable(json.load(fh), cfg["capital"])
    if not symbols:
        print("There are no liquid stocks in today's list (%s). Check EQUITY_L.csv and the floors in "
              "config.json, then run prepare again." % liquid)
        return 1
    state_path, book_path = paths()
    state = ledger.load(state_path, cfg["capital"])
    eng = make_engine(cfg, state, symbols, clock)
    first = ist.at(today, "09:15") + datetime.timedelta(minutes=cfg["or_minutes"]) + GRACE
    now = clock()
    if now < first:
        print("Waiting for the opening range (09:15 to %s)..." % (first - GRACE).strftime("%H:%M"))
        sleep((first - now).total_seconds())
    print("Paper trading %d NSE stocks. Entries until %s, everything closed at %s. Ctrl+C stops it safely.\n"
          "Workbook: %s" % (len(symbols), cfg["no_entries_after"], cfg["close_all"], book_path))
    close_all = ist.at(today, cfg["close_all"])
    try:
        while True:
            now = clock()
            try:
                eng.sweep(now)
            except Exception:
                traceback.print_exc()
                print("%s sweep failed; trying again at the next bar." % now.strftime("%H:%M:%S"))
            st = ledger.day_stats(state, today.isoformat())
            n_open = len([t for t in ledger.open_trades(state) if t["date"] == today.isoformat()])
            print("%s checked. Trades today %d, open %d, net %.2f"
                  % (clock().strftime("%H:%M:%S"), st["trades"], n_open, st["realized_net"]))
            ls = eng.last_sweep
            if now >= ist.at(today, "09:35") and ls["checked"] and not ls["with_bars_today"]:
                print("Yahoo has no 09:15 bar for today in any stock: the market looks closed (a holiday "
                      "missing from config.json?). Exiting.")
                return 0
            if (now >= close_all and not ledger.open_trades(state)) or now >= ist.at(today, "15:30"):
                break
            nxt = ist.floor5(clock()) + ist.BAR + GRACE
            sleep(max(1.0, (nxt - clock()).total_seconds()))
    except KeyboardInterrupt:
        eng.save(clock())
        print("\nStopped. Everything so far is saved; run it again to carry on.")
        return 130
    summary(state, today)
    return 0


def main(argv=None, clock=ist.now, sleep=time.sleep):
    ap = argparse.ArgumentParser(description="NSE opening-range-breakout paper trader (paper only)")
    ap.add_argument("command", choices=["prepare", "run", "report"])
    a = ap.parse_args(argv)
    cfg = config.load(os.path.join(config.DATA, "config.json"))
    if a.command == "prepare":
        return prepare(cfg, session_day(clock(), cfg["holidays"]))
    if a.command == "run":
        return run(cfg, clock, sleep)
    state_path, book_path = paths()
    state = ledger.load(state_path, cfg["capital"])
    ok = workbook.write(state, book_path, today=clock().date().isoformat())
    print(("Wrote %s" if ok else "Could not write %s (open in another program?)") % book_path)
    summary(state, clock().date())
    return 0


if __name__ == "__main__":
    sys.exit(main())
