"""
Tax-adjusted backtest: calculates realized gains, applies 30% tax rate,
and withdraws tax owed 2 trading days before each quarter end.

Compares: no-tax strategy vs tax-adjusted strategy vs buy-and-hold (also taxed).
"""
import pandas as pd
import numpy as np
import config
from backtest import run_backtest, compute_metrics


TAX_RATE = 0.30


def get_quarter_end_dates(trading_dates: pd.DatetimeIndex) -> list:
    """Find the date that is 2 trading days before each quarter end."""
    # Group by year-quarter
    quarters = trading_dates.to_period("Q")
    quarter_boundaries = []
    
    for q in quarters.unique():
        q_dates = trading_dates[quarters == q]
        if len(q_dates) >= 3:
            # 2 trading days before the last trading day of the quarter
            tax_date = q_dates[-3]
            quarter_boundaries.append(tax_date)
    
    return quarter_boundaries


def run_tax_adjusted_backtest(start: str = "2010-02-11", end: str = "2026-05-29"):
    """
    Run backtest with quarterly tax cashouts on realized gains.
    
    Logic:
    - Track cost basis for TQQQ and SQQQ positions
    - When shares are sold, compute realized gain/loss
    - Accumulate unrealized tax liability
    - 2 trading days before each quarter end, withdraw cash to pay taxes
    - Tax withdrawal reduces portfolio value (simulates sending money to IRS)
    """
    print(f"Running tax-adjusted backtest: {start} to {end}")
    print(f"Tax rate: {TAX_RATE*100:.0f}%")
    print("=" * 70)
    
    # Run the normal backtest first to get signals
    results = run_backtest(start=start, end=end, status_callback=lambda x: None)
    signals_df = results["signals_df"]
    
    # Now re-run strategy with tax tracking
    from strategy import run_strategy
    portfolio_df_notax, trades_df = run_strategy(signals_df)
    
    # --- Tax-adjusted simulation ---
    capital = config.STARTING_CAPITAL
    tqqq_shares = 0.0
    sqqq_shares = 0.0
    cash = capital
    
    # Cost basis tracking (average cost method)
    tqqq_cost_basis = 0.0  # Total cost of current TQQQ position
    sqqq_cost_basis = 0.0  # Total cost of current SQQQ position
    
    # Tax tracking — NET gains (losses offset gains, carry forward)
    cumulative_realized_gains = 0.0  # Running total of all realized gains
    cumulative_realized_losses = 0.0  # Running total of all realized losses
    taxes_paid_total = 0.0
    net_gains_this_quarter = 0.0  # Net gain/loss accumulated in current quarter
    loss_carryforward = 0.0  # Losses carried from prior quarters
    quarterly_tax_paid = []  # Log of quarterly payments
    current_quarter = None
    
    # Trailing stop state
    portfolio_peak = capital
    in_cash_mode = False
    cash_mode_days = 0
    TRAILING_STOP_PCT = 0.25
    CASH_MODE_COOLDOWN = 5
    
    # Get quarter tax dates
    trading_dates = signals_df.index
    tax_dates = set(get_quarter_end_dates(trading_dates))
    
    portfolio_records = []
    first_day = True
    prev_tqqq_alloc = 0.0
    prev_sqqq_alloc = 0.0
    
    for date, row in signals_df.iterrows():
        tqqq_close = row["tqqq_close"]
        sqqq_close = row["sqqq_close"]
        tqqq_open = row.get("tqqq_open", tqqq_close)
        sqqq_open = row.get("sqqq_open", sqqq_close)
        target_tqqq_alloc = row["tqqq_alloc"]
        target_sqqq_alloc = row["sqqq_alloc"]
        signal = row["signal"]
        regime = row["regime"]
        
        # Portfolio value at open
        portfolio_value_at_open = (
            tqqq_shares * tqqq_open +
            sqqq_shares * sqqq_open +
            cash
        )
        
        # Current allocations
        if portfolio_value_at_open > 0:
            actual_tqqq_alloc = (tqqq_shares * tqqq_open) / portfolio_value_at_open
            actual_sqqq_alloc = (sqqq_shares * sqqq_open) / portfolio_value_at_open
        else:
            actual_tqqq_alloc = 0
            actual_sqqq_alloc = 0
        
        # --- Trailing Stop ---
        portfolio_peak = max(portfolio_peak, portfolio_value_at_open)
        drawdown_from_peak = (portfolio_value_at_open - portfolio_peak) / portfolio_peak
        
        if in_cash_mode:
            cash_mode_days += 1
            if cash_mode_days >= CASH_MODE_COOLDOWN and regime == "bull":
                in_cash_mode = False
                cash_mode_days = 0
                portfolio_peak = portfolio_value_at_open
            else:
                target_tqqq_alloc = 0.0
                target_sqqq_alloc = 0.0
                signal = "trailing_stop_cash"
        elif drawdown_from_peak < -TRAILING_STOP_PCT and not first_day:
            in_cash_mode = True
            cash_mode_days = 0
            target_tqqq_alloc = 0.0
            target_sqqq_alloc = 0.0
            signal = "trailing_stop_triggered"
        
        # --- Quarterly Tax Withdrawal (before any trades) ---
        date_quarter = pd.Timestamp(date).quarter
        date_year = pd.Timestamp(date).year
        this_quarter_id = (date_year, date_quarter)
        
        if current_quarter is None:
            current_quarter = this_quarter_id
        
        if date in tax_dates:
            # End of quarter: compute tax on NET gains (gains - losses - carryforward)
            taxable_net = net_gains_this_quarter - loss_carryforward
            
            if taxable_net > 0:
                tax_payment = taxable_net * TAX_RATE
                loss_carryforward = 0  # Used up
            else:
                tax_payment = 0
                loss_carryforward = abs(taxable_net)  # Carry losses forward
            
            if tax_payment > 0:
                # Withdraw from cash first, then sell shares if needed
                if cash >= tax_payment:
                    cash -= tax_payment
                else:
                    shortfall = tax_payment - cash
                    cash = 0
                    # Sell TQQQ proportionally to cover
                    if tqqq_shares > 0 and tqqq_open > 0:
                        shares_to_sell = min(tqqq_shares, shortfall / tqqq_open)
                        if tqqq_shares > 0:
                            avg_cost = tqqq_cost_basis / tqqq_shares
                            gain_on_forced = shares_to_sell * (tqqq_open - avg_cost)
                            # This forced-sale gain goes into NEXT quarter
                            net_gains_this_quarter += gain_on_forced  
                            tqqq_cost_basis -= shares_to_sell * avg_cost
                        tqqq_shares -= shares_to_sell
                        shortfall -= shares_to_sell * tqqq_open
                    
                    if shortfall > 0 and sqqq_shares > 0 and sqqq_open > 0:
                        shares_to_sell = min(sqqq_shares, shortfall / sqqq_open)
                        if sqqq_shares > 0:
                            avg_cost = sqqq_cost_basis / sqqq_shares
                            gain_on_forced = shares_to_sell * (sqqq_open - avg_cost)
                            net_gains_this_quarter += gain_on_forced
                            sqqq_cost_basis -= shares_to_sell * avg_cost
                        sqqq_shares -= shares_to_sell
                
                taxes_paid_total += tax_payment
                quarterly_tax_paid.append({
                    "date": date,
                    "tax_paid": tax_payment,
                    "cumulative_taxes": taxes_paid_total,
                    "net_gains_quarter": net_gains_this_quarter,
                    "loss_carryforward": loss_carryforward,
                })
            
            # Reset for next quarter
            net_gains_this_quarter = 0
            current_quarter = this_quarter_id
            
            # Recalculate portfolio value after tax withdrawal
            portfolio_value_at_open = (
                tqqq_shares * tqqq_open +
                sqqq_shares * sqqq_open +
                cash
            )
        
        # --- Rebalance Check ---
        if portfolio_value_at_open > 0:
            actual_tqqq_alloc = (tqqq_shares * tqqq_open) / portfolio_value_at_open
            actual_sqqq_alloc = (sqqq_shares * sqqq_open) / portfolio_value_at_open
        
        tqqq_drift = abs(actual_tqqq_alloc - target_tqqq_alloc)
        sqqq_drift = abs(actual_sqqq_alloc - target_sqqq_alloc)
        needs_rebalance = (
            first_day or
            tqqq_drift > config.REBALANCE_THRESHOLD or
            sqqq_drift > config.REBALANCE_THRESHOLD
        )
        
        if needs_rebalance and portfolio_value_at_open > 0:
            # Calculate target shares
            target_tqqq_dollars = portfolio_value_at_open * target_tqqq_alloc
            target_sqqq_dollars = portfolio_value_at_open * target_sqqq_alloc
            
            new_tqqq_shares = int(target_tqqq_dollars / tqqq_open) if tqqq_open > 0 else 0
            new_sqqq_shares = int(target_sqqq_dollars / sqqq_open) if sqqq_open > 0 else 0
            
            # --- Compute realized gains from share changes ---
            tqqq_delta = new_tqqq_shares - tqqq_shares
            sqqq_delta = new_sqqq_shares - sqqq_shares
            
            # TQQQ realized gain (only on sells)
            if tqqq_delta < 0 and tqqq_shares > 0:
                shares_sold = abs(tqqq_delta)
                avg_cost_per_share = tqqq_cost_basis / tqqq_shares
                proceeds = shares_sold * tqqq_open
                cost_of_sold = shares_sold * avg_cost_per_share
                realized_gain = proceeds - cost_of_sold
                cumulative_realized_gains += realized_gain
                net_gains_this_quarter += realized_gain  # Net: gains AND losses
                # Update cost basis
                tqqq_cost_basis -= cost_of_sold
            
            # TQQQ buys increase cost basis
            if tqqq_delta > 0:
                tqqq_cost_basis += tqqq_delta * tqqq_open
            
            # SQQQ realized gain (only on sells)
            if sqqq_delta < 0 and sqqq_shares > 0:
                shares_sold = abs(sqqq_delta)
                avg_cost_per_share = sqqq_cost_basis / sqqq_shares
                proceeds = shares_sold * sqqq_open
                cost_of_sold = shares_sold * avg_cost_per_share
                realized_gain = proceeds - cost_of_sold
                cumulative_realized_gains += realized_gain
                net_gains_this_quarter += realized_gain  # Net: gains AND losses
                # Update cost basis
                sqqq_cost_basis -= cost_of_sold
            
            # SQQQ buys increase cost basis
            if sqqq_delta > 0:
                sqqq_cost_basis += sqqq_delta * sqqq_open
            
            # Update shares and cash
            cash = portfolio_value_at_open - (new_tqqq_shares * tqqq_open + new_sqqq_shares * sqqq_open)
            tqqq_shares = new_tqqq_shares
            sqqq_shares = new_sqqq_shares
            first_day = False
        
        # End-of-day valuation
        portfolio_value_at_close = (
            tqqq_shares * tqqq_close +
            sqqq_shares * sqqq_close +
            cash
        )
        
        portfolio_records.append({
            "date": date,
            "portfolio_value": portfolio_value_at_close,
            "net_gains_this_quarter": net_gains_this_quarter,
            "taxes_paid_cumulative": taxes_paid_total,
        })
    
    tax_portfolio_df = pd.DataFrame(portfolio_records).set_index("date")
    tax_payments_df = pd.DataFrame(quarterly_tax_paid) if quarterly_tax_paid else pd.DataFrame(columns=["date", "tax_paid", "cumulative_taxes"])
    
    return {
        "portfolio_notax": portfolio_df_notax,
        "portfolio_tax": tax_portfolio_df,
        "tax_payments": tax_payments_df,
        "benchmark_df": results["benchmark_df"],
        "taxes_paid_total": taxes_paid_total,
        "cumulative_realized_gains": cumulative_realized_gains,
    }


def print_yearly_performance(results: dict):
    """Print yearly breakdown comparing no-tax vs tax-adjusted."""
    notax = results["portfolio_notax"]["portfolio_value"]
    tax = results["portfolio_tax"]["portfolio_value"]
    bench = results["benchmark_df"]["portfolio_value"]
    
    # Align indices
    common_idx = notax.index.intersection(tax.index)
    
    print(f"\n{'='*90}")
    print(f"{'YEARLY PERFORMANCE: No-Tax vs Tax-Adjusted (30%) vs Buy & Hold':^90}")
    print(f"{'='*90}")
    print(f"{'Year':<6} {'Strategy':>12} {'After-Tax':>12} {'Tax Drag':>10} {'B&H':>12} {'Taxes Paid':>12}")
    print(f"{'-'*90}")
    
    years = sorted(set(common_idx.year))
    
    for year in years:
        # Strategy no-tax
        yr_notax = notax[notax.index.year == year]
        if len(yr_notax) < 2:
            continue
        notax_ret = (yr_notax.iloc[-1] / yr_notax.iloc[0] - 1) * 100
        
        # Strategy with tax
        yr_tax = tax[tax.index.year == year]
        if len(yr_tax) < 2:
            continue
        tax_ret = (yr_tax.iloc[-1] / yr_tax.iloc[0] - 1) * 100
        
        # Tax drag
        drag = notax_ret - tax_ret
        
        # Benchmark
        yr_bench = bench[bench.index.year == year]
        if len(yr_bench) >= 2:
            bench_ret = (yr_bench.iloc[-1] / yr_bench.iloc[0] - 1) * 100
        else:
            bench_ret = 0
        
        # Taxes paid this year
        tax_payments = results["tax_payments"]
        if len(tax_payments) > 0:
            yr_taxes = tax_payments[pd.to_datetime(tax_payments["date"]).dt.year == year]
            taxes_this_year = yr_taxes["tax_paid"].sum()
        else:
            taxes_this_year = 0
        
        print(f"{year:<6} {notax_ret:>+11.1f}% {tax_ret:>+11.1f}% {drag:>+9.1f}% {bench_ret:>+11.1f}% ${taxes_this_year:>10,.0f}")
    
    # Totals
    print(f"{'-'*90}")
    total_notax = (notax.iloc[-1] / config.STARTING_CAPITAL - 1) * 100
    total_tax = (tax.iloc[-1] / config.STARTING_CAPITAL - 1) * 100
    total_bench = (bench.iloc[-1] / config.STARTING_CAPITAL - 1) * 100
    total_drag = total_notax - total_tax
    
    print(f"{'TOTAL':<6} {total_notax:>+11.1f}% {total_tax:>+11.1f}% {total_drag:>+9.1f}% {total_bench:>+11.1f}% ${results['taxes_paid_total']:>10,.0f}")
    
    print(f"\n{'='*90}")
    print(f"SUMMARY")
    print(f"{'='*90}")
    print(f"  Starting Capital:       ${config.STARTING_CAPITAL:,.0f}")
    print(f"  Final Value (no tax):   ${notax.iloc[-1]:,.0f}")
    print(f"  Final Value (after tax): ${tax.iloc[-1]:,.0f}")
    print(f"  Buy & Hold Final:       ${bench.iloc[-1]:,.0f}")
    print(f"  Total Taxes Paid:       ${results['taxes_paid_total']:,.0f}")
    print(f"  Total Realized Gains:   ${results['cumulative_realized_gains']:,.0f}")
    print(f"  Effective Tax Rate:     {(results['taxes_paid_total']/max(results['cumulative_realized_gains'],1))*100:.1f}%")
    
    # Compute metrics for tax-adjusted
    tax_metrics = compute_metrics(results["portfolio_tax"])
    notax_metrics = compute_metrics(results["portfolio_notax"])
    
    print(f"\n  {'Metric':<25} {'No-Tax':>12} {'After-Tax':>12}")
    print(f"  {'-'*50}")
    print(f"  {'Sharpe Ratio':<25} {notax_metrics.get('sharpe_ratio',0):>12.2f} {tax_metrics.get('sharpe_ratio',0):>12.2f}")
    print(f"  {'Annualized Return':<25} {notax_metrics.get('annualized_return',0)*100:>11.1f}% {tax_metrics.get('annualized_return',0)*100:>11.1f}%")
    print(f"  {'Max Drawdown':<25} {notax_metrics.get('max_drawdown',0)*100:>11.1f}% {tax_metrics.get('max_drawdown',0)*100:>11.1f}%")


if __name__ == "__main__":
    results = run_tax_adjusted_backtest(start="2010-02-11", end="2026-05-29")
    print_yearly_performance(results)
