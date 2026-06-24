"""
Read and parse Alpaca paper-trading bot artifacts for the dashboard.

The live bot (paper_trade/alpaca_bot.py) persists:
- paper_trade/state.json: trailing-stop state {portfolio_peak, in_cash_mode,
  cash_mode_days, last_regime, last_run} plus a "portfolio" snapshot
  {equity, cash, tqqq_shares/value, sqqq_shares/value, day_pl, day_pl_pct, as_of}.
- paper_trade/logs/trade_YYYYMM.log: human-readable monthly logs whose lines
  follow the format "%(asctime)s [%(levelname)s] %(message)s". Order lines carry
  the submit (reference) price and, once filled, the average fill price.

This module loads that state and extracts structured order confirmations and
key events from the logs so the dashboard can surface them without re-running
anything.
"""
import os
import re
import json
import glob

PAPER_TRADE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trade")
LOG_DIR = os.path.join(PAPER_TRADE_DIR, "logs")
STATE_FILE = os.path.join(PAPER_TRADE_DIR, "state.json")

# Matches: "2026-06-19 19:30:01,123 [INFO] message text"
_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[.,]?\d*(?: [A-Z]{2,4})? \[(?P<level>\w+)\] (?P<msg>.*)$"
)

# Order line, e.g. "Submitting: BUY 5 TQQQ @ ~$73.05 (ref)" or
# "✓ Order filled: BUY 5 TQQQ @ $73.21 (submitted @ $73.05, slippage +0.22%)"
_ORDER_RE = re.compile(
    r"(?P<side>buy|sell)\s+(?P<qty>\d+)\s+(?P<symbol>[A-Z]{2,5})", re.IGNORECASE
)

# Prices embedded in order lines. The first "@ $X" is the primary price for the
# line (ref price on a submit line, fill price on a filled line). A trailing
# "submitted @ $Y" gives the original reference price on a filled line.
_PRICE_RE = re.compile(r"@\s*~?\$(?P<price>[\d,]+\.?\d*)")
_SUBMITTED_PRICE_RE = re.compile(r"submitted @\s*\$(?P<price>[\d,]+\.?\d*)")


def load_bot_state() -> dict | None:
    """Return the bot's persisted state.json as a dict, or None if absent."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_log_files() -> list[dict]:
    """
    List available bot log files, newest first.

    Returns a list of {"name", "path", "mtime", "size"} dicts covering the
    monthly trade logs and a cron.log if present.
    """
    if not os.path.isdir(LOG_DIR):
        return []
    paths = glob.glob(os.path.join(LOG_DIR, "*.log"))
    files = []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        files.append({
            "name": os.path.basename(p),
            "path": p,
            "mtime": st.st_mtime,
            "size": st.st_size,
        })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def read_log_file(path: str, max_lines: int = 1000) -> str:
    """Return the last ``max_lines`` lines of a log file (defends against huge files)."""
    if not path or not os.path.isfile(path):
        return ""
    # Only allow reading files inside the bot log directory.
    if os.path.commonpath([os.path.abspath(path), LOG_DIR]) != LOG_DIR:
        return ""
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"(could not read log: {e})"
    if not lines:
        return "(log file is empty)"
    return "".join(lines[-max_lines:])


def _classify(msg: str) -> tuple[str, dict]:
    """Categorize a log message and pull out structured order details."""
    low = msg.lower()
    extra = {}
    order = _ORDER_RE.search(msg)
    if order:
        extra = {
            "side": order.group("side").lower(),
            "qty": int(order.group("qty")),
            "symbol": order.group("symbol").upper(),
        }

    def _to_float(s):
        return float(s.replace(",", ""))

    if "order filled" in low:
        # "@ $fill ... (submitted @ $ref ...)"
        prices = _PRICE_RE.findall(msg)
        if prices:
            extra["fill_price"] = _to_float(prices[0])
        sub = _SUBMITTED_PRICE_RE.search(msg)
        if sub:
            extra["submit_price"] = _to_float(sub.group("price"))
        return "order_filled", extra
    if "order failed" in low or "✗" in msg:
        return "order_failed", extra
    if low.startswith("submitting") or "order submitted" in low:
        # Reference / submit-time price (the first "@ $X" on the line).
        p = _PRICE_RE.search(msg)
        if p:
            extra["submit_price"] = _to_float(p.group("price"))
        return "order_submitted", extra
    if "protective" in low and "stop" in low:
        # Intraday protective stop placement / status (not a drawdown trigger).
        return "protective_stop", extra
    if "trailing stop" in low:
        return "trailing_stop", extra
    if "rebalance complete" in low:
        return "rebalance", extra
    if "no action needed" in low or "no rebalance needed" in low:
        return "no_action", extra
    if "cash mode" in low or "exiting cash mode" in low:
        return "cash_mode", extra
    return "other", extra


# Event categories surfaced as "trading confirmations".
CONFIRMATION_TYPES = {
    "order_submitted", "order_filled", "order_failed",
    "trailing_stop", "protective_stop", "rebalance", "no_action", "cash_mode",
}


def parse_confirmations(max_events: int = 200) -> list[dict]:
    """
    Parse all daily trade logs into a chronological list of trade-related events.

    Returns newest-first list of dicts:
        {"timestamp", "level", "type", "message", and order fields if present}
    Only confirmation-worthy events (orders, rebalances, trailing stops) are kept.
    """
    if not os.path.isdir(LOG_DIR):
        return []
    # Daily trade logs only (sorted by name => chronological).
    paths = sorted(glob.glob(os.path.join(LOG_DIR, "trade_*.log")))
    events = []
    for path in paths:
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    m = _LOG_LINE_RE.match(line.strip())
                    if not m:
                        continue
                    etype, extra = _classify(m.group("msg"))
                    if etype not in CONFIRMATION_TYPES:
                        continue
                    events.append({
                        "timestamp": m.group("ts"),
                        "level": m.group("level"),
                        "type": etype,
                        "message": m.group("msg"),
                        **extra,
                    })
        except OSError:
            continue
    events.reverse()  # newest first
    return events[:max_events]
