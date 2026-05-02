# TQQQ/SQQQ Paper Trading Bot — Alpaca

Automated paper trading bot that runs the regime-based TQQQ/SQQQ strategy
on Alpaca's paper trading platform.

## How It Works

1. Runs daily at **3:30 PM EST** (Mon–Fri) via cron
2. Downloads latest market data from yfinance
3. Computes regime (bull/neutral/bear/crisis) and target allocation
4. Applies trailing stop logic (25% drawdown → go to cash)
5. Submits market orders on Alpaca to rebalance (whole shares only)
6. Logs every decision and trade to `logs/`

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
source .env
python3 alpaca_bot.py
```

### 4. Monitor
```bash
# Live logs
tail -f logs/cron.log

# Today's detailed log
cat logs/trade_$(date +%Y%m%d).log

# Current state (trailing stop, regime)
cat state.json
```

## Files

| File | Purpose |
|------|---------|
| `alpaca_bot.py` | Main bot — computes signals, executes trades |
| `cron_setup.sh` | One-command cron + dependency setup |
| `.env` | API keys (created by setup, not committed) |
| `state.json` | Persistent state (trailing stop peak, cash mode) |
| `run_bot.sh` | Cron wrapper (loads env, runs bot) |
| `logs/` | Daily trade logs |

## Cron Schedule

The bot runs at **both** 19:30 and 20:30 UTC to handle EST/EDT daylight saving:
- 19:30 UTC = 3:30 PM EDT (Mar–Nov)
- 20:30 UTC = 3:30 PM EST (Nov–Mar)

The bot checks if the market is open and exits immediately if not,
so the "wrong" time slot is a harmless no-op.

## Safety

- **Paper trading only** — uses `paper-api.alpaca.markets`
- No fractional shares — whole shares only
- 10% drift threshold before rebalancing (avoids excessive trading)
- All decisions logged with timestamps
- Trailing stop prevents catastrophic drawdowns
