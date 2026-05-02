"""
Technical indicators and signal generation for the regime-based dual-allocation strategy.

Approach:
- Regime layer (QQQ trend + volatility) sets base TQQQ/SQQQ allocation
- Tactical layer (RSI + price/EMA distance) adjusts allocation within regime bounds
- SQQQ used only in bearish regimes; otherwise TQQQ + cash
"""
import pandas as pd
import numpy as np
import config


def compute_rsi(series: pd.Series, period: int = None) -> pd.Series:
    """Compute RSI (Relative Strength Index)."""
    if period is None:
        period = config.RSI_PERIOD
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Compute Average True Range for volatility measurement."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def generate_signals(tqqq: pd.DataFrame, sqqq: pd.DataFrame, qqq: pd.DataFrame) -> pd.DataFrame:
    """
    Generate allocation signals using a regime-based dual-allocation approach.
    
    Returns a DataFrame with:
    - Prices, indicators
    - tqqq_alloc: target TQQQ allocation (0.0 to 1.0)
    - sqqq_alloc: target SQQQ allocation (0.0 to 1.0)
    - signal: human-readable signal description
    - regime: 'bull', 'bear', or 'neutral'
    """
    df = pd.DataFrame(index=tqqq.index)
    df["tqqq_close"] = tqqq["Close"]
    df["tqqq_open"] = tqqq["Open"] if "Open" in tqqq.columns else tqqq["Close"]
    df["sqqq_close"] = sqqq["Close"].reindex(df.index)
    df["sqqq_open"] = (sqqq["Open"] if "Open" in sqqq.columns else sqqq["Close"]).reindex(df.index)
    df["qqq_close"] = qqq["Close"].reindex(df.index)
    
    # --- Core Indicators ---
    df["rsi"] = compute_rsi(df["tqqq_close"])
    df["ema_short"] = compute_ema(df["tqqq_close"], config.EMA_SHORT)
    df["qqq_ema_trend"] = compute_ema(df["qqq_close"], config.EMA_TREND)
    df["qqq_ema_short"] = compute_ema(df["qqq_close"], config.EMA_SHORT)
    
    # Volatility via ATR on QQQ (normalized as % of price)
    if "High" in qqq.columns and "Low" in qqq.columns:
        qqq_atr = compute_atr(
            qqq["High"].reindex(df.index),
            qqq["Low"].reindex(df.index),
            df["qqq_close"]
        )
        df["volatility"] = qqq_atr / df["qqq_close"]
    else:
        # Fallback: use rolling std of returns
        df["volatility"] = df["qqq_close"].pct_change().rolling(14).std()
    
    # QQQ trend metrics
    df["qqq_uptrend"] = df["qqq_close"] > df["qqq_ema_trend"]
    df["qqq_above_short_ema"] = df["qqq_close"] > df["qqq_ema_short"]
    df["qqq_trend_strength"] = (df["qqq_close"] - df["qqq_ema_trend"]) / df["qqq_ema_trend"]
    
    # Price distance from EMA (tactical signal)
    df["price_ema_dist"] = (df["tqqq_close"] - df["ema_short"]) / df["ema_short"]
    
    # Momentum: 10-day rate of change on QQQ
    df["qqq_momentum"] = df["qqq_close"].pct_change(10)
    
    # Median volatility for regime classification
    vol_median = df["volatility"].expanding().median()
    df["high_vol"] = df["volatility"] > vol_median * 1.5
    
    # --- Regime Classification ---
    # Bull: QQQ above 50-EMA OR (above 20-EMA with positive momentum)
    # Bear: QQQ below both EMAs and negative momentum  
    # Crisis: Bear + high volatility (aggressive hedging needed)
    # Neutral: mixed signals
    df["regime"] = "neutral"
    bull_mask = df["qqq_uptrend"] | (df["qqq_above_short_ema"] & (df["qqq_momentum"] > 0))
    bear_mask = ~df["qqq_uptrend"] & ~df["qqq_above_short_ema"] & (df["qqq_momentum"] < 0)
    crisis_mask = bear_mask & df["high_vol"]
    
    df.loc[bull_mask, "regime"] = "bull"
    df.loc[bear_mask & ~crisis_mask, "regime"] = "bear"
    df.loc[crisis_mask, "regime"] = "crisis"
    
    # --- Drawdown Detection ---
    # Track if QQQ has dropped >8% from its 20-day high (rapid decline signal)
    qqq_20d_high = df["qqq_close"].rolling(20).max()
    df["qqq_drawdown"] = (df["qqq_close"] - qqq_20d_high) / qqq_20d_high
    rapid_decline = df["qqq_drawdown"] < -0.08
    
    # --- Base Allocation by Regime ---
    # Bull: 100% TQQQ (ride the trend fully)
    # Neutral: 70% TQQQ, 0% SQQQ, 30% cash
    # Bear: 0% TQQQ, 0% SQQQ, 100% cash (SQQQ decays - cash is safer)
    # Crisis: 0% TQQQ, 20% SQQQ, 80% cash (small SQQQ only in sharp drops)
    df["tqqq_alloc"] = 0.70  # neutral default
    df["sqqq_alloc"] = 0.0
    
    df.loc[df["regime"] == "bull", "tqqq_alloc"] = 1.0
    df.loc[df["regime"] == "bull", "sqqq_alloc"] = 0.0
    
    df.loc[df["regime"] == "bear", "tqqq_alloc"] = 0.0
    df.loc[df["regime"] == "bear", "sqqq_alloc"] = 0.0
    
    df.loc[df["regime"] == "crisis", "tqqq_alloc"] = 0.0
    df.loc[df["regime"] == "crisis", "sqqq_alloc"] = 0.20
    
    # Rapid decline override: go mostly cash with small SQQQ hedge
    df.loc[rapid_decline & (df["regime"] != "crisis"), "tqqq_alloc"] = 0.0
    df.loc[rapid_decline & (df["regime"] != "crisis"), "sqqq_alloc"] = 0.15
    
    # --- Tactical Adjustments ---
    rsi = df["rsi"]
    
    # Bull + RSI oversold → 100% TQQQ (aggressive dip buy)
    oversold_bull = (df["regime"] == "bull") & (rsi < config.RSI_OVERSOLD)
    df.loc[oversold_bull, "tqqq_alloc"] = 1.0
    df.loc[oversold_bull, "sqqq_alloc"] = 0.0
    
    # Bull + RSI overbought → hedge: reduce TQQQ, add SQQQ hedge
    overbought_bull = (df["regime"] == "bull") & (rsi > config.RSI_OVERBOUGHT)
    df.loc[overbought_bull, "tqqq_alloc"] = 0.75
    df.loc[overbought_bull, "sqqq_alloc"] = 0.15
    
    # Bull + RSI very overbought (>80) → stronger hedge
    very_overbought = (df["regime"] == "bull") & (rsi > 80)
    df.loc[very_overbought, "tqqq_alloc"] = 0.60
    df.loc[very_overbought, "sqqq_alloc"] = 0.25
    
    # Bear + RSI > 55 → possible bounce, reduce SQQQ, go more cash
    recovering_bear = (df["regime"] == "bear") & (rsi > 55)
    df.loc[recovering_bear, "sqqq_alloc"] = 0.20
    df.loc[recovering_bear, "tqqq_alloc"] = 0.20
    
    # Neutral + high vol → defensive: mostly cash
    neutral_highvol = (df["regime"] == "neutral") & df["high_vol"]
    df.loc[neutral_highvol, "tqqq_alloc"] = 0.30
    df.loc[neutral_highvol, "sqqq_alloc"] = 0.20
    
    # Clamp allocations
    df["tqqq_alloc"] = df["tqqq_alloc"].clip(0.0, 1.0)
    df["sqqq_alloc"] = df["sqqq_alloc"].clip(0.0, 0.80)
    # Ensure total allocation <= 1.0
    total = df["tqqq_alloc"] + df["sqqq_alloc"]
    over = total > 1.0
    if over.any():
        scale = 1.0 / total[over]
        df.loc[over, "tqqq_alloc"] *= scale
        df.loc[over, "sqqq_alloc"] *= scale
    
    df["cash_alloc"] = 1.0 - df["tqqq_alloc"] - df["sqqq_alloc"]
    
    # --- Shift Signals Forward by 1 Day ---
    # Signals are computed from today's close but can only be ACTED ON next trading day.
    # This eliminates look-ahead bias: we decide after close, execute at next day's open.
    for col in ["tqqq_alloc", "sqqq_alloc", "cash_alloc", "regime"]:
        df[col] = df[col].shift(1)
    
    # --- Signal Labels ---
    df["signal"] = "hold"
    prev_tqqq = df["tqqq_alloc"].shift(1)
    prev_sqqq = df["sqqq_alloc"].shift(1)
    
    rebal_threshold = config.REBALANCE_THRESHOLD
    tqqq_change = (df["tqqq_alloc"] - prev_tqqq).abs()
    sqqq_change = (df["sqqq_alloc"] - prev_sqqq).abs()
    needs_rebalance = (tqqq_change > rebal_threshold) | (sqqq_change > rebal_threshold)
    
    # Label the rebalance direction
    more_tqqq = needs_rebalance & (df["tqqq_alloc"] > prev_tqqq + rebal_threshold)
    more_sqqq = needs_rebalance & (df["sqqq_alloc"] > prev_sqqq + rebal_threshold)
    less_risk = needs_rebalance & ~more_tqqq & ~more_sqqq
    
    df.loc[more_tqqq, "signal"] = "rebalance_bullish"
    df.loc[more_sqqq, "signal"] = "rebalance_bearish"
    df.loc[less_risk, "signal"] = "rebalance_defensive"
    
    return df.dropna()
