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
       # Optional — defaults to paper. Set to "false" for LIVE real-money trading:
       export ALPACA_PAPER="true"
       # (Advanced) or override the endpoint directly:
       # export ALPACA_BASE_URL="https://api.alpaca.markets"
  4. Install deps: pip install alpaca-trade-api yfinance pandas numpy
  5. Schedule with cron (see cron_setup.sh)

Usage:
  python3 paper_trade/alpaca_bot.py            # normal run (places orders)
  python3 paper_trade/alpaca_bot.py --dry-run  # preview signal + intended
                                               # trades; no orders, no state write

Note: paper and live use DIFFERENT API keys. When switching to live, update
ALPACA_API_KEY/ALPACA_SECRET_KEY with your live keys as well as ALPACA_PAPER.
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

try:
    import alpaca_trade_api as tradeapi
except ImportError:
    print("ERROR: Install alpaca-trade-api: pip install alpaca-trade-api")
    sys.exit(1)

# --- Configuration ---
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


def _resolve_endpoint():
    """
    Resolve the Alpaca endpoint and trading mode from the environment.

    Precedence:
      1. ALPACA_BASE_URL  — explicit full URL override (advanced).
      2. ALPACA_PAPER     — "true"/"1"/"yes" (default) → paper; "false"/"0"/"no" → live.

    Defaults to PAPER for safety. Returns (base_url, is_paper).
    """
    explicit = os.environ.get("ALPACA_BASE_URL", "").strip()
    if explicit:
        is_paper = "paper-api" in explicit
        return explicit, is_paper

    paper_flag = os.environ.get("ALPACA_PAPER", "true").strip().lower()
    is_paper = paper_flag not in ("false", "0", "no", "off", "live")
    return (PAPER_URL if is_paper else LIVE_URL), is_paper


ALPACA_BASE_URL, ALPACA_IS_PAPER = _resolve_endpoint()

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# --- Logging ---
# One log file per month (e.g. trade_202606.log); runs append to it all month.
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"trade_{datetime.now().strftime('%Y%m')}.log")
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
    mode = "PAPER" if ALPACA_IS_PAPER else "LIVE 🔴 REAL MONEY"
    logger.info(f"Alpaca mode: {mode} ({ALPACA_BASE_URL})")
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

    Uses the SAME signal engine as the backtest (indicators.generate_signals),
    so the live bot honors config.REGIME_METHOD (e.g. "mom_vol") and produces
    identical decisions to the dashboard/backtest. Signals are computed
    unshifted (shift_signals=False) because the bot runs at/near the close and
    acts on the latest bar's own close.
    """
    import indicators

    # ~400 calendar days of history mirrors the backtest warm-up buffer, so the
    # 50-day EMA and expanding medians are well primed before the latest bar.
    lookback_days = 400

    qqq = yf.download("QQQ", period=f"{lookback_days}d", progress=False, auto_adjust=True)
    tqqq = yf.download("TQQQ", period=f"{lookback_days}d", progress=False, auto_adjust=True)
    sqqq = yf.download("SQQQ", period=f"{lookback_days}d", progress=False, auto_adjust=True)

    for df in [qqq, tqqq, sqqq]:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

    if len(qqq) < 60 or len(tqqq) < 60:
        logger.error(f"Insufficient data: QQQ={len(qqq)}, TQQQ={len(tqqq)} bars")
        return None, None, None, None

    signals = indicators.generate_signals(tqqq, sqqq, qqq, shift_signals=False)
    if signals.empty:
        logger.error("Signal engine returned no rows — insufficient warm-up data")
        return None, None, None, None

    latest = signals.iloc[-1]
    regime = latest["regime"]
    tqqq_alloc = float(latest["tqqq_alloc"])
    sqqq_alloc = float(latest["sqqq_alloc"])
    current_rsi = float(latest["rsi"])

    return regime, tqqq_alloc, sqqq_alloc, current_rsi


def execute_rebalance(api, target_tqqq_alloc, target_sqqq_alloc, dry_run=False):
    """
    Rebalance portfolio to target allocations using market orders.

    When dry_run is True, the intended orders are logged but NOT submitted.
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
        if dry_run:
            logger.info(f"[DRY-RUN] Would submit: {side.upper()} {qty} {symbol} (no order placed)")
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


def run(dry_run=False):
    """Main entry point — called by cron at 3:30 PM EST on trading days.

    When dry_run is True, computes the signal and prints the intended trades
    WITHOUT placing any orders or persisting state. The market-open gate is
    skipped so you can preview the decision at any time.
    """
    logger.info("=" * 60)
    banner = "TQQQ/SQQQ Bot — DRY RUN (no orders)" if dry_run else "TQQQ/SQQQ Paper Trading Bot — Starting"
    logger.info(banner)
    logger.info("=" * 60)

    api = get_alpaca_api()

    # Check market is open (skipped in dry-run so the signal can be previewed anytime)
    if not dry_run and not is_market_open(api):
        logger.info("Market is closed — skipping")
        return
    if dry_run and not is_market_open(api):
        logger.info("Market is closed (dry-run: previewing signal anyway)")

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
    traded = execute_rebalance(api, tqqq_alloc, sqqq_alloc, dry_run=dry_run)

    # Update state (skipped in dry-run — preview only)
    if not dry_run:
        state["last_regime"] = regime
        state["last_run"] = datetime.now().isoformat()
        save_state(state)

    if dry_run:
        logger.info("Dry-run complete — no orders placed, state unchanged ✓")
    elif traded:
        logger.info("Rebalance complete ✓")
    else:
        logger.info("No action needed ✓")

    logger.info("=" * 60)


if __name__ == "__main__":
    dry_run = any(a in ("--dry-run", "--dryrun", "-n") for a in sys.argv[1:])
    run(dry_run=dry_run)
