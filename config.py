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

# --- Regime Detection Method ---
REGIME_METHOD = "mom_vol"  # "rules", "hmm", or "mom_vol"

# --- HMM Parameters ---
HMM_N_STATES = 2
HMM_COVARIANCE_TYPE = "diag"
HMM_MIN_TRAIN_DAYS = 252  # ~1 year minimum before first prediction
HMM_RETRAIN_FREQUENCY = 21  # Retrain every ~1 month (trading days)
HMM_RANDOM_STATE = 42
HMM_N_ITER = 1000
HMM_PROB_THRESHOLD = 0.6  # Min probability to assign a regime (else use weighted)

# --- Momentum + Vol-Scaled Parameters ---
MOM_LOOKBACK = 20  # Days for momentum signal
VOL_LOOKBACK = 20  # Days for volatility calculation
VOL_FLOOR = 1.0  # Vol ratio below this = full allocation
VOL_CEILING = 2.5  # Vol ratio above this = zero allocation
MOM_NEGATIVE_SCALE = 0.3  # Allocation multiplier when momentum is negative
RSI_DIP_BUY_THRESHOLD = 30  # RSI below this in bear = dip-buy override
RSI_DIP_BUY_ALLOC = 0.8  # Allocation during dip-buy override

# --- Dashboard ---
DASHBOARD_PORT = 8050
DASHBOARD_HOST = "127.0.0.1"
