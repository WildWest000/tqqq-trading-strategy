#!/bin/bash
# =============================================================================
# Health check & monitoring script
# Run via cron every hour to ensure services are healthy
# 
# Add to cron:
#   0 * * * * bash /home/ubuntu/tqqq-trading-strategy/deploy/monitor.sh
# =============================================================================

LOG_DIR="/home/$(whoami)/logs"
INSTALL_DIR="/home/$(whoami)/tqqq-trading-strategy"
ALERT_FILE="$LOG_DIR/alerts.log"

timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

alert() {
    echo "[$(timestamp)] ALERT: $1" >> "$ALERT_FILE"
    echo "[$(timestamp)] ALERT: $1"
    # Uncomment below to send email alerts:
    # echo "$1" | mail -s "Trading Bot Alert" your@email.com
}

# Check if dashboard is running
if ! systemctl is-active --quiet trading-dashboard; then
    alert "Dashboard service is DOWN. Restarting..."
    sudo systemctl restart trading-dashboard
fi

# Check dashboard responds
if ! curl -s -o /dev/null -w "%{http_code}" http://localhost:8050 | grep -q "200"; then
    alert "Dashboard not responding on port 8050"
fi

# Check last trade log (should have run today if market was open)
LAST_LOG=$(ls -t "$INSTALL_DIR/paper_trade/logs/" 2>/dev/null | head -1)
if [ -n "$LAST_LOG" ]; then
    LAST_MOD=$(stat -c %Y "$INSTALL_DIR/paper_trade/logs/$LAST_LOG")
    NOW=$(date +%s)
    HOURS_AGO=$(( (NOW - LAST_MOD) / 3600 ))
    
    # Alert if no log in 48 hours (weekends excluded)
    DAY_OF_WEEK=$(date +%u)
    if [ "$HOURS_AGO" -gt 48 ] && [ "$DAY_OF_WEEK" -lt 6 ]; then
        alert "No trade log in ${HOURS_AGO} hours. Bot may not be running."
    fi
fi

# Check disk space
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 90 ]; then
    alert "Disk usage at ${DISK_USAGE}%"
fi

# Log heartbeat
echo "[$(timestamp)] Health check OK" >> "$LOG_DIR/monitor.log"
