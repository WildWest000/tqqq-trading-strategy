"""
Backtesting engine with performance metrics and buy-and-hold benchmark comparison.
"""
import pandas as pd
import numpy as np
import config
from download_data import ensure_data_available
from indicators import generate_signals
from strategy import run_strategy


def compute_metrics(portfolio_df: pd.DataFrame, starting_capital: float = None) -> dict:
    """Compute performance metrics from a portfolio value series."""
    if starting_capital is None:
        starting_capital = config.STARTING_CAPITAL
    
    values = portfolio_df["portfolio_value"]
    if len(values) < 2:
        return {}
    
    total_return = (values.iloc[-1] - starting_capital) / starting_capital
    
    # Annualized return
    days = (values.index[-1] - values.index[0]).days
    if days > 0:
        annualized_return = (1 + total_return) ** (365.25 / days) - 1
    else:
        annualized_return = 0
    
    # Max drawdown
    cummax = values.cummax()
    drawdown = (values - cummax) / cummax
    max_drawdown = drawdown.min()
    
    # Sharpe ratio (assuming 252 trading days)
    daily_returns = values.pct_change().dropna()
    if daily_returns.std() > 0:
        daily_rf = config.RISK_FREE_RATE / 252
        sharpe = ((daily_returns.mean() - daily_rf) / daily_returns.std()) * np.sqrt(252)
    else:
        sharpe = 0
    
    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
        "final_value": values.iloc[-1],
        "total_days": days,
    }


def compute_trade_metrics(trades_df: pd.DataFrame) -> dict:
    """Compute trade-specific metrics from rebalance log."""
    if len(trades_df) == 0:
        return {"num_trades": 0, "rebalances": 0, "bull_rebal": 0, "bear_rebal": 0, "defensive_rebal": 0}
    
    num = len(trades_df)
    bull = len(trades_df[trades_df["signal"] == "rebalance_bullish"])
    bear = len(trades_df[trades_df["signal"] == "rebalance_bearish"])
    defensive = len(trades_df[trades_df["signal"] == "rebalance_defensive"])
    
    return {
        "num_trades": num,
        "rebalances": num,
        "bull_rebal": bull,
        "bear_rebal": bear,
        "defensive_rebal": defensive,
    }


def compute_benchmark(tqqq_data: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    """Compute buy-and-hold TQQQ benchmark portfolio."""
    mask = (tqqq_data.index >= pd.Timestamp(start_date)) & (tqqq_data.index <= pd.Timestamp(end_date))
    prices = tqqq_data.loc[mask, "Close"]
    if len(prices) == 0:
        return pd.DataFrame(columns=["portfolio_value"])
    
    initial_price = prices.iloc[0]
    shares = config.STARTING_CAPITAL / initial_price
    benchmark_df = pd.DataFrame({
        "portfolio_value": prices * shares,
    }, index=prices.index)
    benchmark_df.index.name = "date"
    return benchmark_df


def run_backtest(start: str = None, end: str = None, status_callback=None) -> dict:
    """
    Run the full backtest pipeline.
    
    Args:
        start: Backtest start date (YYYY-MM-DD)
        end: Backtest end date (YYYY-MM-DD)
        status_callback: Progress callback for data downloads
    
    Returns:
        Dict with keys: portfolio_df, trades_df, signals_df, benchmark_df,
                       strategy_metrics, benchmark_metrics, trade_metrics
    """
    if start is None:
        start = config.DEFAULT_BACKTEST_START
    if end is None:
        end = config.DEFAULT_BACKTEST_END
    
    # Daily auto-update: extend cache to the latest bar (no-op if already done today)
    from download_data import refresh_latest
    try:
        refresh_latest(status_callback)
    except Exception as e:
        if status_callback:
            status_callback(f"Daily refresh skipped: {e}")
    
    # Ensure data is available
    if status_callback:
        status_callback("Ensuring data is available...")
    data = ensure_data_available(start, end, status_callback)
    
    tqqq = data["TQQQ"]
    sqqq = data["SQQQ"]
    qqq = data["QQQ"]
    
    if len(tqqq) == 0:
        raise ValueError("No TQQQ data available for the specified date range")
    
    # For HMM: load full historical QQQ data for training context
    qqq_full = None
    if config.REGIME_METHOD == "hmm":
        from download_data import load_cached_data
        qqq_full = load_cached_data("QQQ")
        if qqq_full is not None and len(qqq_full) > 0:
            # Use all cached data up to end date for training
            qqq_full = qqq_full[qqq_full.index <= pd.Timestamp(end)]
    
    # Generate signals
    if status_callback:
        status_callback("Generating signals...")
    signals_df = generate_signals(tqqq, sqqq, qqq, qqq_full=qqq_full)
    
    # Run strategy
    if status_callback:
        status_callback("Running strategy...")
    portfolio_df, trades_df = run_strategy(signals_df)
    
    # Compute benchmark
    benchmark_df = compute_benchmark(tqqq, start, end)
    
    # Compute metrics
    strategy_metrics = compute_metrics(portfolio_df)
    benchmark_metrics = compute_metrics(benchmark_df, config.STARTING_CAPITAL)
    trade_metrics = compute_trade_metrics(trades_df)
    
    return {
        "portfolio_df": portfolio_df,
        "trades_df": trades_df,
        "signals_df": signals_df,
        "benchmark_df": benchmark_df,
        "strategy_metrics": {**strategy_metrics, **trade_metrics},
        "benchmark_metrics": benchmark_metrics,
    }


if __name__ == "__main__":
    def print_status(msg):
        print(f"  {msg}")
    
    print("Running backtest...")
    results = run_backtest(status_callback=print_status)
    
    print("\n=== Strategy Metrics ===")
    for k, v in results["strategy_metrics"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")
    
    print("\n=== Benchmark (Buy & Hold TQQQ) ===")
    for k, v in results["benchmark_metrics"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")
    
    print(f"\nTrades: {len(results['trades_df'])}")
    if len(results["trades_df"]) > 0:
        print(results["trades_df"].to_string())
