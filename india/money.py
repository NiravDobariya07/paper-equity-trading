"""Zerodha intraday charges (estimates from config), share count, and the risk check."""

import math

from india import ist


def charges(buy_value, sell_value, rates):
    def brokerage(v):
        return min(rates["brokerage_cap"], v * rates["brokerage_pct"] / 100)
    brk = brokerage(buy_value) + brokerage(sell_value)
    stt = sell_value * rates["stt_sell_pct"] / 100
    txn = (buy_value + sell_value) * rates["txn_pct"] / 100
    sebi = (buy_value + sell_value) * rates["sebi_per_crore"] / 1e7
    stamp = buy_value * rates["stamp_buy_pct"] / 100
    gst = (brk + txn + sebi) * rates["gst_pct"] / 100
    return round(brk + stt + txn + sebi + stamp + gst, 2)


def size(entry, stop, cash, max_loss):
    """Whole shares: the loss at the stop stays within max_loss and the cost within cash."""
    risk = entry - stop
    if risk <= 0 or entry <= 0:
        return 0
    return max(0, min(math.floor(max_loss / risk + 1e-9), math.floor(cash / entry + 1e-9)))


def risk_check(risk_rupees, est_charges, day, cfg, now):
    """(ok, reason). Skips, never shrinks: a trade that does not fit is not taken."""
    if risk_rupees <= 0:
        return False, "zero shares"
    if now >= ist.at(now.date(), cfg["no_entries_after"]):
        return False, "after %s" % cfg["no_entries_after"]
    if day["trades"] >= cfg["max_trades_per_day"]:
        return False, "trade limit"
    used = -min(day["realized_net"], 0.0) + day["open_risk"]
    if used + risk_rupees + est_charges > cfg["max_daily_loss"]:
        return False, "daily loss"
    return True, "ok"
