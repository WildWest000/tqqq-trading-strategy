#!/bin/bash
# =============================================================================
# Update script - pull latest code and restart services
# Usage: bash deploy/update.sh
# =============================================================================

set -e

INSTALL_DIR="/home/$(whoami)/tqqq-trading-strategy"
VENV_DIR="/home/$(whoami)/trading-venv"

echo "Updating TQQQ Trading Strategy..."

cd "$INSTALL_DIR"

# Pull latest
echo "  Pulling latest code..."
git pull

# Update dependencies
echo "  Updating dependencies..."
source "$VENV_DIR/bin/activate"
pip install -r requirements.txt -q

# Restart dashboard
echo "  Restarting dashboard..."
sudo systemctl restart trading-dashboard

echo "  Done! Dashboard restarted."
echo "  Check status: sudo systemctl status trading-dashboard"
