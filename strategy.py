"""
Core strategy logic: regime-based dual allocation with TQQQ/SQQQ/cash.

Always invested — holds both TQQQ and SQQQ simultaneously with allocation
weights determined by market regime and tactical signals. Rebalances only
when allocation drift exceeds threshold.
"""
import pandas as pd
import numpy as np
import config


def run_strategy(signals_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Execute the dual-allocation strategy.
    
    Args:
        signals_df: Output from indicators.generate_signals() with
                    tqqq_alloc, sqqq_alloc, cash_alloc columns
    
    Returns:
        (portfolio_df, trades_df)
        - portfolio_df: Daily portfolio value with allocation breakdown
        - trades_df: Log of all rebalance trades
    """
    capital = config.STARTING_CAPITAL
    
    # Current holdings
    tqqq_shares = 0.0
    sqqq_shares = 0.0
    cash = capital
    
    # Track actual allocations
    prev_tqqq_alloc = 0.0
    prev_sqqq_alloc = 0.0
    
    # Portfolio-level trailing stop
    portfolio_peak = capital
    in_cash_mode = False  # True when trailing stop triggered
    cash_mode_days = 0
    TRAILING_STOP_PCT = 0.25  # Go to cash if portfolio drops 25% from peak
    CASH_MODE_COOLDOWN = 5  # Stay in cash for at least 5 days after stop
    
    portfolio_records = []
    trade_records = []
    first_day = True
    prev_portfolio_value = capital
    
    for date, row in signals_df.iterrows():
        tqqq_close = row["tqqq_close"]
        sqqq_close = row["sqqq_close"]
        tqqq_open = row.get("tqqq_open", tqqq_close)
        sqqq_open = row.get("sqqq_open", sqqq_close)
        target_tqqq_alloc = row["tqqq_alloc"]
        target_sqqq_alloc = row["sqqq_alloc"]
        signal = row["signal"]
        regime = row["regime"]
        
        # Portfolio value at open (for trade sizing)
        portfolio_value_at_open = (
            tqqq_shares * tqqq_open +
            sqqq_shares * sqqq_open +
            cash
        )
        
        # Current actual allocations (at open, before any trades)
        if portfolio_value_at_open > 0:
            actual_tqqq_alloc = (tqqq_shares * tqqq_open) / portfolio_value_at_open
            actual_sqqq_alloc = (sqqq_shares * sqqq_open) / portfolio_value_at_open
        else:
            actual_tqqq_alloc = 0
            actual_sqqq_alloc = 0
        
        # --- Portfolio Trailing Stop (based on open valuation) ---
        portfolio_peak = max(portfolio_peak, portfolio_value_at_open)
        drawdown_from_peak = (portfolio_value_at_open - portfolio_peak) / portfolio_peak
        
        if in_cash_mode:
            cash_mode_days += 1
            # Exit cash mode after cooldown AND regime is bull
            if cash_mode_days >= CASH_MODE_COOLDOWN and regime == "bull":
                in_cash_mode = False
                cash_mode_days = 0
                portfolio_peak = portfolio_value_at_open  # Reset peak on re-entry
            else:
                # Override: stay in cash
                target_tqqq_alloc = 0.0
                target_sqqq_alloc = 0.0
                signal = "trailing_stop_cash"
        elif drawdown_from_peak < -TRAILING_STOP_PCT and not first_day:
            # Trigger trailing stop
            in_cash_mode = True
            cash_mode_days = 0
            target_tqqq_alloc = 0.0
            target_sqqq_alloc = 0.0
            signal = "trailing_stop_triggered"
        
        # Check if rebalance needed
        tqqq_drift = abs(actual_tqqq_alloc - target_tqqq_alloc)
        sqqq_drift = abs(actual_sqqq_alloc - target_sqqq_alloc)
        needs_rebalance = (
            first_day or
            tqqq_drift > config.REBALANCE_THRESHOLD or
            sqqq_drift > config.REBALANCE_THRESHOLD
        )
        
        if needs_rebalance and portfolio_value_at_open > 0:
            # Calculate target dollar amounts (using open prices for execution)
            target_tqqq_dollars = portfolio_value_at_open * target_tqqq_alloc
            target_sqqq_dollars = portfolio_value_at_open * target_sqqq_alloc
            
            # Calculate share changes (whole shares only, executed at open)
            new_tqqq_shares = int(target_tqqq_dollars / tqqq_open) if tqqq_open > 0 else 0
            new_sqqq_shares = int(target_sqqq_dollars / sqqq_open) if sqqq_open > 0 else 0
            
            # Leftover from rounding goes to cash
            cash = portfolio_value_at_open - (new_tqqq_shares * tqqq_open + new_sqqq_shares * sqqq_open)
            
            tqqq_share_delta = new_tqqq_shares - tqqq_shares
            sqqq_share_delta = new_sqqq_shares - sqqq_shares
            
            # Gain/loss since last trade
            gain_loss = portfolio_value_at_open - prev_portfolio_value
            
            # Determine action label
            if first_day:
                action_label = "initial_buy"
            else:
                action_label = signal if signal != "hold" else "rebalance"
            
            # Log the trade
            trade_records.append({
                "date": date,
                "signal": action_label,
                "regime": regime,
                "tqqq_action": "buy" if tqqq_share_delta > 0 else ("sell" if tqqq_share_delta < 0 else "hold"),
                "tqqq_shares_delta": abs(tqqq_share_delta),
                "tqqq_price": tqqq_open,
                "tqqq_alloc_from": round(actual_tqqq_alloc * 100, 1),
                "tqqq_alloc_to": round(target_tqqq_alloc * 100, 1),
                "sqqq_action": "buy" if sqqq_share_delta > 0 else ("sell" if sqqq_share_delta < 0 else "hold"),
                "sqqq_shares_delta": abs(sqqq_share_delta),
                "sqqq_price": sqqq_open,
                "sqqq_alloc_from": round(actual_sqqq_alloc * 100, 1),
                "sqqq_alloc_to": round(target_sqqq_alloc * 100, 1),
                "cash_after": cash,
                "portfolio_value": portfolio_value_at_open,
                "gain_loss": gain_loss,
            })
            
            prev_portfolio_value = portfolio_value_at_open
            
            # Execute rebalance (shares updated, cash set above)
            tqqq_shares = new_tqqq_shares
            sqqq_shares = new_sqqq_shares
            
            prev_tqqq_alloc = target_tqqq_alloc
            prev_sqqq_alloc = target_sqqq_alloc
            first_day = False
        
        # End-of-day portfolio valuation (at close prices)
        portfolio_value_at_close = (
            tqqq_shares * tqqq_close +
            sqqq_shares * sqqq_close +
            cash
        )
        
        portfolio_records.append({
            "date": date,
            "portfolio_value": portfolio_value_at_close,
            "tqqq_value": tqqq_shares * tqqq_close,
            "sqqq_value": sqqq_shares * sqqq_close,
            "cash": cash,
            "tqqq_alloc": target_tqqq_alloc,
            "sqqq_alloc": target_sqqq_alloc,
            "regime": regime,
            "signal": signal,
        })
    
    portfolio_df = pd.DataFrame(portfolio_records).set_index("date")
    trades_df = pd.DataFrame(trade_records) if trade_records else pd.DataFrame(
        columns=["date", "signal", "regime", "tqqq_action", "tqqq_shares_delta",
                 "tqqq_price", "tqqq_alloc_from", "tqqq_alloc_to",
                 "sqqq_action", "sqqq_shares_delta", "sqqq_price",
                 "sqqq_alloc_from", "sqqq_alloc_to", "cash_after",
                 "portfolio_value", "gain_loss"]
    )
    
    return portfolio_df, trades_df
