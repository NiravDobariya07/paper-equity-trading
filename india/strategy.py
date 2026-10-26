"""Long opening-range breakout: range, trigger, stop/target plan, exit on a bar."""

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


def exit_on_bar(bar, stop, target):
    """(price, reason) if this bar ends the trade. A bar touching both counts as the stop."""
    if bar.o <= stop:
        return bar.o, "stop"
    if bar.o >= target:
        return bar.o, "target"
    if bar.l <= stop:
        return stop, "stop"
    if bar.h >= target:
        return target, "target"
    return None


def strength(price, orh):
    return (price - orh) / orh
