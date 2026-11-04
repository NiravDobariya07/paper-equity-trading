# Gap-fade short (NSE intraday) — research and rules

Date: 2026-11-04. Data: Yahoo 5-minute bars for the 861 stocks in the 2026-11-03 liquid list,
60 sessions (2026-08-10 to 2026-11-04), plus 6 months of daily bars for ATR and previous close.
Costs everywhere: Zerodha intraday charges (`money.charges`) plus 0.05% slippage per side.
Sizing everywhere: the live engine's rules (max loss ₹200, 33% position cap, 3 trades a day,
₹600 daily loss, ₹100 minimum risk, flat at 15:15).

## Why not the ORB

| Run (60 sessions) | Trades | Win | Net ₹ | PF |
|---|---|---|---|---|
| ORB, live rules | 180 | 42% | −1,894 | 0.86 |
| ORB, every signal (20,773), before costs | — | — | −0.003 R/trade | — |
| ORB, every signal, after costs | — | — | −0.30 R/trade | — |

The breakout has no edge before costs, and the midpoint stop is so tight that charges and
slippage take about a third of the risk on every trade. No filter (relative volume, trend,
gap, market direction, 30-minute range, later or earlier entries, targets 1R–3R, wide stop)
made the *signal-level* average positive after costs. Paper results since 2026-10-27 agree:
21 trades, −₹168.

## What the charts did show

Bucketing every stock-day by gap, move since the open, relative volume, market direction
and position in the range, then measuring the return to 15:15 after costs, the one region
that was positive in both halves of the sample was **big gap-ups that start to fail**:
they keep fading into the close. The mirror image on the long side (gap-downs that bounce)
was negative in all 96 variants tried.

## Rules (`"strategy": "gap_fade"`)

1. Gap: today's 09:15 open is 3%–12% above yesterday's close.
2. Failing: at 09:45–09:55 the price is at least 0.5% below today's open.
3. Short at the current price. Biggest gap first, at most 3 a day.
4. Stop: 1.0 × 14-day ATR above the entry. No target.
5. Exit: the stop, or close-all at 15:15.

Settings: `fade_gap_min_pct`, `fade_gap_max_pct`, `fade_below_open_pct`, `fade_stop_atr`,
`fade_entry_from`, `fade_entry_until`.

## Results

Research backtest (sessions split at 2026-09-22; first half in-sample, second out-of-sample):

| | Trades | Win | Net ₹ | PF | Max DD ₹ |
|---|---|---|---|---|---|
| All 60 sessions | 114 | 55% | +1,460 | 1.32 | 1,037 |
| First half | 54 | 57% | +580 | 1.25 | 955 |
| Second half | 60 | 53% | +879 | 1.40 | 457 |

Real engine (`india/engine.py`) replayed over the last 45 sessions: 88 trades, 59% win,
+₹1,897 after charges, before slippage (about +₹1,500 after it).

Robustness (whole 60 sessions, net ₹): stop 0.75 ATR +1,969, 1.25 ATR +1,356, 1.5 ATR +976;
gap ≥2.5% +1,292, ≥3.5% +1,390, ≥4% +1,482; entry 09:40 +1,029, 09:50 +1,458;
slippage 0.10%/side +956, 0.15%/side +466.

## Limits — read before trusting it

- **Thin edge**: about +0.07 R per trade. Without its best 5 trades the 60-day result is +₹171.
- **Timing-sensitive**: entering at 10:00 instead of 09:45–09:50 made it break even.
- **One regime**: 60 sessions, one market. It needs weeks of paper results before any money.
- **Shorting**: intraday (MIS) shorts only, squared off the same day. A stock in the F&O ban
  list, or one the broker blocks for MIS, cannot be shorted; the paper trader does not check this.
- **Expected size**: roughly ₹25–35 a day on ₹20,000 if the backtest holds. That is a reason
  to test, not to scale up.
