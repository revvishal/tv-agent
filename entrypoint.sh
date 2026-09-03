#!/bin/bash
set -e

# Cron jobs run in a minimal environment and don't see variables passed via
# `docker run -e` / docker-compose, so dump the ones we need to a file that
# the cron job sources before each run (see crontab).
printenv | grep -E '^(API_BASE_URL|JWT_TOKEN|REQUEST_DELAY_SECONDS|PLAYWRIGHT_HEADED|SCRAPER_DEBUG)=' \
    > /app/container.env || true

echo "tv-agent container started. Cron schedule:"
cat /etc/cron.d/tv-agent-cron
echo "Tailing /var/log/tv-agent.log (run logs appear here after each scheduled run)."

# Run once immediately on startup so you get instant feedback, then hand off
# to cron for the recurring schedule. Comment out the next line if you only
# want the cron schedule to control runs.
cd /app && python -m src.main >> /var/log/tv-agent.log 2>&1 || true

cron
tail -f /var/log/tv-agent.log
