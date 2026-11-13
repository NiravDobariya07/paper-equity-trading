"""The paper-trading record: one JSON file, the source of truth for the workbook."""

import json
import os
import tempfile

from india import money


def file_mode():
    """What a plain open() would give: mkstemp's 0600 would hide the files from other readers."""
    m = os.umask(0)
    os.umask(m)
    return 0o666 & ~m


def load(path, capital):
    if not os.path.exists(path):
        return {"capital_start": float(capital), "trades": [], "days": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(path, state):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
        os.chmod(tmp, file_mode())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def new_trade(date, symbol, setup, entry_time, entry, shares, stop, target, odds, side=1):
    """side=1 long (bought at entry), side=-1 short (sold at entry, bought back at the exit)."""
    odds = odds or {}
    return {
        "id": "%s-%s" % (date, symbol), "date": date, "symbol": symbol, "setup": setup, "side": side,
        "entry_time": entry_time, "entry": entry, "shares": shares, "value": round(entry * shares, 2),
        "stop": stop, "target": target, "risk": round((entry - stop) * side * shares, 2),
        "tm_target": odds.get("target"), "tm_stop": odds.get("stop"),
        "checked_through": None, "exit_time": None, "exit": None, "reason": None,
        "gross": None, "charges": None, "net": None,
    }


def unrealized(t, price, rates):
    """{"gross", "charges", "net"} if the trade were closed at `price` now. Changes nothing."""
    side = t.get("side", 1)   # trades recorded before shorts existed are longs
    gross = round((price - t["entry"]) * side * t["shares"], 2)
    buy, sell = (t["entry"], price) if side == 1 else (price, t["entry"])
    chg = money.charges(buy * t["shares"], sell * t["shares"], rates)
    return {"gross": gross, "charges": chg, "net": round(gross - chg, 2)}


def close(t, price, when, reason, rates):
    price = round(price, 2)
    t["exit"], t["exit_time"], t["reason"] = price, when, reason
    t.update(unrealized(t, price, rates))


def open_trades(state):
    return [t for t in state["trades"] if t["exit"] is None]


def capital(state):
    """Starting capital plus every closed trade's net P&L."""
    return state["capital_start"] + sum(t["net"] for t in state["trades"] if t["exit"] is not None)


def cash(state):
    return capital(state) - sum(t["value"] for t in open_trades(state))


def day(state, date):
    return state["days"].setdefault(date, {"done": [], "skipped": []})


def day_stats(state, date):
    ts = [t for t in state["trades"] if t["date"] == date]
    return {
        "realized_net": sum(t["net"] for t in ts if t["exit"] is not None),
        "open_risk": sum(t["risk"] for t in ts if t["exit"] is None),
        "trades": len(ts),
    }
