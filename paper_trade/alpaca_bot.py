#!/usr/bin/env python3
"""
Alpaca Paper Trading Bot for TQQQ/SQQQ Regime-Based Strategy.

Runs daily at 3:30 PM EST when market is open.
Uses the same regime detection and allocation logic as the backtest.

Setup:
  1. Create Alpaca account at https://alpaca.markets
  2. Go to Paper Trading → copy API Key ID and Secret Key
  3. Set environment variables:
       export ALPACA_API_KEY="your-key"
       export ALPACA_SECRET_KEY="your-secret"
  4. Install deps: pip install alpaca-trade-api yfinance pandas numpy
  5. Schedule with cron (see cron_setup.sh)
"""
import os
import sys
import json
import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import yfinance as yf

# Add parent directory for strategy modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from indicators import compute_rsi, compute_ema, compute_atr

try:
    import alpaca_trade_api as tradeapi
except ImportError:
    print("ERROR: Install alpaca-trade-api: pip install alpaca-trade-api")
    sys.exit(1)

# --- Configuration ---
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# --- Logging ---
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"trade_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def load_state():
    """Load persistent state (trailing stop tracking)."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "portfolio_peak": 0,
        "in_cash_mode": False,
        "cash_mode_days": 0,
        "last_regime": "unknown",
        "last_run": None
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def get_alpaca_api():
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
        sys.exit(1)
    return tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, api_version="v2")


def is_market_open(api):
    """Check if market is currently open."""
    clock = api.get_clock()
    return clock.is_open


def get_current_positions(api):
    """Get current TQQQ and SQQQ positions."""
    positions = {p.symbol: p for p in api.list_positions()}
    tqqq_shares = int(positions["TQQQ"].qty) if "TQQQ" in positions else 0
    sqqq_shares = int(positions["SQQQ"].qty) if "SQQQ" in positions else 0
    tqqq_value = float(positions["TQQQ"].market_value) if "TQQQ" in positions else 0
    sqqq_value = float(positions["SQQQ"].market_value) if "SQQQ" in positions else 0
    return tqqq_shares, sqqq_shares, tqqq_value, sqqq_value


def get_account_value(api):
    """Get total portfolio value (equity)."""
    account = api.get_account()
    return float(account.equity), float(account.cash)


def compute_regime_and_allocation():
    """
    Download recent data and compute current regime + target allocation.
    Uses the same logic as indicators.py but on live data.
    """
    lookback_days = 120  # Need enough history for indicators

    qqq = yf.download("QQQ", period=f"{lookback_days}d", progress=False, auto_adjust=True)
    tqqq = yf.download("TQQQ", period=f"{lookback_days}d", progress=False, auto_adjust=True)
    sqqq = yf.download("SQQQ", period=f"{lookback_days}d", progress=False, auto_adjust=True)

    for df in [qqq, tqqq, sqqq]:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

    if len(qqq) < 60 or len(tqqq) < 60:
        logger.error(f"Insufficient data: QQQ={len(qqq)}, TQQQ={len(tqqq)} bars")
        return None, None, None, None

    # --- Core Indicators (on QQQ) ---
    qqq_close = qqq["Close"]
    qqq_ema_trend = compute_ema(qqq_close, config.EMA_TREND)
    qqq_ema_short = compute_ema(qqq_close, config.EMA_SHORT)
    qqq_momentum = qqq_close.pct_change(10)

    # Volatility
    if "High" in qqq.columns and "Low" in qqq.columns:
        qqq_atr = compute_atr(qqq["High"], qqq["Low"], qqq_close)
        volatility = qqq_atr / qqq_close
    else:
        volatility = qqq_close.pct_change().rolling(14).std()

    vol_median = volatility.expanding().median()
    high_vol = volatility > vol_median * 1.5

    # RSI on TQQQ
    rsi = compute_rsi(tqqq["Close"])

    # --- Regime (latest value) ---
    latest = len(qqq_close) - 1
    uptrend = qqq_close.iloc[latest] > qqq_ema_trend.iloc[latest]
    above_short = qqq_close.iloc[latest] > qqq_ema_short.iloc[latest]
    mom = qqq_momentum.iloc[latest]
    hv = high_vol.iloc[latest]
    current_rsi = rsi.iloc[latest]

    bull = uptrend or (above_short and mom > 0)
    bear = (not uptrend) and (not above_short) and (mom < 0)
    crisis = bear and hv

    if crisis:
        regime = "crisis"
    elif bear:
        regime = "bear"
    elif bull:
        regime = "bull"
    else:
        regime = "neutral"

    # Drawdown detection
    qqq_20d_high = qqq_close.rolling(20).max().iloc[latest]
    qqq_drawdown = (qqq_close.iloc[latest] - qqq_20d_high) / qqq_20d_high
    rapid_decline = qqq_drawdown < -0.08

    # --- Base Allocation ---
    if regime == "bull":
        tqqq_alloc, sqqq_alloc = 1.0, 0.0
    elif regime == "neutral":
        tqqq_alloc, sqqq_alloc = 0.70, 0.0
    elif regime == "bear":
        tqqq_alloc, sqqq_alloc = 0.0, 0.0
    elif regime == "crisis":
        tqqq_alloc, sqqq_alloc = 0.0, 0.20
    else:
        tqqq_alloc, sqqq_alloc = 0.70, 0.0

    # Rapid decline override
    if rapid_decline and regime != "crisis":
        tqqq_alloc, sqqq_alloc = 0.0, 0.15

    # Tactical adjustments
    if regime == "bull" and current_rsi < config.RSI_OVERSOLD:
        tqqq_alloc, sqqq_alloc = 1.0, 0.0
    elif regime == "bull" and current_rsi > 80:
        tqqq_alloc, sqqq_alloc = 0.60, 0.25
    elif regime == "bull" and current_rsi > config.RSI_OVERBOUGHT:
        tqqq_alloc, sqqq_alloc = 0.75, 0.15
    elif regime == "bear" and current_rsi > 55:
        tqqq_alloc, sqqq_alloc = 0.20, 0.20

    if regime == "neutral" and hv:
        tqqq_alloc, sqqq_alloc = 0.30, 0.20

    # Clamp
    tqqq_alloc = max(0.0, min(1.0, tqqq_alloc))
    sqqq_alloc = max(0.0, min(0.80, sqqq_alloc))
    total = tqqq_alloc + sqqq_alloc
    if total > 1.0:
        tqqq_alloc /= total
        sqqq_alloc /= total

    return regime, tqqq_alloc, sqqq_alloc, current_rsi


def execute_rebalance(api, target_tqqq_alloc, target_sqqq_alloc):
    """
    Rebalance portfolio to target allocations using market orders.
    """
    equity, cash = get_account_value(api)
    tqqq_shares, sqqq_shares, tqqq_value, sqqq_value = get_current_positions(api)

    # Current allocations
    actual_tqqq_alloc = tqqq_value / equity if equity > 0 else 0
    actual_sqqq_alloc = sqqq_value / equity if equity > 0 else 0

    logger.info(f"Current: TQQQ={actual_tqqq_alloc:.1%} ({tqqq_shares} shares), "
                f"SQQQ={actual_sqqq_alloc:.1%} ({sqqq_shares} shares), Cash=${cash:,.2f}")

    # Check drift
    tqqq_drift = abs(actual_tqqq_alloc - target_tqqq_alloc)
    sqqq_drift = abs(actual_sqqq_alloc - target_sqqq_alloc)

    if tqqq_drift < config.REBALANCE_THRESHOLD and sqqq_drift < config.REBALANCE_THRESHOLD:
        logger.info("Drift below threshold — no rebalance needed")
        return False

    # Get current prices
    tqqq_quote = api.get_latest_trade("TQQQ")
    sqqq_quote = api.get_latest_trade("SQQQ")
    tqqq_price = tqqq_quote.price
    sqqq_price = sqqq_quote.price

    # Target shares (whole shares only)
    target_tqqq_shares = int((equity * target_tqqq_alloc) / tqqq_price)
    target_sqqq_shares = int((equity * target_sqqq_alloc) / sqqq_price)

    # Calculate deltas
    tqqq_delta = target_tqqq_shares - tqqq_shares
    sqqq_delta = target_sqqq_shares - sqqq_shares

    logger.info(f"Target: TQQQ={target_tqqq_alloc:.0%} ({target_tqqq_shares} shares), "
                f"SQQQ={target_sqqq_alloc:.0%} ({target_sqqq_shares} shares)")

    # Execute sells first (to free up cash), then buys
    orders = []

    # TQQQ
    if tqqq_delta < 0:
        orders.insert(0, ("TQQQ", "sell", abs(tqqq_delta)))
    elif tqqq_delta > 0:
        orders.append(("TQQQ", "buy", tqqq_delta))

    # SQQQ
    if sqqq_delta < 0:
        orders.insert(0, ("SQQQ", "sell", abs(sqqq_delta)))
    elif sqqq_delta > 0:
        orders.append(("SQQQ", "buy", sqqq_delta))

    for symbol, side, qty in orders:
        if qty == 0:
            continue
        logger.info(f"Submitting: {side.upper()} {qty} {symbol}")
        try:
            api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type="market",
                time_in_force="day"
            )
            logger.info(f"  ✓ Order submitted: {side} {qty} {symbol}")
        except Exception as e:
            logger.error(f"  ✗ Order failed: {e}")

    return True


def run():
    """Main entry point — called by cron at 3:30 PM EST on trading days."""
    logger.info("=" * 60)
    logger.info("TQQQ/SQQQ Paper Trading Bot — Starting")
    logger.info("=" * 60)

    api = get_alpaca_api()

    # Check market is open
    if not is_market_open(api):
        logger.info("Market is closed — skipping")
        return

    # Load state
    state = load_state()

    # Get portfolio value
    equity, cash = get_account_value(api)
    logger.info(f"Account equity: ${equity:,.2f}, Cash: ${cash:,.2f}")

    # Compute signals
    regime, tqqq_alloc, sqqq_alloc, rsi = compute_regime_and_allocation()
    if regime is None:
        logger.error("Failed to compute signals — aborting")
        return

    logger.info(f"Regime: {regime} | RSI: {rsi:.1f} | "
                f"Target: TQQQ={tqqq_alloc:.0%}, SQQQ={sqqq_alloc:.0%}")

    # --- Portfolio Trailing Stop ---
    TRAILING_STOP_PCT = 0.25
    CASH_MODE_COOLDOWN = 5

    if state["portfolio_peak"] == 0:
        state["portfolio_peak"] = equity

    state["portfolio_peak"] = max(state["portfolio_peak"], equity)
    drawdown = (equity - state["portfolio_peak"]) / state["portfolio_peak"]

    if state["in_cash_mode"]:
        state["cash_mode_days"] += 1
        if state["cash_mode_days"] >= CASH_MODE_COOLDOWN and regime == "bull":
            logger.info(f"Exiting cash mode after {state['cash_mode_days']} days (regime=bull)")
            state["in_cash_mode"] = False
            state["cash_mode_days"] = 0
            state["portfolio_peak"] = equity
        else:
            logger.info(f"In cash mode (day {state['cash_mode_days']}/{CASH_MODE_COOLDOWN})")
            tqqq_alloc = 0.0
            sqqq_alloc = 0.0
    elif drawdown < -TRAILING_STOP_PCT:
        logger.warning(f"TRAILING STOP triggered! Drawdown: {drawdown:.1%} from peak ${state['portfolio_peak']:,.2f}")
        state["in_cash_mode"] = True
        state["cash_mode_days"] = 0
        tqqq_alloc = 0.0
        sqqq_alloc = 0.0

    # Execute rebalance
    traded = execute_rebalance(api, tqqq_alloc, sqqq_alloc)

    # Update state
    state["last_regime"] = regime
    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    if traded:
        logger.info("Rebalance complete ✓")
    else:
        logger.info("No action needed ✓")

    logger.info("=" * 60)


if __name__ == "__main__":
    run()
