# TQQQ/SQQQ Regime-Based Trading Strategy

## The 30-Second Pitch

We trade TQQQ and SQQQ using a regime-detection engine that reads the market's mood — bull, neutral, bear, or crisis — based on QQQ's trend, momentum, and volatility.

In bull markets, we scale up to 100% TQQQ to ride the wave. When momentum turns or volatility spikes, we scale down toward cash — not SQQQ, because inverse ETFs decay over time. (The default `mom_vol` engine is purely TQQQ↔cash; an optional `rules` engine can deploy a small SQQQ hedge during sharp crashes — see Strategy Overview.)

A portfolio-level trailing stop kicks us to cash if we drop 25% from peak, and we don't re-enter until the trend confirms bullish again.

**The results over 6+ years (Jan 2020 – Jun 2026):** ~917% return vs ~661% buy-and-hold, with a max drawdown of -42% compared to -82%. Better returns, with roughly half the pain. Sharpe ratio of 0.93 vs 0.74.

**The edge:** We capture almost all the upside in bull markets while sitting safely in cash during the worst drops — COVID crash, 2022 bear market, 2025 tariff selloff. It's not about timing the market perfectly — it's about not being in the market when it's falling off a cliff.

---

## Strategy Overview

> **Regime engines.** The default engine is **`mom_vol`** (`config.REGIME_METHOD`), which
> sizes a single TQQQ↔cash position from QQQ momentum (bull/bear gate) and volatility
> scaling — it never holds SQQQ. The tables below describe the alternative **`rules`**
> engine (bull/neutral/bear/crisis with SQQQ hedging). Both share the same trailing-stop
> and execution logic; the headline results above use `mom_vol`.

### Regime Detection (via QQQ) — `rules` engine

| Regime | Condition | TQQQ Allocation | SQQQ Allocation | Cash |
|--------|-----------|-----------------|-----------------|------|
| **Bull** | QQQ > 50-EMA or (QQQ > 20-EMA + positive momentum) | 100% | 0% | 0% |
| **Neutral** | Mixed signals | 70% | 0% | 30% |
| **Bear** | QQQ < both EMAs + negative momentum | 0% | 0% | 100% |
| **Crisis** | Bear + high volatility (ATR > 1.5× median) | 0% | 20% | 80% |

### Tactical Adjustments (via RSI on TQQQ)

| Condition | Adjustment |
|-----------|------------|
| Bull + RSI < 30 (oversold) | 100% TQQQ (aggressive dip buy) |
| Bull + RSI > 70 (overbought) | 75% TQQQ, 15% SQQQ hedge |
| Bull + RSI > 80 (very overbought) | 60% TQQQ, 25% SQQQ hedge |
| Bear + RSI > 55 (bouncing) | 20% TQQQ, 20% SQQQ |
| Neutral + high volatility | 30% TQQQ, 20% SQQQ |
| QQQ drops > 8% from 20-day high | 0% TQQQ, 15% SQQQ (rapid decline override) |

### Risk Management

- **Trailing stop**: If portfolio drops 25% from its peak, go to 100% cash
- **Cash mode cooldown**: Stay in cash for at least 5 trading days after stop triggers
- **Re-entry**: Only re-enter when regime returns to "bull" AND cooldown is met
- **Peak reset**: Portfolio peak resets when exiting cash mode (prevents repeated triggers)
- **Rebalance threshold**: Only trade when allocation drifts > 10% from target

### Execution Model

- **Signal timing**: Computed from the previous day's close (no look-ahead bias)
- **Execution**: At the next trading day's open price
- **Whole shares only**: No fractional shares; leftover goes to cash
- **Stock split handling**: Cache auto-validates against fresh data; invalidates on mismatch

---

## Backtest Results

Default engine (`mom_vol`), $10K starting capital.

### Full Period (Jan 2020 – Jun 2026)

| Metric | Strategy | Buy & Hold TQQQ |
|--------|----------|-----------------|
| Total Return | **+917%** | +661% |
| Annualized Return | **+43%** | +37% |
| Max Drawdown | **-42%** | -82% |
| Sharpe Ratio | **0.93** | 0.74 |
| Final Value ($10K) | **$101,662** | $76,142 |
| Trades | 177 | 1 |
| Days in SQQQ | 0 | — |

### Per-Regime Breakdown (`mom_vol`)

The `mom_vol` engine classifies each day as bull or bear (no neutral/crisis states):

| Regime | Days | Win Rate | Annualized (approx) |
|--------|------|----------|---------------------|
| Bull | 1,083 | 54% | +81% |
| Bear | 542 | 39% | +19% |

### Recent Downturn (Jan–May 2025)

| Metric | Strategy | Buy & Hold |
|--------|----------|------------|
| Return | **+12%** | -11% |
| Max Drawdown | **-19%** | -57% |

### 4h Intraday Experiment (Jun 2023 – May 2026)

Using 4h candles with mid-day execution (see `TradingStrategy1_Intraday/`):

| Metric | Daily (Open) | 4h (Mid-Day) | Benchmark |
|--------|-------------|--------------|-----------|
| Total Return | +144% | **+208%** | +255% |
| Sharpe | 0.86 | **1.06** | 0.96 |
| Max Drawdown | -42% | **-40%** | -58% |

---

## Quick Start

```bash
# Install dependencies
python3 -m pip install --user --break-system-packages -r requirements.txt

# Download and cache data
python3 main.py download

# Refresh cached data to the latest bar (daily incremental update)
python3 main.py update

# Run backtest (custom dates; end defaults to today)
python3 main.py backtest 2020-01-01 2026-05-01

# Run forward test (paper trading simulation)
python3 main.py forward

# Launch interactive dashboard
python3 main.py dashboard
# Open http://127.0.0.1:8050
```

## Project Structure

```
TradingStrategy1/
├── config.py            # All configurable parameters
├── download_data.py     # Chunked data downloader with CSV caching
├── indicators.py        # Regime detection, RSI, EMA, allocation signals
├── strategy.py          # Portfolio execution engine (trailing stop, rebalancing)
├── backtest.py          # Backtesting with metrics + benchmark comparison
├── forward_test.py      # Paper trading simulation with JSON state
├── dashboard.py         # Dash/Plotly interactive dashboard
├── main.py              # CLI entry point
├── requirements.txt     # Python dependencies
├── data/                # Cached CSV price data (TQQQ, SQQQ, QQQ)
├── paper_trade/         # Alpaca paper trading bot
│   ├── alpaca_bot.py    # Live trading bot (cron-scheduled)
│   ├── cron_setup.sh    # One-command Oracle Cloud deployment
│   ├── README.md        # Deployment instructions
│   ├── .env             # API keys (not committed)
│   ├── state.json       # Persistent trailing stop state
│   └── logs/            # Monthly trade logs (trade_YYYYMM.log)
├── paper_trade_reader.py # Reads bot state/logs for the dashboard tabs
└── README.md            # This file
```

## Dashboard

Runs at `http://127.0.0.1:8050` (or `0.0.0.0:8050` when deployed). Organized into three tabs:

**1. Backtesting**
- **Equity curve** — strategy vs buy-and-hold, with regime shading (green/yellow/red) and
  **buy / sell / rebalance markers overlaid directly on the curve** (green ▲ buy,
  red ▼ sell/rebalance, yellow ✕ trailing-stop exit; hover shows the exact signal)
- **Signals chart** — TQQQ price with buy/sell markers, RSI, allocation breakdown
- **Drawdown chart** — strategy vs benchmark
- **Metrics (KPI cards)** — return, Sharpe, drawdown, alpha vs B&H, trade counts, win rate
- **Trade log** — every trade with execution price, allocation changes, cash, gain/loss
- **Date picker** — select any period (auto-downloads missing data); a
  "Data current → YYYY-MM-DD" indicator and 6-hour auto-refresh keep the cache fresh

**2. Trading Confirmations** — live Alpaca paper-bot status and order confirmations,
read from `paper_trade/state.json` and `paper_trade/logs/trade_*.log`.

**3. Logs** — browse the bot's monthly trade logs directly in the UI.

## Configuration

Edit `config.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `RSI_PERIOD` | 14 | RSI lookback |
| `EMA_SHORT` | 20 | Short-term EMA for TQQQ |
| `EMA_TREND` | 50 | QQQ trend filter EMA |
| `RSI_OVERSOLD` | 30 | Oversold threshold |
| `RSI_OVERBOUGHT` | 70 | Overbought threshold |
| `REBALANCE_THRESHOLD` | 0.10 | Min allocation drift to trigger trade |
| `STARTING_CAPITAL` | $10,000 | Initial capital |
| `RISK_FREE_RATE` | 4.5% | For Sharpe ratio calculation |

## Data Caching

- Price data cached as CSV in `data/`
- Downloads in 6-month chunks to avoid yfinance throttling
- Incremental updates (only fetches new data)
- **Auto stock-split detection**: validates cache against fresh data on each run; re-downloads if prices differ > 1%

### Daily Data Updates

The backtest data is kept current with the latest market day in two ways:

1. **In-app auto-refresh** — the dashboard extends the cache to the latest bar on
   load and every 6 hours (guarded to hit the network at most once per calendar
   day). A "Data current → YYYY-MM-DD" indicator shows in the header. Every
   backtest run also triggers the same guarded refresh, so `DEFAULT_BACKTEST_END`
   defaults to today.

2. **Scheduled cron** — for headless daily updates, run `main.py update`. It
   incrementally appends only the new bars and records the last update date in
   `data/.last_update.json` (use `--force` to bypass the once-per-day guard):

   ```bash
   # Update every weekday at 6:00 PM ET (22:00 UTC), after market close.
   # Adjust the venv/repo paths to your deployment.
   0 22 * * 1-5 cd ~/tqqq-trading-strategy && source ~/trading-venv/bin/activate && python3 main.py update >> ~/logs/data_update.log 2>&1
   ```

## Alpaca Paper / Live Trading

Deploy on Oracle Cloud for automated daily trading. See `paper_trade/README.md`.

```bash
cd paper_trade
bash cron_setup.sh   # Sets up cron for 3:30 PM EST daily
```

The bot uses the **same signal engine as the backtest** (`indicators.generate_signals`,
honoring `config.REGIME_METHOD`), so live decisions match the dashboard. It computes
signals unshifted (it runs near the close and acts on the latest bar).

**Paper vs live** is controlled by environment variables (defaults to paper for safety):

```bash
export ALPACA_API_KEY="..."      # paper and live use DIFFERENT keys
export ALPACA_SECRET_KEY="..."
export ALPACA_PAPER="true"       # "false"/"0"/"live" → real-money trading
# (advanced) export ALPACA_BASE_URL="https://api.alpaca.markets"
```

**Preview without trading** — print the current signal and intended orders, no orders placed:

```bash
python3 paper_trade/alpaca_bot.py --dry-run
```

> **Cron note:** the bot's cron line uses `source`, which is a bash builtin. Cron defaults
> to `/bin/sh` (dash), so the crontab must set `SHELL=/bin/bash` or the job fails silently.

**Dashboard as a systemd service** (recommended on the server) — auto-starts on boot and
restarts on crash. Manage it with `systemctl`, never `kill`/`pkill`:

```bash
sudo systemctl restart trading-dashboard   # after a git pull
journalctl -u trading-dashboard -f         # live logs
```

## Key Design Decisions

1. **Cash over SQQQ for protection**: Inverse leveraged ETFs decay over time due to daily rebalancing. Holding SQQQ for weeks/months bleeds value. Cash preserves capital with zero decay.

2. **Next-day-open execution**: Eliminates look-ahead bias. Signals from yesterday's close are actionable — you review after market close, trade at tomorrow's open.

3. **Trailing stop with cooldown**: A hard 25% drawdown stop prevents catastrophic losses. The 5-day cooldown + bull-regime requirement prevents whipsawing back in during continued declines.

4. **Whole shares only**: Reflects real brokerage constraints. Leftover cash from rounding acts as a small buffer.

5. **QQQ for regime detection**: QQQ (non-leveraged) provides cleaner trend signals than TQQQ, which amplifies noise with 3× leverage.

## Inspiration

Based on the [Cortex Alpha TQQQ trading strategy](https://www.youtube.com/watch?v=FhEq6dKNYqY), adapted from a daily mean-reversion scalping approach to a regime-based allocation system with drawdown protection.
