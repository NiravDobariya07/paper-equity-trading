"""NSE equity list -> today's liquid symbols (price and 20-day traded value floors)."""

import csv
import json
import os
import urllib.error

from india import config, yahoo

NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def read_equity_list(path):
    """Symbols in the EQ series: the only series that can be traded intraday."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            "NSE's stock list is missing. Open %s in your browser and save it as %s" % (NSE_URL, path))
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = csv.reader(fh)
        header = [h.strip().upper() for h in next(rows)]
        si, ser = header.index("SYMBOL"), header.index("SERIES")
        return [r[si].strip() for r in rows if len(r) > ser and r[ser].strip() == "EQ"]


def liquid(daily, before, min_price, min_turnover, days):
    """Symbols whose last close >= min_price and mean close*volume over the last `days`
    sessions before `before` >= min_turnover, most traded first."""
    out = []
    for sym, bars in daily.items():
        past = [b for b in bars if b.t.date() < before][-days:]
        if len(past) < days:
            continue
        avg = sum(b.c * b.v for b in past) / days
        if past[-1].c >= min_price and avg >= min_turnover:
            out.append({"symbol": sym, "close": past[-1].c, "avg_turnover": avg})
    out.sort(key=lambda r: -r["avg_turnover"])
    return out


def liquid_path(day, data_dir=None):
    return os.path.join(data_dir or config.DATA, "liquid-%s.json" % day.isoformat())


def prepare(cfg, day, fetch=yahoo.fetch_chart, log=print, data_dir=None):
    syms = read_equity_list(cfg["equity_list"])
    log("Checking %d NSE EQ stocks for price >= %s and 20-day traded value >= %s (one request each)..."
        % (len(syms), cfg["min_price"], cfg["min_turnover"]))
    daily, nodata, failed = {}, 0, 0
    for n, sym in enumerate(syms, 1):
        try:
            daily[sym] = fetch(sym, "2mo", "1d").bars
        except yahoo.NoData:
            nodata += 1
        except (urllib.error.URLError, OSError, ValueError):
            failed += 1
        if n % 200 == 0:
            log("  %d of %d" % (n, len(syms)))
    if syms and not daily:
        raise RuntimeError("no daily data at all (%d failed requests): is the network up?" % failed)
    rows = liquid(daily, day, cfg["min_price"], cfg["min_turnover"], cfg["turnover_days"])
    path = liquid_path(day, data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"day": day.isoformat(), "symbols": [r["symbol"] for r in rows],
                   "dropped_nodata": nodata, "failed": failed, "rows": rows}, fh, indent=1)
    log("%d liquid stocks for %s (%d with no Yahoo data, %d failed requests)."
        % (len(rows), day.isoformat(), nodata, failed))
    return path
