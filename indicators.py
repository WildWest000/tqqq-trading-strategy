"""
Technical indicators and signal generation for the regime-based dual-allocation strategy.

Approach:
- Regime layer (QQQ trend + volatility) sets base TQQQ/SQQQ allocation
- Tactical layer (RSI + price/EMA distance) adjusts allocation within regime bounds
- SQQQ used only in bearish regimes; otherwise TQQQ + cash

Supports two regime detection methods (config.REGIME_METHOD):
- "rules": Original rule-based (EMA crossovers + momentum + ATR)
- "hmm": Hidden Markov Model with expanding-window training
"""
import pandas as pd
import numpy as np
import warnings
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


def classify_regimes_hmm(qqq_close: pd.Series, qqq_high: pd.Series = None, qqq_low: pd.Series = None) -> pd.DataFrame:
    """
    Classify market regimes using a Hidden Markov Model with expanding-window training.
    
    Features: daily returns, 14-day rolling volatility, 10-day momentum.
    Retrains every HMM_RETRAIN_FREQUENCY days using only past data (no look-ahead).
    
    Returns DataFrame with 'regime' column and probability columns for each regime.
    """
    from hmmlearn.hmm import GaussianHMM
    from sklearn.preprocessing import StandardScaler
    
    # Compute features from QQQ
    returns = qqq_close.pct_change()
    volatility = returns.rolling(14).std()
    momentum = qqq_close.pct_change(10)
    
    features_df = pd.DataFrame({
        "returns": returns,
        "volatility": volatility,
        "momentum": momentum,
    }, index=qqq_close.index).dropna()
    
    n = len(features_df)
    min_train = config.HMM_MIN_TRAIN_DAYS
    retrain_freq = config.HMM_RETRAIN_FREQUENCY
    n_states = config.HMM_N_STATES
    
    # Output arrays
    regimes = pd.Series("neutral", index=features_df.index)
    regime_probs = pd.DataFrame(0.0, index=features_df.index, 
                                columns=["p_bull", "p_neutral", "p_bear", "p_crisis"])
    
    if n < min_train:
        # Not enough data — return neutral for everything
        return pd.DataFrame({"regime": regimes, **regime_probs}, index=features_df.index)
    
    # Walk forward with periodic retraining
    model = None
    scaler = None
    state_mapping = None
    last_train_idx = 0
    
    for i in range(min_train, n):
        # Retrain periodically
        if model is None or (i - last_train_idx) >= retrain_freq:
            train_data = features_df.iloc[:i].values
            
            # Scale features using only training data
            scaler = StandardScaler()
            train_scaled = scaler.fit_transform(train_data)
            
            # Fit HMM
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = GaussianHMM(
                    n_components=n_states,
                    covariance_type=config.HMM_COVARIANCE_TYPE,
                    n_iter=config.HMM_N_ITER,
                    random_state=config.HMM_RANDOM_STATE,
                )
                model.fit(train_scaled)
            
            # Map states to regimes using training data statistics
            train_states = model.predict(train_scaled)
            state_mapping = _map_states_to_regimes(
                train_states, features_df.iloc[:i], n_states
            )
            last_train_idx = i
        
        # Predict current day's regime
        current_features = features_df.iloc[i:i+1].values
        current_scaled = scaler.transform(current_features)
        
        state = model.predict(current_scaled)[0]
        probs = model.predict_proba(current_scaled)[0]
        
        date = features_df.index[i]
        regimes.iloc[i] = state_mapping[state]
        
        # Map probabilities to regime names
        for s, regime_name in state_mapping.items():
            prob_col = f"p_{regime_name}"
            if prob_col in regime_probs.columns:
                regime_probs.loc[date, prob_col] += probs[s]
    
    return pd.DataFrame({"regime": regimes, **regime_probs}, index=features_df.index)


def _map_states_to_regimes(states: np.ndarray, features_df: pd.DataFrame, n_states: int) -> dict:
    """
    Map HMM states to regime labels using mean return AND volatility.
    
    For 3 states: bull (high return), bear (low return + high vol), neutral (middle)
    For 4 states: adds crisis (lowest return + highest vol)
    """
    state_stats = {}
    for s in range(n_states):
        mask = states == s
        if mask.sum() == 0:
            state_stats[s] = {"mean_ret": 0, "mean_vol": 0}
            continue
        rows = features_df.iloc[np.where(mask)[0]]
        state_stats[s] = {
            "mean_ret": rows["returns"].mean(),
            "mean_vol": rows["volatility"].mean(),
        }
    
    # Sort states by mean return (descending)
    sorted_by_return = sorted(state_stats.keys(), key=lambda s: state_stats[s]["mean_ret"], reverse=True)
    
    if n_states == 3:
        # Simple: best return = bull, worst = bear, middle = neutral
        mapping = {
            sorted_by_return[0]: "bull",
            sorted_by_return[1]: "neutral",
            sorted_by_return[2]: "bear",
        }
    elif n_states == 4:
        # Among the two worst-return states, highest vol = crisis
        bottom_two = sorted_by_return[2:]
        crisis_state = max(bottom_two, key=lambda s: state_stats[s]["mean_vol"])
        bear_state = [s for s in bottom_two if s != crisis_state][0]
        mapping = {
            sorted_by_return[0]: "bull",
            sorted_by_return[1]: "neutral",
            bear_state: "bear",
            crisis_state: "crisis",
        }
    else:
        # Fallback: best = bull, worst = bear, rest = neutral
        mapping = {}
        mapping[sorted_by_return[0]] = "bull"
        mapping[sorted_by_return[-1]] = "bear"
        for s in sorted_by_return[1:-1]:
            mapping[s] = "neutral"
    
    return mapping


def generate_signals(tqqq: pd.DataFrame, sqqq: pd.DataFrame, qqq: pd.DataFrame, qqq_full: pd.DataFrame = None) -> pd.DataFrame:
    """
    Generate allocation signals using a regime-based dual-allocation approach.
    
    Args:
        tqqq, sqqq, qqq: Price data for the backtest period
        qqq_full: Full historical QQQ data for HMM training (optional, used when REGIME_METHOD="hmm")
    
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
    if config.REGIME_METHOD == "mom_vol":
        # Momentum + Vol-Scaled: smooth allocation based on momentum gate and volatility scaling
        mom = df["qqq_close"].pct_change(config.MOM_LOOKBACK)
        vol = df["qqq_close"].pct_change().rolling(config.VOL_LOOKBACK).std()
        vol_med = vol.expanding().median()
        vol_ratio = vol / vol_med
        
        # Vol-scaled allocation: 100% at vol<=floor, linearly to 0% at vol>=ceiling
        vol_alloc = ((config.VOL_CEILING - vol_ratio.clip(config.VOL_FLOOR, config.VOL_CEILING)) 
                     / (config.VOL_CEILING - config.VOL_FLOOR)).clip(0.0, 1.0)
        
        # Momentum positive: full vol-scaled; negative: reduced (soft exit)
        alloc = pd.Series(
            np.where(mom > 0, vol_alloc, config.MOM_NEGATIVE_SCALE * vol_alloc),
            index=df.index
        )
        
        # RSI dip-buy: when momentum is negative but TQQQ is oversold, aggressively buy the dip
        oversold_bear = (mom <= 0) & (df["rsi"] < config.RSI_DIP_BUY_THRESHOLD)
        alloc[oversold_bear] = config.RSI_DIP_BUY_ALLOC
        
        df["tqqq_alloc"] = alloc
        df["sqqq_alloc"] = 0.0
        df["regime"] = np.where(mom > 0, "bull", "bear")
        
        # Shift signals forward 1 day (no look-ahead)
        for col in ["tqqq_alloc", "sqqq_alloc", "regime"]:
            df[col] = df[col].shift(1)
        
        df["cash_alloc"] = 1.0 - df["tqqq_alloc"] - df["sqqq_alloc"]
        
        # Signal labels
        df["signal"] = "hold"
        prev_tqqq = df["tqqq_alloc"].shift(1)
        tqqq_change = (df["tqqq_alloc"] - prev_tqqq).abs()
        needs_rebalance = tqqq_change > config.REBALANCE_THRESHOLD
        df.loc[needs_rebalance & (df["tqqq_alloc"] > prev_tqqq), "signal"] = "rebalance_bullish"
        df.loc[needs_rebalance & (df["tqqq_alloc"] < prev_tqqq), "signal"] = "rebalance_defensive"
        
        return df.dropna()
    
    elif config.REGIME_METHOD == "hmm":
        # HMM-based regime detection (expanding window, no look-ahead)
        # Use full historical QQQ data for training if available
        hmm_qqq_close = qqq_full["Close"] if qqq_full is not None else df["qqq_close"]
        hmm_result = classify_regimes_hmm(hmm_qqq_close)
        
        # Align HMM results with our backtest DataFrame (only keep backtest period)
        hmm_aligned = hmm_result.reindex(df.index)
        df["regime"] = hmm_aligned["regime"].fillna("neutral")
        
        # Use probability-weighted allocations if below threshold
        if "p_bull" in hmm_result.columns:
            for col in ["p_bull", "p_neutral", "p_bear", "p_crisis"]:
                df[col] = 0.0
                if col in hmm_aligned.columns:
                    df[col] = hmm_aligned[col].fillna(0.0)
    else:
        # Original rule-based regime classification
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
    
    if config.REGIME_METHOD == "hmm" and "p_bull" in df.columns:
        # Probability-weighted allocations for smoother transitions
        p_bull = df.get("p_bull", 0.0)
        p_neutral = df.get("p_neutral", 0.0)
        p_bear = df.get("p_bear", 0.0)
        p_crisis = df.get("p_crisis", 0.0)
        
        df["tqqq_alloc"] = (
            p_bull * 1.0 +
            p_neutral * 0.70 +
            p_bear * 0.0 +
            p_crisis * 0.0
        )
        df["sqqq_alloc"] = (
            p_bull * 0.0 +
            p_neutral * 0.0 +
            p_bear * 0.0 +
            p_crisis * 0.20
        )
        # If dominant regime probability exceeds threshold, use hard allocation
        prob_cols = [c for c in ["p_bull", "p_neutral", "p_bear", "p_crisis"] if c in df.columns]
        if prob_cols:
            max_prob = df[prob_cols].max(axis=1)
            hard_regime = max_prob >= config.HMM_PROB_THRESHOLD
            df.loc[hard_regime & (df["regime"] == "bull"), "tqqq_alloc"] = 1.0
            df.loc[hard_regime & (df["regime"] == "bull"), "sqqq_alloc"] = 0.0
            df.loc[hard_regime & (df["regime"] == "bear"), "tqqq_alloc"] = 0.0
            df.loc[hard_regime & (df["regime"] == "bear"), "sqqq_alloc"] = 0.0
            df.loc[hard_regime & (df["regime"] == "crisis"), "tqqq_alloc"] = 0.0
            df.loc[hard_regime & (df["regime"] == "crisis"), "sqqq_alloc"] = 0.20
            df.loc[hard_regime & (df["regime"] == "neutral"), "tqqq_alloc"] = 0.70
            df.loc[hard_regime & (df["regime"] == "neutral"), "sqqq_alloc"] = 0.0
    else:
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
