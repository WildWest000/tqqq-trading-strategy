"""
Forward testing (paper trading) mode.
Simulates live trading by downloading latest data and applying the strategy.
Persists state in a JSON file across runs.
"""
import json
import os
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import config
from download_data import download_ticker
from indicators import generate_signals


FORWARD_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forward_state.json")


def load_state() -> dict:
    """Load forward test state from JSON."""
    if os.path.exists(FORWARD_STATE_FILE):
        with open(FORWARD_STATE_FILE, "r") as f:
            return json.load(f)
    return {
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "starting_capital": config.STARTING_CAPITAL,
        "trades": [],
        "portfolio_history": [],
        "tqqq_shares": 0,
        "sqqq_shares": 0,
        "cash": config.STARTING_CAPITAL,
    }


def save_state(state: dict):
    """Save forward test state to JSON."""
    with open(FORWARD_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def run_forward_test(status_callback=None) -> dict:
    """
    Run one iteration of forward testing with dual-allocation strategy.
    """
    state = load_state()
    
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    
    if status_callback:
        status_callback("Forward test: downloading latest data...")
    
    tqqq = download_ticker("TQQQ", start_date, end_date, status_callback)
    sqqq = download_ticker("SQQQ", start_date, end_date, status_callback)
    qqq = download_ticker("QQQ", start_date, end_date, status_callback)
    
    if len(tqqq) == 0:
        if status_callback:
            status_callback("Forward test: no data available")
        return state
    
    signals_df = generate_signals(tqqq, sqqq, qqq)
    if len(signals_df) == 0:
        return state
    
    latest = signals_df.iloc[-1]
    today = signals_df.index[-1].strftime("%Y-%m-%d")
    
    if state["portfolio_history"] and state["portfolio_history"][-1].get("date") == today:
        if status_callback:
            status_callback(f"Forward test: already processed {today}")
        return state
    
    tqqq_price = float(latest["tqqq_close"])
    sqqq_price = float(latest["sqqq_close"])
    target_tqqq_alloc = float(latest["tqqq_alloc"])
    target_sqqq_alloc = float(latest["sqqq_alloc"])
    regime = latest["regime"]
    signal = latest["signal"]
    rsi = float(latest["rsi"])
    
    tqqq_shares = state["tqqq_shares"]
    sqqq_shares = state["sqqq_shares"]
    cash = state["cash"]
    
    portfolio_value = tqqq_shares * tqqq_price + sqqq_shares * sqqq_price + cash
    
    # Rebalance
    actual_tqqq_alloc = (tqqq_shares * tqqq_price) / portfolio_value if portfolio_value > 0 else 0
    actual_sqqq_alloc = (sqqq_shares * sqqq_price) / portfolio_value if portfolio_value > 0 else 0
    
    action = "hold"
    if (abs(actual_tqqq_alloc - target_tqqq_alloc) > config.REBALANCE_THRESHOLD or
        abs(actual_sqqq_alloc - target_sqqq_alloc) > config.REBALANCE_THRESHOLD or
        not state["portfolio_history"]):
        
        target_tqqq_val = portfolio_value * target_tqqq_alloc
        target_sqqq_val = portfolio_value * target_sqqq_alloc
        tqqq_shares = int(target_tqqq_val / tqqq_price) if tqqq_price > 0 else 0
        sqqq_shares = int(target_sqqq_val / sqqq_price) if sqqq_price > 0 else 0
        cash = portfolio_value - (tqqq_shares * tqqq_price + sqqq_shares * sqqq_price)
        action = signal if signal != "hold" else "rebalance"
        
        state["trades"].append({
            "date": today, "action": action,
            "tqqq_alloc": f"{target_tqqq_alloc*100:.0f}%",
            "sqqq_alloc": f"{target_sqqq_alloc*100:.0f}%",
            "regime": regime,
            "portfolio_value": round(portfolio_value, 2),
        })
    
    state["tqqq_shares"] = tqqq_shares
    state["sqqq_shares"] = sqqq_shares
    state["cash"] = cash
    state["portfolio_history"].append({
        "date": today,
        "portfolio_value": round(portfolio_value, 2),
        "regime": regime,
        "action": action,
        "rsi": round(rsi, 1),
        "tqqq_alloc": f"{target_tqqq_alloc*100:.0f}%",
        "sqqq_alloc": f"{target_sqqq_alloc*100:.0f}%",
    })
    
    save_state(state)
    
    if status_callback:
        status_callback(
            f"Forward test: {today} | {action} | Regime: {regime} | "
            f"TQQQ: {target_tqqq_alloc*100:.0f}% SQQQ: {target_sqqq_alloc*100:.0f}% | "
            f"Value: ${portfolio_value:.2f} | RSI: {rsi:.1f}"
        )
    
    return state


if __name__ == "__main__":
    def print_status(msg):
        print(f"  {msg}")
    
    print("Running forward test iteration...")
    state = run_forward_test(status_callback=print_status)
    if state["portfolio_history"]:
        latest = state["portfolio_history"][-1]
        print(f"\nPortfolio: ${latest['portfolio_value']}")
        print(f"Regime: {latest['regime']}")
        print(f"TQQQ: {latest['tqqq_alloc']} | SQQQ: {latest['sqqq_alloc']}")
    print(f"Total rebalances: {len(state['trades'])}")
