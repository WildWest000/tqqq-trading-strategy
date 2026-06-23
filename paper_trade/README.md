# TQQQ/SQQQ Paper Trading Bot — Alpaca

Automated paper trading bot that runs the regime-based TQQQ/SQQQ strategy
on Alpaca's paper trading platform.

## How It Works

1. Runs daily at **3:30 PM EST** (Mon–Fri) via cron
2. Downloads latest market data from yfinance
3. Computes regime (bull/neutral/bear/crisis) and target allocation
4. Applies trailing stop logic (25% drawdown → go to cash)
5. Cancels any stale protective stop, then submits market orders to rebalance (whole shares only)
6. Arms an **intraday protective stop** on the TQQQ position (trailing, default 8%) so a fast crash is cut within the day
7. Logs every decision and trade to `logs/`

## Quick Start

### 1. Get Alpaca API Keys
- Sign up at https://alpaca.markets (free)
- Go to **Paper Trading** → copy API Key + Secret

### 2. Deploy to Oracle Cloud
```bash
# SSH into your Oracle Cloud instance
ssh opc@your-instance-ip

# Clone or copy the TradingStrategy1 directory
scp -r TradingStrategy1/ opc@your-instance-ip:~/

# Run setup
cd ~/TradingStrategy1/paper_trade
bash cron_setup.sh
# Edit .env with your API keys, then run again:
nano .env
bash cron_setup.sh
```

### 3. Test Manually
```bash
source .env   # or: source ~/.trading_env

# Preview the signal + intended trades WITHOUT placing orders (recommended first):
python3 alpaca_bot.py --dry-run

# Real run (places orders on the configured account):
python3 alpaca_bot.py
```

### Paper vs Live

The endpoint is selected by environment variables and **defaults to paper** for safety:

```bash
export ALPACA_API_KEY="..."     # NOTE: paper and live use DIFFERENT keys
export ALPACA_SECRET_KEY="..."
export ALPACA_PAPER="true"      # "false" / "0" / "live" → real-money trading
# (advanced) export ALPACA_BASE_URL="https://api.alpaca.markets"
```

The bot logs its active mode loudly each run (`Alpaca mode: PAPER` or `LIVE 🔴 REAL MONEY`).
When switching to live, update the API key/secret with your **live** keys as well as the flag.

### 4. Monitor
```bash
# This month's detailed log (one file per month)
cat logs/trade_$(date +%Y%m).log

# Cron stdout/stderr (whole-chain output)
tail -f ~/logs/trading.log

# Current state (trailing stop, regime)
cat state.json
```

## Files

| File | Purpose |
|------|---------|
| `alpaca_bot.py` | Main bot — computes signals (shared `generate_signals`), executes trades |
| `cron_setup.sh` | One-command cron + dependency setup |
| `.env` | API keys (created by setup, not committed) |
| `state.json` | Persistent state (trailing stop peak, cash mode) |
| `logs/` | Monthly trade logs (`trade_YYYYMM.log`) |

## Cron Schedule

The bot runs at **3:30 PM ET** on weekdays (`30 19 * * 1-5` in summer/EDT; use
`30 20 * * 1-5` in winter/EST, or schedule both — the bot no-ops when the market is closed).

> **Important — `SHELL=/bin/bash`:** the cron command uses `source`, a bash builtin.
> Cron defaults to `/bin/sh` (dash on Ubuntu), where `source` does not exist, so the job
> fails **silently**. Add `SHELL=/bin/bash` as the first line of the crontab. Wrapping the
> whole chain in braces ensures all output is logged:
>
> ```
> SHELL=/bin/bash
> 30 19 * * 1-5 { source ~/.trading_env && source ~/trading-venv/bin/activate && cd ~/tqqq-trading-strategy && python3 paper_trade/alpaca_bot.py; } >> ~/logs/trading.log 2>&1
> ```

## Dashboard Service

On the server the dashboard runs as a systemd service (`trading-dashboard.service`,
`Restart=always`, boot-enabled). Manage it with `systemctl` — **never** `kill`/`pkill`
(systemd just respawns it):

```bash
sudo systemctl restart trading-dashboard   # reload after a git pull
sudo systemctl status trading-dashboard
journalctl -u trading-dashboard -f          # live logs
```

## Safety

- **Paper trading by default** — `ALPACA_PAPER` must be explicitly set to `false`/`live`
  (and live keys supplied) to trade real money
- `--dry-run` previews the signal and intended orders without placing any
- No fractional shares — whole shares only
- 10% drift threshold before rebalancing (avoids excessive trading)
- All decisions logged with timestamps
- Trailing stop prevents catastrophic drawdowns
- **Intraday protective stop** (`INTRADAY_STOP_ENABLED`, default on) places a
  broker-side trailing stop (default 8%) on TQQQ after each buy, cutting fast
  single-day crashes the daily loop can't react to; re-armed every run
