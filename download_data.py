"""
Data downloader with chunked downloads, local CSV caching, incremental updates,
and status callbacks for the dashboard.
"""
import os
import time
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd
import yfinance as yf
import config


def get_cache_path(ticker: str) -> str:
    """Get the CSV cache file path for a ticker."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    return os.path.join(config.DATA_DIR, f"{ticker}.csv")


# Tracks the last calendar date on which a "refresh to latest" was performed,
# so daily auto-updates hit the network at most once per day.
LAST_UPDATE_FILE = os.path.join(config.DATA_DIR, ".last_update.json")


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_last_update() -> str | None:
    """Return the date (YYYY-MM-DD) of the last successful refresh_latest, or None."""
    if not os.path.exists(LAST_UPDATE_FILE):
        return None
    try:
        import json
        with open(LAST_UPDATE_FILE) as f:
            return json.load(f).get("last_update")
    except (ValueError, OSError):
        return None


def mark_updated(date: str = None):
    """Record that data was refreshed on the given date (defaults to today)."""
    import json
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(LAST_UPDATE_FILE, "w") as f:
        json.dump({"last_update": date or _today_str()}, f)


def refresh_latest(status_callback=None, force=False) -> dict:
    """
    Extend the cached price data for all tickers up to today's latest bar.

    Guarded to perform network downloads at most once per calendar day unless
    ``force=True``. Safe to call on every backtest/dashboard load — it is a
    no-op (no network) when data was already refreshed today.

    Returns a summary dict: {updated, last_update, latest_date}.
    """
    today = _today_str()
    if not force and get_last_update() == today:
        latest = None
        cached = load_cached_data(config.TICKERS[0])
        if cached is not None and len(cached) > 0:
            latest = cached.index.max().strftime("%Y-%m-%d")
        if status_callback:
            status_callback(f"Data already up to date for {today} (latest bar: {latest})")
        return {"updated": False, "last_update": today, "latest_date": latest}

    # yfinance end is exclusive — request tomorrow so today's completed bar is included.
    end_excl = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    start = config.DEFAULT_BACKTEST_START

    latest_date = None
    for ticker in config.TICKERS:
        try:
            df = download_ticker(ticker, start, end_excl, status_callback)
            if df is not None and len(df) > 0:
                d = df.index.max().strftime("%Y-%m-%d")
                if latest_date is None or d > latest_date:
                    latest_date = d
        except Exception as e:
            if status_callback:
                status_callback(f"{ticker}: refresh failed ({e})")

    mark_updated(today)
    if status_callback:
        status_callback(f"Data refreshed for {today} (latest bar: {latest_date})")
    return {"updated": True, "last_update": today, "latest_date": latest_date}


def load_cached_data(ticker: str) -> pd.DataFrame | None:
    """Load cached data from CSV if it exists."""
    path = get_cache_path(ticker)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col="Date", parse_dates=True)
    return df


def save_cached_data(ticker: str, df: pd.DataFrame):
    """Save data to CSV cache."""
    path = get_cache_path(ticker)
    df.to_csv(path)


def get_date_chunks(start: str, end: str, chunk_months: int = None):
    """Split a date range into smaller chunks."""
    if chunk_months is None:
        chunk_months = config.DOWNLOAD_CHUNK_MONTHS
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    chunks = []
    current = start_dt
    while current < end_dt:
        chunk_end = current + relativedelta(months=chunk_months)
        if chunk_end > end_dt:
            chunk_end = end_dt
        chunks.append((current.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        current = chunk_end
    return chunks


def validate_cache(ticker: str, cached: pd.DataFrame, status_callback=None) -> bool:
    """
    Validate cached data against a fresh download of the last few days.
    If prices differ (e.g., due to a stock split), the cache is stale.
    Returns True if cache is valid, False if it needs full refresh.
    """
    if cached is None or len(cached) < 5:
        return True  # Nothing meaningful to validate
    
    # Pick a recent date range from the cache (last 5 trading days)
    recent = cached.tail(5)
    check_start = recent.index[0].strftime("%Y-%m-%d")
    check_end = (recent.index[-1] + timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        fresh = yf.download(ticker, start=check_start, end=check_end,
                            progress=False, auto_adjust=True)
        if fresh is None or len(fresh) == 0:
            return True  # Can't verify, assume valid
        if isinstance(fresh.columns, pd.MultiIndex):
            fresh.columns = fresh.columns.droplevel(1)
        
        # Compare Close prices on overlapping dates
        common_dates = recent.index.intersection(fresh.index)
        if len(common_dates) == 0:
            return True
        
        cached_prices = recent.loc[common_dates, "Close"]
        fresh_prices = fresh.loc[common_dates, "Close"]
        # If any price differs by more than 1%, cache is stale (likely a split)
        pct_diff = ((cached_prices - fresh_prices) / fresh_prices).abs()
        if (pct_diff > 0.01).any():
            if status_callback:
                status_callback(
                    f"{ticker}: Cache invalidated — prices changed "
                    f"(likely a stock split). Re-downloading..."
                )
            return False
    except Exception:
        pass  # Network error — assume cache is fine
    
    return True


def download_ticker(ticker: str, start: str, end: str, status_callback=None) -> pd.DataFrame:
    """
    Download data for a ticker with chunked downloads and caching.
    
    Args:
        ticker: Stock ticker symbol
        start: Start date string (YYYY-MM-DD)
        end: End date string (YYYY-MM-DD)
        status_callback: Optional callable(message: str) for progress updates
    
    Returns:
        DataFrame with OHLCV data
    """
    cached = load_cached_data(ticker)
    
    # Validate cache against fresh data to detect splits
    if cached is not None and len(cached) > 0:
        if not validate_cache(ticker, cached, status_callback):
            # Cache is stale — delete and re-download from scratch
            os.remove(get_cache_path(ticker))
            cached = None
    
    # Determine what date ranges are missing
    if cached is not None and len(cached) > 0:
        cached_start = cached.index.min()
        cached_end = cached.index.max()
        requested_start = pd.Timestamp(start)
        requested_end = pd.Timestamp(end)
        
        ranges_to_download = []
        # Need earlier data?
        if requested_start < cached_start - timedelta(days=1):
            ranges_to_download.append((start, (cached_start - timedelta(days=1)).strftime("%Y-%m-%d")))
        # Need later data?
        if requested_end > cached_end + timedelta(days=1):
            ranges_to_download.append(((cached_end + timedelta(days=1)).strftime("%Y-%m-%d"), end))
        
        if not ranges_to_download:
            if status_callback:
                status_callback(f"{ticker}: Using cached data (no download needed)")
            # Filter to requested range
            mask = (cached.index >= start) & (cached.index <= end)
            return cached.loc[mask]
    else:
        ranges_to_download = [(start, end)]
    
    # Download missing ranges in chunks
    all_new_data = []
    for range_start, range_end in ranges_to_download:
        chunks = get_date_chunks(range_start, range_end)
        total_chunks = len(chunks)
        
        for i, (chunk_start, chunk_end) in enumerate(chunks):
            if status_callback:
                status_callback(
                    f"{ticker}: Downloading chunk {i+1}/{total_chunks} "
                    f"({chunk_start} to {chunk_end})..."
                )
            
            try:
                data = yf.download(
                    ticker, start=chunk_start, end=chunk_end,
                    progress=False, auto_adjust=True
                )
                if data is not None and len(data) > 0:
                    # yfinance may return MultiIndex columns for single ticker
                    if isinstance(data.columns, pd.MultiIndex):
                        data.columns = data.columns.droplevel(1)
                    all_new_data.append(data)
            except Exception as e:
                if status_callback:
                    status_callback(f"{ticker}: Error downloading chunk {i+1}: {e}")
            
            # Delay between chunks to avoid throttling
            if i < total_chunks - 1:
                time.sleep(config.DOWNLOAD_DELAY_SEC)
    
    # Merge new data with cached data
    if all_new_data:
        new_df = pd.concat(all_new_data)
        if cached is not None and len(cached) > 0:
            combined = pd.concat([cached, new_df])
        else:
            combined = new_df
        # Remove duplicates and sort
        combined = combined[~combined.index.duplicated(keep='last')]
        combined = combined.sort_index()
        save_cached_data(ticker, combined)
        if status_callback:
            status_callback(f"{ticker}: Download complete. {len(combined)} total rows cached.")
    else:
        combined = cached if cached is not None else pd.DataFrame()
    
    # Filter to requested range
    if len(combined) > 0:
        mask = (combined.index >= start) & (combined.index <= end)
        return combined.loc[mask]
    return combined


def download_all(start: str = None, end: str = None, status_callback=None) -> dict:
    """
    Download data for all configured tickers.
    
    Returns:
        Dict of {ticker: DataFrame}
    """
    if start is None:
        start = config.DEFAULT_BACKTEST_START
    if end is None:
        end = config.DEFAULT_BACKTEST_END
    
    data = {}
    for ticker in config.TICKERS:
        data[ticker] = download_ticker(ticker, start, end, status_callback)
    return data


def ensure_data_available(start: str, end: str, status_callback=None) -> dict:
    """
    Ensure data is available for the given date range, downloading if necessary.
    Used by the dashboard when user selects a new date range.
    """
    return download_all(start, end, status_callback)


if __name__ == "__main__":
    def print_status(msg):
        print(f"  {msg}")
    
    print("Downloading data for all tickers...")
    data = download_all(status_callback=print_status)
    for ticker, df in data.items():
        print(f"\n{ticker}: {len(df)} rows ({df.index.min()} to {df.index.max()})")
