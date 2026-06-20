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

**Signal timing:** Signals are computed from day N's close but shifted forward 1 day — execution happens at day N+1's open price. This eliminates look-ahead bias.

## Conventions

- All tunable parameters live in `config.py` — never hardcode thresholds in strategy/indicator code.
- Allocations are floats 0.0–1.0 where `tqqq_alloc + sqqq_alloc + cash_alloc = 1.0`.
- The strategy uses whole shares only (no fractional); leftover goes to cash.
- Rebalancing only triggers when allocation drift exceeds `REBALANCE_THRESHOLD` (10%).
- The trailing stop (25% drawdown from peak) overrides all allocation signals and forces 100% cash with a 5-day cooldown before re-entry.
- Forward test and paper trading bot persist state as JSON files — never lose state between runs.
- `paper_trade/alpaca_bot.py` is a standalone cron-scheduled bot deployed on Oracle Cloud; it reuses the same indicator/strategy logic via imports.
