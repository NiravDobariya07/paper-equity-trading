"""One sweep of the paper trader: update open trades, find setups (ORB or gap fade), enter, save.

`sweep(now)` only reads charts through the fetch functions it was given, so a
whole day can be replayed offline in tests.
"""

import datetime

from india import ist, ledger, money, strategy, tm, workbook

FIVE = datetime.timedelta(minutes=5)
# Any failure while fetching or parsing one symbol's data (404, timeout, truncated reply,
# odd JSON) skips that symbol for this sweep; it must never abort the sweep.
FETCH_ERRORS = (Exception,)
ENTRY_GRACE = datetime.timedelta(seconds=30)  # an entry this close to a bar's open owns that bar
SETTLE = datetime.timedelta(seconds=60)       # Yahoo may still revise a bar this soon after it closes


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _when(iso):
    return datetime.datetime.fromisoformat(iso)


class Engine(object):
    def __init__(self, cfg, state, symbols, fetch_today, fetch_60d, log=print,
                 state_path=None, book_path=None, fetch_spark=None, spark_over=300, clock=None,
                 info=None, fetch_spark_meta=None):
        self.cfg, self.state, self.symbols = cfg, state, list(symbols)
        self.info = info or {}            # symbol -> {"close": previous close, "atr": 14-day ATR}
        self.fetch_spark_meta = fetch_spark_meta
        self.clock = clock
        self.fetched_at = {}  # symbol -> when this sweep's chart was fetched
        self.last_sweep = {"checked": 0, "with_bars_today": 0, "failed": 0}
        self.fetch_today, self.fetch_60d, self.log = fetch_today, fetch_60d, log
        self.fetch_spark, self.spark_over = fetch_spark, spark_over
        self.state_path, self.book_path = state_path, book_path
        self.n_range = cfg["or_minutes"] // 5
        self.ranges = {}      # symbol -> (high, low), today's opening range once known

    # ------------------------------------------------------------- open trades

    def _replay(self, t, bars, now, close_all):
        """Walk completed bars since the entry; close on the first stop/target hit or at close-all."""
        entered = _when(t["entry_time"])
        start = ist.floor5(entered)
        if entered - start > ENTRY_GRACE:
            start += FIVE   # that bar's high/low may come from before the position existed
        seen = _when(t["checked_through"]) if t["checked_through"] else None
        for b in bars:
            if b.t < start or (seen and b.t <= seen) or b.t + FIVE > now or b.t >= close_all:
                continue
            t["checked_through"] = _iso(b.t)
            hit = strategy.exit_on_bar(b, t["stop"], t["target"], t.get("side", 1))
            if hit:
                ledger.close(t, hit[0], _iso(b.t), hit[1], self.cfg["charges"])
                return "%s %s at %.2f (%s bar), net %.2f" % (t["symbol"], hit[1], hit[0],
                                                              b.t.strftime("%H:%M"), t["net"])
        if now >= close_all:
            last = [b for b in bars if b.t + FIVE <= close_all and b.t.date() == close_all.date()]
            if last:
                ledger.close(t, last[-1].c, _iso(close_all), "close-all", self.cfg["charges"])
                return "%s closed at %s: %.2f, net %.2f" % (t["symbol"], close_all.strftime("%H:%M"),
                                                           t["exit"], t["net"])
        return None

    def _update_open(self, now, charts, events):
        for t in ledger.open_trades(self.state):
            day = datetime.date.fromisoformat(t["date"])
            close_all = ist.at(day, self.cfg["close_all"])
            try:
                if day == now.date():
                    bars = self._chart(t["symbol"], charts, now).bars
                else:
                    bars = [b for b in self.fetch_60d(t["symbol"]) if b.t.date() == day]
            except FETCH_ERRORS as e:
                events.append("could not update %s: %s" % (t["symbol"], e))
                continue
            msg = self._replay(t, [b for b in bars if b.t.date() == day], now, close_all)
            if msg:
                events.append(msg)

    # ------------------------------------------------------------- entries

    def _chart(self, sym, charts, now):
        if sym not in charts:
            charts[sym] = self.fetch_today(sym)
            self.fetched_at[sym] = self.clock() if self.clock else now
        return charts[sym]

    def _to_check(self, syms, now):
        """With a big list, only symbols whose range is unknown or whose spark closes broke above it."""
        if not self.fetch_spark or len(syms) <= self.spark_over:
            return syms
        known = [s for s in syms if s in self.ranges]
        if not known:
            return syms
        try:
            sp = self.fetch_spark(known)
        except FETCH_ERRORS:
            return syms
        hot = set()
        for s in known:
            orh = self.ranges[s][0]
            if any(t + FIVE <= now and ist.bar_index(t) >= self.n_range and c > orh for t, c in sp.get(s) or ()):
                hot.add(s)
        return [s for s in syms if s not in self.ranges or s in hot]

    def _candidates(self, now, charts, dd, events):
        out, failed, checked, with_today = [], 0, 0, 0
        traded = {t["symbol"] for t in self.state["trades"] if t["date"] == now.date().isoformat()}
        syms = [s for s in self.symbols if s not in dd["done"] and s not in traded]
        for sym in self._to_check(syms, now):
            try:
                ch = self._chart(sym, charts, now)
            except FETCH_ERRORS:
                failed += 1
                continue
            checked += 1
            bars = [b for b in ch.bars if b.t.date() == now.date()]
            if bars:
                with_today += 1
            rng = self.ranges.get(sym) or strategy.opening_range(bars, self.n_range)
            if not rng:
                continue
            if now >= ist.bar_start(now.date(), self.n_range) + SETTLE:
                self.ranges[sym] = rng
            done = [ist.bar_index(b.t) for b in bars if b.t + FIVE <= now]
            if not done:
                continue
            i = strategy.first_trigger(bars, rng[0], self.n_range, upto=max(done))
            if i is None:
                continue
            dd["done"].append(sym)
            chase_cap = rng[0] + self.cfg["max_chase_range"] * (rng[0] - rng[1])
            if ch.price > chase_cap:
                self._skip(dd, sym, "too far above breakout", now)
            elif ch.price > rng[0]:
                out.append({"symbol": sym, "price": ch.price, "orh": rng[0], "orl": rng[1], "bar": i,
                            "at": self.fetched_at.get(sym, now)})
            else:
                self._skip(dd, sym, "back below range high", now)
        self.last_sweep = {"checked": checked, "with_bars_today": with_today, "failed": failed}
        if failed:
            events.append("no data for %d symbol(s) this sweep" % failed)
        if checked - with_today:
            events.append("%d symbol(s) have no bars today" % (checked - with_today))
        # closest to its breakout first: a stock that has already run has a far stop and few shares
        out.sort(key=lambda c: strategy.strength(c["price"], c["orh"]))
        return out

    def _skip(self, dd, sym, reason, now):
        dd["skipped"].append({"symbol": sym, "reason": reason, "time": _iso(now)})

    def _enter(self, c, now, dd, events):
        plan = strategy.make_plan(c["price"], c["orh"], c["orl"], self.cfg["target_r"], self.cfg["tick"])
        if not plan:
            return self._skip(dd, c["symbol"], "stop not below entry", c["at"])
        self._open(c, plan, 1, "orb", dd, events)

    def _open(self, c, plan, side, setup, dd, events):
        """Size, risk-check and record one paper trade. side=1 long, -1 short."""
        cfg, sym, entry = self.cfg, c["symbol"], c["price"]
        now = c["at"]   # when the price was seen: a big first sweep can take minutes
        verb = "broke out" if side == 1 else "faded"
        risk_ps = (entry - plan["stop"]) * side
        # one trade may not tie up all the money: the day's other slots need some too
        budget = min(ledger.cash(self.state), ledger.capital(self.state) * cfg["max_position_pct"] / 100.0)
        shares = money.size(entry, entry - risk_ps, budget, cfg["max_loss_per_trade"])
        if shares == 0:
            why = ("stop too far for max loss" if money.size(entry, entry - risk_ps, entry, cfg["max_loss_per_trade"]) == 0
                   else "not enough free cash")
            events.append("%s %s at %.2f: skipped (%s)" % (sym, verb, entry, why))
            return self._skip(dd, sym, why, now)
        risk = round(risk_ps * shares, 2)
        if risk < cfg["min_risk"]:
            # a cash-starved sliver of a trade would use up one of the day's slots for nothing
            why = "risk below %g" % cfg["min_risk"]
            events.append("%s %s at %.2f: skipped (%s: %d shares, %.2f at risk)" % (sym, verb, entry, why, shares, risk))
            return self._skip(dd, sym, why, now)
        exit_est = plan["target"] if plan["target"] is not None else entry
        est = money.charges(entry * shares, exit_est * shares, cfg["charges"])
        ok, why = money.risk_check(risk, est, ledger.day_stats(self.state, now.date().isoformat()), cfg, now)
        if not ok:
            events.append("%s %s at %.2f: skipped (%s)" % (sym, verb, entry, why))
            return self._skip(dd, sym, why, now)
        odds = None
        if side == 1:   # the Time Machine simulator is long-only
            try:
                odds = tm.odds(self.fetch_60d(sym), now.date(), entry, plan["stop"], plan["target"],
                               k=ist.bar_index(now), replays=cfg["tm_replays"], seed=cfg["tm_seed"])
            except FETCH_ERRORS:
                odds = None
        t = ledger.new_trade(now.date().isoformat(), sym, setup, _iso(now), entry, shares,
                             plan["stop"], plan["target"], odds, side=side)
        self.state["trades"].append(t)
        events.append("%s %s %d @ %.2f  stop %.2f  target %s  risk %.2f%s" % (
            "BUY" if side == 1 else "SHORT", sym, shares, entry, plan["stop"],
            "%.2f" % plan["target"] if plan["target"] is not None else "none (close-all)", risk,
            "  (TM target %.0f%% / stop %.0f%%)" % (odds["target"] * 100, odds["stop"] * 100) if odds else ""))

    # ------------------------------------------------------------- gap fade (short)

    def _fade_candidates(self, now, charts, dd, events):
        """Stocks that gapped up fade_gap_min..max % and now trade fade_below_open % under the open,
        biggest gap first. Only inside fade_entry_from..fade_entry_until."""
        cfg, day = self.cfg, now.date()
        if not (ist.at(day, cfg["fade_entry_from"]) <= now < ist.at(day, cfg["fade_entry_until"])):
            return []
        traded = {t["symbol"] for t in self.state["trades"] if t["date"] == day.isoformat()}
        syms = [s for s in self.symbols if s not in dd["done"] and s not in traded]
        meta = None
        if self.fetch_spark_meta and syms:
            try:
                meta = self.fetch_spark_meta(syms)
            except FETCH_ERRORS:
                meta = None
        if meta is not None:
            # the open is never above the day's high, so this misses no gap of fade_gap_min or more
            floor = 1 + cfg["fade_gap_min_pct"] / 100.0
            syms = [s for s in syms if s in meta and meta[s]["day_high"] >= meta[s]["prev_close"] * floor]
        out, failed, checked, with_today = [], 0, 0, 0
        for sym in syms:
            pc = meta[sym]["prev_close"] if meta is not None else (self.info.get(sym) or {}).get("close")
            if not pc:
                continue
            try:
                ch = self._chart(sym, charts, now)
            except FETCH_ERRORS:
                failed += 1
                continue
            checked += 1
            bars = [b for b in ch.bars if b.t.date() == day]
            if bars:
                with_today += 1
            first = [b for b in bars if ist.bar_index(b.t) == 0]
            if not first:
                continue
            plan = strategy.fade_plan(first[0].o, pc, ch.price, (self.info.get(sym) or {}).get("atr"), cfg)
            if isinstance(plan, str):
                if not plan.startswith("not "):     # a gap out of range or no ATR will not change today
                    dd["done"].append(sym)
                    if plan == "no ATR":
                        self._skip(dd, sym, plan, now)
                continue                            # not below the open yet: look again next sweep
            dd["done"].append(sym)
            out.append({"symbol": sym, "price": ch.price, "plan": plan, "at": self.fetched_at.get(sym, now)})
        self.last_sweep = {"checked": checked, "with_bars_today": with_today, "failed": failed}
        if failed:
            events.append("no data for %d symbol(s) this sweep" % failed)
        out.sort(key=lambda c: -c["plan"]["gap"])
        return out

    # ------------------------------------------------------------- the sweep

    def settle(self, now):
        """Only bring open trades up to date (after the close, or on a day off), then save."""
        events = []
        self._update_open(now, {}, events)
        self.save(now)
        for e in events:
            self.log(e)
        return events

    def sweep(self, now):
        events, charts = [], {}
        self.fetched_at = {}
        dd = ledger.day(self.state, now.date().isoformat())
        self._update_open(now, charts, events)
        if now < ist.at(now.date(), self.cfg["no_entries_after"]):
            if self.cfg.get("strategy", "orb") == "gap_fade":
                for c in self._fade_candidates(now, charts, dd, events):
                    self._open(c, c["plan"], -1, "gap_fade", dd, events)
            else:
                for c in self._candidates(now, charts, dd, events):
                    self._enter(c, now, dd, events)
        self.save(now)
        for e in events:
            self.log(e)
        return events

    def save(self, now):
        if self.state_path:
            ledger.save(self.state_path, self.state)
        if self.book_path and not workbook.write(self.state, self.book_path, today=now.date().isoformat()):
            self.log("warning: could not write %s (open in another program?); state is saved" % self.book_path)
