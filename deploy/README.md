# Oracle Cloud Deployment

## Quick Start

1. **Create OCI Compute Instance**
   - Shape: `VM.Standard.E2.1.Micro` (Always Free)
   - OS: Ubuntu 22.04
   - Add your SSH public key

2. **Open port 8050** (for dashboard)
   - OCI Console → Networking → VCN → Security List
   - Add Ingress Rule: Source `0.0.0.0/0`, Protocol TCP, Port 8050

3. **Deploy**
   ```bash
   scp deploy/setup.sh ubuntu@<your-instance-ip>:~/
   ssh ubuntu@<your-instance-ip>
   bash setup.sh
   ```

## What Gets Deployed

| Component | Schedule | Description |
|-----------|----------|-------------|
| Trading Bot | Weekdays 3:30 PM EST | Executes trades via Alpaca API |
| Dashboard | Always running | Web UI on port 8050 |
| Health Monitor | Every hour (if configured) | Checks services, alerts on failures |

## Files

- `setup.sh` — Full automated setup (run once)
- `update.sh` — Pull latest code & restart services
- `monitor.sh` — Health check script (add to cron)

## Common Commands

```bash
# Check dashboard
sudo systemctl status trading-dashboard
sudo systemctl restart trading-dashboard

# View logs
tail -f ~/logs/trading.log
tail -f ~/tqqq-trading-strategy/paper_trade/logs/trade_$(date +%Y%m%d).log

# Manual bot run
source ~/.trading_env
cd ~/tqqq-trading-strategy
python3 paper_trade/alpaca_bot.py

# Update to latest code
bash ~/tqqq-trading-strategy/deploy/update.sh

# Check cron
crontab -l
```

## Security Notes

- Credentials stored in `~/.trading_env` (chmod 600)
- Dashboard has no auth by default — consider:
  - Restricting Security List to your IP only
  - Adding nginx with basic auth in front
  - Using OCI's network security groups

## Timezone

OCI instances use UTC by default. The cron is set to 19:30 UTC = 3:30 PM EST.

To change timezone on the instance:
```bash
sudo timedatectl set-timezone America/New_York
```
Then update cron to `30 15 * * 1-5` instead.
