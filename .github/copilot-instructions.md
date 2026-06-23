# Copilot Instructions

## Build & Run

```bash
# Install dependencies
python3 -m pip install --user --break-system-packages -r requirements.txt

# Download price data (cached to data/ as CSV)
python3 main.py download

# Refresh cached data to the latest bar (incremental daily update)
python3 main.py update

# Run backtest with custom dates (end defaults to today)
python3 main.py backtest 2020-01-01 2026-05-01

# Run forward test (paper trading simulation, persists state in forward_state.json)
python3 main.py forward

# Launch Dash dashboard at http://127.0.0.1:8050
python3 main.py dashboard
```

There are no automated tests or linters configured.

## Architecture

This is a regime-based leveraged ETF trading strategy (TQQQ/SQQQ) with a pipeline architecture:

```
download_data.py → indicators.py → strategy.py → backtest.py
                                                → forward_test.py
                                                → dashboard.py (Dash/Plotly)
```

**Data flow:**
1. `download_data.py` fetches OHLC data from yfinance in 6-month chunks, caches as CSV, and auto-detects stock splits by comparing cache vs fresh data.
2. `indicators.py` classifies each day into a regime (bull/neutral/bear/crisis) using QQQ's trend and volatility, then computes target allocations with RSI-based tactical adjustments.
3. `strategy.py` executes portfolio rebalancing with a trailing stop, cooldown logic, and whole-share rounding.
4. `backtest.py` orchestrates the pipeline and computes metrics (Sharpe, drawdown, return) vs a buy-and-hold benchmark.

**Key separation:** Regime detection uses QQQ (non-leveraged, cleaner signal), but trades execute on TQQQ/SQQQ (3× leveraged).

**Signal timing:** Signals are computed from day N's close. For backtests they are shifted
forward 1 day (execution at day N+1's open) to eliminate look-ahead bias. The live bot runs
near the close and calls `generate_signals(..., shift_signals=False)`, acting on the latest
bar's own close.

## Conventions

- All tunable parameters live in `config.py` — never hardcode thresholds in strategy/indicator code.
- Allocations are floats 0.0–1.0 where `tqqq_alloc + sqqq_alloc + cash_alloc = 1.0`.
- The strategy uses whole shares only (no fractional); leftover goes to cash.
- Rebalancing only triggers when allocation drift exceeds `REBALANCE_THRESHOLD` (10%).
- The trailing stop (25% drawdown from peak) overrides all allocation signals and forces 100% cash with a 5-day cooldown before re-entry.
- Forward test and paper trading bot persist state as JSON files — never lose state between runs.
- `paper_trade/alpaca_bot.py` is a standalone cron-scheduled bot deployed on Oracle Cloud; it reuses the **same** `indicators.generate_signals` as the backtest (honoring `config.REGIME_METHOD`), so live and backtest decisions match.
- Paper vs live is set by env vars (`ALPACA_PAPER`, default paper; or `ALPACA_BASE_URL`); `--dry-run` previews without trading.
- The bot writes one log file per month: `paper_trade/logs/trade_YYYYMM.log`.
- `mom_vol` short-horizon overlays (`indicators.apply_short_horizon_overlays`: vol-targeting, exposure cap, re-entry cooldown) are causal (applied pre-shift) and **default OFF** — backtests show they trim long-run return without improving Sharpe.
- The live bot arms a broker-side intraday trailing stop on TQQQ after buys (`INTRADAY_STOP_*`); it's re-armed each run and is the only mechanism that reacts to single-day crashes (not backtestable in the daily engine).
- On the server the dashboard runs as `trading-dashboard.service` (systemd); restart with `sudo systemctl restart trading-dashboard`, never `kill`/`pkill`.
- Cron must set `SHELL=/bin/bash` (the bot/data-update lines use the bash builtin `source`).
