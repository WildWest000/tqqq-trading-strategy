#!/bin/bash
# ============================================================
# Cron Setup for TQQQ/SQQQ Paper Trading Bot on Oracle Cloud
# Runs at 3:30 PM EST (Mon-Fri) when US market is open
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BOT_SCRIPT="$SCRIPT_DIR/alpaca_bot.py"
ENV_FILE="$SCRIPT_DIR/.env"

echo "=== TQQQ/SQQQ Paper Trading Bot — Cron Setup ==="
echo ""

# --- Step 1: Check .env file ---
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE — fill in your Alpaca API keys:"
    cat > "$ENV_FILE" << 'EOF'
# Alpaca Paper Trading API Keys
# Get these from: https://app.alpaca.markets/paper/dashboard/overview
ALPACA_API_KEY=your-api-key-here
ALPACA_SECRET_KEY=your-secret-key-here
EOF
    chmod 600 "$ENV_FILE"
    echo ""
    echo "  ✗ Edit $ENV_FILE with your Alpaca API keys first!"
    echo "    Then re-run this script."
    exit 1
fi

# Validate keys are set
source "$ENV_FILE"
if [ "$ALPACA_API_KEY" = "your-api-key-here" ]; then
    echo "  ✗ Update ALPACA_API_KEY in $ENV_FILE"
    exit 1
fi

echo "  ✓ API keys found in .env"

# --- Step 2: Install dependencies ---
echo ""
echo "Installing Python dependencies..."
pip install --user --break-system-packages -q alpaca-trade-api yfinance pandas numpy 2>/dev/null || \
pip install --user -q alpaca-trade-api yfinance pandas numpy
echo "  ✓ Dependencies installed"

# --- Step 2b: Ensure log directories exist ---
# (the bot writes to paper_trade/logs/; data updates and any ~/logs cron go to ~/logs)
mkdir -p "$SCRIPT_DIR/logs" "$HOME/logs"
echo "  ✓ Log directories ready: $SCRIPT_DIR/logs and $HOME/logs"

# --- Step 3: Create wrapper script ---
WRAPPER="$SCRIPT_DIR/run_bot.sh"
cat > "$WRAPPER" << WRAPPER_EOF
#!/bin/bash
# Wrapper script for cron — loads env vars and runs the bot
set -e

# Load API keys
source "$ENV_FILE"
export ALPACA_API_KEY
export ALPACA_SECRET_KEY

# Ensure Python can find user-installed packages
export PATH="\$HOME/.local/bin:\$PATH"

# Run the bot
cd "$SCRIPT_DIR/.."
python3 "$BOT_SCRIPT" >> "$SCRIPT_DIR/logs/cron.log" 2>&1
WRAPPER_EOF
chmod +x "$WRAPPER"
echo "  ✓ Wrapper script created: $WRAPPER"

# --- Step 3b: Create data-update wrapper script ---
# Keeps the cached price data current to the latest bar (runs after market close).
UPDATE_WRAPPER="$SCRIPT_DIR/run_update.sh"
cat > "$UPDATE_WRAPPER" << UPDATE_EOF
#!/bin/bash
# Wrapper script for cron — refreshes cached price data to the latest bar
set -e

# Ensure Python can find user-installed packages
export PATH="\$HOME/.local/bin:\$PATH"

cd "$REPO_DIR"
python3 main.py update >> "\$HOME/logs/data_update.log" 2>&1
UPDATE_EOF
chmod +x "$UPDATE_WRAPPER"
echo "  ✓ Data-update wrapper created: $UPDATE_WRAPPER"

# --- Step 3c: Create snapshot wrapper script ---
# Refreshes the dashboard's portfolio snapshot (equity/cash/positions) without
# trading, so the Portfolio Summary stays fresh intraday and on no-trade days.
SNAPSHOT_WRAPPER="$SCRIPT_DIR/run_snapshot.sh"
cat > "$SNAPSHOT_WRAPPER" << SNAPSHOT_EOF
#!/bin/bash
# Wrapper script for cron — writes a portfolio snapshot to state.json (no orders)
set -e

# Load API keys
source "$ENV_FILE"
export ALPACA_API_KEY
export ALPACA_SECRET_KEY

# Ensure Python can find user-installed packages
export PATH="\$HOME/.local/bin:\$PATH"

cd "$SCRIPT_DIR/.."
python3 "$BOT_SCRIPT" --snapshot >> "$SCRIPT_DIR/logs/cron.log" 2>&1
SNAPSHOT_EOF
chmod +x "$SNAPSHOT_WRAPPER"
echo "  ✓ Snapshot wrapper created: $SNAPSHOT_WRAPPER"

# --- Step 4: Set up cron ---
# 3:30 PM EST = 19:30 UTC (EST = UTC-5)
# During EDT (daylight saving, Mar-Nov): 3:30 PM EDT = 19:30 UTC
# During EST (standard, Nov-Mar): 3:30 PM EST = 20:30 UTC
#
# To handle DST automatically, we schedule both and let the bot
# check if market is open (it exits immediately if market is closed).
CRON_LINE_EDT="30 19 * * 1-5 $WRAPPER"
CRON_LINE_EST="30 20 * * 1-5 $WRAPPER"
# Data refresh: 22:00 UTC (after US market close in both EST/EDT)
CRON_LINE_UPDATE="0 22 * * 1-5 $UPDATE_WRAPPER"
# Portfolio snapshot: hourly during US market hours (13:00-21:00 UTC, Mon-Fri)
# keeps the dashboard's Portfolio Summary fresh even when no trade happens.
CRON_LINE_SNAPSHOT="0 13-21 * * 1-5 $SNAPSHOT_WRAPPER"

echo ""
echo "Setting up cron job..."

# Remove any existing bot/update/snapshot entries
(crontab -l 2>/dev/null || true) | grep -v "$WRAPPER" | grep -v "$UPDATE_WRAPPER" | grep -v "$SNAPSHOT_WRAPPER" > /tmp/cron_tmp || true

# Add both time slots
echo "$CRON_LINE_EDT" >> /tmp/cron_tmp
echo "$CRON_LINE_EST" >> /tmp/cron_tmp
# Add daily data refresh
echo "$CRON_LINE_UPDATE" >> /tmp/cron_tmp
# Add hourly portfolio snapshot
echo "$CRON_LINE_SNAPSHOT" >> /tmp/cron_tmp

crontab /tmp/cron_tmp
rm /tmp/cron_tmp

echo "  ✓ Cron jobs installed:"
echo "    $CRON_LINE_EDT"
echo "    $CRON_LINE_EST"
echo "    $CRON_LINE_UPDATE"
echo "    $CRON_LINE_SNAPSHOT"
echo ""
echo "  The bot runs at both 19:30 and 20:30 UTC to cover EST/EDT."
echo "  It checks if the market is open and exits early if not."
echo "  Price data is refreshed daily at 22:00 UTC (after market close)."
echo "  The portfolio snapshot refreshes hourly during market hours."

# --- Step 5: Verify ---
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Verify cron is set up:"
echo "  crontab -l"
echo ""
echo "Test the bot manually:"
echo "  source $ENV_FILE && python3 $BOT_SCRIPT"
echo ""
echo "View logs:"
echo "  tail -f $SCRIPT_DIR/logs/cron.log"
echo "  tail -f $HOME/logs/data_update.log"
echo "  ls $SCRIPT_DIR/logs/"
echo ""
echo "View/edit state:"
echo "  cat $SCRIPT_DIR/state.json"
