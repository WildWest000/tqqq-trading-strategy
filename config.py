"""
Configuration for TQQQ/SQQQ Mean Reversion Trading Strategy.
All parameters are centralized here for easy tuning.
"""
import os

# --- Tickers ---
TICKERS = ["TQQQ", "SQQQ", "QQQ"]

# --- Indicator Parameters ---
RSI_PERIOD = 14
EMA_SHORT = 20
EMA_TREND = 50  # QQQ trend filter

# --- Signal Thresholds ---
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_NEUTRAL = 50

# --- Risk Management ---
STOP_LOSS_PCT = 0.03
TAKE_PROFIT_PCT = 0.10
REBALANCE_THRESHOLD = 0.10  # Only rebalance when allocation drifts by >10%

# --- Capital ---
STARTING_CAPITAL = 10_000
RISK_FREE_RATE = 0.045  # Annual risk-free rate (T-bill ~4.5%)

# --- Default Backtest Period (user overrides in dashboard) ---
DEFAULT_BACKTEST_START = "2020-01-01"
DEFAULT_BACKTEST_END = "2026-05-01"

# --- Data Download Settings ---
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DOWNLOAD_CHUNK_MONTHS = 6
DOWNLOAD_DELAY_SEC = 2

# --- Dashboard ---
DASHBOARD_PORT = 8050
DASHBOARD_HOST = "127.0.0.1"
