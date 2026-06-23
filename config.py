"""
Configuration for TQQQ/SQQQ Mean Reversion Trading Strategy.
All parameters are centralized here for easy tuning.
"""
import os
from datetime import datetime

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

# --- Intraday Stop-Loss (live bot only) ---
# After the bot buys TQQQ, it can place a resting stop order at the broker so a
# fast intraday crash is cut WITHOUT waiting for the next daily run. This is the
# only mechanism that can react within a single day (the daily backtest cannot
# model it). Set INTRADAY_STOP_ENABLED=False to disable.
INTRADAY_STOP_ENABLED = True
INTRADAY_TRAILING_STOP = True   # True = trailing stop (trails the high); False = fixed stop
INTRADAY_STOP_PCT = 0.08        # Stop distance below entry/high (e.g. 0.08 = 8%)

# --- Capital ---
STARTING_CAPITAL = 10_000
RISK_FREE_RATE = 0.045  # Annual risk-free rate (T-bill ~4.5%)

# --- Default Backtest Period (user overrides in dashboard) ---
DEFAULT_BACKTEST_START = "2020-01-01"
# End defaults to today so backtests always include the latest available data.
DEFAULT_BACKTEST_END = datetime.now().strftime("%Y-%m-%d")

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

# --- Short-Horizon Risk Overlays (mom_vol path) ---
# These act on the TQQQ allocation BEFORE the 1-day forward shift, so they are
# causal (no look-ahead). They aim to reduce 2-3 week tail risk / whipsaw.
# Defaults below are tuned via backtest; see README "Short-horizon overlays".
TQQQ_LEVERAGE = 3.0  # TQQQ tracks ~3x QQQ daily; used to estimate position vol

# Volatility targeting: scale exposure so annualized position vol ~= target.
# Caps exposure in high-vol regimes (reduces crash-window pain), allows full
# exposure in calm regimes. NOTE: backtests show this trims long-run return
# without improving Sharpe, so it ships OFF by default (opt-in tail control).
VOL_TARGET_ENABLED = False
VOL_TARGET_ANNUAL = 0.55  # Target annualized portfolio volatility (TQQQ-based)

# Hard exposure cap: maximum TQQQ allocation (1.0 = no cap). Lowering this
# reduces short-term tail risk at the cost of long-run upside.
MAX_TQQQ_ALLOC = 1.0

# Re-entry cooldown (whipsaw guard): after a sharp de-risk (alloc drops by
# REENTRY_DERISK_DROP in one step), hold the reduced exposure for N trading
# days before rebuilding. 0 = disabled.
REENTRY_COOLDOWN_DAYS = 0
REENTRY_DERISK_DROP = 0.20

# --- Dashboard ---
DASHBOARD_PORT = 8050
DASHBOARD_HOST = "0.0.0.0"
