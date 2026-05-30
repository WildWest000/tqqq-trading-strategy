#!/bin/bash
# =============================================================================
# Oracle Cloud (OCI) Deployment Script for TQQQ Trading Strategy
# 
# Usage:
#   1. SSH into your OCI instance
#   2. Copy this script: scp deploy/setup.sh ubuntu@<ip>:~/
#   3. Run: bash setup.sh
#
# Prerequisites:
#   - OCI Compute instance (Ubuntu 22.04, free tier VM.Standard.E2.1.Micro works)
#   - SSH access configured
#   - Alpaca API keys ready (paper or live)
# =============================================================================

set -e

REPO_URL="https://github.com/WildWest000/tqqq-trading-strategy.git"
INSTALL_DIR="/home/$(whoami)/tqqq-trading-strategy"
VENV_DIR="/home/$(whoami)/trading-venv"
LOG_DIR="/home/$(whoami)/logs"

echo "============================================"
echo "  TQQQ Trading Strategy - OCI Deployment"
echo "============================================"
echo ""

# --- 1. System Dependencies ---
echo "[1/6] Installing system dependencies..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-pip python3-venv git curl

# --- 2. Clone Repository ---
echo "[2/6] Cloning repository..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  Repository already exists, pulling latest..."
    cd "$INSTALL_DIR" && git pull
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# --- 3. Python Virtual Environment ---
echo "[3/6] Setting up Python virtual environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install alpaca-trade-api -q
echo "  Python packages installed."

# --- 4. Environment Variables ---
echo "[4/6] Configuring environment..."
ENV_FILE="/home/$(whoami)/.trading_env"

if [ ! -f "$ENV_FILE" ]; then
    echo ""
    read -p "  Enter Alpaca API Key: " ALPACA_KEY
    read -p "  Enter Alpaca Secret Key: " ALPACA_SECRET
    read -p "  Use paper trading? (y/n): " USE_PAPER
    
    if [ "$USE_PAPER" = "y" ] || [ "$USE_PAPER" = "Y" ]; then
        BASE_URL="https://paper-api.alpaca.markets"
    else
        BASE_URL="https://api.alpaca.markets"
    fi
    
    cat > "$ENV_FILE" << EOF
export ALPACA_API_KEY="$ALPACA_KEY"
export ALPACA_SECRET_KEY="$ALPACA_SECRET"
export ALPACA_BASE_URL="$BASE_URL"
EOF
    chmod 600 "$ENV_FILE"
    echo "  Credentials saved to $ENV_FILE"
else
    echo "  Environment file already exists at $ENV_FILE"
fi

# --- 5. Create Log Directory ---
echo "[5/6] Setting up logging..."
mkdir -p "$LOG_DIR"
mkdir -p "$INSTALL_DIR/paper_trade/logs"

# --- 6. Install Systemd Services & Cron ---
echo "[6/6] Installing services..."

# Trading bot cron job (3:30 PM EST = 19:30 UTC on weekdays)
CRON_CMD="30 19 * * 1-5 source $ENV_FILE && source $VENV_DIR/bin/activate && cd $INSTALL_DIR && python3 paper_trade/alpaca_bot.py >> $LOG_DIR/trading.log 2>&1"

# Check if cron already exists
(crontab -l 2>/dev/null | grep -v "alpaca_bot.py"; echo "$CRON_CMD") | crontab -
echo "  Cron job installed: weekdays at 3:30 PM EST (19:30 UTC)"

# Dashboard systemd service
sudo tee /etc/systemd/system/trading-dashboard.service > /dev/null << EOF
[Unit]
Description=TQQQ Trading Strategy Dashboard
After=network.target

[Service]
User=$(whoami)
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python3 dashboard.py
Environment="PATH=$VENV_DIR/bin:/usr/bin"
EnvironmentFile=$ENV_FILE
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable trading-dashboard
sudo systemctl start trading-dashboard
echo "  Dashboard service started on port 8050"

# --- Done ---
echo ""
echo "============================================"
echo "  Deployment Complete!"
echo "============================================"
echo ""
echo "  Bot schedule:  Weekdays 3:30 PM EST (19:30 UTC)"
echo "  Dashboard:     http://$(curl -s ifconfig.me):8050"
echo "  Logs:          $LOG_DIR/trading.log"
echo "  Bot logs:      $INSTALL_DIR/paper_trade/logs/"
echo "  Env file:      $ENV_FILE"
echo ""
echo "  IMPORTANT: Open port 8050 in OCI Security List"
echo "  (Networking → Virtual Cloud Network → Security List → Ingress Rules)"
echo ""
echo "  Commands:"
echo "    Check dashboard:  sudo systemctl status trading-dashboard"
echo "    View bot logs:    tail -f $LOG_DIR/trading.log"
echo "    Manual run:       source $ENV_FILE && cd $INSTALL_DIR && python3 paper_trade/alpaca_bot.py"
echo "    Update code:      cd $INSTALL_DIR && git pull && sudo systemctl restart trading-dashboard"
echo ""
