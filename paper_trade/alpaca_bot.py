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
  python3 paper_trade/alpaca_bot.py --snapshot # write portfolio snapshot to
                                               # state.json only; no orders

Note: paper and live use DIFFERENT API keys. When switching to live, update
ALPACA_API_KEY/ALPACA_SECRET_KEY with your live keys as well as ALPACA_PAPER.
"""
import os
import sys
import json
import time
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


def update_portfolio_snapshot(api, state):
    """
    Read live equity/cash/positions and store a portfolio snapshot in `state`
    (under "portfolio") so the dashboard can render a live summary from disk
    without needing Alpaca credentials.

    `day_pl` is measured against the day's starting equity (the last snapshot
    from the prior day ≈ prior close), so it stays a correct intraday day P&L
    even if snapshots are taken many times per day. Mutates and returns state.
    """
    post_equity, post_cash = get_account_value(api)
    tqqq_sh, sqqq_sh, tqqq_val, sqqq_val = get_current_positions(api)
    prev = state.get("portfolio") or {}

    today = datetime.now().strftime("%Y-%m-%d")
    if prev.get("day_start_date") == today and prev.get("day_start_equity"):
        day_start_equity = prev["day_start_equity"]
    else:
        # New trading day: baseline against the prior snapshot's equity
        # (≈ prior close) so the first reading reflects the overnight change.
        day_start_equity = prev.get("equity") or post_equity

    state["portfolio"] = {
        "equity": post_equity,
        "cash": post_cash,
        "tqqq_shares": tqqq_sh,
        "tqqq_value": tqqq_val,
        "sqqq_shares": sqqq_sh,
        "sqqq_value": sqqq_val,
        "day_start_equity": day_start_equity,
        "day_start_date": today,
        "day_pl": post_equity - day_start_equity,
        "day_pl_pct": ((post_equity - day_start_equity) / day_start_equity * 100) if day_start_equity else 0.0,
        "as_of": datetime.now().isoformat(),
    }
    return state


def write_snapshot():
    """
    Backfill the portfolio snapshot immediately (no trading).

    Connects to Alpaca, reads equity/cash/positions, writes the snapshot into
    state.json, and exits. Useful to populate the dashboard's Portfolio Summary
    right away instead of waiting for the next scheduled bot run.
    """
    logger.info("=" * 60)
    logger.info("TQQQ/SQQQ Bot — SNAPSHOT (no orders)")
    logger.info("=" * 60)
    api = get_alpaca_api()
    state = load_state()
    update_portfolio_snapshot(api, state)
    save_state(state)
    snap = state["portfolio"]
    logger.info(f"Equity: ${snap['equity']:,.2f} | Cash: ${snap['cash']:,.2f} | "
                f"TQQQ: {snap['tqqq_shares']} sh (${snap['tqqq_value']:,.2f}) | "
                f"SQQQ: {snap['sqqq_shares']} sh (${snap['sqqq_value']:,.2f})")
    logger.info("Snapshot written to state.json ✓")
    logger.info("=" * 60)


def cancel_open_tqqq_orders(api, dry_run=False):
    """
    Cancel any resting TQQQ orders (e.g. a prior protective stop) before
    rebalancing, so they don't tie up share quantity or conflict with new
    market orders. Safe no-op if there are none.
    """
    try:
        open_orders = api.list_orders(status="open")
    except Exception as e:
        logger.warning(f"Could not list open orders: {e}")
        return
    for o in open_orders:
        if o.symbol != "TQQQ":
            continue
        if dry_run:
            logger.info(f"[DRY-RUN] Would cancel open {o.type} {o.side} order {o.id} for TQQQ")
            continue
        try:
            api.cancel_order(o.id)
            logger.info(f"Cancelled stale {o.type} {o.side} order for TQQQ")
        except Exception as e:
            logger.warning(f"Could not cancel order {o.id}: {e}")


def get_protective_stop_order(api):
    """
    Return the open TQQQ protective stop sell order (trailing_stop or stop), or
    None. Used to inspect the current stop's trigger price and quantity so we can
    decide whether to keep it and preserve its dollar floor when re-arming.
    """
    try:
        open_orders = api.list_orders(status="open")
    except Exception as e:
        logger.warning(f"Could not list open orders: {e}")
        return None
    for o in open_orders:
        if (o.symbol == "TQQQ" and getattr(o, "side", "") == "sell"
                and getattr(o, "type", "") in ("trailing_stop", "stop")):
            return o
    return None


def manage_protective_stop(api, dry_run=False, expect_position=False,
                           preserve_stop_price=None):
    """
    Place a fresh intraday protective stop on the current TQQQ position so a
    fast intraday crash is cut WITHOUT waiting for the next daily run.

    Uses a trailing stop (trails the high) or a fixed stop depending on config.
    This is the only mechanism that reacts within a single trading day — the
    daily backtest cannot model it, so it lives only in the live bot.

    When expect_position is True (a buy was just submitted), briefly polls for
    the market order to fill so the stop is armed the SAME day as the entry.

    When preserve_stop_price is given (the trigger price of the stop we just
    cancelled because of a rebalance), the trail % is RECALCULATED so the new
    stop keeps the SAME dollar floor instead of resetting to INTRADAY_STOP_PCT
    below the current price. Falls back to the default % if the prior floor is
    no longer valid (price at/below it).
    """
    if not getattr(config, "INTRADAY_STOP_ENABLED", False):
        return

    tqqq_shares, _, _, _ = get_current_positions(api)

    # A market buy may not have settled yet — give it a moment so the entry day
    # isn't left unprotected. Skip the wait in dry-run (no real fill).
    if expect_position and tqqq_shares <= 0 and not dry_run:
        for _ in range(6):
            time.sleep(2)
            tqqq_shares, _, _, _ = get_current_positions(api)
            if tqqq_shares > 0:
                break

    if tqqq_shares <= 0:
        logger.info("No TQQQ position — no protective stop placed")
        return

    pct = config.INTRADAY_STOP_PCT
    trailing = getattr(config, "INTRADAY_TRAILING_STOP", True)

    # If asked to preserve a prior stop floor, recompute the trail % from the
    # current price so the dollar trigger is maintained.
    trail_pct = pct
    preserved = False
    if preserve_stop_price and not dry_run:
        try:
            current_price = api.get_latest_trade("TQQQ").price
        except Exception:
            current_price = None
        if current_price and current_price > preserve_stop_price:
            computed = (current_price - preserve_stop_price) / current_price
            if 0 < computed < 1:
                trail_pct = computed
                preserved = True

    if dry_run:
        kind = f"trailing stop {pct:.0%}" if trailing else f"stop {pct:.0%} below price"
        logger.info(f"[DRY-RUN] Would place protective {kind} SELL {tqqq_shares} TQQQ (GTC)")
        return

    try:
        if trailing:
            api.submit_order(
                symbol="TQQQ",
                qty=tqqq_shares,
                side="sell",
                type="trailing_stop",
                trail_percent=str(round(trail_pct * 100, 2)),
                time_in_force="gtc",
            )
            if preserved:
                logger.info(f"  ✓ Protective TRAILING STOP placed: SELL {tqqq_shares} TQQQ, "
                            f"trail {trail_pct:.2%} (preserving prior stop @ ${preserve_stop_price:.2f})")
            else:
                logger.info(f"  ✓ Protective TRAILING STOP placed: SELL {tqqq_shares} TQQQ, "
                            f"trail {trail_pct:.0%}")
        else:
            current = api.get_latest_trade("TQQQ").price
            # Preserve the prior floor if valid, else default % below current.
            if preserve_stop_price and current > preserve_stop_price:
                stop_price = round(preserve_stop_price, 2)
            else:
                stop_price = round(current * (1 - pct), 2)
            api.submit_order(
                symbol="TQQQ",
                qty=tqqq_shares,
                side="sell",
                type="stop",
                stop_price=str(stop_price),
                time_in_force="gtc",
            )
            logger.info(f"  ✓ Protective STOP placed: SELL {tqqq_shares} TQQQ @ ${stop_price}"
                        + (" (preserved)" if preserve_stop_price and current > preserve_stop_price else f" ({pct:.0%} below)"))
    except Exception as e:
        logger.error(f"  ✗ Protective stop failed: {e}")


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


def _await_fill(api, order_id, timeout=20, poll=1.5):
    """
    Poll an order until it fills, returning the average fill price (float) or
    None if it hasn't filled within `timeout` seconds. Market orders during
    market hours fill almost immediately; this just confirms the actual price.
    """
    waited = 0.0
    while waited <= timeout:
        try:
            o = api.get_order(order_id)
        except Exception as e:
            logger.warning(f"Could not poll order {order_id}: {e}")
            return None
        status = getattr(o, "status", "")
        if status == "filled" and getattr(o, "filled_avg_price", None):
            return float(o.filled_avg_price)
        if status in ("canceled", "expired", "rejected"):
            return None
        time.sleep(poll)
        waited += poll
    return None


def plan_rebalance(api, target_tqqq_alloc, target_sqqq_alloc):
    """
    Compute (without placing) whether a rebalance is needed and the orders to
    get there. Returns a dict:
      {"needed": bool, "orders": [(symbol, side, qty), ...],
       "ref_prices": {symbol: price}, "target_tqqq_shares": int}
    Logs the current vs target snapshot. "needed" is False when drift is below
    the threshold (no trade required).
    """
    equity, cash = get_account_value(api)
    tqqq_shares, sqqq_shares, tqqq_value, sqqq_value = get_current_positions(api)

    actual_tqqq_alloc = tqqq_value / equity if equity > 0 else 0
    actual_sqqq_alloc = sqqq_value / equity if equity > 0 else 0

    logger.info(f"Current: TQQQ={actual_tqqq_alloc:.1%} ({tqqq_shares} shares), "
                f"SQQQ={actual_sqqq_alloc:.1%} ({sqqq_shares} shares), Cash=${cash:,.2f}")

    tqqq_drift = abs(actual_tqqq_alloc - target_tqqq_alloc)
    sqqq_drift = abs(actual_sqqq_alloc - target_sqqq_alloc)

    if tqqq_drift < config.REBALANCE_THRESHOLD and sqqq_drift < config.REBALANCE_THRESHOLD:
        return {"needed": False, "orders": [], "ref_prices": {}, "target_tqqq_shares": tqqq_shares}

    tqqq_price = api.get_latest_trade("TQQQ").price
    sqqq_price = api.get_latest_trade("SQQQ").price

    target_tqqq_shares = int((equity * target_tqqq_alloc) / tqqq_price)
    target_sqqq_shares = int((equity * target_sqqq_alloc) / sqqq_price)

    tqqq_delta = target_tqqq_shares - tqqq_shares
    sqqq_delta = target_sqqq_shares - sqqq_shares

    logger.info(f"Target: TQQQ={target_tqqq_alloc:.0%} ({target_tqqq_shares} shares), "
                f"SQQQ={target_sqqq_alloc:.0%} ({target_sqqq_shares} shares)")

    # Execute sells first (to free up cash), then buys
    orders = []
    if tqqq_delta < 0:
        orders.insert(0, ("TQQQ", "sell", abs(tqqq_delta)))
    elif tqqq_delta > 0:
        orders.append(("TQQQ", "buy", tqqq_delta))
    if sqqq_delta < 0:
        orders.insert(0, ("SQQQ", "sell", abs(sqqq_delta)))
    elif sqqq_delta > 0:
        orders.append(("SQQQ", "buy", sqqq_delta))

    return {
        "needed": True,
        "orders": orders,
        "ref_prices": {"TQQQ": tqqq_price, "SQQQ": sqqq_price},
        "target_tqqq_shares": target_tqqq_shares,
    }


def place_orders(api, orders, ref_prices, dry_run=False):
    """
    Submit the planned market orders, logging the submit (reference) price and
    polling for the average fill price. Returns True if any order was placed
    (or would be in dry-run).
    """
    placed = False
    for symbol, side, qty in orders:
        if qty == 0:
            continue
        placed = True
        ref_price = ref_prices.get(symbol, 0.0)
        if dry_run:
            logger.info(f"[DRY-RUN] Would submit: {side.upper()} {qty} {symbol} "
                        f"@ ~${ref_price:.2f} (no order placed)")
            continue
        logger.info(f"Submitting: {side.upper()} {qty} {symbol} @ ~${ref_price:.2f} (ref)")
        try:
            order = api.submit_order(
                symbol=symbol,
                qty=qty,
                side=side,
                type="market",
                time_in_force="day"
            )
            fill_price = _await_fill(api, order.id)
            if fill_price:
                slip = ((fill_price - ref_price) / ref_price * 100) if ref_price else 0.0
                logger.info(f"  ✓ Order filled: {side.upper()} {qty} {symbol} "
                            f"@ ${fill_price:.2f} (submitted @ ${ref_price:.2f}, "
                            f"slippage {slip:+.2f}%)")
            else:
                logger.info(f"  ✓ Order submitted: {side.upper()} {qty} {symbol} "
                            f"@ ~${ref_price:.2f} (fill pending)")
        except Exception as e:
            logger.error(f"  ✗ Order failed: {side.upper()} {qty} {symbol}: {e}")
    return placed


def execute_rebalance(api, target_tqqq_alloc, target_sqqq_alloc, dry_run=False):
    """
    Rebalance to target allocations using market orders (thin wrapper around
    plan_rebalance + place_orders). Returns True if a rebalance was performed.

    When dry_run is True, the intended orders are logged but NOT submitted.
    """
    plan = plan_rebalance(api, target_tqqq_alloc, target_sqqq_alloc)
    if not plan["needed"]:
        logger.info("Drift below threshold — no rebalance needed")
        return False
    place_orders(api, plan["orders"], plan["ref_prices"], dry_run=dry_run)
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

    # --- Rebalance + protective stop management ---
    # Inspect the existing protective stop so we can decide whether to keep it.
    existing_stop = None if dry_run else get_protective_stop_order(api)
    prev_stop_price = None
    if existing_stop is not None and getattr(existing_stop, "stop_price", None):
        try:
            prev_stop_price = float(existing_stop.stop_price)
        except (TypeError, ValueError):
            prev_stop_price = None
    existing_stop_qty = int(float(existing_stop.qty)) if existing_stop is not None else 0

    # Plan the rebalance (no orders placed yet) so we know whether we must touch
    # the resting stop at all.
    plan = plan_rebalance(api, tqqq_alloc, sqqq_alloc)
    if not plan["needed"]:
        logger.info("Drift below threshold — no rebalance needed")

    if dry_run:
        traded = place_orders(api, plan["orders"], plan["ref_prices"], dry_run=True)
        manage_protective_stop(api, dry_run=True, expect_position=(tqqq_alloc > 0))
    elif plan["needed"]:
        # A trade will change the position — cancel the resting stop first (so it
        # doesn't tie up shares), execute, then re-arm preserving the prior floor.
        cancel_open_tqqq_orders(api)
        traded = place_orders(api, plan["orders"], plan["ref_prices"])
        manage_protective_stop(api, expect_position=(tqqq_alloc > 0),
                               preserve_stop_price=prev_stop_price)
    else:
        # No trade: leave the existing stop in place so it keeps trailing the
        # high-water mark. Only (re-)arm if missing or its qty no longer matches.
        traded = False
        cur_tqqq_shares, _, _, _ = get_current_positions(api)
        if existing_stop is None:
            manage_protective_stop(api, expect_position=(tqqq_alloc > 0))
        elif existing_stop_qty != cur_tqqq_shares:
            logger.info(f"Protective stop qty ({existing_stop_qty}) != position "
                        f"({cur_tqqq_shares}) — re-arming")
            cancel_open_tqqq_orders(api)
            manage_protective_stop(api, preserve_stop_price=prev_stop_price)
        else:
            floor = f" (stop @ ${prev_stop_price:.2f})" if prev_stop_price else ""
            logger.info(f"Keeping existing protective stop{floor} — unchanged")

    # Update state (skipped in dry-run — preview only)
    if not dry_run:
        # Persist a portfolio snapshot so the dashboard can show a live summary
        # (equity, cash, positions) without needing Alpaca credentials itself.
        update_portfolio_snapshot(api, state)
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
    args = sys.argv[1:]
    if any(a in ("--snapshot", "--snap") for a in args):
        write_snapshot()
    else:
        dry_run = any(a in ("--dry-run", "--dryrun", "-n") for a in args)
        run(dry_run=dry_run)
