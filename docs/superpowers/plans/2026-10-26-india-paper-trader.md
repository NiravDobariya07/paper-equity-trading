# India Paper Trader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An unattended NSE opening-range-breakout paper trader, `python3 india/paper_trader.py run`, that enters and exits paper trades on its own and records them in `trader/india/paper_trades.xlsx`.

**Architecture:** Small stdlib modules under `india/` (clock, Yahoo client, universe filter, strategy, money, ledger, workbook, Time Machine wrapper, engine, CLI). The engine's `sweep(now)` is a pure step over fetched bars so a whole day can be replayed offline in tests; the CLI wraps it in a sleep loop. JSON state is the record; the workbook is regenerated from it.

**Tech Stack:** Python 3.10 (code stays 3.8-compatible), stdlib + `openpyxl` 3.1.5 (installed), `unittest`. Reuses `day-trader-skill/trader-timemachine/timemachine.py` (`build_pool`, `simulate`) read-only.

**Spec:** `docs/superpowers/specs/2026-10-26-india-paper-trader-design.md`

## Global Constraints

- Workspace root `ROOT = /var/www/html/python/Day-trader-skill`; every path is resolved from `india/`'s parent (`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`), never from the cwd.
- Data dir `ROOT/trader/india/`: `config.json`, `EQUITY_L.csv`, `liquid-YYYY-MM-DD.json`, `state.json`, `paper_trades.xlsx`.
- Never modify anything under `ROOT/day-trader-skill/`.
- Never disable TLS verification.
- Defaults: capital 20000, max_loss_per_trade 200, max_daily_loss 600, max_trades_per_day 3, min_price 50, min_turnover 100000000 (₹10 crore), turnover_days 20, or_minutes 15, no_entries_after "14:30", close_all "15:15", target_r 2, tick 0.05, tm_replays 10000, tm_seed 19.
- Charges defaults: brokerage_pct 0.1, brokerage_cap 20 (per side), stt_sell_pct 0.025, txn_pct 0.00297, sebi_per_crore 10, stamp_buy_pct 0.003, gst_pct 18.
- IST = UTC+05:30 fixed. Session 09:15 to 15:30, 75 bars of 5 minutes; bar index 0 starts 09:15.
- Yahoo: `https://query1.finance.yahoo.com` / `query2`, header `User-Agent: Mozilla/5.0`, pause 0.3 s between requests, one retry after HTTP 429 after 5 s, symbol `<SYM>.NS` (URL-quote `&` as in `M&M`).
- Money rounded to 2 decimals for display; prices to the ₹0.05 tick (stop down, target up).

## Review Focus

1. **Started late, on a weekend/holiday, or after 15:30:** must print why and exit 0, never sit in a loop. Test in Task 9.
2. **Workbook open in a spreadsheet app / unwritable:** run continues, prints a warning, state.json still saved. Test in Task 6.
3. **Symbol 404s or empty data mid-run (renames like ZOMATO, LTIM):** skipped for that sweep with a count, never crashes the sweep. Test in Task 2 and Task 8.
4. **Program down for several bars (crash, laptop sleep, slow sweep):** open trades replayed through every missed bar; no symbol entered twice. Test in Task 8.
5. **Stop/target arithmetic edge:** stop at or above entry (entry barely above range mid), zero shares from the cash cap, both stop and target in one bar, gap through the stop. Tests in Tasks 4 and 5.

---

### Task 0: Workspace git, package skeleton, config

**Files:**
- Create: `ROOT/.gitignore`, `india/__init__.py`, `india/config.py`, `india/config.example.json`, `india/tests/__init__.py`, `india/tests/test_config.py`

**Interfaces:**
- Produces: `config.ROOT: str`, `config.DATA: str` (`ROOT/trader/india`), `config.load(path=None) -> dict` (creates `DATA/config.json` from the example if missing; merges defaults for missing keys; returns dict with all Global Constraints keys plus `charges: dict`, `holidays: list[str]`, `equity_list: str`).

- [ ] **Step 1:** `git init -b main` in ROOT; `.gitignore` = `day-trader-skill/`, `trader/`, `.claude/`, `__pycache__/`.
- [ ] **Step 2: Failing test** `test_load_creates_config_with_defaults`: point `config.DATA` at a temp dir, `load()` → file created, `cfg["capital"] == 20000`, `cfg["min_turnover"] == 100000000`, `cfg["charges"]["brokerage_cap"] == 20`; `test_load_fills_missing_keys`: a file with only `{"capital": 50000}` → `cfg["capital"] == 50000` and `cfg["max_trades_per_day"] == 3`.
- [ ] **Step 3:** Run `python3 -m unittest discover -s india/tests -t .` → FAIL (no module).
- [ ] **Step 4:** Implement `config.py` and `config.example.json` with exactly the defaults above.
- [ ] **Step 5:** Run → PASS. Commit `chore: workspace repo and india config`.

### Task 1: IST clock

**Files:** Create `india/ist.py`, `india/tests/test_ist.py`

**Interfaces:**
- Produces: `IST: tzinfo`; `from_ts(ts: int) -> datetime` (aware IST); `at(day: date, hhmm: str) -> datetime`; `bar_index(dt) -> int` (floor((dt - 09:15)/5 min)); `bar_start(day, i) -> datetime`; `is_trading_day(day, holidays: list[str]) -> bool`; `SESSION_BARS = 75`.

- [ ] **Step 1: Failing tests:** `from_ts(1792986300)` == 2026-10-26 09:15 IST (the `start` Yahoo returned today); `bar_index(at(d,"10:07")) == 10`; `bar_start(d, 71)` == 15:10; `is_trading_day(date(2026,10,25), []) is False` (Sunday); `is_trading_day(date(2026,10,26), ["2026-10-26"]) is False`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS. Commit `feat(india): IST clock`.

### Task 2: Yahoo client + real fixtures

**Files:** Create `india/yahoo.py`, `india/tests/test_yahoo.py`, `india/tests/fixtures/` (`RELIANCE-1d-5m.json`, `RELIANCE-60d-5m.json.gz`, `RELIANCE-1mo-1d.json`, `spark-1d-5m.json`, `EQUITY_L-excerpt.csv`)

**Interfaces:**
- Produces: `Bar = namedtuple("Bar", "t o h l c v")` (`t` = bar start, IST aware); `Chart = namedtuple("Chart", "bars price price_time")`; `parse_chart(doc: dict) -> Chart` (drops rows with any None); `class NoData(Exception)`; `fetch_chart(sym: str, rng: str, interval: str, get=None) -> Chart` (raises `NoData` on 404 or empty result); `fetch_spark(syms: list[str], rng, interval, get=None) -> dict[str, list[tuple[datetime, float]]]` (batches of 20); `get` is an injectable `callable(url) -> dict` for tests.

- [ ] **Step 1: Capture fixtures** (network): save Yahoo JSON for `RELIANCE.NS` `range=1d&interval=5m`, `range=60d&interval=5m` (gzip), `range=1mo&interval=1d`, and spark for `RELIANCE.NS,TCS.NS`; hand-write `EQUITY_L-excerpt.csv` with the real header `SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE` (note leading spaces) and rows for RELIANCE (EQ), TCS (EQ), M&M (EQ), a BE row and a BZ row.
- [ ] **Step 2: Failing tests:** `parse_chart` on the 1d fixture → bars all on one IST date, first bar `t` 09:15, all fields floats, `price > 0`; `fetch_chart` with a `get` raising a 404 `HTTPError` → `NoData`; a `get` that raises 429 once then returns the fixture → returns bars and called twice (patch `yahoo.SLEEP` to a no-op); `fetch_spark` with 45 symbols → `get` called 3 times; URL for `M&M` contains `M%26M.NS`.
- [ ] **Step 3:** Run → FAIL. **Step 4:** Implement (urllib, timeout 20, alternate query1/query2). **Step 5:** Run → PASS. Commit `feat(india): Yahoo client with fixtures`.

### Task 3: Universe and `prepare`

**Files:** Create `india/universe.py`, `india/tests/test_universe.py`

**Interfaces:**
- Consumes: `yahoo.fetch_chart`, `yahoo.NoData`, `config.DATA`.
- Produces: `read_equity_list(path) -> list[str]` (EQ only, header whitespace stripped; raises `FileNotFoundError` with a message giving the NSE URL and the target path); `liquid(daily: dict[str, list[Bar]], before: date, min_price, min_turnover, days) -> list[dict]` with keys `symbol, close, avg_turnover`, sorted by `avg_turnover` desc, using only bars dated before `before` and requiring at least `days` such bars; `prepare(cfg, day, fetch=yahoo.fetch_chart, log=print) -> str` (writes `DATA/liquid-<day>.json` = `{"day", "symbols": [...], "dropped_nodata": n}`, returns its path).

- [ ] **Step 1: Failing tests:** `read_equity_list(excerpt)` == `["RELIANCE", "TCS", "M&M"]`; missing file message contains `nsearchives.nseindia.com` and `EQUITY_L.csv`; `liquid` with synthetic bars: a ₹40 stock excluded, a ₹100 stock with 20 × ₹9 crore excluded, a ₹100 stock with 20 × ₹11 crore included, a stock with 19 bars excluded, bars dated `before` ignored; `prepare` with a fake fetch where one symbol raises `NoData` → file written, `dropped_nodata == 1`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement (fetch `1mo`/`1d` per symbol). **Step 4:** PASS. Commit `feat(india): liquid universe`.

### Task 4: Strategy (range, trigger, plan, exits)

**Files:** Create `india/strategy.py`, `india/tests/test_strategy.py`

**Interfaces:**
- Produces: `opening_range(bars: list[Bar], n: int = 3) -> tuple[float, float] | None` (high, low of bars 0..n-1; None unless all n present); `first_trigger(bars, orh, n=3, upto: int) -> int | None` (index of first bar i in [n, upto] with `c > orh`; `upto` = last completed bar index); `make_plan(entry, orh, orl, target_r, tick) -> dict | None` (`stop` = floor-to-tick of (orh+orl)/2, `target` = ceil-to-tick of entry + target_r*(entry-stop), `risk` = entry-stop; None if stop >= entry); `exit_on_bar(bar, stop, target) -> tuple[float, str] | None` (order: `o <= stop` → (o,"stop"); `o >= target` → (o,"target"); `l <= stop` → (stop,"stop"); `h >= target` → (target,"target")); `strength(price, orh) -> float` = (price-orh)/orh.

- [ ] **Step 1: Failing tests:** range of bars (h 102,103,101; l 99,98,100) == (103, 98); 2 bars → None; trigger: closes [.., 102.9, 103.5] after range → index of 103.5; close equal to orh not a trigger; `upto` before the breakout → None; plan(entry 104, orh 103, orl 98) → stop 100.5, target 111.0, risk 3.5; plan(entry 100.52, orh 103, orl 98) → stop 100.5, target 100.6 (100.56 ceiled), risk 0.02; plan(entry 100.5, ...) → None; exits: bar touching both (l 100, h 112) → (100.5,"stop"); gap open 99 → (99,"stop"); gap open 113 → (113,"target"); neither → None.
- [ ] **Step 2:** FAIL. **Step 3:** Implement (use `Decimal` or integer paise for tick rounding to avoid float drift). **Step 4:** PASS. Commit `feat(india): ORB strategy rules`.

### Task 5: Money (charges, size, risk check)

**Files:** Create `india/money.py`, `india/tests/test_money.py`

**Interfaces:**
- Produces: `charges(buy_value, sell_value, rates: dict) -> float` (formula in the spec, rounded to 2); `size(entry, stop, cash, max_loss) -> int` (`min(floor(max_loss/(entry-stop)), floor(cash/entry))`, 0 if risk <= 0); `risk_check(risk_rupees, est_charges, day: dict, cfg, now) -> tuple[bool, str]` where `day = {"realized_net": float, "open_risk": float, "trades": int}`; fails with reasons `"trade limit"`, `"daily loss"`, `"after 14:30"`, `"zero shares"`; daily rule: `-min(realized_net,0) + open_risk + risk_rupees + est_charges <= max_daily_loss`.

- [ ] **Step 1: Failing tests:** `charges(20000, 20200, defaults)`: brokerage 20+20=40, STT 5.05, txn 1.19, SEBI 0.04, stamp 0.60, GST 18% of 41.23 = 7.42 → total 54.30 (assert `abs(x-54.30) < 0.02`); `charges(5000, 5000)` uses 0.1% brokerage (5 per side); `size(500,495,20000,200) == 40`; `size(500,499,20000,200) == 40` (cash cap); `size(500,500,...) == 0`; `size(30000, 29900, 20000, 200) == 0`; risk: 3 trades done → (False,"trade limit"); realized -230, open 200, new 200 + charges 50 → (False,"daily loss"); realized +100, open 0, new 200+50 → True; now 14:31 → (False,"after 14:30").
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit `feat(india): charges, sizing, risk check`.

### Task 6: Ledger and workbook

**Files:** Create `india/ledger.py`, `india/workbook.py`, `india/tests/test_ledger.py`, `india/tests/test_workbook.py`

**Interfaces:**
- Produces (ledger): state dict `{"capital_start": float, "trades": [trade], "days": {"YYYY-MM-DD": {"done": [sym], "skipped": [{"symbol","reason","time"}]}}}`; trade dict keys: `id, date, symbol, setup, entry_time, entry, shares, value, stop, target, risk, tm_target, tm_stop, checked_through, exit_time, exit, reason, gross, charges, net`; `load(path, capital) -> dict`; `save(path, state)` (write temp file in same dir, `os.replace`); `open_trades(state) -> list`; `cash(state) -> float` (capital_start + sum closed net - value of open trades); `day_stats(state, day) -> dict(realized_net, open_risk, trades)`; `close(trade, price, time, reason, rates)` fills exit fields.
- Produces (workbook): `write(state, path) -> bool` (False + no exception when the file cannot be written).

- [ ] **Step 1: Failing tests:** load of a missing file → empty state with capital; save then load round-trips; `save` leaves no temp file; cash after one open ₹10,000 trade == 10,000; `close` at target computes gross = (exit-entry)*shares and net = gross - charges; `day_stats` counts only that date; workbook: write → openpyxl reopens it, sheet names `["Trades","Summary"]`, Trades header row matches the spec's column list, one data row per trade, Summary "Current capital" cell == cash; `write` to a path inside a read-only directory returns False.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit `feat(india): ledger and Excel workbook`.

### Task 7: Time Machine wrapper

**Files:** Create `india/tm.py`, `india/tests/test_tm.py`

**Interfaces:**
- Consumes: upstream `timemachine.build_pool(sessions)` and `simulate(pool, entry, stop, target, k, last, replays, seed)`; sessions are `(day, [ {"o","h","l","c"} ] * 75)`.
- Produces: `odds(bars60: list[Bar], day: date, entry, stop, target, k: int, last: int = 71, replays=10000, seed=19) -> dict | None` returning `{"target": p_target, "stop": p_stop, "neither": p_neither}`; None if fewer than 20 complete 75-bar sessions before `day`.

- [ ] **Step 1: Failing tests:** on `RELIANCE-60d-5m.json.gz`, odds for a plan taken from the fixture's last day → keys present, values in [0,1], sum within 0.001 of 1; same call twice → identical (seed); with only 10 sessions → None.
- [ ] **Step 2:** FAIL. **Step 3:** Load upstream module via `importlib` (as `day-trader-skill/tests/_load.py` does), set its `SESSION_BARS = 75` on the loaded module object (never edit the file), group bars by IST date, keep 75-bar sessions only. **Step 4:** PASS. Commit `feat(india): Time Machine odds`.

### Task 8: Engine

**Files:** Create `india/engine.py`, `india/tests/test_engine.py`

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: `class Engine(cfg, state, symbols: list[str], fetch_today, fetch_60d, log=print)` where `fetch_today(sym) -> Chart` and `fetch_60d(sym) -> list[Bar]`; `sweep(now) -> list[str]` (events) does, in order: (1) for each open trade, fetch its chart and apply `exit_on_bar` to completed bars with `t >= floor5(entry_time)` and `t > checked_through`, closing on the first hit; advance `checked_through`; (2) at/after `close_all`, close every open trade at `Chart.price`, reason `"close-all"`; (3) before `no_entries_after`, for each symbol not in today's `done`: fetch, compute range and `first_trigger` up to the last completed bar; if triggered, add to `done`; if `Chart.price > orh`, it is a candidate with `entry = Chart.price`; (4) sort candidates by `strength` desc; for each: `make_plan`, `size` with `cash(state)`, `charges` estimate at entry and target-side value, `risk_check`; skipped ones go to `skipped` with reason; taken ones get `tm.odds` and are appended; (5) `ledger.save` and `workbook.write` once per sweep. A fetch raising `NoData` or `URLError` is counted and skipped.
- Completed bar: `bar.t + 5 min <= now`.

- [ ] **Step 1: Failing tests** with synthetic charts (helper builds a 75-bar day from a list of closes):
  - breakout at 10:00 bar, sweep at 10:05:05 → one trade, entry = chart price, stop = range mid floored, shares from `size`;
  - same symbol on the next sweep → no second trade;
  - four symbols breaking out together → 3 trades, the weakest skipped with `"trade limit"`;
  - catch-up: breakout at 09:45, price back below orh at first sweep 10:05 → no trade, symbol in `done`;
  - restart: state with an open trade whose stop was touched by the 10:20 bar, `checked_through` = 10:05, sweep at 11:00 → trade closed at stop with `exit_time` = 10:20 bar;
  - sweep at 15:16 → all open closed `"close-all"`;
  - a symbol whose fetch raises `NoData` → other symbols still processed, event mentions the skip;
  - sweep at 14:35 with a fresh breakout → no trade.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit `feat(india): sweep engine`.

### Task 9: CLI and live loop

**Files:** Create `india/paper_trader.py`, `india/tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv=None, now=None, sleep=time.sleep) -> int`; commands `prepare` (runs `universe.prepare` for today), `run` (prepares if today's liquid file is missing, then loops: sweep at each bar close + 5 s until 15:30 + 1 min, then final sweep, prints day summary, exit 0), `report` (rewrites the workbook from state and prints the summary).
- If the list has more than 300 symbols, the sweep pre-filters with `fetch_spark` (last close >= 0.99 × that symbol's range high from the first sweep's charts) before full fetches; otherwise fetch all.

- [ ] **Step 1: Failing tests:** `main(["run"], now=Sunday 10:00)` → 0, prints "not a trading day"; `now=` a configured holiday → same; `now=` 15:45 on a trading day → 0, prints "market closed"; `now=` 08:00 → sleeps until 09:30 (assert the injected `sleep` got the right seconds, then stop by raising from the fake sleep); missing `EQUITY_L.csv` → exit 2 with the download message.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS; full suite `python3 -m unittest discover -s india/tests -t .` → OK. Commit `feat(india): paper_trader CLI`.
- [ ] **Step 5: Live smoke (needs EQUITY_L.csv and market hours):** `python3 india/paper_trader.py prepare` → prints liquid count (expect a few hundred) and dropped count; `python3 india/paper_trader.py run` during market hours → one sweep line per 5 minutes; `trader/india/paper_trades.xlsx` opens in LibreOffice/Excel.
