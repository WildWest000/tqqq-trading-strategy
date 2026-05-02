"""
CLI entry point for the TQQQ/SQQQ Mean Reversion Trading Strategy.
Usage:
    python main.py download       - Download/update price data
    python main.py backtest       - Run backtest and print results
    python main.py forward        - Run one forward test iteration
    python main.py dashboard      - Launch the interactive dashboard
"""
import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    
    command = sys.argv[1].lower()
    
    def print_status(msg):
        print(f"  {msg}")
    
    if command == "download":
        from download_data import download_all
        print("Downloading data for all tickers...")
        data = download_all(status_callback=print_status)
        for ticker, df in data.items():
            if len(df) > 0:
                print(f"  {ticker}: {len(df)} rows ({df.index.min().date()} to {df.index.max().date()})")
            else:
                print(f"  {ticker}: No data")
    
    elif command == "backtest":
        from backtest import run_backtest
        start = sys.argv[2] if len(sys.argv) > 2 else None
        end = sys.argv[3] if len(sys.argv) > 3 else None
        
        print("Running backtest...")
        results = run_backtest(start=start, end=end, status_callback=print_status)
        
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
        
        print(f"\nTotal trades: {len(results['trades_df'])}")
    
    elif command == "forward":
        from forward_test import run_forward_test
        print("Running forward test...")
        state = run_forward_test(status_callback=print_status)
        if state["portfolio_history"]:
            latest = state["portfolio_history"][-1]
            print(f"\n  Position: {state['current_position']}")
            print(f"  Portfolio: ${latest['portfolio_value']:.2f}")
            print(f"  Trades: {len(state['trades'])}")
    
    elif command == "dashboard":
        from dashboard import run_dashboard
        print(f"Starting dashboard at http://127.0.0.1:8050 ...")
        run_dashboard()
    
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
