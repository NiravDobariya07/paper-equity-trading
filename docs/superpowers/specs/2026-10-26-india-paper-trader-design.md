# India Paper Trader: Design

Date: 2026-10-26. Status: draft for approval.

## Intent

Nirav trades Indian stocks (broker: Angel One). He wants the Day Trader pack's method (opening-range breakout, stop and target set before entry, sizing to a max loss, risk limits, Time Machine odds) running on the NSE, as **unattended paper trading recorded in an Excel workbook**, so he can see how the rule performs before risking money. He starts one command around 10:00 IST and it runs until after the close. Nothing is connected to a broker.

Success: on any trading day, he starts the command at about 10:00, leaves it alone, and after 15:30 the workbook holds every paper trade the rule took that day (entry, exit, reason, charges, net P&L) and an updated summary, with numbers that a real trader could plausibly have gotten.

## Goals

- Scan the **whole NSE equity market**, filtered to liquid stocks, every 5 minutes.
- Enter paper trades **automatically, without asking**, when a long opening-range breakout fires and the risk check passes.
- Exit paper trades automatically at stop, target, or the end-of-day cutoff.
- Record everything in an `.xlsx` workbook with a Trades sheet and a Summary sheet.
- Subtract estimated Angel One intraday charges from every trade.

## Non-goals (this version)

- Any broker connection or real order (Angel One SmartAPI comes later, and will bring back a human "yes" for real orders).
- Short selling. Long only.
- BSE. NSE only.
- Setups other than the opening-range breakout as entry triggers.
- Skipping trades on Time Machine odds (odds are recorded, not used to filter).
- News, earnings and Congress tools for India.

## Anti-goals (would be a failure even if it runs)

- **Flattering fills.** Paper results that a real trader could not have gotten: fills at prices before the program was running, fills in stocks too thin to absorb the order, a stop and target both touched in one bar counted as a win, trades in stocks that cannot be traded intraday.
- **Losing or duplicating trades** on a crash or restart.
- **Silently doing nothing** (blocked network, holiday, bad symbol list) without saying why.
- **Modifying the upstream repo** in `day-trader-skill/`.

## Decided (by Nirav)

| Item | Value |
|---|---|
| Market | NSE equities, long only |
| Universe filter | price >= ₹50 and 20-day average daily traded value (close x volume) >= ₹10 crore |
| Paper capital | ₹20,000 |
| Max loss per trade | ₹200 |
| Max loss per day | ₹600 (realized net loss + open risk at stops counts toward it) |
| Max trades per day | 3 |
| Margin / leverage | none: position value <= available paper cash |
| Entry | automatic, no confirmation |
| Charges | estimated Angel One intraday charges subtracted per trade |
| Time Machine | runs on each entry; odds recorded in the sheet; does not filter |
| Start / stop | started by hand around 10:00 IST, exits on its own after 15:30 |
| Output | `.xlsx` workbook (openpyxl, already installed) |

## Decided (Claude's proposals, accepted with "yes" to the defaults)

- **Opening range:** 09:15 to 09:30 IST (three 5-minute bars).
- **Trigger:** first completed 5-minute bar whose close is above the opening-range high.
- **Stop:** opening-range midpoint. **Target:** 2R (2 x (entry - stop)).
- **Sizing:** shares = floor(₹200 / (entry - stop)), then capped so shares x entry <= available cash; 0 shares means skip.
- **No new entries after 14:30.** **Close all open trades at 15:15** at the then-current price.
- **Several triggers in one sweep:** take them in order of breakout strength (close above range high, as % of the range high) until a limit stops it.
- **Catch-up at start:** a stock that triggered between 09:30 and the program's start is entered at the current price at start (never the earlier trigger price), only if it is still above its range high and the trade still passes sizing and risk.
- **Code location:** new `india/` folder in the workspace `/var/www/html/python/Day-trader-skill/`. It may import pure, market-agnostic functions from `day-trader-skill/` (Yahoo parsers, the Time Machine simulator), never edit them.
- **Run command:** `python3 india/paper_trader.py run` (plus a once-a-day `prepare` step, below).

## Settled after the builder check (Claude's calls)

- **Entry price for a normal trigger:** the live price when the sweep runs (Yahoo's latest price, a few seconds after the trigger bar closes), not the trigger bar's close: that is the price an order sent at that moment could get. No extra slippage allowance. The Time Machine starts from the bar after the trigger bar, using that entry price.
- **One shot per stock per day:** a stock's trigger is the first completed bar closing above its range high. Once it has triggered (whether entered, skipped by a limit, or seen as already past at start), that stock is done for the day. A skipped trigger is never taken on a later bar.
- **Restart:** on start, open trades are replayed bar by bar through every 5-minute bar missed while the program was down, using the same stop / target / both-touched rules, so a stop hit at 11:20 is recorded at 11:20. Stocks that triggered during the downtime follow the catch-up rule (current price, only if still above the range high).
- **Daily loss room:** a new trade is taken only if (today's realized net loss + risk at stop of open trades + this trade's risk + this trade's estimated round-trip charges) <= ₹600. If it does not fit, the trade is skipped, never shrunk (same as the upstream risk check, which refuses rather than resizes).

## Constraints found during recon (2026-10-26)

- **Office network (Sophos HTTPS inspection):** Yahoo works (`query1.finance.yahoo.com` chart and spark endpoints return NSE data). NSE's own site (`archives.nseindia.com`, `nsearchives.nseindia.com`) fails certificate verification. The symbol list therefore cannot be downloaded from this network; the tool must accept a local copy of NSE's `EQUITY_L.csv` saved by hand from a browser, and say so clearly when it is missing. Never disable certificate verification.
- **Yahoo NSE data:** symbols are `<NSE symbol>.NS`; timestamps are live to the second (checked 14:57:01 IST against 14:57:02 local); session 09:15 to 15:30 IST is 75 five-minute bars. Spark (20 symbols per request) returns closes only, no highs and no volume history; the v8 chart endpoint returns OHLCV. Some NSE symbols have no Yahoo data (e.g. `TATAMOTORS.NS` after the demerger): drop them and report the count.
- **Throughput (measured 2026-10-26, 15:00 IST, office network):** 300 sequential v8 chart requests with a 0.3 s pause took 173 s (about 0.58 s each), no HTTP 429. 6 returned 404 (renamed symbols such as `ZOMATO`, `LTIM`). So one request per symbol per sweep covers about 300 symbols in under 3 minutes; if the liquid list is larger than that, the sweep must pre-filter with spark (closes near or above the range high) before fetching full charts, so a sweep always finishes inside the 5-minute bar. Keep the upstream polite pause and the single retry after a 429.
- **Intraday eligibility:** only `EQ` series in `EQUITY_L.csv`. `BE`/`BZ` (trade-to-trade) cannot be traded intraday and must be excluded.
- **IST** is UTC+05:30 with no daylight saving.
- **Trading days:** NSE holidays are not in the pack. Keep a holiday list in the config, and also treat "no 09:15 bar from Yahoo by 09:35" as a closed day: say so and exit.
- **Fixed-tick prices:** round stop down and target up to ₹0.05.

## Charges model (estimates; editable in config)

Per round trip, on buy value B and sell value S:

- Brokerage: min(₹20, 0.1% of order value), each side (Angel One intraday; verify against the current rate card)
- STT: 0.025% of S
- NSE transaction charge: 0.00297% of (B + S)
- SEBI fee: ₹10 per crore of (B + S)
- Stamp duty: 0.003% of B
- GST: 18% of (brokerage + transaction charge + SEBI fee)

All rates live in the config file so Nirav can correct them. The sizing uses the ₹200 risk before charges; the sheet shows gross P&L, charges and net P&L separately, and the daily loss limit uses net.

## The day

1. **`prepare`** (once per trading day, any time before the run, also fine the evening before): read `EQUITY_L.csv`, keep `EQ` series, fetch 1 month of daily bars per symbol (v8 chart), keep symbols with last close >= ₹50 and 20-day average close x volume >= ₹10 crore. Write the day's liquid list. Expect a few hundred symbols; about 2,000 requests at a polite pace, so it takes several minutes.
2. **`run`** (started around 10:00):
   - If there is no liquid list for today, run `prepare` first.
   - Every 5 minutes, a couple of seconds after each bar closes: fetch today's 5-minute bars for the liquid list, find new triggers, update open trades, write the workbook.
   - On a trigger: plan (stop, target), size, risk check, Time Machine (60 days of that stock's 5-minute bars, 10,000 replays, fixed seed), then record the entry.
   - Open trades: a bar whose low <= stop exits at the stop (or at the bar's open if it opened below the stop); a bar whose high >= target exits at the target (or the open if it opened above); a bar that touches both counts as the stop. At 15:15 any open trade exits at the latest price.
   - After 15:30: final workbook write, day summary, exit.
3. **Restart safety:** the program's state (open and closed trades, today's counts) is saved after every change to a JSON file next to the workbook. On start it reloads today's state; it never re-enters a stock it already traded today.

## The workbook

`trader/india/paper_trades.xlsx` (workspace data folder, outside the repo).

- **Trades** (one row per trade, all days): date, symbol, setup, entry time, entry price, shares, position value, stop, target, risk ₹, TM target-first %, TM stop-first %, exit time, exit price, exit reason (stop / target / close-all), gross P&L, charges, net P&L, capital after.
- **Summary**: starting capital, current capital, total net P&L, number of trades, win rate, average win, average loss, today's net P&L, today's trade count, and a per-day table (date, trades, net P&L, capital at close).

The JSON state is the source of truth; the workbook is regenerated from it on every write (written to a temp file, then renamed), so a workbook open in a spreadsheet app cannot corrupt the record. If the workbook cannot be written, the run continues and says so.

## Configuration

`trader/india/config.json`, created from an example on first run: capital, max loss per trade, max daily loss, max trades, price and turnover floors, opening-range minutes, entry cutoff, close-all time, target R, charge rates, holiday list, path to `EQUITY_L.csv`.

## Left to the builder

- Module layout inside `india/`, function names, and whether spark pre-filtering speeds up the 5-minute sweep (requirement: a sweep of a few hundred symbols finishes well inside 5 minutes).
- How the Time Machine simulator is reused (it hard-codes 78 bars and New York minutes; adapt by parameter or a thin IST-native wrapper, without editing upstream).
- Console output wording while running.
- Test fixtures: capture real Yahoo NSE responses (chart 60 days of 5-minute bars, daily bars, a spark answer) and a small `EQUITY_L.csv` excerpt for offline `unittest` tests, matching the upstream suite's style.
