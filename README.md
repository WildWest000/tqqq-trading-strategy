# TQQQ/SQQQ Regime-Based Trading Strategy

## The 30-Second Pitch

We trade TQQQ and SQQQ using a regime-detection engine that reads the market's mood — bull, neutral, bear, or crisis — based on QQQ's trend, momentum, and volatility.

In bull markets, we go 100% TQQQ to ride the wave. When conditions deteriorate, we move to cash — not SQQQ, because inverse ETFs decay over time. We only deploy SQQQ as a short-term hedge during sharp crashes.

A portfolio-level trailing stop kicks us to cash if we drop 25% from peak, and we don't re-enter until the trend confirms bullish again.

**The results over 5+ years (2020–2026):** 583% return vs 502% buy-and-hold, with a max drawdown of -56% compared to -82%. Better returns, with nearly half the pain. Sharpe ratio of 0.78 vs 0.70.

**The edge:** We capture almost all the upside in bull markets while sitting safely in cash during the worst drops — COVID crash, 2022 bear market, 2025 tariff selloff. It's not about timing the market perfectly — it's about not being in the market when it's falling off a cliff.

---

## Strategy Overview

### Regime Detection (via QQQ)

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

### Full Period (Jan 2020 – May 2026)

| Metric | Strategy | Buy & Hold TQQQ |
|--------|----------|-----------------|
| Total Return | **+583%** | +502% |
| Annualized Return | **+36%** | +33% |
| Max Drawdown | **-56%** | -82% |
| Sharpe Ratio | **0.78** | 0.70 |
| Final Value ($10K) | **$68,283** | $60,209 |
| Trades | 275 | 1 |

### Per-Regime Breakdown

| Regime | Days | Win Rate | Annualized |
|--------|------|----------|------------|
| Bull | 1,160 | 56% | +61% |
| Neutral | 52 | 37% | -14% |
| Bear | 245 | 18% | -19% |
| Crisis | 114 | 18% | -15% |

### Recent Downturn (Jan–May 2025)

| Metric | Strategy | Buy & Hold |
|--------|----------|------------|
| Return | **-4%** | -28% |
| Max Drawdown | **-25%** | -57% |

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

# Run backtest (custom dates)
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
│   └── logs/            # Daily trade logs
└── README.md            # This file
```

## Dashboard

Runs at `http://127.0.0.1:8050` with:

- **Equity curve** — strategy vs buy-and-hold, with regime shading (green/yellow/red)
- **Signals chart** — TQQQ price with buy/sell/trailing-stop markers, RSI, allocation breakdown
- **Drawdown chart** — strategy vs benchmark
- **Metrics table** — return, Sharpe, drawdown, trade counts by type
- **Trade log** — every trade with execution price, allocation changes, cash, gain/loss
- **Forward test panel** — paper trading results
- **Date picker** — select any period (auto-downloads missing data)

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

## Alpaca Paper Trading

Deploy on Oracle Cloud for automated daily trading. See `paper_trade/README.md`.

```bash
cd paper_trade
bash cron_setup.sh   # Sets up cron for 3:30 PM EST daily
```

## Key Design Decisions

1. **Cash over SQQQ for protection**: Inverse leveraged ETFs decay over time due to daily rebalancing. Holding SQQQ for weeks/months bleeds value. Cash preserves capital with zero decay.

2. **Next-day-open execution**: Eliminates look-ahead bias. Signals from yesterday's close are actionable — you review after market close, trade at tomorrow's open.

3. **Trailing stop with cooldown**: A hard 25% drawdown stop prevents catastrophic losses. The 5-day cooldown + bull-regime requirement prevents whipsawing back in during continued declines.

4. **Whole shares only**: Reflects real brokerage constraints. Leftover cash from rounding acts as a small buffer.

5. **QQQ for regime detection**: QQQ (non-leveraged) provides cleaner trend signals than TQQQ, which amplifies noise with 3× leverage.

## Inspiration

Based on the [Cortex Alpha TQQQ trading strategy](https://www.youtube.com/watch?v=FhEq6dKNYqY), adapted from a daily mean-reversion scalping approach to a regime-based allocation system with drawdown protection.
