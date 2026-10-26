"""Time Machine odds for an NSE trade, using the upstream simulator read-only.

Yahoo almost never fills NSE's 15:20 and 15:25 bars, and every trade is closed
by 15:15, so a session here is bars 0..71 (09:15 to the 15:10 bar's close).
"""

import collections
import importlib.util

from india import config, ist

UPSTREAM = config.ROOT + "/day-trader-skill/trader-timemachine/timemachine.py"
SESSION = 72
MAX_GAPS = 3        # missing bars per session filled flat at the previous close
MIN_SESSIONS = 20
_mod = None


def upstream():
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location("upstream_timemachine", UPSTREAM)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        _mod.SESSION_BARS = SESSION   # on the loaded module object only, never the file
    return _mod


def sessions_before(bars, day):
    by_day = collections.defaultdict(dict)
    for b in bars:
        i = ist.bar_index(b.t)
        if b.t.date() < day and 0 <= i < SESSION:
            by_day[b.t.date()][i] = b
    out = []
    for d in sorted(by_day):
        got = by_day[d]
        if 0 not in got or SESSION - len(got) > MAX_GAPS:
            continue
        D, prev = [], None
        for i in range(SESSION):
            b = got.get(i)
            D.append({"o": b.o, "h": b.h, "l": b.l, "c": b.c} if b else {"o": prev, "h": prev, "l": prev, "c": prev})
            prev = D[-1]["c"]
        out.append((d, D))
    return out


def odds(bars60, day, entry, stop, target, k, last=SESSION - 1, replays=10000, seed=19):
    """{"target", "stop", "neither", "sessions"} for bars k+1..last, or None without enough history."""
    sessions = sessions_before(bars60, day)
    if len(sessions) < MIN_SESSIONS or k >= last:
        return None
    m = upstream()
    mc = m.simulate(m.build_pool(sessions), entry, stop, target, k, last, replays=replays, seed=seed)
    return {"target": mc["p_target"], "stop": mc["p_stop"], "neither": mc["p_neither"],
            "sessions": len(sessions)}
