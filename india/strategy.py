"""Setups and their rules.

orb       long opening-range breakout: range, trigger, stop at the range midpoint, target at R.
gap_fade  short a stock that gapped up and is already trading below its open; stop an ATR above,
          no target (held to close-all). Backtest over 60 sessions of 5-minute bars: see
          docs/superpowers/specs/2026-11-04-gap-fade-short.md.
"""

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from india import ist


def opening_range(bars, n=3):
    """(high, low) of the session's first n bars, or None until all n are there."""
    first = [b for b in bars if ist.bar_index(b.t) < n]
    if sorted(ist.bar_index(b.t) for b in first) != list(range(n)):
        return None
    return max(b.h for b in first), min(b.l for b in first)


def first_trigger(bars, orh, n=3, upto=None):
    """Index of the first bar after the range (up to `upto`) that closes above the range high."""
    for b in bars:
        i = ist.bar_index(b.t)
        if i < n:
            continue
        if upto is not None and i > upto:
            break
        if b.c > orh:
            return i
    return None


def _tick(x, tick, rounding):
    t = Decimal(str(tick))
    return float((Decimal(str(x)) / t).quantize(Decimal(1), rounding=rounding) * t)


def make_plan(entry, orh, orl, target_r, tick):
    """Stop at the range midpoint (down to the tick), target at target_r x risk (up to the tick)."""
    stop = _tick((orh + orl) / 2, tick, ROUND_FLOOR)
    if stop >= entry:
        return None
    risk = round(entry - stop, 2)
    target = _tick(round(entry + target_r * (entry - stop), 6), tick, ROUND_CEILING)
    return {"stop": stop, "target": target, "risk": risk}


def exit_on_bar(bar, stop, target, side=1):
    """(price, reason) if this bar ends the trade. A bar touching both counts as the stop.
    side=1 long, side=-1 short; target None means only the stop (or close-all) ends it."""
    if side == 1:
        if bar.o <= stop:
            return bar.o, "stop"
        if target is not None and bar.o >= target:
            return bar.o, "target"
        if bar.l <= stop:
            return stop, "stop"
        if target is not None and bar.h >= target:
            return target, "target"
        return None
    if bar.o >= stop:
        return bar.o, "stop"
    if target is not None and bar.o <= target:
        return bar.o, "target"
    if bar.h >= stop:
        return stop, "stop"
    if target is not None and bar.l <= target:
        return target, "target"
    return None


def atr(daily, n=14):
    """Mean true range of the last n daily bars (needs n+1 bars), or None."""
    if len(daily) < n + 1:
        return None
    rows = daily[-(n + 1):]
    trs = [max(b.h, p.c) - min(b.l, p.c) for p, b in zip(rows[:-1], rows[1:])]
    return sum(trs) / float(n)


def fade_plan(open_, prev_close, price, atr_, cfg):
    """Short plan for a gap-up that is fading, or the reason it is not one (a string)."""
    gap = open_ / prev_close - 1
    if gap * 100 < cfg["fade_gap_min_pct"]:
        return "gap below %g%%" % cfg["fade_gap_min_pct"]
    if gap * 100 > cfg["fade_gap_max_pct"]:
        return "gap above %g%%" % cfg["fade_gap_max_pct"]
    if price > open_ * (1 - cfg["fade_below_open_pct"] / 100.0):
        return "not %g%% below the open" % cfg["fade_below_open_pct"]
    if not atr_:
        return "no ATR"
    stop = _tick(round(price + cfg["fade_stop_atr"] * atr_, 6), cfg["tick"], ROUND_CEILING)
    return {"stop": stop, "target": None, "risk": round(stop - price, 2), "gap": gap}


def strength(price, orh):
    return (price - orh) / orh
