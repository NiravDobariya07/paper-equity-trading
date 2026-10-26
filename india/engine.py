"""One sweep of the paper trader: update open trades, find breakouts, enter, save.

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
                 state_path=None, book_path=None, fetch_spark=None, spark_over=300, clock=None):
        self.cfg, self.state, self.symbols = cfg, state, list(symbols)
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
            hit = strategy.exit_on_bar(b, t["stop"], t["target"])
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
            if ch.price > rng[0]:
                out.append({"symbol": sym, "price": ch.price, "orh": rng[0], "orl": rng[1], "bar": i,
                            "at": self.fetched_at.get(sym, now)})
            else:
                self._skip(dd, sym, "back below range high", now)
        self.last_sweep = {"checked": checked, "with_bars_today": with_today, "failed": failed}
        if failed:
            events.append("no data for %d symbol(s) this sweep" % failed)
        if checked - with_today:
            events.append("%d symbol(s) have no bars today" % (checked - with_today))
        out.sort(key=lambda c: -strategy.strength(c["price"], c["orh"]))
        return out

    def _skip(self, dd, sym, reason, now):
        dd["skipped"].append({"symbol": sym, "reason": reason, "time": _iso(now)})

    def _enter(self, c, now, dd, events):
        cfg, sym, entry = self.cfg, c["symbol"], c["price"]
        now = c["at"]   # when the price was seen: a big first sweep can take minutes
        plan = strategy.make_plan(entry, c["orh"], c["orl"], cfg["target_r"], cfg["tick"])
        if not plan:
            return self._skip(dd, sym, "stop not below entry", now)
        shares = money.size(entry, plan["stop"], ledger.cash(self.state), cfg["max_loss_per_trade"])
        if shares == 0:
            why = ("stop too far for max loss" if money.size(entry, plan["stop"], entry, cfg["max_loss_per_trade"]) == 0
                   else "not enough free cash")
            events.append("%s broke out at %.2f: skipped (%s)" % (sym, entry, why))
            return self._skip(dd, sym, why, now)
        risk = round(plan["risk"] * shares, 2)
        est = money.charges(entry * shares, plan["target"] * shares, cfg["charges"])
        ok, why = money.risk_check(risk, est, ledger.day_stats(self.state, now.date().isoformat()), cfg, now)
        if not ok:
            events.append("%s broke out at %.2f: skipped (%s)" % (sym, entry, why))
            return self._skip(dd, sym, why, now)
        try:
            odds = tm.odds(self.fetch_60d(sym), now.date(), entry, plan["stop"], plan["target"],
                           k=ist.bar_index(now), replays=cfg["tm_replays"], seed=cfg["tm_seed"])
        except FETCH_ERRORS:
            odds = None
        t = ledger.new_trade(now.date().isoformat(), sym, "orb", _iso(now), entry, shares,
                             plan["stop"], plan["target"], odds)
        self.state["trades"].append(t)
        events.append("BUY %s %d @ %.2f  stop %.2f  target %.2f  risk %.2f%s" % (
            sym, shares, entry, plan["stop"], plan["target"], risk,
            "  (TM target %.0f%% / stop %.0f%%)" % (odds["target"] * 100, odds["stop"] * 100) if odds else ""))

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
